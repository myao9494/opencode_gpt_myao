"""Excalidraw の図 (Obsidian の .excalidraw.md / 標準の .excalidraw) を、小さな仕様から作る。

LLM は、図の仕様 (ノード、矢印、レーン、木) だけを JSON で書く。座標、箱の大きさ、日本語の幅、矢印の経路と接続は、
このモジュールが計算する。対応する図: flow (フロー図) / swimlane (スイムレーン) / tree (ツリー) / mindmap (マインドマップ)。

標準ライブラリだけ。ファイルの読み書きはしない (呼び出し側 excalidraw_cli.py が行う)。
設計ルール (配色の意味、フォントサイズの下限、余白、座標の考え方) と、出力形式の細部は、
axtonliu/axton-obsidian-visual-skills (MIT) の excalidraw-diagram を参考にした。NOTICE.md を参照。
"""
from __future__ import annotations

import json
import math
import re
import unicodedata
import zlib
from dataclasses import dataclass, field
from typing import Any

FONTS = {"virgil": 1, "helvetica": 2, "cascadia": 3, "excalifont": 5, "nunito": 6}
# 塗りの色 (役割は SKILL.md)。axtonliu 版の配色に、灰色を足した
COLORS = {
    "blue": "#a5d8ff", "green": "#b2f2bb", "orange": "#ffd8a8", "purple": "#d0bfff",
    "red": "#ffc9c9", "yellow": "#fff3bf", "cyan": "#c3fae8", "pink": "#eebefa",
    "gray": "#e9ecef", "white": "#ffffff", "none": "transparent",
}
BRANCH_COLORS = ["blue", "green", "orange", "purple", "pink", "cyan", "yellow", "red"]
STROKE = "#1e1e1e"
LANE_STROKE = "#868e96"
TITLE_COLOR = "#1e40af"
SHAPES = ("round", "rect", "ellipse", "diamond")
TYPES = ("flow", "swimlane", "tree", "mindmap")
MAX_NODES = 80
MAX_LABEL = 200
LINE_HEIGHT = 1.25
MARGIN = 50


class SpecError(Exception):
    """仕様の書き間違い。利用者 (LLM) に見せてよいメッセージ。"""


# ---------------------------------------------------------------------------
# 文字の幅
# ---------------------------------------------------------------------------

def char_width(ch: str, fs: float) -> float:
    """1 文字の幅の見積もり (px)。全角は 1em、半角の英数字は約 0.6em。"""
    width_class = unicodedata.east_asian_width(ch)
    if width_class in ("W", "F", "A"):
        return fs * 1.0
    if ch == " ":
        return fs * 0.3
    if width_class == "H":
        return fs * 0.5
    if ch.isupper() or ch in "MW@%":
        return fs * 0.72
    return fs * 0.6


def text_width(line: str, fs: float) -> float:
    return sum(char_width(ch, fs) for ch in line)


NO_LINE_START = set("、。，．,.)）」』】〉》!?！？：；:;ーぁぃぅぇぉっゃゅょァィゥェォッャュョ")


ASCII_WORD_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_./:#@%+")


def ascii_word_start(current: str) -> int:
    """行の末尾の英単語 (英数字と -_./: など) の始まりの位置。直前が英数字以外 (日本語など) のときだけ返す。無ければ -1。"""
    i = len(current)
    while i > 0 and current[i - 1] in ASCII_WORD_CHARS:
        i -= 1
    if i == len(current) or i == 0 or current[i - 1].isspace():
        return -1
    return i


def wrap_text(text: str, max_width: float, fs: float) -> list[str]:
    """日本語は任意の位置で、英単語は空白で折り返す。行頭に来てはいけない記号は、前の行にぶら下げる。"""
    lines: list[str] = []
    for paragraph in str(text).replace("\r\n", "\n").split("\n"):
        current = ""
        for ch in paragraph:
            if current and text_width(current + ch, fs) > max_width and ch not in NO_LINE_START:
                cut = current.rfind(" ") if ch.isascii() and not ch.isspace() and current[-1].isascii() else -1
                word = ascii_word_start(current) if ch.isascii() and not ch.isspace() else -1
                if cut > 0:
                    lines.append(current[:cut].rstrip())
                    current = current[cut + 1:] + ch
                elif word > 0:
                    # 日本語に続く英単語 (空白なし) は、単語の途中で切らずに、丸ごと次の行へ送る
                    lines.append(current[:word])
                    current = current[word:] + ch
                else:
                    lines.append(current)
                    current = ch
            else:
                current += ch
        lines.append(current)
    return lines or [""]


# ---------------------------------------------------------------------------
# 仕様の読み取り
# ---------------------------------------------------------------------------

@dataclass
class Node:
    idx: int
    key: str
    label: str
    detail: str = ""
    shape: str = "round"
    color: str = "blue"
    dashed: bool = False
    lane: int | None = None
    fs: int = 18
    parent: int | None = None
    level: int = 0
    lines: list[str] = field(default_factory=list)
    tw: float = 0
    th: float = 0
    w: float = 0
    h: float = 0
    x: float = 0  # 左上
    y: float = 0
    side: int = 1  # mindmap: 1 右 / -1 左

    @property
    def cx(self) -> float:
        return self.x + self.w / 2

    @property
    def cy(self) -> float:
        return self.y + self.h / 2


@dataclass
class Edge:
    src: int
    dst: int
    label: str = ""
    dashed: bool = False
    arrow: str = "end"  # end / both / none


@dataclass
class Spec:
    kind: str
    title: str = ""
    direction: str = "TB"
    font: int = 1
    background: str = "#ffffff"
    routing: str = "elbow"
    nodes: list[Node] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    lanes: list[tuple[str, str, str]] = field(default_factory=list)  # (key, label, color)
    edge_arrows: bool = False  # tree


def _str(value: Any, what: str, required: bool = True, limit: int = MAX_LABEL) -> str:
    if value is None or value == "":
        if required:
            raise SpecError(f"{what} がありません")
        return ""
    if not isinstance(value, (str, int, float)):
        raise SpecError(f"{what} は文字列にしてください")
    text = str(value)
    if len(text) > limit:
        raise SpecError(f"{what} が長すぎます ({len(text)} 文字。上限 {limit})。短くして、詳しい説明は detail へ")
    return text


