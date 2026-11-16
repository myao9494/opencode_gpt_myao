"""excalidraw-diagram のテスト (生成・点検・コマンド)。

実行: python -m unittest discover -s skills/excalidraw-diagram/tests -v   (リポジトリのルートから)
Obsidian は使わず、生成した図を、点検 (excalidraw_check) で確かめる。図を読む機能は、スキル diagram-reader にある。
"""
from __future__ import annotations

import io
import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SKILL_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_DIR / "scripts"))

import excalidraw_cli as cli  # noqa: E402
import excalidraw_core as core  # noqa: E402
import excalidraw_check as reader  # noqa: E402

FLOW = {"type": "flow", "title": "申請フロー", "direction": "TB", "nodes": [
    {"id": "a", "label": "申請を受け付ける", "shape": "ellipse", "color": "green"},
    {"id": "b", "label": "内容を確認", "detail": "必須項目の漏れがないか"},
    {"id": "c", "label": "問題なし?", "shape": "diamond", "color": "yellow"},
    {"id": "d", "label": "承認する", "color": "green"},
    {"id": "e", "label": "差し戻す", "color": "red"},
    {"id": "g", "label": "通知メールを送る", "color": "purple"},
    {"id": "f", "label": "完了", "shape": "ellipse", "color": "gray"}],
    "edges": [{"from": "a", "to": "b"}, {"from": "b", "to": "c"}, {"from": "c", "to": "d", "label": "はい"}, {"from": "c", "to": "e", "label": "いいえ"},
              {"from": "d", "to": "g"}, {"from": "g", "to": "f"}, {"from": "d", "to": "f", "dashed": True}, {"from": "e", "to": "b", "label": "再提出"}]}

SWIM = {"type": "swimlane", "title": "経費精算フロー", "direction": "LR",
        "lanes": [{"id": "u", "label": "申請者"}, {"id": "b", "label": "上長"}, {"id": "k", "label": "経理"}],
        "nodes": [{"id": "s", "label": "開始", "shape": "ellipse", "lane": "u"}, {"id": "i", "label": "経費を入力", "lane": "u"},
                  {"id": "r", "label": "領収書を添付", "lane": "u"}, {"id": "ok", "label": "内容は妥当?", "shape": "diamond", "lane": "b"},
                  {"id": "fix", "label": "修正して再申請", "lane": "u", "color": "orange"}, {"id": "pay", "label": "支払処理", "lane": "k"},
                  {"id": "reg", "label": "会計システムへ登録", "detail": "仕訳を作成", "lane": "k"}, {"id": "e", "label": "終了", "shape": "ellipse", "lane": "k"}],
        "edges": [{"from": "s", "to": "i"}, {"from": "i", "to": "r"}, {"from": "r", "to": "ok"}, {"from": "ok", "to": "pay", "label": "はい"},
                  {"from": "ok", "to": "fix", "label": "いいえ"}, {"from": "fix", "to": "i"}, {"from": "pay", "to": "reg"}, {"from": "reg", "to": "e"}]}

TREE = {"type": "tree", "title": "システム構成", "root": {"label": "業務システム", "children": [
    {"label": "フロントエンド", "children": [{"label": "ログイン画面"}, {"label": "一覧画面", "detail": "検索と並べ替え"}, {"label": "入力フォーム"}]},
    {"label": "バックエンド", "children": [{"label": "認証API"}, {"label": "データAPI", "children": [{"label": "読み取り"}, {"label": "書き込み"}]}]},
    {"label": "データベース", "children": [{"label": "マスタ"}, {"label": "履歴"}]}]}}

MIND = {"type": "mindmap", "title": "方針", "root": {"label": "図解の方針", "children": [
    {"label": "作る", "children": [{"label": "フロー図"}, {"label": "スイムレーン"}, {"label": "ツリー"}, {"label": "マインドマップ"}]},
    {"label": "読む", "children": [{"label": "圧縮された図も"}, {"label": "文章にする"}, {"label": "Mermaid にする"}]},
    {"label": "点検", "children": [{"label": "重なり"}, {"label": "矢印の接続"}]},
    {"label": "制約", "children": [{"label": "画像は使えない"}, {"label": "標準ライブラリだけ"}]}]}}

ALL_SPECS = {"flow": FLOW, "swimlane": SWIM, "tree": TREE, "mindmap": MIND}


def build(spec: dict) -> tuple[core.Spec, dict]:
    return core.build_from_spec(json.loads(json.dumps(spec)))


def els(doc: dict, kind: str | None = None) -> list[dict]:
    return [e for e in doc["elements"] if kind is None or e["type"] == kind]


