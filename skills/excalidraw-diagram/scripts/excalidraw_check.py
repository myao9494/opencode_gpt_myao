"""Excalidraw の図の点検 (重なり、矢印の接続、文字のはみ出し、ID など)。生成した図の確認に使う。

読み込み (画像を見なくても分かる文章にする機能) は、スキル diagram-reader にある。ここは、作る側の点検だけ。
標準ライブラリだけ。ファイルの読み書きはしない。
"""
from __future__ import annotations

import json
import math
import re
from typing import Any

import excalidraw_core as core

SHAPES = ("rectangle", "ellipse", "diamond")
Box = tuple[float, float, float, float]  # x, y, w, h


class CheckError(Exception):
    """利用者に見せてよいメッセージ。"""


def load_plain(text: str, name: str = "") -> dict[str, Any]:
    """点検する図 (.excalidraw.md の ```json、または .excalidraw) を読む。圧縮されたものは、点検できない。"""
    stripped = text.lstrip("\ufeff").lstrip()
    if name.lower().endswith(".excalidraw") or stripped.startswith("{"):
        raw = stripped
    else:
        match = re.search(r"##? Drawing\s*\n```(compressed-json|json)\s*\n(.*?)```", text, re.S)
        if not match:
            raise CheckError("図のデータ (## Drawing の下の json) が見つかりません。Excalidraw のファイルではないかもしれません")
        if match.group(1) == "compressed-json":
            raise CheckError("図のデータが圧縮されているため、点検できません (読むだけなら、スキル diagram-reader の describe)")
        raw = match.group(2)
    try:
        doc = json.loads(raw)
    except ValueError as exc:
        raise CheckError(f"図のデータ (JSON) を読めません: {exc}") from exc
    if not isinstance(doc, dict) or not isinstance(doc.get("elements"), list):
        raise CheckError("図のデータに elements (要素の配列) がありません")
    return doc


def num(value: Any, default: float = 0.0) -> float:
    return float(value) if isinstance(value, (int, float)) and math.isfinite(value) else default


def bbox(el: dict[str, Any]) -> Box:
    return (num(el.get("x")), num(el.get("y")), abs(num(el.get("width"))), abs(num(el.get("height"))))


def contains(outer: Box, inner: Box, tol: float = 3.0) -> bool:
    return inner[0] >= outer[0] - tol and inner[1] >= outer[1] - tol and inner[0] + inner[2] <= outer[0] + outer[2] + tol and inner[1] + inner[3] <= outer[1] + outer[3] + tol


def dist_point_box(p: tuple[float, float], b: Box) -> float:
    dx = max(b[0] - p[0], 0, p[0] - (b[0] + b[2]))
    dy = max(b[1] - p[1], 0, p[1] - (b[1] + b[3]))
    return math.hypot(dx, dy)


def arrow_points(el: dict[str, Any]) -> list[tuple[float, float]]:
    x, y = num(el.get("x")), num(el.get("y"))
    pts = el.get("points")
    if not isinstance(pts, list) or len(pts) < 2:
        return []
    return [(x + num(p[0]), y + num(p[1])) for p in pts if isinstance(p, (list, tuple)) and len(p) >= 2]


def flat(text: str) -> str:
    return " / ".join(part.strip() for part in str(text).replace("\r", "").split("\n") if part.strip())


# ---------------------------------------------------------------------------
# 点検
# ---------------------------------------------------------------------------

EMOJI_RE = re.compile("[\U0001F300-\U0001FAFF☀-➿]")


