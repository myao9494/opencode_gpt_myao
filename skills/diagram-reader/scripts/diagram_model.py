"""図 (Excalidraw / drawio) を、画像を見なくても分かる文章 (または Mermaid) にする。共通の部分。

形式ごとの読み込み (load_excalidraw.py、load_drawio.py) が、図を「共通の中間形式」(Excalidraw の要素に似た辞書の並び) にする。
このモジュールは、その中間形式から、箱・矢印・領域 (枠、レーン)・文字を読み取って、文章にする。

- 箱の中の文字は、箱に結び付いていなくても、位置から割り当てる (手で描いた図は、結び付いていないことが多い)
- 矢印の端は、結び付いていなければ、近くの箱に割り当てる
標準ライブラリだけ。ファイルの読み書きはしない。
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any

SHAPES = ("rectangle", "ellipse", "diamond")
ATTACH_TOLERANCE = 28.0
LABEL_TOLERANCE = 24.0  # 矢印と、その文字の枠との距離


def assign_layers(n: int, edges: list[tuple[int, int]]) -> tuple[list[int], set[int]]:
    """各ノードの層 (0 から) と、戻る向きの矢印 (循環を作るもの) の番号を返す。"""
    out: list[list[tuple[int, int]]] = [[] for _ in range(n)]
    for ei, (a, b) in enumerate(edges):
        out[a].append((b, ei))
    color = [0] * n
    back: set[int] = set()
    for start in range(n):
        if color[start]:
            continue
        stack = [(start, 0)]
        color[start] = 1
        while stack:
            u, i = stack[-1]
            if i < len(out[u]):
                stack[-1] = (u, i + 1)
                v, ei = out[u][i]
                if color[v] == 1:
                    back.add(ei)
                elif color[v] == 0:
                    color[v] = 1
                    stack.append((v, 0))
            else:
                color[u] = 2
                stack.pop()
    preds: list[list[int]] = [[] for _ in range(n)]
    succs: list[list[int]] = [[] for _ in range(n)]
    for ei, (a, b) in enumerate(edges):
        if ei not in back:
            preds[b].append(a)
            succs[a].append(b)
    indeg = [len(p) for p in preds]
    order = [i for i in range(n) if indeg[i] == 0]
    layer = [0] * n
    head = 0
    while head < len(order):
        u = order[head]
        head += 1
        for v in succs[u]:
            layer[v] = max(layer[v], layer[u] + 1)
            indeg[v] -= 1
            if indeg[v] == 0:
                order.append(v)
    for u in range(n):
        if not preds[u] and succs[u]:
            layer[u] = max(layer[u], min(layer[v] for v in succs[u]) - 1)
    return layer, back


# ---------------------------------------------------------------------------
# 幾何
# ---------------------------------------------------------------------------

Box = tuple[float, float, float, float]  # x, y, w, h


def num(value: Any, default: float = 0.0) -> float:
    return float(value) if isinstance(value, (int, float)) and math.isfinite(value) else default


def bbox(el: dict[str, Any]) -> Box:
    x, y, w, h = num(el.get("x")), num(el.get("y")), abs(num(el.get("width"))), abs(num(el.get("height")))
    return (x, y, w, h)


def center(b: Box) -> tuple[float, float]:
    return (b[0] + b[2] / 2, b[1] + b[3] / 2)


def contains(outer: Box, inner: Box, tol: float = 3.0) -> bool:
    return inner[0] >= outer[0] - tol and inner[1] >= outer[1] - tol and inner[0] + inner[2] <= outer[0] + outer[2] + tol and inner[1] + inner[3] <= outer[1] + outer[3] + tol


def point_in(b: Box, p: tuple[float, float], tol: float = 0.0) -> bool:
    return b[0] - tol <= p[0] <= b[0] + b[2] + tol and b[1] - tol <= p[1] <= b[1] + b[3] + tol


def dist_point_box(p: tuple[float, float], b: Box) -> float:
    dx = max(b[0] - p[0], 0, p[0] - (b[0] + b[2]))
    dy = max(b[1] - p[1], 0, p[1] - (b[1] + b[3]))
    return math.hypot(dx, dy)


def dist_point_segment(p: tuple[float, float], a: tuple[float, float], b: tuple[float, float]) -> float:
    ax, ay, bx, by = a[0], a[1], b[0], b[1]
    dx, dy = bx - ax, by - ay
    if dx == dy == 0:
        return math.hypot(p[0] - ax, p[1] - ay)
    t = max(0.0, min(1.0, ((p[0] - ax) * dx + (p[1] - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(p[0] - (ax + t * dx), p[1] - (ay + t * dy))


def dist_point_polyline(p: tuple[float, float], pts: list[tuple[float, float]]) -> float:
    if len(pts) == 1:
        return math.hypot(p[0] - pts[0][0], p[1] - pts[0][1])
    return min(dist_point_segment(p, a, b) for a, b in zip(pts, pts[1:]))


def dist_polyline_box(pts: list[tuple[float, float]], b: Box) -> float:
    """折れ線と、箱 (文字の枠) との、最も近い距離。線を細かく区切って測る。"""
    best = float("inf")
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        steps = max(1, int(math.hypot(x1 - x0, y1 - y0) / 6))
        for i in range(steps + 1):
            best = min(best, dist_point_box((x0 + (x1 - x0) * i / steps, y0 + (y1 - y0) * i / steps), b))
    return best


def arrow_points(el: dict[str, Any]) -> list[tuple[float, float]]:
    x, y = num(el.get("x")), num(el.get("y"))
    pts = el.get("points")
    if not isinstance(pts, list) or len(pts) < 2:
        return []
    out = []
    for p in pts:
        if isinstance(p, (list, tuple)) and len(p) >= 2:
            out.append((x + num(p[0]), y + num(p[1])))
    return out


# ---------------------------------------------------------------------------
# 解析
# ---------------------------------------------------------------------------

@dataclass
class Item:
    """図の中の、箱 (または、矢印がつながる文字)。"""
    eid: str
    kind: str  # rectangle / ellipse / diamond / text / frame
    box: Box
    texts: list[str] = field(default_factory=list)
    fill: str = "transparent"
    stroke: str = ""
    dashed: bool = False
    rounded: bool = False
    parent: str | None = None  # 含む領域の eid
    is_region: bool = False
    caption: str = ""
    notes: list[str] = field(default_factory=list)
    ref: str = ""  # N1 / R1
    header_of: str | None = None
    shape_label: str = ""  # draw.io の円柱・書類など、形の名前


@dataclass
class Link:
    src: str
    dst: str
    label: str = ""
    dashed: bool = False
    both: bool = False
    plain: bool = False  # 矢じりが無い


@dataclass
class Diagram:
    name: str
    items: dict[str, Item]
    links: list[Link]
    free_texts: list[tuple[str, Box, float]]  # (文字, 位置, フォントサイズ)
    loose_ends: int
    counts: dict[str, int]
    title: str = ""
    lanes: tuple[str, list[str]] | None = None  # ("行" / "列", [領域の eid ...])
    order: list[str] = field(default_factory=list)  # 読み取り順のノードの eid
    rows: list[list[Item]] = field(default_factory=list)


def _is_wide(ch: str) -> bool:
    import unicodedata
    return unicodedata.east_asian_width(ch) in ("W", "F", "A")


BULLET_START = re.compile(r"^\s*([・•\-\*※●○■□▶►]|\d+[.)．、]|[①-⑩])")


def flat(text: str) -> str:
    """1 つの文字要素の改行を、1 行にする。日本語の折り返しの改行はつなぎ、箇条書きや文の区切りは ' / ' にする。"""
    paragraphs = [p for p in re.split(r"\n\s*\n", str(text).replace("\r", "")) if p.strip()]  # 空行は、はっきりした区切り
    done = []
    for paragraph in paragraphs:
        parts = [part.strip() for part in paragraph.split("\n") if part.strip()]
        out = parts[0]
        for prev, cur in zip(parts, parts[1:]):
            wrapped = _is_wide(prev[-1]) and _is_wide(cur[0]) and not BULLET_START.match(cur) and prev[-1] not in "。：:；;！？!?"
            out += ("" if wrapped else " / ") + cur
        done.append(out)
    return " / ".join(done)


def analyze(doc: dict[str, Any], name: str = "") -> Diagram:
    els = [e for e in doc["elements"] if isinstance(e, dict) and not e.get("isDeleted")]
    by_id = {e.get("id"): e for e in els if e.get("id")}
    counts: dict[str, int] = {}
    for e in els:
        counts[e.get("type", "?")] = counts.get(e.get("type", "?"), 0) + 1
    items: dict[str, Item] = {}
    for e in els:
        kind = e.get("type")
        if kind in SHAPES or kind == "frame":
            items[e["id"]] = Item(e["id"], kind, bbox(e), fill=str(e.get("backgroundColor") or "transparent"), stroke=str(e.get("strokeColor") or ""),
                                  dashed=e.get("strokeStyle") in ("dashed", "dotted"), rounded=bool(e.get("roundness")), shape_label=str(e.get("shapeLabel") or ""))
            if kind == "frame" and e.get("name"):
                items[e["id"]].caption = flat(e["name"])
    shape_ids = list(items)
    # 含む関係: 最も小さい、含む箱を親にする
    for sid in shape_ids:
        best = None
        for oid in shape_ids:
            if oid == sid:
                continue
            ob, ib = items[oid].box, items[sid].box
            if contains(ob, ib) and ob[2] * ob[3] > ib[2] * ib[3] + 1:
                if best is None or ob[2] * ob[3] < items[best].box[2] * items[best].box[3]:
                    best = oid
        items[sid].parent = best
        if best:
            items[best].is_region = True
    for it in items.values():
        if it.kind == "frame":
            it.is_region = True
    # 文字を、箱に割り当てる
    texts = [e for e in els if e.get("type") == "text"]
    owner: dict[str, list[dict[str, Any]]] = {}
    free: list[dict[str, Any]] = []
    arrow_ids = {e["id"] for e in els if e.get("type") in ("arrow", "line") and e.get("id")}
    bound_labels: dict[str, str] = {}
    for t in texts:
        cid = t.get("containerId")
        if cid in items:
            owner.setdefault(cid, []).append(t)
        elif cid in arrow_ids:
            bound_labels[cid] = flat(t.get("text") or t.get("originalText") or "")
        else:
            c = center(bbox(t))
            cands = [it for it in items.values() if it.kind != "frame" and point_in(it.box, c)]
            if cands:
                best = min(cands, key=lambda it: it.box[2] * it.box[3])
                owner.setdefault(best.eid, []).append(t)
            else:
                free.append(t)
    for eid, ts in owner.items():
        ts.sort(key=lambda t: (round(num(t.get("y")) / 6), num(t.get("x"))))
        parts = [flat(t.get("text") or t.get("originalText") or "") for t in ts]
        parts = [p for p in parts if p]
        it = items[eid]
        if it.is_region:
            it.caption = it.caption or (parts[0] if parts else "")
            it.notes = parts[1:] if parts else []
        else:
            it.texts = parts
    # 枠の見出し (レーンの見出しの箱) を、領域の見出しにする
    regions = [it for it in items.values() if it.is_region]
    for it in list(items.values()):
        if it.is_region or not it.texts:
            continue
        for rg in regions:
            if rg.eid == it.eid or rg.parent == it.eid:
                continue
            rb, ib = rg.box, it.box
            v_overlap = min(rb[1] + rb[3], ib[1] + ib[3]) - max(rb[1], ib[1])
            h_overlap = min(rb[0] + rb[2], ib[0] + ib[2]) - max(rb[0], ib[0])
            touches_left = abs((ib[0] + ib[2]) - rb[0]) < 8 and v_overlap >= 0.8 * rb[3] and ib[3] >= 0.8 * rb[3]
            touches_top = abs((ib[1] + ib[3]) - rb[1]) < 8 and h_overlap >= 0.8 * rb[2] and ib[2] >= 0.8 * rb[2]
            if (touches_left or touches_top) and not contains(rg.box, ib) and rg.is_region:
                it.header_of = rg.eid
                if not rg.caption:
                    rg.caption = " / ".join(it.texts)
                break
    for it in items.values():
        if it.header_of:
            it.texts = []
    # 矢印
    nodes = [it for it in items.values() if not it.is_region and not it.header_of]
    node_ids = {it.eid for it in nodes}
    text_nodes: dict[str, Item] = {}

    def attach(point: tuple[float, float], bound: Any) -> str | None:
        if isinstance(bound, dict) and bound.get("elementId") in by_id:
            target = bound["elementId"]
            if target in items:
                return target
            t = by_id[target]
            if t.get("type") == "text":
                cid = t.get("containerId")
                if cid in items:
                    return cid
                return _text_node(t)
        best, best_d = None, ATTACH_TOLERANCE
        for it in nodes:
            d = dist_point_box(point, it.box)
            if d < best_d:
                best, best_d = it.eid, d
        if best:
            return best
        for t in free:
            d = dist_point_box(point, bbox(t))
            if d < best_d:
                best, best_d = _text_node(t), d
        if best:
            return best
        inside = [it for it in regions if it.kind != "frame" and point_in(it.box, point)]
        if inside:
            return min(inside, key=lambda it: it.box[2] * it.box[3]).eid
        return None

    def _text_node(t: dict[str, Any]) -> str:
        tid = "text:" + str(t.get("id"))
        if tid not in text_nodes:
            text_nodes[tid] = Item(tid, "text", bbox(t), texts=[flat(t.get("text") or t.get("originalText") or "")])
            node_ids.add(tid)
        return tid

    links: list[Link] = []
    loose = 0
    used_free: set[str] = set()
    for e in els:
        if e.get("type") not in ("arrow", "line"):
            continue
        pts = arrow_points(e)
        if len(pts) < 2:
            continue
        src = attach(pts[0], e.get("startBinding"))
        dst = attach(pts[-1], e.get("endBinding"))
        if src is not None and src == dst:
            continue  # 箱の内側の装飾線
        if src is None or dst is None:
            loose += 1 if e.get("type") == "arrow" else 0
            continue
        label = bound_labels.get(e.get("id"), "")
        if not label:  # 近くの、箱に属さない文字を、矢印の文字にする
            best, best_d = None, LABEL_TOLERANCE
            for t in free:
                if t["id"] in used_free or "text:" + str(t["id"]) in text_nodes:
                    continue
                if len(str(t.get("text", ""))) > 40:
                    continue
                d = dist_polyline_box(pts, bbox(t))
                if d < best_d:
                    best, best_d = t, d
            if best is not None:
                used_free.add(best["id"])
                label = flat(best.get("text") or "")
        head_end = e.get("endArrowhead") not in (None, "")
        head_start = e.get("startArrowhead") not in (None, "")
        link = Link(src, dst, label, e.get("strokeStyle") in ("dashed", "dotted"), head_start and head_end, not head_start and not head_end)
        if head_start and not head_end:
            link.src, link.dst = dst, src
        links.append(link)
    items.update(text_nodes)
    free_texts = [(flat(t.get("text") or ""), bbox(t), num(t.get("fontSize"), 20)) for t in free
                  if t["id"] not in used_free and "text:" + str(t["id"]) not in text_nodes and flat(t.get("text") or "")]
    diagram = Diagram(name, items, links, free_texts, loose, counts)
    _number(diagram)
    _find_lanes(diagram)
    _find_title(diagram)
    return diagram


def _number(dg: Diagram) -> None:
    nodes = [it for it in dg.items.values() if not it.is_region and not it.header_of]
    heights = sorted(it.box[3] for it in nodes) or [40]
    tol = max(20.0, 0.6 * heights[len(heights) // 2])
    nodes.sort(key=lambda it: center(it.box)[1])
    rows: list[list[Item]] = []
    for it in nodes:
        if rows and center(it.box)[1] - sum(center(x.box)[1] for x in rows[-1]) / len(rows[-1]) <= tol:
            rows[-1].append(it)
        else:
            rows.append([it])
    n = 0
    dg.order = []
    for row in rows:
        row.sort(key=lambda it: center(it.box)[0])
        for it in row:
            n += 1
            it.ref = f"N{n}"
            dg.order.append(it.eid)
    regions = sorted((it for it in dg.items.values() if it.is_region), key=lambda it: (round(it.box[1] / 10), it.box[0]))
    for i, it in enumerate(regions, 1):
        it.ref = f"R{i}"
    dg.rows = rows


def _find_lanes(dg: Diagram) -> None:
    groups: dict[str | None, list[Item]] = {}
    for it in dg.items.values():
        if it.is_region and it.kind != "frame":
            groups.setdefault(it.parent, []).append(it)
    best: tuple[str, list[Item]] | None = None
    for kids in groups.values():
        if len(kids) < 2:
            continue
        xs, ws = [k.box[0] for k in kids], [k.box[2] for k in kids]
        ys, hs = [k.box[1] for k in kids], [k.box[3] for k in kids]
        kind = None
        if max(xs) - min(xs) < 0.1 * max(ws) and max(ws) - min(ws) < 0.15 * max(ws):
            kind = "行"
        elif max(ys) - min(ys) < 0.1 * max(hs) and max(hs) - min(hs) < 0.15 * max(hs):
            kind = "列"
        if kind and (best is None or len(kids) > len(best[1])):
            best = (kind, kids)
    if best:
        key = (lambda it: it.box[1]) if best[0] == "行" else (lambda it: it.box[0])
        dg.lanes = (best[0], [it.eid for it in sorted(best[1], key=key)])


def _find_title(dg: Diagram) -> None:
    if not dg.free_texts:
        return
    ordered = sorted(dg.free_texts, key=lambda t: (-t[2], t[1][1]))
    biggest = ordered[0]
    all_y = [it.box[1] for it in dg.items.values()] + [t[1][1] for t in dg.free_texts]
    if biggest[2] >= 24 or biggest[1][1] <= min(all_y) + 1:
        dg.title = biggest[0]


# ---------------------------------------------------------------------------
# 文章にする
# ---------------------------------------------------------------------------

def color_name(hex_color: str) -> str:
    if not hex_color or hex_color == "transparent":
        return "塗りなし"
    m = re.fullmatch(r"#([0-9a-fA-F]{6})", hex_color.strip())
    if not m:
        return hex_color
    r, g, b = (int(m.group(1)[i:i + 2], 16) / 255 for i in (0, 2, 4))
    mx, mn = max(r, g, b), min(r, g, b)
    light, delta = (mx + mn) / 2, mx - mn
    if delta < 0.08:
        return "白" if light > 0.93 else "黒" if light < 0.2 else "灰"
    if mx == r:
        hue = (60 * ((g - b) / delta)) % 360
    elif mx == g:
        hue = 60 * ((b - r) / delta) + 120
    else:
        hue = 60 * ((r - g) / delta) + 240
    if hue < 15 or hue >= 345:
        return "ピンク" if light > 0.8 else "赤"
    if hue < 40:
        return "茶" if light < 0.4 else "橙"
    if hue < 70:
        return "黄"
    if hue < 165:
        return "緑"
    if hue < 200:
        return "水色"
    if hue < 255:
        return "青"
    if hue < 295:
        return "紫"
    return "ピンク"


SHAPE_NAME = {"rectangle": "長方形", "ellipse": "楕円", "diamond": "ひし形", "text": "文字", "frame": "枠"}


def clip(text: str, limit: int, full: bool) -> str:
    return text if full or len(text) <= limit else text[:limit - 1] + "…"


def label_of(it: Item, full: bool = False) -> str:
    text = " / ".join(it.texts) if it.texts else (it.caption if it.is_region else "")
    return f"「{clip(text, 200, full)}」" if text else "(文字なし)"


def short_of(it: Item) -> str:
    """つながりの行で使う、短い名前 (最初の文字要素の、先頭の部分)。全文は、箱の一覧にある。"""
    text = ((it.texts[0] if it.texts else it.caption) or "").split(" / ")[0]  # 見出しの部分だけ
    return f"「{clip(text, 20, False)}」" if text else "(文字なし)"


def describe_text(dg: Diagram, full: bool = False, max_lines: int = 220) -> str:
    items = dg.items
    ref = lambda eid: items[eid].ref  # noqa: E731
    nodes = [items[e] for e in dg.order]
    regions = sorted((it for it in items.values() if it.is_region), key=lambda it: it.ref and int(it.ref[1:]))
    lines: list[str] = [f"図の読み取り: {dg.name}"]
    kinds = []
    if dg.lanes:
        kinds.append(f"スイムレーン図 (レーンは{'横長の行 (上から下に並ぶ)' if dg.lanes[0] == '行' else '縦長の列 (左から右に並ぶ)'})")
    elif dg.links:
        kinds.append("矢印でつないだ図")
    elif nodes:
        kinds.append("箱の配置図 (矢印なし)")
    summary = [f"箱 {len(nodes)} 個", f"矢印 {len(dg.links)} 本"]
    if regions:
        summary.append(f"領域(大きな枠) {len(regions)} 個")
    for key, label in (("image", "画像"), ("freedraw", "手書き"), ("embeddable", "埋め込み")):
        if dg.counts.get(key):
            summary.append(f"{label} {dg.counts[key]} 個 (中身は読めません)")
    lines.append("概要: " + " / ".join(summary) + (f" / 種類(推定): {kinds[0]}" if kinds else ""))
    if dg.title:
        lines.append(f"タイトル(推定): {dg.title}")
    if dg.loose_ends:
        lines.append(f"注意: 箱につながっていない矢印・線が {dg.loose_ends} 本あります")
    if regions:
        lines.append("")
        lines.append("領域:")
        for rg in regions:
            members = [items[e].ref for e in dg.order if _inside(items, e, rg.eid)]
            note = f" 注記: {clip(' / '.join(rg.notes), 80, full)}" if rg.notes else ""
            tag = "(レーン)" if dg.lanes and rg.eid in dg.lanes[1] else ""
            subs = [x.ref for x in sorted((x for x in items.values() if x.is_region and x.parent == rg.eid), key=lambda x: int(x.ref[1:]))]
            sub_text = f" 含む領域 {', '.join(subs)} /" if subs else ""
            lines.append(f"  {rg.ref}{('「' + clip(rg.caption, 60, full) + '」') if rg.caption else '(名前なし)'}{tag}:{sub_text} 含む箱 {', '.join(members) if members else 'なし'}{note}")
    lines.append("")
    lines.append("箱 (上から下、左から右の順):")
    rows = dg.rows
    row_of = {it.eid: ri for ri, row in enumerate(rows, 1) for it in row}
    col_of = {it.eid: ci for row in rows for ci, it in enumerate(row, 1)}
    for it in nodes:
        where = ""
        chain = _regions_of(items, it.eid)
        if chain:
            inner = items[chain[-1]]
            is_lane = dg.lanes is not None and inner.eid in dg.lanes[1]
            where = f" [{'レーン' if is_lane else '領域'}: {inner.ref}「{clip(inner.caption, 20, False)}」]" if inner.caption else f" [{inner.ref}]"
        base = it.shape_label or (SHAPE_NAME.get(it.kind, it.kind) + ("(角丸)" if it.rounded and it.kind == "rectangle" else ""))
        style = base + "・" + color_name(it.fill) + ("・点線" if it.dashed else "")
        lines.append(f"  {it.ref}{label_of(it, full)} {style}{where} 位置: {row_of[it.eid]}段目・左から{col_of[it.eid]}番目")
    if dg.links:
        lines.append("")
        lines.append("つながり (矢印の向き):")
        for lk in dg.links:
            arrow = "↔" if lk.both else "—" if lk.plain else "→"
            label = f" ({lk.label})" if lk.label else ""
            lines.append(f"  {ref(lk.src)}{short_of(items[lk.src])} {arrow} {ref(lk.dst)}{short_of(items[lk.dst])}{label}{' [点線]' if lk.dashed else ''}")
        lines += _flow_summary(dg, full)
    isolated = [items[e] for e in dg.order if not any(e in (lk.src, lk.dst) for lk in dg.links)]
    if dg.links and isolated:
        lines.append("")
        lines.append("矢印につながっていない箱: " + ", ".join(f"{it.ref}{short_of(it)}" for it in isolated))
    others = [t for t in dg.free_texts if t[0] != dg.title]
    if others:
        lines.append("")
        lines.append("箱に属さない文字 (見出し・注記):")
        for text, box, fs in sorted(others, key=lambda t: (t[1][1], t[1][0])):
            lines.append(f"  「{clip(text, 150, full)}」 (フォント {int(fs)}, 位置 y={int(box[1])})")
    palette: dict[str, list[str]] = {}
    for it in nodes:
        if it.fill != "transparent":
            palette.setdefault(color_name(it.fill), []).append(it.ref)
    if len(palette) >= 2:
        lines.append("")
        lines.append("色の使い分け: " + " / ".join(f"{name}: {', '.join(refs[:12])}{'…' if len(refs) > 12 else ''}" for name, refs in palette.items()))
    if len(lines) > max_lines:
        extra = len(lines) - max_lines
        lines = lines[:max_lines] + [f"… (あと {extra} 行は省略。--max-lines で増やす、または --format mermaid)"]
    return "\n".join(lines) + "\n"


def _inside(items: dict[str, Item], eid: str, region: str) -> bool:
    cur = items[eid].parent
    while cur:
        if cur == region:
            return True
        cur = items[cur].parent
    return False


def _regions_of(items: dict[str, Item], eid: str) -> list[str]:
    chain: list[str] = []
    cur = items[eid].parent
    while cur:
        chain.append(cur)
        cur = items[cur].parent
    return list(reversed(chain))


def _flow_summary(dg: Diagram, full: bool) -> list[str]:
    items = dg.items
    ids = [e for e in dg.order if any(e in (lk.src, lk.dst) for lk in dg.links)]
    index = {e: i for i, e in enumerate(ids)}
    pseudo = [(index[lk.src], index[lk.dst]) for lk in dg.links if lk.src in index and lk.dst in index and not lk.both]
    if not pseudo:
        return []
    layer, back = assign_layers(len(ids), pseudo)
    out: list[str] = ["", "流れの推定 (矢印をたどった順。同じ段は並行):"]
    levels: dict[int, list[str]] = {}
    for e in ids:
        levels.setdefault(layer[index[e]], []).append(f"{items[e].ref}{short_of(items[e])}")
    for lv in sorted(levels):
        out.append(f"  段{lv + 1}: " + " / ".join(levels[lv]))
    incoming = {e: 0 for e in ids}
    outgoing: dict[str, list[Link]] = {e: [] for e in ids}
    back_links = {pi for pi in back}
    forward = [lk for lk in dg.links if lk.src in index and lk.dst in index and not lk.both]
    for pi, lk in enumerate(forward):
        outgoing[lk.src].append(lk)
        if pi not in back_links:
            incoming[lk.dst] += 1
    starts = [items[e].ref for e in ids if incoming[e] == 0]
    ends = [items[e].ref for e in ids if not outgoing[e]]
    out.append(f"  始点: {', '.join(starts) or 'なし (循環)'} / 終点: {', '.join(ends) or 'なし (循環)'}")
    branches = [e for e in ids if len(outgoing[e]) >= 2]
    for e in branches:
        parts = [f"{lk.label + ': ' if lk.label else ''}{items[lk.dst].ref}" for lk in outgoing[e]]
        out.append(f"  分岐: {items[e].ref}{short_of(items[e])} → " + " / ".join(parts))
    if back:
        loops = [forward[i] for i in sorted(back) if i < len(forward)]
        out.append("  戻り(ループ): " + ", ".join(f"{items[lk.src].ref}→{items[lk.dst].ref}" for lk in loops))
    return out


def describe_mermaid(dg: Diagram) -> str:
    items = dg.items

    def esc(text: str) -> str:
        return text.replace('"', "#quot;").replace("\n", "<br/>") or " "

    def node_line(it: Item, indent: str) -> str:
        text = esc(" / ".join(it.texts) or "(文字なし)")
        if it.kind == "diamond":
            return f'{indent}{it.ref}{{"{text}"}}'
        if it.kind == "ellipse":
            return f'{indent}{it.ref}(["{text}"])'
        if it.rounded:
            return f'{indent}{it.ref}("{text}")'
        return f'{indent}{it.ref}["{text}"]'

    dx = dy = 0.0
    for lk in dg.links:
        a, b = center(items[lk.src].box), center(items[lk.dst].box)
        dx += abs(b[0] - a[0])
        dy += abs(b[1] - a[1])
    direction = "TD" if dy >= dx else "LR"
    lines = [f"flowchart {direction}"]
    placed: set[str] = set()
    top_regions = [it for it in items.values() if it.is_region and it.parent is None]

    def emit_region(rg: Item, depth: int) -> None:
        indent = "  " * depth
        lines.append(f'{indent}subgraph {rg.ref}["{esc(rg.caption or rg.ref)}"]')
        for e in dg.order:
            it = items[e]
            if it.parent == rg.eid and e not in placed:
                lines.append(node_line(it, indent + "  "))
                placed.add(e)
        for child in sorted((x for x in items.values() if x.is_region and x.parent == rg.eid), key=lambda x: x.ref):
            emit_region(child, depth + 1)
        lines.append(f"{indent}end")

    for rg in sorted(top_regions, key=lambda x: int(x.ref[1:])):
        emit_region(rg, 1)
    for e in dg.order:
        if e not in placed:
            lines.append(node_line(items[e], "  "))
    for lk in dg.links:
        op = "<-->" if lk.both else ("-.->" if lk.dashed else "-->") if not lk.plain else ("-.-" if lk.dashed else "---")
        label = f"|{esc(lk.label)}|" if lk.label else ""
        lines.append(f"  {items[lk.src].ref} {op}{label} {items[lk.dst].ref}")
    return "\n".join(lines) + "\n"