class WrapAsciiWordTests(unittest.TestCase):
    """0.12.0: 日本語に続く英単語 (空白なし) を、単語の途中で折り返さない (図で「open-p / ath」になった)。"""

    def test_日本語に続く英単語は丸ごと次の行へ(self) -> None:
        width = core.text_width("hyperlinkにopen-p", 16) + 1
        self.assertEqual(core.wrap_text("hyperlinkにopen-path", width, 16), ["hyperlinkに", "open-path"])

    def test_長すぎる英単語は従来どおり途中で折る(self) -> None:
        lines = core.wrap_text("abcdefghijklmnopqrstuvwxyz", core.text_width("abcdefghij", 16), 16)
        self.assertEqual("".join(lines), "abcdefghijklmnopqrstuvwxyz")
        self.assertGreater(len(lines), 1)

    def test_空白での折り返しと日本語は変わらない(self) -> None:
        self.assertEqual(core.wrap_text("open path", core.text_width("open pa", 16), 16), ["open", "path"])
        self.assertEqual(core.wrap_text("あいうえおかきくけこ", core.text_width("あいうえお", 16) + 1, 16), ["あいうえお", "かきくけこ"])


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name).resolve()


# ---------------------------------------------------------------------------
# 圧縮データ
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# 文字の幅
# ---------------------------------------------------------------------------

class TextTests(unittest.TestCase):
    def test_全角は1em_半角は約0_6em(self) -> None:
        self.assertEqual(core.text_width("あいう", 20), 60)
        self.assertAlmostEqual(core.text_width("abc", 20), 36)
        self.assertGreater(core.text_width("ABC", 20), core.text_width("abc", 20))

    def test_折り返し(self) -> None:
        lines = core.wrap_text("あいうえおかきくけこ", 100, 20)  # 5 文字ずつ
        self.assertEqual(lines, ["あいうえお", "かきくけこ"])
        self.assertEqual(core.wrap_text("一行\n二行", 500, 20), ["一行", "二行"])
        self.assertEqual(core.wrap_text("", 100, 20), [""])

    def test_行頭に来てはいけない記号はぶら下げる(self) -> None:
        lines = core.wrap_text("あいうえお。かき", 100, 20)
        self.assertTrue(all(not line.startswith("。") for line in lines))

    def test_英単語は空白で折り返す(self) -> None:
        lines = core.wrap_text("hello world foo", 90, 20)
        self.assertTrue(all(" " not in line.strip() or True for line in lines))
        self.assertEqual(lines[0], "hello")

# ---------------------------------------------------------------------------
# 仕様の検査
# ---------------------------------------------------------------------------

class SpecTests(unittest.TestCase):
    def bad(self, spec: dict, needle: str) -> None:
        with self.assertRaises(core.SpecError) as ctx:
            core.parse_spec(spec)
        self.assertIn(needle, str(ctx.exception))

    def test_正しい仕様は読める(self) -> None:
        for name, spec in ALL_SPECS.items():
            parsed = core.parse_spec(json.loads(json.dumps(spec)))
            self.assertEqual(parsed.kind, name)

    def test_種類_方向_フォント(self) -> None:
        self.bad({"type": "gantt"}, "type")
        self.bad({**FLOW, "direction": "UP"}, "direction")
        self.bad({**FLOW, "font": "comic"}, "font")
        self.bad({**FLOW, "routing": "curve"}, "routing")
        self.assertEqual(core.parse_spec({**FLOW, "font": "excalifont"}).font, 5)
        self.assertEqual(core.parse_spec({**FLOW, "font": 3}).font, 3)

    def test_ノードと矢印の検査(self) -> None:
        self.bad({"type": "flow", "nodes": []}, "nodes")
        self.bad({"type": "flow", "nodes": [{"id": "a"}]}, "label")
        self.bad({"type": "flow", "nodes": [{"id": "a", "label": "x"}, {"id": "a", "label": "y"}]}, "重複")
        self.bad({"type": "flow", "nodes": [{"id": "a", "label": "x", "shape": "star"}]}, "shape")
        self.bad({"type": "flow", "nodes": [{"id": "a", "label": "x", "color": "magenta"}]}, "色")
        self.bad({"type": "flow", "nodes": [{"id": "a", "label": "x"}], "edges": [{"from": "a", "to": "zz"}]}, "zz")
        self.bad({"type": "flow", "nodes": [{"id": "a", "label": "x"}], "edges": [{"from": "a", "to": "a"}]}, "自分自身")
        self.bad({"type": "flow", "nodes": [{"id": "a", "label": "あ" * 300}]}, "長すぎ")
        self.bad({"type": "flow", "nodes": [{"id": f"n{i}", "label": "x"} for i in range(81)]}, "多すぎ")

    def test_スイムレーンとツリーの検査(self) -> None:
        self.bad({"type": "swimlane", "nodes": [{"id": "a", "label": "x", "lane": "u"}]}, "lanes")
        self.bad({**SWIM, "nodes": [{"id": "a", "label": "x", "lane": "nope"}]}, "lane")
        self.bad({"type": "tree"}, "root")
        self.bad({"type": "tree", "root": {"label": "r", "children": "x"}}, "children")
        self.assertEqual(len(core.parse_spec({"type": "mindmap", "root": {"label": "r"}}).nodes), 1)

    def test_色は名前か_rrggbb(self) -> None:
        spec = core.parse_spec({"type": "flow", "nodes": [{"id": "a", "label": "x", "color": "#aabbcc"}]})
        self.assertEqual(spec.nodes[0].color, "#aabbcc")


