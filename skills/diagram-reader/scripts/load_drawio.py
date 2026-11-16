"""draw.io (diagrams.net) の図を読み込んで、共通の中間形式 (Excalidraw の要素に似た辞書の並び) にする。

読める形式:
  .drawio / .dio         XML (mxfile)。先頭に XML コメントがあってもよい
  .drawio.svg / .dio.svg SVG の content 属性に、mxfile の XML が入っているもの (Obsidian の diagrams.net プラグインが作る)
  図の中身 (diagram) が、圧縮されたもの (base64 + deflate + URL エンコード) と、圧縮されていないもの
  複数のページ

変換の考え方:
  - 箱 (vertex) の座標は、親 (swimlane などの入れ物) の左上が基準なので、絶対座標にする
  - 箱の形: ellipse / terminator は楕円、rhombus / decision はひし形、それ以外は長方形 (円柱・書類などは、形の名前を付ける)
  - ラベルは HTML のことがあるので、タグを外して文字にする (<br> は改行)
  - 矢印 (edge) は、source / target を結合先にする。矢じりは、endArrow / startArrow から決める
  - 矢印のラベルは、edge の value と、edge の子の edgeLabel の両方を読む
標準ライブラリだけ。ファイルの読み書きはしない。
"""
from __future__ import annotations

import base64
import html
import re
import urllib.parse
import xml.etree.ElementTree as ET
import zlib
from dataclasses import dataclass, field
from typing import Any

MAX_INFLATED = 30 * 1024 * 1024
MAX_CELLS = 10000


class DrawioError(Exception):
    """利用者に見せてよいメッセージ。"""


# ---------------------------------------------------------------------------
# XML の取り出し
# ---------------------------------------------------------------------------

def _guard(data: str) -> None:
    if re.search(r"<!(doctype|entity)", data, re.IGNORECASE):
        raise DrawioError("XML に DTD / 実体の宣言が含まれているため、処理しません (安全のため)")


def _parse(data: str) -> ET.Element:
    _guard(data)
    try:
        return ET.fromstring(data)
    except ET.ParseError as exc:
        raise DrawioError(f"XML を読めません: {exc}") from exc


def extract_mxfile(text: str, name: str = "") -> str:
    """ファイルの内容から、mxfile (または mxGraphModel) の XML の文字列を取り出す。"""
    stripped = text.lstrip("﻿")
    _guard(stripped)  # ファイル全体を検査する (先頭の DOCTYPE を切り捨てる前に)
    if name.lower().endswith(".svg") or re.match(r"\s*(<\?xml[^>]*\?>\s*)?(<!--.*?-->\s*)*<svg\b", stripped, re.S):
        root = _parse(re.sub(r"^\s*<\?xml[^>]*\?>", "", stripped).strip())
        content = root.attrib.get("content")
        if not content:
            raise DrawioError("SVG に draw.io のデータ (content 属性) がありません。draw.io が作った SVG ではないかもしれません")
        return content
    start = min([i for i in (stripped.find("<mxfile"), stripped.find("<mxGraphModel")) if i >= 0] or [-1])
    if start < 0:
        raise DrawioError("draw.io のデータ (mxfile) が見つかりません。draw.io のファイルではないかもしれません")
    return stripped[start:]