def _color(value: Any, default: str, what: str) -> str:
    if value is None:
        return default
    if value in COLORS or (isinstance(value, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", value)):
        return str(value)
    raise SpecError(f"{what} の色が正しくありません: {value!r} (使える名前: {', '.join(COLORS)}、または #rrggbb)")


def fill_color(value: str) -> str:
    return COLORS.get(value, value)


def _node(raw: Any, idx: int, default_color: str, what: str) -> Node:
    if not isinstance(raw, dict):
        raise SpecError(f"{what} は、オブジェクトにしてください")
    shape = raw.get("shape", "round")
    if shape not in SHAPES:
        raise SpecError(f"{what}.shape が正しくありません: {shape!r} (使える形: {', '.join(SHAPES)})")
    key = _str(raw.get("id", f"n{idx + 1}"), f"{what}.id", limit=60)
    return Node(idx=idx, key=key, label=_str(raw.get("label"), f"{what}.label"), detail=_str(raw.get("detail"), f"{what}.detail", False),
                shape=shape, color=_color(raw.get("color"), default_color, what), dashed=bool(raw.get("dashed", False)))


def parse_spec(raw: Any) -> Spec:
    if not isinstance(raw, dict):
        raise SpecError("仕様は、JSON のオブジェクトにしてください")
    kind = raw.get("type")
    if kind not in TYPES:
        raise SpecError(f"type が正しくありません: {kind!r} (使えるもの: {', '.join(TYPES)})")
    font = raw.get("font", "virgil")
    if isinstance(font, str):
        if font.lower() not in FONTS:
            raise SpecError(f"font が正しくありません: {font!r} (使えるもの: {', '.join(FONTS)})")
        font = FONTS[font.lower()]
    elif font not in FONTS.values():
        raise SpecError(f"font が正しくありません: {font!r}")
    direction = str(raw.get("direction", "LR" if kind == "swimlane" else "TB")).upper()
    if direction not in ("TB", "LR"):
        raise SpecError("direction は TB (上から下) か LR (左から右) にしてください")
    routing = raw.get("routing", "elbow")
    if routing not in ("elbow", "straight"):
        raise SpecError("routing は elbow か straight にしてください")
    spec = Spec(kind=kind, title=_str(raw.get("title"), "title", False, 100), direction=direction, font=int(font),
                routing=routing, edge_arrows=bool(raw.get("edge_arrows", False)))
    if raw.get("background") is not None:
        spec.background = fill_color(_color(raw.get("background"), "#ffffff", "background"))
    if kind in ("tree", "mindmap"):
        _parse_tree(raw, spec)
    else:
        _parse_graph(raw, spec)
    if len(spec.nodes) > MAX_NODES:
        raise SpecError(f"ノードが多すぎます ({len(spec.nodes)} 個。上限 {MAX_NODES})。図を分けてください")
    return spec


def _parse_graph(raw: dict, spec: Spec) -> None:
    nodes = raw.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        raise SpecError("nodes (ノードの配列) がありません")
    lane_index: dict[str, int] = {}
    if spec.kind == "swimlane":
        lanes = raw.get("lanes")
        if not isinstance(lanes, list) or not lanes:
            raise SpecError("swimlane には lanes (レーンの配列) が必要です")
        for i, lane in enumerate(lanes):
            if not isinstance(lane, dict):
                raise SpecError(f"lanes[{i}] は、オブジェクトにしてください")
            key = _str(lane.get("id", f"lane{i + 1}"), f"lanes[{i}].id", limit=60)
            if key in lane_index:
                raise SpecError(f"レーンの id が重複しています: {key}")
            lane_index[key] = i
            spec.lanes.append((key, _str(lane.get("label"), f"lanes[{i}].label", limit=60), _color(lane.get("color"), BRANCH_COLORS[i % len(BRANCH_COLORS)], f"lanes[{i}]")))
    seen: dict[str, int] = {}
    for i, item in enumerate(nodes):
        node = _node(item, i, "blue", f"nodes[{i}]")
        if node.key in seen:
            raise SpecError(f"ノードの id が重複しています: {node.key}")
        seen[node.key] = i
        if spec.kind == "swimlane":
            lane = item.get("lane")
            if lane not in lane_index:
                raise SpecError(f"nodes[{i}].lane が正しくありません: {lane!r} (レーンの id: {', '.join(lane_index)})")
            node.lane = lane_index[lane]
            if "color" not in item:
                node.color = spec.lanes[node.lane][2]
        spec.nodes.append(node)
    for i, item in enumerate(raw.get("edges") or []):
        if not isinstance(item, dict):
            raise SpecError(f"edges[{i}] は、オブジェクトにしてください")
        src, dst = item.get("from"), item.get("to")
        for end, name in ((src, "from"), (dst, "to")):
            if end not in seen:
                raise SpecError(f"edges[{i}].{name} が、nodes にありません: {end!r}")
        if src == dst:
            raise SpecError(f"edges[{i}]: 自分自身へ戻る矢印は使えません ({src})。別のノードを経由してください")
        arrow = item.get("arrow", "end")
        if arrow not in ("end", "both", "none"):
            raise SpecError(f"edges[{i}].arrow は end / both / none のどれかにしてください")
        spec.edges.append(Edge(seen[src], seen[dst], _str(item.get("label"), f"edges[{i}].label", False, 40), bool(item.get("dashed", False)), arrow))


def _parse_tree(raw: dict, spec: Spec) -> None:
    root = raw.get("root")
    if not isinstance(root, dict):
        raise SpecError("root (根のノード。children に子を入れ子で書く) がありません")

    def walk(item: Any, parent: int | None, level: int, branch: int, what: str) -> int:
        color = "blue"
        if spec.kind == "mindmap":
            color = "yellow" if level == 0 else BRANCH_COLORS[branch % len(BRANCH_COLORS)]
        elif level > 0:
            color = ["blue", "green", "orange", "purple"][min(level, 4) - 1] if level < 5 else "gray"
        node = _node(item, len(spec.nodes), color, what)
        node.key = f"n{node.idx + 1}"
        node.parent, node.level = parent, level
        if level == 0 and spec.kind == "mindmap" and "shape" not in item:
            node.shape = "ellipse"
        spec.nodes.append(node)
        children = item.get("children") or []
        if not isinstance(children, list):
            raise SpecError(f"{what}.children は、配列にしてください")
        for j, child in enumerate(children):
            walk(child, node.idx, level + 1, j if level == 0 else branch, f"{what}.children[{j}]")
            if len(spec.nodes) > MAX_NODES:
                raise SpecError(f"ノードが多すぎます (上限 {MAX_NODES})。図を分けてください")
        return node.idx

    walk(root, None, 0, 0, "root")
    for node in spec.nodes:
        if node.parent is not None:
            spec.edges.append(Edge(node.parent, node.idx, arrow="end" if spec.edge_arrows else "none"))


# ---------------------------------------------------------------------------
# 箱の大きさ
# ---------------------------------------------------------------------------

def size_node(node: Node, fs: int, max_text_w: float, min_w: float = 120, min_h: float = 56) -> None:
    node.fs = fs
    text = node.label + ("\n\n" + node.detail if node.detail else "")  # 見出しと説明の間は空行 (読み取り側が区切りと分かる)
    node.lines = wrap_text(text, max_text_w, fs)
    node.tw = max(text_width(line, fs) for line in node.lines)
    node.th = len(node.lines) * fs * LINE_HEIGHT
    if node.shape == "diamond":
        w, h = max(150, node.tw * 1.9 + 30), max(90, node.th * 2.0 + 30)
    elif node.shape == "ellipse":
        w, h = max(min_w, node.tw * 1.42 + 28), max(min_h + 8, node.th * 1.5 + 20)
    else:
        w, h = max(min_w, node.tw + 36), max(min_h, node.th + 26)
    node.w, node.h = float(math.ceil(w / 2) * 2), float(math.ceil(h / 2) * 2)


# ---------------------------------------------------------------------------
# 層の割り当て (フロー図・スイムレーン)
# ---------------------------------------------------------------------------

def assign_layers(n: int, edges: list[Edge]) -> tuple[list[int], set[int]]:
    """各ノードの層 (0 から) と、戻る向きの矢印 (循環を作るもの) の番号を返す。"""
    out: list[list[tuple[int, int]]] = [[] for _ in range(n)]
    for ei, e in enumerate(edges):
        out[e.src].append((e.dst, ei))
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
    for ei, e in enumerate(edges):
        if ei not in back:
            preds[e.dst].append(e.src)
            succs[e.src].append(e.dst)
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
    for u in range(n):  # 前の層に何も無い始点は、最初の行き先の直前に寄せる (長い矢印を減らす)
        if not preds[u] and succs[u]:
            layer[u] = max(layer[u], min(layer[v] for v in succs[u]) - 1)
    return layer, back


# ---------------------------------------------------------------------------
# 配置 (抽象座標: a = 流れの向き、c = それに直角の向き。中心の座標を持つ)
# ---------------------------------------------------------------------------

def _sa(node: Node, direction: str) -> float:
    return node.h if direction == "TB" else node.w


def _sc(node: Node, direction: str) -> float:
    return node.w if direction == "TB" else node.h


def layered_layout(spec: Spec) -> tuple[dict[int, tuple[float, float]], dict[int, list[tuple[float, float]]], list[int], list[tuple[float, float]]]:
    """フロー図の配置。(ノードの中心 (a, c)、長い矢印が通る中間点、層、層ごとの (開始, 終了)) を返す。"""
    d = spec.direction
    n = len(spec.nodes)
    layer, back = assign_layers(n, spec.edges)
    # 頂点: 0..n-1 が本物、それ以降が、長い矢印の中継点
    v_layer = list(layer)
    v_sa = [_sa(nd, d) for nd in spec.nodes]
    v_sc = [_sc(nd, d) for nd in spec.nodes]
    chains: dict[int, list[int]] = {}
    links: list[tuple[int, int]] = []
    for ei, e in enumerate(spec.edges):
        if ei in back or layer[e.src] >= layer[e.dst]:
            continue
        prev = e.src
        chain: list[int] = []
        for lay in range(layer[e.src] + 1, layer[e.dst]):
            v_layer.append(lay)
            v_sa.append(0.0)
            v_sc.append(10.0)
            chain.append(len(v_layer) - 1)
            links.append((prev, len(v_layer) - 1))
            prev = len(v_layer) - 1
        links.append((prev, e.dst))
        chains[ei] = chain
    total = len(v_layer)
    layers_n = max(v_layer) + 1
    rows: list[list[int]] = [[] for _ in range(layers_n)]
    for v in range(total):
        rows[v_layer[v]].append(v)
    up: list[list[int]] = [[] for _ in range(total)]
    down: list[list[int]] = [[] for _ in range(total)]
    for a, b in links:
        down[a].append(b)
        up[b].append(a)

    def pos_map() -> dict[int, int]:
        return {v: i for row in rows for i, v in enumerate(row)}

    for _ in range(4):  # 辺の交差を減らす (重心法)
        for sweep in (1, -1):
            rng = range(1, layers_n) if sweep == 1 else range(layers_n - 2, -1, -1)
            for li in rng:
                pm = pos_map()
                neigh = up if sweep == 1 else down
                rows[li].sort(key=lambda v: (sum(pm[x] for x in neigh[v]) / len(neigh[v])) if neigh[v] else pm[v])
    gap_c = 52.0

    def min_dist(a: int, b: int) -> float:
        both_real = a < n and b < n
        return (v_sc[a] + v_sc[b]) / 2 + (gap_c if both_real else 26.0)

    c_pos = [0.0] * total
    for row in rows:
        cursor = 0.0
        for i, v in enumerate(row):
            if i:
                cursor += min_dist(row[i - 1], v)
            c_pos[v] = cursor
        mid = (c_pos[row[0]] + c_pos[row[-1]]) / 2 if row else 0
        for v in row:
            c_pos[v] -= mid
    for it in range(10):  # 隣り合う層の中心に寄せる
        order = range(layers_n) if it % 2 == 0 else range(layers_n - 1, -1, -1)
        for li in order:
            row = rows[li]
            want = []
            for v in row:
                neigh = up[v] + down[v]
                want.append(sum(c_pos[x] for x in neigh) / len(neigh) if neigh else c_pos[v])
            fwd = list(want)
            for i in range(1, len(row)):
                fwd[i] = max(fwd[i], fwd[i - 1] + min_dist(row[i - 1], row[i]))
            bwd = list(want)
            for i in range(len(row) - 2, -1, -1):
                bwd[i] = min(bwd[i], bwd[i + 1] - min_dist(row[i], row[i + 1]))
            for i, v in enumerate(row):
                c_pos[v] = (fwd[i] + bwd[i]) / 2
    thickness = [max([v_sa[v] for v in row if v < n] or [0.0]) for row in rows]
    gap_a = 74.0
    bands: list[tuple[float, float]] = []
    cursor = 0.0
    for li in range(layers_n):
        bands.append((cursor, cursor + thickness[li]))
        cursor += thickness[li] + gap_a
    centers = {i: ((bands[layer[i]][0] + bands[layer[i]][1]) / 2, c_pos[i]) for i in range(n)}
    waypoints: dict[int, list[tuple[float, float]]] = {}
    for ei, chain in chains.items():
        waypoints[ei] = [((bands[v_layer[v]][0] + bands[v_layer[v]][1]) / 2, c_pos[v]) for v in chain]
    return centers, waypoints, layer, bands


def to_xy(a: float, c: float, direction: str) -> tuple[float, float]:
    return (c, a) if direction == "TB" else (a, c)


def dedupe_points(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    for p in points:
        if out and abs(out[-1][0] - p[0]) < 0.5 and abs(out[-1][1] - p[1]) < 0.5:
            continue
        out.append(p)
    i = 1
    while i < len(out) - 1:
        (x0, y0), (x1, y1), (x2, y2) = out[i - 1], out[i], out[i + 1]
        if (abs(x0 - x1) < 0.5 and abs(x1 - x2) < 0.5) or (abs(y0 - y1) < 0.5 and abs(y1 - y2) < 0.5):
            out.pop(i)
        else:
            i += 1
    return out


def assign_xy_from_centers(spec: Spec, centers: dict[int, tuple[float, float]]) -> None:
    for i, (a, c) in centers.items():
        node = spec.nodes[i]
        x, y = to_xy(a, c, spec.direction)
        node.x, node.y = x - node.w / 2, y - node.h / 2


def side_anchor(node: Node, side: str) -> tuple[float, float]:
    return {"top": (node.cx, node.y), "bottom": (node.cx, node.y + node.h), "left": (node.x, node.cy), "right": (node.x + node.w, node.cy)}[side]


def loop_route(spec: Spec, u: Node, v: Node, layer: list[int], k: int) -> dict[str, Any]:
    """戻る矢印を、箱の横(または上下)を回り込ませる。両側を試して、箱を横切らない方を選ぶ。"""
    d = spec.direction
    lo, hi = sorted((layer[u.idx], layer[v.idx]))
    span = [nd for nd in spec.nodes if lo <= layer[nd.idx] <= hi]
    others = [(nd.x, nd.y, nd.w, nd.h) for nd in spec.nodes if nd.idx not in (u.idx, v.idx)]
    margin = 34 + 16 * k
    if d == "TB":
        far, near = max(nd.x + nd.w for nd in span) + margin, min(nd.x for nd in span) - margin
        cands = [([(u.x + u.w, u.cy), (far, u.cy), (far, v.cy), (v.x + v.w, v.cy)], ("right", "right")),
                 ([(u.x, u.cy), (near, u.cy), (near, v.cy), (v.x, v.cy)], ("left", "left"))]
    else:
        far, near = max(nd.y + nd.h for nd in span) + margin, min(nd.y for nd in span) - margin
        cands = [([(u.cx, u.y + u.h), (u.cx, far), (v.cx, far), (v.cx, v.y + v.h)], ("bottom", "bottom")),
                 ([(u.cx, u.y), (u.cx, near), (v.cx, near), (v.cx, v.y)], ("top", "top"))]
    pts, sides = min(cands, key=lambda c: box_hits(c[0], others))
    return {"points": pts, "sides": sides}


def flow_routes(spec: Spec, waypoints: dict[int, list[tuple[float, float]]], layer: list[int], bands: list[tuple[float, float]]) -> list[dict[str, Any]]:
    """各矢印の、(点の列、始点の側、終点の側) を返す。座標は、図全体の絶対座標 (左上が原点)。"""
    d = spec.direction
    routes: list[dict[str, Any]] = []
    n = len(spec.nodes)
    out_edges: dict[int, list[int]] = {}
    for ei, e in enumerate(spec.edges):
        out_edges.setdefault(e.src, []).append(ei)
    exit_side: dict[int, str] = {}
    fwd = "bottom" if d == "TB" else "right"
    enter = "top" if d == "TB" else "left"
    # ひし形から複数の矢印が出るとき、左右(上下)の頂点からも出す
    for u, eis in out_edges.items():
        node = spec.nodes[u]
        forward = [ei for ei in eis if layer[spec.edges[ei].dst] > layer[u]]
        if node.shape != "diamond" or len(forward) < 2:
            continue
        for ei in forward:
            t = spec.nodes[spec.edges[ei].dst]
            diff = (t.cx - node.cx) if d == "TB" else (t.cy - node.cy)
            if diff < -20:
                exit_side[ei] = "left" if d == "TB" else "top"
            elif diff > 20:
                exit_side[ei] = "right" if d == "TB" else "bottom"
    loops = 0
    for ei, e in enumerate(spec.edges):
        u, v = spec.nodes[e.src], spec.nodes[e.dst]
        if layer[e.src] >= layer[e.dst]:  # 戻る矢印: 横を回り込む
            loops += 1
            routes.append(loop_route(spec, u, v, layer, loops - 1))
            continue
        side = exit_side.get(ei, fwd)
        start = side_anchor(u, side)
        end = side_anchor(v, enter)
        via = [to_xy(a, c, d) for a, c in waypoints.get(ei, [])]
        if spec.routing == "straight":
            pts = [start] + via + [end]
        else:
            stops = via + [end]
            pts = [start]
            if side != fwd:  # 側面の頂点から出て、行き先の列(行)まで進んでから、まっすぐ進む (近すぎるときは、40px だけ外へ出る)
                first = stops[0]
                if d == "TB":
                    sign = 1 if side == "right" else -1
                    pts.append((first[0] if sign * (first[0] - start[0]) >= 40 else start[0] + sign * 40, start[1]))
                else:
                    sign = 1 if side == "bottom" else -1
                    pts.append((start[0], first[1] if sign * (first[1] - start[1]) >= 40 else start[1] + sign * 40))
            cur_layer = layer[e.src]
            for stop in stops:
                next_layer = cur_layer + 1
                gap_mid = (bands[cur_layer][1] + bands[next_layer][0]) / 2
                prev = pts[-1]
                if d == "TB":
                    pts += [(prev[0], gap_mid), (stop[0], gap_mid), stop]
                else:
                    pts += [(gap_mid, prev[1]), (gap_mid, stop[1]), stop]
                cur_layer = next_layer
        routes.append({"points": dedupe_points(pts), "sides": (side, enter)})
    return routes


# ---------------------------------------------------------------------------
# ツリー・マインドマップの配置
# ---------------------------------------------------------------------------

def tidy_tree(spec: Spec, indices: list[int], root: int, direction: str) -> dict[int, tuple[float, float]]:
    """root から下の部分木を並べる。root を (a=0, c=0) の中心とした相対座標で、各ノードの (a, c) を返す。"""
    d = direction
    children: dict[int, list[int]] = {i: [] for i in indices}
    for i in indices:
        p = spec.nodes[i].parent
        if p is not None and p in children and i != root:
            children[p].append(i)
    gap_c = 30.0
    extent: dict[int, float] = {}

    def measure(i: int) -> float:
        kids = children[i]
        own = _sc(spec.nodes[i], d)
        extent[i] = max(own, sum(measure(k) for k in kids) + gap_c * max(len(kids) - 1, 0)) if kids else own
        return extent[i]

    measure(root)
    c_center: dict[int, float] = {}
    depth: dict[int, int] = {root: 0}

    def place(i: int, start: float) -> None:
        kids = children[i]
        if not kids:
            c_center[i] = start + extent[i] / 2
            return
        span = sum(extent[k] for k in kids) + gap_c * (len(kids) - 1)
        cursor = start + (extent[i] - span) / 2
        for k in kids:
            depth[k] = depth[i] + 1
            place(k, cursor)
            cursor += extent[k] + gap_c
        c_center[i] = (c_center[kids[0]] + c_center[kids[-1]]) / 2

    place(root, 0.0)
    levels = max(depth.values()) + 1
    thickness = [0.0] * levels
    for i, dep in depth.items():
        thickness[dep] = max(thickness[dep], _sa(spec.nodes[i], d))
    gap_a = 64.0 if d == "TB" else 72.0
    a_center: list[float] = []
    cursor = 0.0
    for dep in range(levels):
        a_center.append(cursor + thickness[dep] / 2)
        cursor += thickness[dep] + gap_a
    base = a_center[0]
    c0 = c_center[root]
    return {i: (a_center[depth[i]] - base, c_center[i] - c0) for i in depth}


# ---------------------------------------------------------------------------
# スイムレーンの配置
# ---------------------------------------------------------------------------

def swimlane_layout(spec: Spec) -> dict[str, Any]:
    d = spec.direction  # LR: レーンは横長 (行) で、左から右へ流れる。TB: レーンは縦長 (列) で、上から下へ流れる
    n = len(spec.nodes)
    layer, back = assign_layers(n, spec.edges)
    layers_n = max(layer) + 1
    lanes_n = len(spec.lanes)
    pad = 28.0
    gap_c, gap_a = 36.0, 76.0
    header_a = 132.0 if d == "LR" else 60.0  # レーンの見出しの大きさ (流れの向き)
    cells: dict[tuple[int, int], list[int]] = {}
    for nd in spec.nodes:
        cells.setdefault((nd.lane, layer[nd.idx]), []).append(nd.idx)  # type: ignore[arg-type]
    thickness = [0.0] * layers_n
    for nd in spec.nodes:
        thickness[layer[nd.idx]] = max(thickness[layer[nd.idx]], _sa(nd, d))
    cell_size = {key: sum(_sc(spec.nodes[i], d) for i in idxs) + gap_c * (len(idxs) - 1) for key, idxs in cells.items()}
    lane_size = []
    for li in range(lanes_n):
        need = max([cell_size.get((li, lay), 0.0) for lay in range(layers_n)] + [0.0]) + 2 * pad
        label_lines = wrap_text(spec.lanes[li][1], header_a - 24 if d == "LR" else 400, 18)
        header_need = len(label_lines) * 18 * LINE_HEIGHT + 24 if d == "LR" else 0
        lane_size.append(max(need, header_need, 110.0))
    lane_start = []
    cursor = 0.0
    for size in lane_size:
        lane_start.append(cursor)
        cursor += size
    bands = []
    cursor = header_a + pad
    for lay in range(layers_n):
        bands.append((cursor, cursor + thickness[lay]))
        cursor += thickness[lay] + gap_a
    body_end = cursor - gap_a + pad
    centers: dict[int, tuple[float, float]] = {}
    for (li, lay), idxs in cells.items():
        total = cell_size[(li, lay)]
        c_cursor = lane_start[li] + lane_size[li] / 2 - total / 2
        for i in idxs:
            sc = _sc(spec.nodes[i], d)
            centers[i] = ((bands[lay][0] + bands[lay][1]) / 2, c_cursor + sc / 2)
            c_cursor += sc + gap_c
    return {"centers": centers, "layer": layer, "back": back, "bands": bands, "lane_start": lane_start, "lane_size": lane_size,
            "header_a": header_a, "body_end": body_end}


def box_hits(points: list[tuple[float, float]], boxes: list[tuple[float, float, float, float]], shrink: float = 3.0) -> int:
    """折れ線が、箱 (x, y, w, h) を横切る数 (端点を含む箱は数えない)。"""
    hits = 0
    for x, y, w, h in boxes:
        l, t, r, b = x + shrink, y + shrink, x + w - shrink, y + h - shrink
        hit = False
        for (x0, y0), (x1, y1) in zip(points, points[1:]):
            if max(x0, x1) < l or min(x0, x1) > r or max(y0, y1) < t or min(y0, y1) > b:
                continue
            # 軸に平行な線分だけ (経路は水平・垂直のみ)。斜めの線は外接矩形で近似する
            hit = True
            break
        hits += hit
    return hits


def swimlane_routes(spec: Spec, lay: dict[str, Any]) -> list[dict[str, Any]]:
    d = spec.direction
    layer, bands = lay["layer"], lay["bands"]
    fwd, enter = ("right", "left") if d == "LR" else ("bottom", "top")
    boxes = [(nd.x, nd.y, nd.w, nd.h) for nd in spec.nodes]
    routes: list[dict[str, Any]] = []
    loops = 0
    for ei, e in enumerate(spec.edges):
        u, v = spec.nodes[e.src], spec.nodes[e.dst]
        if layer[e.src] >= layer[e.dst]:
            loops += 1
            routes.append(loop_route(spec, u, v, layer, loops - 1))
            continue
        s, t = side_anchor(u, fwd), side_anchor(v, enter)
        if spec.routing == "straight":
            routes.append({"points": [s, t], "sides": (fwd, enter)})
            continue
        candidates = []
        for gap_layer in (layer[e.src], layer[e.dst] - 1):
            mid = (bands[gap_layer][1] + bands[gap_layer + 1][0]) / 2
            pts = [s, (mid, s[1]), (mid, t[1]), t] if d == "LR" else [s, (s[0], mid), (t[0], mid), t]
            candidates.append(dedupe_points(pts))
        others = [b for k, b in enumerate(boxes) if k not in (e.src, e.dst)]
        best = min(candidates, key=lambda pts: box_hits(pts, others))
        routes.append({"points": best, "sides": (fwd, enter)})
    return routes


# ---------------------------------------------------------------------------
# Excalidraw の要素
# ---------------------------------------------------------------------------

class Builder:
    def __init__(self, font: int, background: str) -> None:
        self.font = font
        self.background = background
        self.elements: list[dict[str, Any]] = []
        self.counter = 0

    def _id(self, prefix: str) -> str:
        self.counter += 1
        return f"{prefix}-{self.counter}"

    def _base(self, kind: str, ident: str, x: float, y: float, w: float, h: float, stroke: str, fill: str, sw: int = 2, dashed: bool = False, opacity: int = 100) -> dict[str, Any]:
        return {
            "id": ident, "type": kind, "x": round(x, 1), "y": round(y, 1), "width": round(w, 1), "height": round(h, 1), "angle": 0,
            "strokeColor": stroke, "backgroundColor": fill, "fillStyle": "solid", "strokeWidth": sw,
            "strokeStyle": "dashed" if dashed else "solid", "roughness": 1, "opacity": opacity, "groupIds": [],
            "roundness": None, "seed": zlib.crc32(ident.encode()) & 0x7FFFFFFF, "version": 1, "isDeleted": False,
            "boundElements": None, "updated": 1, "link": None, "locked": False,
        }

    def shape(self, shape: str, x: float, y: float, w: float, h: float, fill: str, stroke: str = STROKE, sw: int = 2, dashed: bool = False, opacity: int = 100) -> dict[str, Any]:
        kind = {"round": "rectangle", "rect": "rectangle", "ellipse": "ellipse", "diamond": "diamond"}[shape]
        el = self._base(kind, self._id("shape"), x, y, w, h, stroke, fill, sw, dashed, opacity)
        if shape == "round":
            el["roundness"] = {"type": 3}  # 角丸。ひし形 (分岐) と、shape: rect は、角を丸めない
        self.elements.append(el)
        return el

    def _text(self, ident: str, x: float, y: float, lines: list[str], fs: int, color: str, align: str, container: str | None) -> dict[str, Any]:
        width = max(text_width(line, fs) for line in lines)
        height = len(lines) * fs * LINE_HEIGHT
        el = self._base("text", ident, x, y, width, height, color, "transparent", 1)
        text = "\n".join(lines)
        el.update({"text": text, "originalText": text, "fontSize": fs, "fontFamily": self.font, "textAlign": align,
                   "verticalAlign": "middle" if container else "top", "containerId": container, "lineHeight": LINE_HEIGHT, "autoResize": True})
        self.elements.append(el)
        return el

    def text_in(self, container: dict[str, Any], lines: list[str], fs: int, color: str = STROKE, point: tuple[float, float] | None = None) -> dict[str, Any]:
        width = max(text_width(line, fs) for line in lines)
        height = len(lines) * fs * LINE_HEIGHT
        cx = point[0] if point else container["x"] + container["width"] / 2
        cy = point[1] if point else container["y"] + container["height"] / 2
        el = self._text(self._id("text"), cx - width / 2, cy - height / 2, lines, fs, color, "center", container["id"])
        container["boundElements"] = (container["boundElements"] or []) + [{"id": el["id"], "type": "text"}]
        return el

    def text_free(self, x: float, y: float, lines: list[str], fs: int, color: str = STROKE, align: str = "left") -> dict[str, Any]:
        return self._text(self._id("text"), x, y, lines, fs, color, align, None)

    def arrow(self, points: list[tuple[float, float]], src: dict[str, Any] | None, dst: dict[str, Any] | None, arrow: str = "end", dashed: bool = False,
              color: str = STROKE, sw: int = 2, label: str = "") -> dict[str, Any]:
        if label:
            points = balance_for_label(points)
        x0, y0 = points[0]
        rel = [[round(px - x0, 1), round(py - y0, 1)] for px, py in points]
        xs, ys = [p[0] for p in rel], [p[1] for p in rel]
        el = self._base("arrow", self._id("arrow"), x0, y0, max(xs) - min(xs), max(ys) - min(ys), color, "transparent", sw, dashed)
        el.update({"points": rel, "lastCommittedPoint": None,
                   "startBinding": {"elementId": src["id"], "focus": 0, "gap": 1} if src else None,
                   "endBinding": {"elementId": dst["id"], "focus": 0, "gap": 1} if dst else None,
                   "startArrowhead": "arrow" if arrow == "both" else None, "endArrowhead": None if arrow == "none" else "arrow"})
        self.elements.append(el)
        for shape in (src, dst):
            if shape is not None:
                shape["boundElements"] = (shape["boundElements"] or []) + [{"id": el["id"], "type": "arrow"}]
        if label:
            mid = ((points[0][0] + points[-1][0]) / 2, (points[0][1] + points[-1][1]) / 2) if len(points) == 2 else points[len(points) // 2]
            self.text_in(el, wrap_text(label, 160, 16), 16, STROKE, mid)
        return el

    def document(self) -> dict[str, Any]:
        return {"type": "excalidraw", "version": 2, "source": "https://github.com/zsviczian/obsidian-excalidraw-plugin", "elements": self.elements,
                "appState": {"gridSize": None, "viewBackgroundColor": self.background}, "files": {}}


def balance_for_label(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """ラベルを、いちばん長い線分の中点に置けるように、同じ線上に点を足す (見た目は変わらない)。

    Excalidraw は、矢印の文字を、点の数が奇数なら真ん中の点に、偶数なら真ん中の線分の中点に置く。"""
    if len(points) <= 2:
        return points
    lengths = [math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(points, points[1:])]
    k = max(range(len(lengths)), key=lambda i: lengths[i])
    a, b = points[k], points[k + 1]
    q = points[: k + 1] + [((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)] + points[k + 1:]
    idx = k + 1
    while idx < len(q) - 1 - idx:  # 左が少ない: 最初の線分を割る
        q.insert(1, ((q[0][0] + q[1][0]) / 2, (q[0][1] + q[1][1]) / 2))
        idx += 1
    while idx > len(q) - 1 - idx:  # 右が少ない: 最後の線分を割る
        q.insert(len(q) - 1, ((q[-2][0] + q[-1][0]) / 2, (q[-2][1] + q[-1][1]) / 2))
    return q


def polyline_midpoint(points: list[tuple[float, float]]) -> tuple[float, float]:
    lengths = [math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(points, points[1:])]
    half = sum(lengths) / 2
    for (a, b), length in zip(zip(points, points[1:]), lengths):
        if half <= length or length == 0:
            ratio = half / length if length else 0
            return (a[0] + (b[0] - a[0]) * ratio, a[1] + (b[1] - a[1]) * ratio)
        half -= length
    return points[-1]


def draw_node(b: Builder, node: Node, stroke: str = STROKE) -> dict[str, Any]:
    el = b.shape(node.shape, node.x, node.y, node.w, node.h, fill_color(node.color), stroke, 2, node.dashed)
    b.text_in(el, node.lines, node.fs)
    return el


def shift_all(spec: Spec, dx: float, dy: float) -> None:
    for nd in spec.nodes:
        nd.x += dx
        nd.y += dy


def title_height(spec: Spec) -> float:
    return 28 * LINE_HEIGHT + 34 if spec.title else 0.0


# ---------------------------------------------------------------------------
# 図の種類ごとの組み立て
# ---------------------------------------------------------------------------

def build_flow(spec: Spec, b: Builder) -> None:
    for nd in spec.nodes:
        size_node(nd, 18, 220)
    centers, waypoints, layer, bands = layered_layout(spec)
    assign_xy_from_centers(spec, centers)
    left = min(nd.x for nd in spec.nodes)
    top = min(nd.y for nd in spec.nodes)
    shift_all(spec, MARGIN - left, MARGIN + title_height(spec) - top)
    # 層の帯の位置も、同じだけずらす
    dx, dy = MARGIN - left, MARGIN + title_height(spec) - top
    shift_a, _ = (dy, dx) if spec.direction == "TB" else (dx, dy)
    bands = [(s + shift_a, e + shift_a) for s, e in bands]
    waypoints = {ei: [(a + shift_a, c + (dx if spec.direction == "TB" else dy)) for a, c in pts] for ei, pts in waypoints.items()}
    routes = flow_routes(spec, waypoints, layer, bands)
    els = [draw_node(b, nd) for nd in spec.nodes]
    for e, r in zip(spec.edges, routes):
        b.arrow(r["points"], els[e.src], els[e.dst], e.arrow, e.dashed, label=e.label)


def build_swimlane(spec: Spec, b: Builder) -> None:
    d = spec.direction
    for nd in spec.nodes:
        size_node(nd, 18, 200, 130, 56)
    lay = swimlane_layout(spec)
    top = MARGIN + title_height(spec)
    for i, (a, c) in lay["centers"].items():
        x, y = to_xy(a, c, d)
        nd = spec.nodes[i]
        nd.x, nd.y = x - nd.w / 2 + MARGIN, y - nd.h / 2 + top
    header_a, body_end = lay["header_a"], lay["body_end"]
    a_shift = MARGIN if d == "LR" else top
    lay["bands"] = [(s0 + a_shift, e0 + a_shift) for s0, e0 in lay["bands"]]
    for li, (key, label, color) in enumerate(spec.lanes):
        start, size = lay["lane_start"][li], lay["lane_size"][li]
        if d == "LR":
            b.shape("rect", MARGIN + header_a, top + start, body_end - header_a, size, "transparent", LANE_STROKE, 2)
            head = b.shape("rect", MARGIN, top + start, header_a, size, fill_color(color), LANE_STROKE, 2)
        else:
            b.shape("rect", MARGIN + start, top + header_a, size, body_end - header_a, "transparent", LANE_STROKE, 2)
            head = b.shape("rect", MARGIN + start, top, size, header_a, fill_color(color), LANE_STROKE, 2)
        b.text_in(head, wrap_text(label, header_a - 24 if d == "LR" else size - 24, 18), 18)
    routes = swimlane_routes(spec, lay)
    els = [draw_node(b, nd) for nd in spec.nodes]
    for e, r in zip(spec.edges, routes):
        b.arrow(r["points"], els[e.src], els[e.dst], e.arrow, e.dashed, label=e.label)


def build_tree(spec: Spec, b: Builder) -> None:
    d = spec.direction
    for nd in spec.nodes:
        size_node(nd, 20 if nd.level == 0 else 18, 200)
    rel = tidy_tree(spec, [nd.idx for nd in spec.nodes], 0, d)
    for i, (a, c) in rel.items():
        x, y = to_xy(a, c, d)
        spec.nodes[i].x, spec.nodes[i].y = x - spec.nodes[i].w / 2, y - spec.nodes[i].h / 2
    left, top = min(nd.x for nd in spec.nodes), min(nd.y for nd in spec.nodes)
    shift_all(spec, MARGIN - left, MARGIN + title_height(spec) - top)
    els = [draw_node(b, nd) for nd in spec.nodes]
    for e in spec.edges:
        u, v = spec.nodes[e.src], spec.nodes[e.dst]
        if d == "TB":
            s, t = side_anchor(u, "bottom"), side_anchor(v, "top")
            mid = (s[1] + t[1]) / 2
            pts = dedupe_points([s, (s[0], mid), (t[0], mid), t])
        else:
            s, t = side_anchor(u, "right"), side_anchor(v, "left")
            mid = (s[0] + t[0]) / 2
            pts = dedupe_points([s, (mid, s[1]), (mid, t[1]), t])
        b.arrow(pts, els[e.src], els[e.dst], e.arrow)


def build_mindmap(spec: Spec, b: Builder) -> None:
    sizes = {0: 28, 1: 22}
    for nd in spec.nodes:
        size_node(nd, sizes.get(nd.level, 18), 180, 100, 48)
    root = spec.nodes[0]
    kids: dict[int, list[int]] = {nd.idx: [] for nd in spec.nodes}
    for nd in spec.nodes:
        if nd.parent is not None:
            kids[nd.parent].append(nd.idx)

    def leaves(i: int) -> int:
        return sum(leaves(k) for k in kids[i]) if kids[i] else 1

    load = {1: 0, -1: 0}
    for idx in kids[0]:  # 葉の数が少ない側へ振り分ける (右から)
        side = 1 if load[1] <= load[-1] else -1
        spec.nodes[idx].side = side
        load[side] += leaves(idx)
    for nd in spec.nodes:
        if nd.parent not in (None, 0):
            nd.side = spec.nodes[nd.parent].side
    gap_root, gap_level = 96.0, 72.0
    for side in (1, -1):
        members = [0] + [nd.idx for nd in spec.nodes if nd.parent is not None and nd.side == side]
        if len(members) == 1:
            continue
        rel = tidy_tree(spec, members, 0, "LR")  # 縦(c)の位置だけ使う。横は、根の端から層ごとに積む
        by_level: dict[int, list[Node]] = {}
        for i in members[1:]:
            spec.nodes[i].y = rel[i][1] - spec.nodes[i].h / 2
            by_level.setdefault(spec.nodes[i].level, []).append(spec.nodes[i])
        cursor = root.w / 2 + gap_root
        for level in sorted(by_level):
            width = max(nd.w for nd in by_level[level])
            for nd in by_level[level]:
                center = cursor + width / 2
                nd.x = (center if side == 1 else -center) - nd.w / 2
            cursor += width + gap_level
    root.x, root.y = -root.w / 2, -root.h / 2
    left, top = min(nd.x for nd in spec.nodes), min(nd.y for nd in spec.nodes)
    shift_all(spec, MARGIN - left, MARGIN + title_height(spec) - top)
    els = [draw_node(b, nd) for nd in spec.nodes]
    for e in spec.edges:
        u, v = spec.nodes[e.src], spec.nodes[e.dst]
        s = side_anchor(u, "right" if v.side == 1 else "left")
        t = side_anchor(v, "left" if v.side == 1 else "right")
        b.arrow([s, t], els[e.src], els[e.dst], "none", color="#495057" if v.level > 1 else STROKE, sw=3 if v.level == 1 else 2)


BUILDERS = {"flow": build_flow, "swimlane": build_swimlane, "tree": build_tree, "mindmap": build_mindmap}


def build_document(spec: Spec) -> dict[str, Any]:
    b = Builder(spec.font, spec.background)
    BUILDERS[spec.kind](spec, b)
    if spec.title:
        xs = [e["x"] for e in b.elements]
        xe = [e["x"] + e["width"] for e in b.elements]
        center = (min(xs) + max(xe)) / 2
        width = text_width(spec.title, 28)
        b.text_free(center - width / 2, MARGIN - 6, [spec.title], 28, TITLE_COLOR, "center")
        b.elements.insert(0, b.elements.pop())  # タイトルを先頭へ (他の要素の下に描く)
    return b.document()


# ---------------------------------------------------------------------------
# 出力
# ---------------------------------------------------------------------------

OBSIDIAN_HEAD = (
    "---\n\nexcalidraw-plugin: parsed\nai_generated: opencode\nai_reviewed: false\ntags: [excalidraw]\n\n---\n"
    "==⚠  Switch to EXCALIDRAW VIEW in the MORE OPTIONS menu of this document. ⚠== "
    "You can decompress Drawing data with the command palette: 'Decompress current Excalidraw file'. "
    "For more info check in plugin settings under 'Saving'\n\n\n# Excalidraw Data\n\n## Text Elements\n"
)


def to_obsidian_markdown(doc: dict[str, Any]) -> str:
    parts = [OBSIDIAN_HEAD]
    for el in doc["elements"]:
        if el["type"] == "text":
            parts.append(f"{el['text']} ^{el['id']}\n\n")
    parts.append("%%\n## Drawing\n```json\n")
    parts.append(json.dumps(doc, ensure_ascii=False, indent="\t"))
    parts.append("\n```\n%%\n")
    return "".join(parts)


def to_excalidraw_json(doc: dict[str, Any]) -> str:
    out = dict(doc)
    out["source"] = "https://excalidraw.com"
    return json.dumps(out, ensure_ascii=False, indent="\t") + "\n"


def build_from_spec(raw: Any) -> tuple[Spec, dict[str, Any]]:
    spec = parse_spec(raw)
    return spec, build_document(spec)