# ---------------------------------------------------------------------------
# 生成した図の性質
# ---------------------------------------------------------------------------

class BuildTests(unittest.TestCase):
    def test_4種類すべて_点検でエラーも注意も出ない(self) -> None:
        for name, spec in ALL_SPECS.items():
            _, doc = build(spec)
            errors, warns = reader.check_doc(doc)
            self.assertEqual((errors, warns), ([], []), name)

    def test_同じ仕様なら_同じ出力(self) -> None:
        for name, spec in ALL_SPECS.items():
            self.assertEqual(core.to_obsidian_markdown(build(spec)[1]), core.to_obsidian_markdown(build(spec)[1]), name)

    def test_IDは一意で_矢印は両端が箱に結び付く(self) -> None:
        for name, spec in ALL_SPECS.items():
            _, doc = build(spec)
            ids = [e["id"] for e in doc["elements"]]
            self.assertEqual(len(ids), len(set(ids)), name)
            for arrow in els(doc, "arrow"):
                self.assertTrue(arrow["startBinding"] and arrow["endBinding"], name)
                for key in ("startBinding", "endBinding"):
                    target = next(e for e in doc["elements"] if e["id"] == arrow[key]["elementId"])
                    self.assertTrue(any(b["id"] == arrow["id"] for b in target["boundElements"]), name)

    def test_Excalidrawが嫌うフィールドを出さない(self) -> None:
        _, doc = build(FLOW)
        for e in doc["elements"]:
            for banned in ("frameId", "index", "versionNonce", "rawText"):
                self.assertNotIn(banned, e)

    def test_文字は箱の中央に結び付く(self) -> None:
        _, doc = build(FLOW)
        by_id = {e["id"]: e for e in doc["elements"]}
        bound = [e for e in els(doc, "text") if e["containerId"] in by_id and by_id[e["containerId"]]["type"] != "arrow"]
        self.assertGreaterEqual(len(bound), 7)
        for t in bound:
            c = by_id[t["containerId"]]
            self.assertAlmostEqual(t["x"] + t["width"] / 2, c["x"] + c["width"] / 2, delta=1)
            self.assertAlmostEqual(t["y"] + t["height"] / 2, c["y"] + c["height"] / 2, delta=1)
            self.assertEqual((t["textAlign"], t["verticalAlign"]), ("center", "middle"))

    def test_矢印のラベルは_Excalidrawが置く位置(self) -> None:
        # 点の数が奇数なら真ん中の点、偶数なら真ん中の線分の中点にラベルが置かれる。それと同じ位置にしてある
        _, doc = build(FLOW)
        by_id = {e["id"]: e for e in doc["elements"]}
        for t in els(doc, "text"):
            arrow = by_id.get(t["containerId"] or "")
            if not arrow or arrow["type"] != "arrow":
                continue
            pts = [(arrow["x"] + p[0], arrow["y"] + p[1]) for p in arrow["points"]]
            mid = ((pts[0][0] + pts[-1][0]) / 2, (pts[0][1] + pts[-1][1]) / 2) if len(pts) == 2 else pts[len(pts) // 2]
            self.assertAlmostEqual(t["x"] + t["width"] / 2, mid[0], delta=1)
            self.assertAlmostEqual(t["y"] + t["height"] / 2, mid[1], delta=1)
            self.assertEqual(len(pts) % 2, 1 if len(pts) > 2 else 0)

    def test_矢印の端は箱の縁にある(self) -> None:
        for name, spec in ALL_SPECS.items():
            _, doc = build(spec)
            by_id = {e["id"]: e for e in doc["elements"]}
            for arrow in els(doc, "arrow"):
                pts = reader.arrow_points(arrow)
                for key, point in (("startBinding", pts[0]), ("endBinding", pts[-1])):
                    box = reader.bbox(by_id[arrow[key]["elementId"]])
                    self.assertLessEqual(reader.dist_point_box(point, box), 2, name)

    def test_文字は箱からはみ出さない_長い文字も(self) -> None:
        spec = {"type": "flow", "nodes": [{"id": "a", "label": "とても長い名前の処理を行うステップで、折り返される文字です。ABCDEFG hijklmn", "detail": "詳しい説明がここに入ります。二行以上になる長さ"},
                                          {"id": "b", "label": "短い"}], "edges": [{"from": "a", "to": "b"}]}
        _, doc = build(spec)
        self.assertEqual(reader.check_doc(doc), ([], []))

    def test_ひし形は角を丸めない_角丸は長方形だけ(self) -> None:
        _, doc = build(FLOW)
        for diamond in els(doc, "diamond"):
            self.assertIsNone(diamond["roundness"])
        spec = {"type": "flow", "nodes": [{"id": "a", "label": "角丸"}, {"id": "b", "label": "角あり", "shape": "rect"}, {"id": "c", "label": "判断", "shape": "diamond"}, {"id": "d", "label": "楕円", "shape": "ellipse"}],
                "edges": [{"from": "a", "to": "b"}, {"from": "b", "to": "c"}, {"from": "c", "to": "d"}]}
        _, doc = build(spec)
        rounded = {e["type"]: e["roundness"] for e in doc["elements"] if e["type"] in ("diamond", "ellipse")}
        self.assertIsNone(rounded["diamond"])
        self.assertEqual(sorted(str(e["roundness"]) for e in els(doc, "rectangle")), ["None", "{'type': 3}"])

    def test_フォントサイズの下限(self) -> None:
        for name, spec in ALL_SPECS.items():
            _, doc = build(spec)
            for t in els(doc, "text"):
                self.assertGreaterEqual(t["fontSize"], 16, name)

    def test_フォントと背景(self) -> None:
        _, doc = build({**FLOW, "font": "excalifont", "background": "#f8f9fa"})
        self.assertTrue(all(t["fontFamily"] == 5 for t in els(doc, "text")))
        self.assertEqual(doc["appState"]["viewBackgroundColor"], "#f8f9fa")
        _, doc = build(FLOW)
        self.assertTrue(all(t["fontFamily"] == 1 for t in els(doc, "text")))  # この Vault の既存の図に合わせた既定


class FlowLayoutTests(unittest.TestCase):
    def positions(self, spec: dict) -> dict[str, core.Node]:
        parsed, _ = build(spec)
        return {n.key: n for n in parsed.nodes}

    def test_縦_上から下へ層が進み_分岐は左右に分かれる(self) -> None:
        pos = self.positions(FLOW)
        order = ["a", "b", "c"]
        ys = [pos[k].y for k in order]
        self.assertEqual(ys, sorted(ys))
        self.assertTrue(pos["d"].y > pos["c"].y and pos["e"].y > pos["c"].y)
        self.assertNotEqual(round(pos["d"].cx), round(pos["e"].cx))
        self.assertTrue(pos["f"].y > pos["g"].y)

    def test_横(self) -> None:
        pos = self.positions({**FLOW, "direction": "LR"})
        self.assertLess(pos["a"].x, pos["b"].x)
        self.assertLess(pos["b"].x, pos["c"].x)
        self.assertLess(pos["c"].x, pos["d"].x)

    def test_層を飛ばす矢印は_箱を避ける(self) -> None:
        spec = {"type": "flow", "nodes": [{"id": k, "label": k.upper() * 3} for k in "abcd"],
                "edges": [{"from": "a", "to": "b"}, {"from": "b", "to": "c"}, {"from": "c", "to": "d"}, {"from": "a", "to": "d", "label": "直行"}]}
        for direction in ("TB", "LR"):
            _, doc = build({**spec, "direction": direction})
            self.assertEqual(reader.check_doc(doc), ([], []), direction)

    def test_戻る矢印(self) -> None:
        for direction in ("TB", "LR"):
            _, doc = build({**FLOW, "direction": direction})
            self.assertEqual(reader.check_doc(doc), ([], []), direction)
            loops = [a for a in els(doc, "arrow") if len(a["points"]) >= 4 and a["endBinding"]]
            self.assertTrue(loops)

    def test_箱が重ならない_たくさんの分岐(self) -> None:
        nodes = [{"id": "r", "label": "根"}] + [{"id": f"c{i}", "label": f"子{i}", "detail": "説明" * (i % 3)} for i in range(8)] + [{"id": "z", "label": "合流"}]
        edges = [{"from": "r", "to": f"c{i}"} for i in range(8)] + [{"from": f"c{i}", "to": "z"} for i in range(8)]
        for direction in ("TB", "LR"):
            _, doc = build({"type": "flow", "direction": direction, "nodes": nodes, "edges": edges})
            errors, warns = reader.check_doc(doc)
            self.assertEqual(errors, [])
            self.assertFalse([w for w in warns if "重なって" in w], direction)

    def test_ひし形の側面の出口(self) -> None:
        _, doc = build(FLOW)
        diamond = next(e for e in els(doc, "diamond"))
        starts = []
        for arrow in els(doc, "arrow"):
            if arrow["startBinding"] and arrow["startBinding"]["elementId"] == diamond["id"]:
                starts.append(reader.arrow_points(arrow)[0])
        self.assertEqual(len(starts), 2)
        cx = diamond["x"] + diamond["width"] / 2
        self.assertTrue(any(p[0] < cx - 10 for p in starts) and any(p[0] > cx + 10 for p in starts))  # 左と右の頂点から出る

    def test_循環があっても作れる(self) -> None:
        spec = {"type": "flow", "nodes": [{"id": "a", "label": "A"}, {"id": "b", "label": "B"}, {"id": "c", "label": "C"}],
                "edges": [{"from": "a", "to": "b"}, {"from": "b", "to": "c"}, {"from": "c", "to": "a"}]}
        _, doc = build(spec)
        self.assertEqual(reader.check_doc(doc), ([], []))

    def test_孤立したノードがあっても作れる(self) -> None:
        _, doc = build({"type": "flow", "nodes": [{"id": "a", "label": "A"}, {"id": "b", "label": "B"}], "edges": []})
        self.assertEqual(len(els(doc, "arrow")), 0)
        self.assertEqual(reader.check_doc(doc), ([], []))

    def test_直線の経路(self) -> None:
        _, doc = build({**FLOW, "routing": "straight"})
        self.assertEqual(reader.check_doc(doc)[0], [])


class SwimlaneTests(unittest.TestCase):
    def test_ノードは自分のレーンの中にある(self) -> None:
        for direction in ("LR", "TB"):
            parsed, doc = build({**SWIM, "direction": direction})
            lanes = [e for e in els(doc, "rectangle") if e["backgroundColor"] == "transparent"]
            self.assertEqual(len(lanes), 3)
            ordered = sorted(lanes, key=lambda e: e["y"] if direction == "LR" else e["x"])
            for node in parsed.nodes:
                lane = ordered[node.lane]
                self.assertTrue(lane["x"] <= node.x and node.x + node.w <= lane["x"] + lane["width"], (direction, node.key))
                self.assertTrue(lane["y"] <= node.y and node.y + node.h <= lane["y"] + lane["height"], (direction, node.key))

    def test_レーンは並び_流れは軸に沿う(self) -> None:
        parsed, _ = build(SWIM)
        pos = {n.key: n for n in parsed.nodes}
        self.assertLess(pos["s"].x, pos["i"].x)
        self.assertLess(pos["i"].x, pos["r"].x)
        self.assertLess(pos["r"].x, pos["ok"].x)
        self.assertLess(pos["s"].y, pos["ok"].y)  # 上のレーン → 次のレーン
        parsed, _ = build({**SWIM, "direction": "TB"})
        pos = {n.key: n for n in parsed.nodes}
        self.assertLess(pos["s"].y, pos["i"].y)
        self.assertLess(pos["s"].x, pos["ok"].x)

    def test_点検が通る_横も縦も(self) -> None:
        for direction in ("LR", "TB"):
            _, doc = build({**SWIM, "direction": direction})
            self.assertEqual(reader.check_doc(doc), ([], []), direction)

    def test_同じレーン_同じ層に複数のノード(self) -> None:
        spec = {"type": "swimlane", "lanes": [{"id": "x", "label": "担当"}, {"id": "y", "label": "別"}],
                "nodes": [{"id": "a", "label": "A", "lane": "x"}, {"id": "b", "label": "B", "lane": "x"}, {"id": "c", "label": "C", "lane": "y"}, {"id": "d", "label": "D", "lane": "y"}],
                "edges": [{"from": "a", "to": "c"}, {"from": "b", "to": "d"}]}
        parsed, doc = build(spec)
        self.assertEqual(reader.check_doc(doc)[0], [])
        a, b = parsed.nodes[0], parsed.nodes[1]
        self.assertFalse(abs(a.cy - b.cy) < 10 and abs(a.cx - b.cx) < 10)  # 重ならず、積み重なる

    def test_スイムレーンの戻る矢印は箱を横切らない(self) -> None:
        _, doc = build(SWIM)
        self.assertFalse([w for w in reader.check_doc(doc)[1] if "横切" in w])


class TreeAndMindmapTests(unittest.TestCase):
    def test_親は子の真ん中の上(self) -> None:
        parsed, _ = build(TREE)
        by_parent: dict[int, list[core.Node]] = {}
        for n in parsed.nodes:
            if n.parent is not None:
                by_parent.setdefault(n.parent, []).append(n)
        for parent, kids in by_parent.items():
            p = parsed.nodes[parent]
            self.assertAlmostEqual(p.cx, (kids[0].cx + kids[-1].cx) / 2, delta=1)
            self.assertTrue(all(k.y > p.y for k in kids))

    def test_横向きのツリー(self) -> None:
        parsed, doc = build({**TREE, "direction": "LR"})
        self.assertTrue(all(parsed.nodes[n.parent].x < n.x for n in parsed.nodes if n.parent is not None))
        self.assertEqual(reader.check_doc(doc), ([], []))

    def test_マインドマップは左右に振り分けられ_葉の数が偏らない(self) -> None:
        parsed, _ = build(MIND)
        root = parsed.nodes[0]
        right = [n for n in parsed.nodes[1:] if n.cx > root.cx]
        left = [n for n in parsed.nodes[1:] if n.cx < root.cx]
        self.assertTrue(right and left)
        self.assertLessEqual(abs(len(right) - len(left)), 5)
        self.assertEqual(len(right) + len(left), len(parsed.nodes) - 1)

    def test_マインドマップの枝ごとに色が違う(self) -> None:
        parsed, _ = build(MIND)
        tops = [n for n in parsed.nodes if n.parent == 0]
        self.assertEqual(len({n.color for n in tops}), len(tops))
        for n in parsed.nodes:
            if n.level > 1:
                top = n
                while top.level > 1:
                    top = parsed.nodes[top.parent]
                self.assertEqual(n.color, top.color)

    def test_根だけのマインドマップ_1段だけのツリー(self) -> None:
        _, doc = build({"type": "mindmap", "root": {"label": "ひとつだけ"}})
        self.assertEqual(len(els(doc, "ellipse")), 1)
        _, doc = build({"type": "tree", "root": {"label": "根", "children": [{"label": "子"}]}})
        self.assertEqual(reader.check_doc(doc), ([], []))

    def test_たくさんの葉でも重ならない(self) -> None:
        spec = {"type": "mindmap", "root": {"label": "根", "children": [{"label": f"枝{i}", "children": [{"label": f"葉{i}-{j}"} for j in range(5)]} for i in range(7)]}}
        _, doc = build(spec)
        errors, warns = reader.check_doc(doc)
        self.assertEqual(errors, [])
        self.assertFalse([w for w in warns if "重なって" in w])


# ---------------------------------------------------------------------------
# 出力の形式と読み込み
# ---------------------------------------------------------------------------

class FormatTests(unittest.TestCase):
    def test_Obsidian形式の構造(self) -> None:
        _, doc = build(FLOW)
        md = core.to_obsidian_markdown(doc)
        # 0.12.0: AI が作った図の印 (人が確かめて ai_reviewed を ✓ にするまで、ハーネスは根拠にしない)
        self.assertTrue(md.startswith("---\n\nexcalidraw-plugin: parsed\nai_generated: opencode\nai_reviewed: false\ntags: [excalidraw]\n\n---\n==⚠  Switch to EXCALIDRAW VIEW"))
        self.assertIn("\n\n\n# Excalidraw Data\n\n## Text Elements\n", md)
        self.assertIn("\n%%\n## Drawing\n```json\n", md)
        self.assertTrue(md.endswith("\n```\n%%\n"))
        for t in els(doc, "text"):  # 全ての文字が ^id つきで一覧にある
            self.assertIn(f"{t['text']} ^{t['id']}\n", md)

    def test_書いたものを読み戻せる(self) -> None:
        _, doc = build(SWIM)
        self.assertEqual(reader.load_plain(core.to_obsidian_markdown(doc), "a.excalidraw.md"), doc)
        plain = core.to_excalidraw_json(doc)
        loaded = reader.load_plain(plain, "a.excalidraw")
        self.assertEqual(loaded["source"], "https://excalidraw.com")
        self.assertEqual(loaded["elements"], doc["elements"])

def raw_rect(eid: str, x: float, y: float, w: float, h: float, **kw) -> dict:
    return {"id": eid, "type": kw.pop("type", "rectangle"), "x": x, "y": y, "width": w, "height": h, "backgroundColor": kw.pop("fill", "#a5d8ff"), "strokeColor": "#1e1e1e", **kw}


def raw_text(eid: str, text: str, x: float, y: float, w: float = 80, h: float = 24, **kw) -> dict:
    return {"id": eid, "type": "text", "x": x, "y": y, "width": w, "height": h, "text": text, "fontSize": kw.pop("fontSize", 18), **kw}


def raw_arrow(eid: str, x: float, y: float, pts: list[list[float]], **kw) -> dict:
    return {"id": eid, "type": "arrow", "x": x, "y": y, "width": 1, "height": 1, "points": pts, "endArrowhead": kw.pop("end", "arrow"), "startArrowhead": kw.pop("start", None), **kw}


class CheckTests(unittest.TestCase):
    def check(self, *elements: dict) -> tuple[list[str], list[str]]:
        return reader.check_doc({"elements": list(elements)})

    def test_正常(self) -> None:
        self.assertEqual(self.check(raw_rect("a", 0, 0, 100, 60), raw_rect("b", 200, 0, 100, 60)), ([], []))

    def test_重なり(self) -> None:
        _, warns = self.check(raw_rect("a", 0, 0, 100, 60), raw_rect("b", 50, 20, 100, 60))
        self.assertTrue(any("重なって" in w for w in warns))
        self.assertEqual(self.check(raw_rect("a", 0, 0, 300, 200, fill="transparent"), raw_rect("b", 50, 50, 100, 60))[1], [])  # 含むのは重なりではない

    def test_ID(self) -> None:
        errors, _ = self.check(raw_rect("a", 0, 0, 100, 60), raw_rect("a", 200, 0, 100, 60))
        self.assertTrue(any("重複" in e for e in errors))

    def test_結合の欠け(self) -> None:
        errors, _ = self.check(raw_rect("a", 0, 0, 100, 60), raw_arrow("ar", 100, 30, [[0, 0], [50, 0]], startBinding={"elementId": "nope", "focus": 0, "gap": 1}))
        self.assertTrue(any("相手がありません" in e for e in errors))
        errors, _ = self.check(raw_text("t", "x", 0, 0, containerId="nope"))
        self.assertTrue(any("containerId" in e for e in errors))

    def test_文字(self) -> None:
        _, warns = self.check(raw_text("t", "小さい", 0, 0, fontSize=10), raw_text("u", "絵文字😀", 0, 40))
        self.assertTrue(any("小さすぎ" in w for w in warns))
        self.assertTrue(any("絵文字" in w for w in warns))

    def test_矢印が箱を横切る(self) -> None:
        _, warns = self.check(raw_rect("a", 0, 0, 100, 60), raw_rect("m", 200, 0, 100, 60), raw_rect("b", 400, 0, 100, 60),
                              raw_arrow("ar", 100, 30, [[0, 0], [300, 0]], startBinding={"elementId": "a", "focus": 0, "gap": 1}, endBinding={"elementId": "b", "focus": 0, "gap": 1}))
        self.assertTrue(any("横切" in w for w in warns))

    def test_矢印の端が離れている(self) -> None:
        _, warns = self.check(raw_rect("a", 0, 0, 100, 60), raw_rect("b", 400, 0, 100, 60),
                              raw_arrow("ar", 150, 30, [[0, 0], [100, 0]], startBinding={"elementId": "a", "focus": 0, "gap": 1}, endBinding={"elementId": "b", "focus": 0, "gap": 1}))
        self.assertTrue(sum("離れて" in w for w in warns) == 2)

    def test_はみ出す文字(self) -> None:
        box = raw_rect("a", 0, 0, 60, 30, boundElements=[{"id": "t", "type": "text"}])
        _, warns = self.check(box, raw_text("t", "とても長い文字列です", 0, 0, containerId="a"))
        self.assertTrue(any("はみ出" in w for w in warns))


# ---------------------------------------------------------------------------
# コマンド
# ---------------------------------------------------------------------------

class CliTests(Base):
    def setUp(self) -> None:
        super().setUp()
        self.vault = self.tmp / "vault"
        (self.vault / ".obsidian").mkdir(parents=True)
        (self.vault / ".handoff").mkdir()
        (self.vault / "notes").mkdir()
        self.spec = self.vault / ".handoff" / "flow.json"
        self.spec.write_text(json.dumps(FLOW, ensure_ascii=False), encoding="utf-8")

    def run_cli(self, *argv: str, cwd: Path | None = None) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        code = cli.main(list(argv), stdout=out, stderr=err, cwd=cwd or self.vault)
        return code, out.getvalue(), err.getvalue()

    def test_作って_読み戻す(self) -> None:
        code, out, err = self.run_cli("build", ".handoff/flow.json", "--out", "notes/申請.excalidraw.md")
        self.assertEqual((code, err), (0, ""))
        self.assertIn("作りました: notes/申請.excalidraw.md", out)
        self.assertIn("種類 flow / 箱 7 個 / 矢印 8 本", out)
        self.assertIn("ノートへの埋め込み: ![[申請.excalidraw]]", out)
        self.assertNotIn("注意:", out)
        code, out, _ = self.run_cli("check", "notes/申請.excalidraw.md")
        self.assertEqual(code, 0)
        self.assertIn("エラー 0 件 / 注意 0 件", out)

    def test_標準のexcalidraw形式(self) -> None:
        code, out, _ = self.run_cli("build", ".handoff/flow.json", "--out", "notes/a.excalidraw")
        self.assertEqual(code, 0)
        self.assertNotIn("埋め込み", out)
        self.assertEqual(json.loads((self.vault / "notes" / "a.excalidraw").read_text(encoding="utf-8"))["source"], "https://excalidraw.com")
        self.assertEqual(self.run_cli("check", "notes/a.excalidraw")[0], 0)

    def test_上書きしない(self) -> None:
        for _ in range(3):
            code, out, _ = self.run_cli("build", ".handoff/flow.json", "--out", "notes/同名.excalidraw.md")
        self.assertEqual(sorted(p.name for p in (self.vault / "notes").iterdir()), ["同名.excalidraw.md", "同名_2.excalidraw.md", "同名_3.excalidraw.md"])
        self.assertIn("上書きしていません", out)

    def test_dry_runは作らない(self) -> None:
        code, out, _ = self.run_cli("build", ".handoff/flow.json", "--out", "notes/x.excalidraw.md", "--dry-run")
        self.assertEqual(code, 0)
        self.assertIn("[試行]", out)
        self.assertIn("実際には何も作っていません", out)
        self.assertEqual(list((self.vault / "notes").iterdir()), [])

    def test_出力先の制限(self) -> None:
        for bad in ("notes/a.md", "notes/a.excalidraw.txt", "../outside.excalidraw.md", ".obsidian/a.excalidraw.md", ".handoff/a.excalidraw.md", "notes/.hidden/a.excalidraw.md"):
            code, _, err = self.run_cli("build", ".handoff/flow.json", "--out", bad)
            self.assertEqual(code, 2, bad)
        self.assertEqual(list((self.vault / "notes").iterdir()), [])
        self.assertFalse((self.tmp / "outside.excalidraw.md").exists())

    def test_シンボリックリンクで外へ出られない(self) -> None:
        outside = self.tmp / "outside"
        outside.mkdir()
        try:
            os.symlink(outside, self.vault / "link")
        except (OSError, NotImplementedError):
            self.skipTest("シンボリックリンクを作れない環境")
        self.assertEqual(self.run_cli("build", ".handoff/flow.json", "--out", "link/a.excalidraw.md")[0], 2)
        self.assertEqual(list(outside.iterdir()), [])

    def test_名前の安全化(self) -> None:
        code, out, _ = self.run_cli("build", ".handoff/flow.json", "--out", "notes/a:b*c#d.excalidraw.md")
        self.assertEqual(code, 0)
        self.assertTrue((self.vault / "notes" / "a_b_c_d.excalidraw.md").exists())
        self.assertEqual(cli.safe_stem("CON"), "_CON")

    def test_仕様の間違いは分かるメッセージ(self) -> None:
        bad = self.vault / ".handoff" / "bad.json"
        bad.write_text(json.dumps({"type": "flow", "nodes": [{"id": "a", "label": "x"}], "edges": [{"from": "a", "to": "zz"}]}), encoding="utf-8")
        code, _, err = self.run_cli("build", ".handoff/bad.json", "--out", "notes/b.excalidraw.md")
        self.assertEqual(code, 2)
        self.assertIn("zz", err)
        bad.write_text("{壊れ", encoding="utf-8")
        self.assertEqual(self.run_cli("build", ".handoff/bad.json", "--out", "notes/b.excalidraw.md")[0], 2)
        self.assertEqual(self.run_cli("build", ".handoff/ない.json", "--out", "notes/b.excalidraw.md")[0], 2)

    def test_読める場所の制限(self) -> None:
        outside = self.tmp / "outside.excalidraw.md"
        outside.write_text(core.to_obsidian_markdown(build(FLOW)[1]), encoding="utf-8")
        code, _, err = self.run_cli("check", str(outside))
        self.assertEqual(code, 2)
        self.assertIn("読んでよい場所の外", err)
        code, _, err = self.run_cli("build", str(outside), "--out", "notes/a.excalidraw.md")
        self.assertEqual(code, 2)

    def test_点検でエラーがあれば終了コード4(self) -> None:
        doc = {"type": "excalidraw", "version": 2, "elements": [raw_rect("a", 0, 0, 100, 60), raw_rect("a", 200, 0, 100, 60)], "appState": {}, "files": {}}
        (self.vault / "notes" / "bad.excalidraw").write_text(json.dumps(doc), encoding="utf-8")
        code, out, _ = self.run_cli("check", "notes/bad.excalidraw")
        self.assertEqual(code, 4)
        self.assertIn("重複", out)

    def test_Vaultが無いときは_いまのフォルダの中だけ(self) -> None:
        plain = self.tmp / "plain"
        plain.mkdir()
        (plain / "s.json").write_text(json.dumps(FLOW, ensure_ascii=False), encoding="utf-8")
        code, out, _ = self.run_cli("build", "s.json", "--out", "a.excalidraw.md", cwd=plain)
        self.assertEqual(code, 0)
        self.assertTrue((plain / "a.excalidraw.md").exists())
        self.assertEqual(self.run_cli("build", "s.json", "--out", "../b.excalidraw.md", cwd=plain)[0], 2)


class SafetyTests(unittest.TestCase):
    def test_ソースに削除_実行_通信が無く_書き込みが限られる(self) -> None:
        banned = (".unlink(", "os.remove", "rmtree", "rmdir", "shutil", "subprocess", "os.system", "urlopen", "socket", "eval(", "exec(", "rename(", "os.replace", "extractall")
        for name in ("excalidraw_core.py", "excalidraw_check.py", "excalidraw_cli.py"):
            source = (SKILL_DIR / "scripts" / name).read_text(encoding="utf-8")
            for needle in banned:
                self.assertNotIn(needle, source, f"{name} に {needle} があります")
        for name in ("excalidraw_core.py", "excalidraw_check.py"):
            source = (SKILL_DIR / "scripts" / name).read_text(encoding="utf-8")
            self.assertNotIn(".write_text(", source, name)
            self.assertNotIn("open(", source, name)
        cli_source = (SKILL_DIR / "scripts" / "excalidraw_cli.py").read_text(encoding="utf-8")
        self.assertEqual(cli_source.count(".write_text("), 1, "書き込みは build の出力の 1 か所だけ")

    def test_ライセンス表示がある(self) -> None:
        notice = (SKILL_DIR / "NOTICE.md").read_text(encoding="utf-8")
        self.assertIn("Axton Liu", notice)
        self.assertIn("MIT", notice)
        self.assertTrue(re.search(r"Permission is hereby granted", (SKILL_DIR / "LICENSE-axton-obsidian-visual-skills.txt").read_text(encoding="utf-8")))


if __name__ == "__main__":
    unittest.main()