def check_doc(doc: dict[str, Any]) -> tuple[list[str], list[str]]:
    """(エラー、注意) の一覧を返す。"""
    errors: list[str] = []
    warns: list[str] = []
    els = [e for e in doc.get("elements", []) if isinstance(e, dict) and not e.get("isDeleted")]
    ids: dict[str, dict[str, Any]] = {}
    for e in els:
        eid = e.get("id")
        if not isinstance(eid, str) or not eid:
            errors.append("id の無い要素があります")
            continue
        if eid in ids:
            errors.append(f"id が重複しています: {eid}")
        ids[eid] = e
        for key in ("x", "y"):
            if not isinstance(e.get(key), (int, float)):
                errors.append(f"{eid}: {key} が数値ではありません")
        if e.get("type") in SHAPES + ("text",):
            for key in ("width", "height"):
                if not isinstance(e.get(key), (int, float)) or e.get(key, 0) <= 0:
                    errors.append(f"{eid}: {key} が正の数ではありません")
    shapes = {i: e for i, e in ids.items() if e.get("type") in SHAPES}
    for eid, e in ids.items():
        bound = e.get("boundElements") or []
        for be in bound:
            target = ids.get(be.get("id")) if isinstance(be, dict) else None
            if target is None:
                warns.append(f"{eid}: boundElements が、存在しない要素を指しています")
            elif be.get("type") == "text" and target.get("containerId") != eid:
                warns.append(f"{eid}: 結び付けた文字 {be.get('id')} の containerId が合っていません")
            elif be.get("type") == "arrow":
                bs = [(target.get("startBinding") or {}).get("elementId"), (target.get("endBinding") or {}).get("elementId")]
                if eid not in bs:
                    warns.append(f"{eid}: 矢印 {be.get('id')} の結合が、この要素を指していません")
        if e.get("type") == "text":
            cid = e.get("containerId")
            if cid and cid not in ids:
                errors.append(f"{eid}: containerId の要素がありません ({cid})")
            elif cid and not any(isinstance(b, dict) and b.get("id") == eid for b in (ids[cid].get("boundElements") or [])):
                warns.append(f"{eid}: 入れ物 {cid} の boundElements に、この文字がありません")
            text = str(e.get("text", ""))
            if not text.strip():
                warns.append(f"{eid}: 空の文字です")
            if num(e.get("fontSize"), 20) < 14:
                warns.append(f"{eid}: 文字が小さすぎます (fontSize {e.get('fontSize')}。14 以上にする)")
            if EMOJI_RE.search(text):
                warns.append(f"{eid}: 絵文字が含まれています (フォントによっては表示されません)")
            if cid in shapes:
                box = bbox(shapes[cid])
                fs = num(e.get("fontSize"), 20)
                lines = text.split("\n")
                if max(core.text_width(line, fs) for line in lines) > box[2] - 6 or len(lines) * fs * 1.25 > box[3] - 2:
                    warns.append(f"{eid}: 文字が箱からはみ出しそうです ({flat(text)[:20]})")
        if e.get("type") in ("arrow", "line"):
            pts = arrow_points(e)
            if len(pts) < 2:
                errors.append(f"{eid}: 矢印の points がありません")
                continue
            for end, point in (("startBinding", pts[0]), ("endBinding", pts[-1])):
                binding = e.get(end)
                if binding:
                    target = ids.get(binding.get("elementId"))
                    if target is None:
                        errors.append(f"{eid}: {end} の相手がありません ({binding.get('elementId')})")
                    elif target.get("type") in SHAPES and dist_point_box(point, bbox(target)) > 14:
                        warns.append(f"{eid}: 矢印の端が、結び付いた箱から離れています ({binding.get('elementId')})")
            ends = {(e.get("startBinding") or {}).get("elementId"), (e.get("endBinding") or {}).get("elementId")}
            for sid, s in shapes.items():
                if sid in ends or _has_children(sid, shapes):
                    continue
                if any(_segment_hits_box(a, b, bbox(s)) for a, b in zip(pts, pts[1:])):
                    warns.append(f"{eid}: 矢印が、箱 {sid} を横切っています")
    top = [(i, bbox(e)) for i, e in shapes.items() if not (e.get("backgroundColor") in ("transparent", None) and _has_children(i, shapes))]
    for ai in range(len(top)):
        for bi in range(ai + 1, len(top)):
            (ia, a), (ib, b) = top[ai], top[bi]
            if contains(a, b, 1) or contains(b, a, 1):
                continue
            ox = min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0])
            oy = min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1])
            if ox > 4 and oy > 4:
                warns.append(f"箱が重なっています: {ia} と {ib}")
    return errors, warns


def _has_children(eid: str, shapes: dict[str, dict[str, Any]]) -> bool:
    outer = bbox(shapes[eid])
    return any(i != eid and contains(outer, bbox(s), 1) for i, s in shapes.items())


def _segment_hits_box(a: tuple[float, float], b: tuple[float, float], box: Box, shrink: float = 4.0) -> bool:
    l, t, r, bt = box[0] + shrink, box[1] + shrink, box[0] + box[2] - shrink, box[1] + box[3] - shrink
    if l >= r or t >= bt:
        return False
    steps = max(2, int(math.hypot(b[0] - a[0], b[1] - a[1]) / 6))
    for i in range(steps + 1):
        x = a[0] + (b[0] - a[0]) * i / steps
        y = a[1] + (b[1] - a[1]) * i / steps
        if l < x < r and t < y < bt:
            return True
    return False