def decode_diagram(text: str) -> str:
    """圧縮された diagram の中身 (base64 → 生の deflate → URL エンコード) を、XML の文字列にする。"""
    try:
        raw = base64.b64decode("".join(text.split()), validate=False)
    except ValueError as exc:
        raise DrawioError("図のデータを base64 として読めません") from exc
    try:
        inflater = zlib.decompressobj(-15)
        data = inflater.decompress(raw, MAX_INFLATED + 1)
        if len(data) > MAX_INFLATED:
            raise DrawioError("展開すると大きすぎる図です (安全のため処理しません)")
    except zlib.error:
        data = raw  # 圧縮されていない (URL エンコードだけの) 場合
    try:
        return urllib.parse.unquote(data.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise DrawioError("図のデータを文字として読めません (壊れている可能性があります)") from exc


def load_pages(text: str, name: str = "") -> list[tuple[str, dict[str, Any]]]:
    """(ページの名前, 中間形式の図) の一覧を返す。"""
    root = _parse(extract_mxfile(text, name))
    models: list[tuple[str, ET.Element]] = []
    if root.tag == "mxGraphModel":
        models.append(("Page-1", root))
    elif root.tag == "mxfile":
        for i, d in enumerate(root.findall("diagram"), 1):
            page = d.attrib.get("name") or f"Page-{i}"
            inner = d.find("mxGraphModel")
            if inner is None:
                if not (d.text or "").strip():
                    continue
                inner = _parse(decode_diagram(d.text))
                if inner.tag != "mxGraphModel":
                    raise DrawioError("図のデータの中身が mxGraphModel ではありません")
            models.append((page, inner))
    else:
        raise DrawioError(f"draw.io のデータではありません (先頭の要素: {root.tag})")
    if not models:
        raise DrawioError("図のページがありません")
    return [(page, model_to_scene(model)) for page, model in models]


# ---------------------------------------------------------------------------
# 変換
# ---------------------------------------------------------------------------

@dataclass
class Cell:
    cid: str
    value: str = ""
    style: dict[str, str] = field(default_factory=dict)
    vertex: bool = False
    edge: bool = False
    parent: str = ""
    source: str = ""
    target: str = ""
    x: float = 0
    y: float = 0
    w: float = 0
    h: float = 0
    points: list[tuple[float, float]] = field(default_factory=list)
    source_point: tuple[float, float] | None = None
    target_point: tuple[float, float] | None = None
    visible: bool = True


SHAPE_LABELS = {
    "cylinder": "円柱", "cylinder3": "円柱", "datastore": "データストア", "mxgraph.flowchart.database": "円柱(データベース)",
    "mxgraph.flowchart.document": "書類", "mxgraph.flowchart.multi-document": "複数の書類",
    "mxgraph.flowchart.predefined_process": "定義済み処理", "mxgraph.flowchart.manual_input": "手入力",
    "mxgraph.flowchart.preparation": "準備", "mxgraph.flowchart.data": "データ(平行四辺形)", "mxgraph.flowchart.stored_data": "保存データ",
    "mxgraph.flowchart.sort": "分類", "mxgraph.flowchart.summing_function": "合計", "mxgraph.flowchart.or": "OR",
    "mxgraph.flowchart.on-page_reference": "ページ内の参照", "mxgraph.flowchart.off-page_reference": "ページ外の参照",
    "parallelogram": "平行四辺形", "hexagon": "六角形", "triangle": "三角形", "cloud": "雲", "actor": "人", "umlactor": "人",
    "note": "ノート", "card": "カード", "step": "ステップ", "process": "処理", "folder": "フォルダ", "component": "コンポーネント",
    "trapezoid": "台形", "callout": "吹き出し", "document": "書類",
}


def parse_style(style: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for i, token in enumerate(style.split(";")):
        token = token.strip()
        if not token:
            continue
        if "=" in token:
            key, _, value = token.partition("=")
            out[key.strip()] = value.strip()
        else:
            out[token] = "1"
            if i == 0:
                out["_first"] = token
    return out


def clean_label(value: str) -> str:
    """HTML のラベルを、文字にする (<br> は改行、div / p / li の終わりは段落の区切り)。"""
    text = value or ""
    if "<" in text or "&" in text:
        text = re.sub(r"<\s*br\s*/?\s*>", "\n", text, flags=re.IGNORECASE)  # 改行 (強制の折り返し)
        text = re.sub(r"</\s*(?:div|p|li|tr|h[1-6])\s*>", "\n\n", text, flags=re.IGNORECASE)  # 段落の区切り
        text = re.sub(r"<[^>]+>", "", text)
        text = html.unescape(text).replace("\xa0", " ")
    text = re.sub(r"\n{3,}", "\n\n", text.replace("\r", ""))
    return text.strip()


def _f(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _read_cells(model: ET.Element) -> dict[str, Cell]:
    root = model.find("root")
    if root is None:
        raise DrawioError("mxGraphModel に root がありません")
    cells: dict[str, Cell] = {}
    for el in list(root):
        wrapper = el if el.tag in ("object", "UserObject") else None
        mx = el.find("mxCell") if wrapper is not None else (el if el.tag == "mxCell" else None)
        if mx is None:
            continue
        cid = (wrapper.attrib.get("id") if wrapper is not None else mx.attrib.get("id")) or ""
        if not cid:
            continue
        value = (wrapper.attrib.get("label") if wrapper is not None else mx.attrib.get("value")) or ""
        cell = Cell(cid, value, parse_style(mx.attrib.get("style", "")), mx.attrib.get("vertex") == "1", mx.attrib.get("edge") == "1",
                    mx.attrib.get("parent", ""), mx.attrib.get("source", ""), mx.attrib.get("target", ""),
                    visible=(wrapper.attrib.get("visible") if wrapper is not None else mx.attrib.get("visible")) != "0")
        geo = mx.find("mxGeometry")
        if geo is not None:
            cell.x, cell.y, cell.w, cell.h = _f(geo.attrib.get("x")), _f(geo.attrib.get("y")), _f(geo.attrib.get("width")), _f(geo.attrib.get("height"))
            arr = geo.find("Array")
            if arr is not None and arr.attrib.get("as") == "points":
                cell.points = [(_f(p.attrib.get("x")), _f(p.attrib.get("y"))) for p in arr.findall("mxPoint")]
            for p in geo.findall("mxPoint"):
                point = (_f(p.attrib.get("x")), _f(p.attrib.get("y")))
                if p.attrib.get("as") == "sourcePoint":
                    cell.source_point = point
                elif p.attrib.get("as") == "targetPoint":
                    cell.target_point = point
        cells[cid] = cell
        if len(cells) > MAX_CELLS:
            raise DrawioError(f"図が大きすぎます (セルが {MAX_CELLS} 個を超えました)")
    return cells


def _shape_kind(cell: Cell) -> tuple[str, str]:
    """(種類, 形の名前) を返す。種類は rectangle / ellipse / diamond / image / text。"""
    st = cell.style
    shape = st.get("shape", st.get("_first", ""))
    if "image" in st and st.get("image") not in ("", "none") or shape == "image":
        return "image", ""
    if "text" in st and "shape" not in st or shape == "text":
        return "text", ""
    low = shape.lower()
    if "rhombus" in st or "rhombus" in low or "decision" in low:
        return "diamond", ""
    if "ellipse" in st or "ellipse" in low or "terminator" in low or low.endswith(("start_1", "start_2")) or low in ("mxgraph.flowchart.start_1", "mxgraph.flowchart.start_2", "mxgraph.flowchart.end"):
        return "ellipse", ""
    if "swimlane" in st:
        return "rectangle", ""
    return "rectangle", SHAPE_LABELS.get(low, SHAPE_LABELS.get(shape, ""))


def model_to_scene(model: ET.Element) -> dict[str, Any]:
    cells = _read_cells(model)
    origin_cache: dict[str, tuple[float, float]] = {}

    def origin(cid: str, depth: int = 0) -> tuple[float, float]:
        """親の入れ物の左上 (絶対座標)。レイヤー (id が 0 や 1 のもの) は、原点。"""
        cell = cells.get(cid)
        if cell is None or depth > 50 or not cell.vertex:
            return (0.0, 0.0)
        if cid not in origin_cache:
            ox, oy = origin(cell.parent, depth + 1)
            origin_cache[cid] = (ox + cell.x, oy + cell.y)
        return origin_cache[cid]

    def abs_box(cell: Cell) -> tuple[float, float, float, float]:
        ox, oy = origin(cell.parent)
        return (ox + cell.x, oy + cell.y, cell.w, cell.h)

    elements: list[dict[str, Any]] = []
    labels_for_edge: dict[str, list[str]] = {}
    for cell in cells.values():  # 矢印の子の、ラベル用の箱
        if cell.vertex and cells.get(cell.parent) is not None and cells[cell.parent].edge:
            text = clean_label(cell.value)
            if text:
                labels_for_edge.setdefault(cell.parent, []).append(text)
    for cell in cells.values():
        if not cell.visible or not cell.vertex:
            continue
        parent = cells.get(cell.parent)
        if parent is not None and parent.edge:
            continue
        kind, shape_label = _shape_kind(cell)
        x, y, w, h = abs_box(cell)
        text = clean_label(cell.value)
        if kind == "text":
            if text:
                elements.append({"id": cell.cid, "type": "text", "x": x, "y": y, "width": max(w, 1), "height": max(h, 1), "text": text, "fontSize": 14, "containerId": None})
            continue
        fill = cell.style.get("fillColor", "#ffffff")
        element = {"id": cell.cid, "type": "image" if kind == "image" else kind, "x": x, "y": y, "width": max(w, 1), "height": max(h, 1),
                   "backgroundColor": "transparent" if fill in ("none", "") else fill, "strokeColor": cell.style.get("strokeColor", "#000000"),
                   "strokeStyle": "dashed" if cell.style.get("dashed") == "1" else "solid",
                   "roundness": {"type": 3} if cell.style.get("rounded") == "1" else None}
        if shape_label:
            element["shapeLabel"] = shape_label
        elements.append(element)
        if text and kind != "image":
            elements.append({"id": cell.cid + ":text", "type": "text", "x": x, "y": y, "width": max(w, 1), "height": 20, "text": text, "fontSize": 14, "containerId": cell.cid})
    for cell in cells.values():
        if not cell.visible or not cell.edge:
            continue
        src, dst = cells.get(cell.source), cells.get(cell.target)
        ox, oy = origin(cell.parent)
        start = (abs_box(src)[0] + abs_box(src)[2] / 2, abs_box(src)[1] + abs_box(src)[3] / 2) if src is not None and src.vertex else \
            ((ox + cell.source_point[0], oy + cell.source_point[1]) if cell.source_point else None)
        end = (abs_box(dst)[0] + abs_box(dst)[2] / 2, abs_box(dst)[1] + abs_box(dst)[3] / 2) if dst is not None and dst.vertex else \
            ((ox + cell.target_point[0], oy + cell.target_point[1]) if cell.target_point else None)
        middle = [(ox + px, oy + py) for px, py in cell.points]
        pts = [p for p in [start, *middle, end] if p is not None]
        if len(pts) < 2:
            continue
        end_arrow = cell.style.get("endArrow", "classic")
        start_arrow = cell.style.get("startArrow", "none")
        label = "\n".join(t for t in [clean_label(cell.value), *labels_for_edge.get(cell.cid, [])] if t)
        elements.append({"id": cell.cid, "type": "arrow", "x": pts[0][0], "y": pts[0][1], "width": 1, "height": 1,
                         "points": [[px - pts[0][0], py - pts[0][1]] for px, py in pts],
                         "startBinding": {"elementId": cell.source} if src is not None and src.vertex else None,
                         "endBinding": {"elementId": cell.target} if dst is not None and dst.vertex else None,
                         "startArrowhead": None if start_arrow in ("none", "") else "arrow", "endArrowhead": None if end_arrow in ("none", "") else "arrow",
                         "strokeStyle": "dashed" if cell.style.get("dashed") == "1" else "solid"})
        if label:
            elements.append({"id": cell.cid + ":label", "type": "text", "x": pts[0][0], "y": pts[0][1], "width": 40, "height": 20, "text": label, "fontSize": 12, "containerId": cell.cid})
    return {"type": "drawio", "elements": elements, "appState": {}, "files": {}}
