"""diagram-reader のテスト (図の読み込み・文章化。Excalidraw と draw.io)。

実行: python -m unittest discover -s skills/diagram-reader/tests -v   (リポジトリのルートから)
Obsidian も draw.io も使わず、テストの中で組み立てた図を読む。Excalidraw の圧縮データは、プラグインが実際に保存したものを使う。
作った図を読み戻すテストのために、作る側 (excalidraw-diagram) の core を、テストの中だけで使う。
"""
from __future__ import annotations

import base64
import html
import io
import json
import os
import re
import sys
import tempfile
import unittest
import urllib.parse
import zlib
from pathlib import Path
from unittest.mock import patch

SKILL_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_DIR / "scripts"))
sys.path.insert(0, str(SKILL_DIR.parent / "excalidraw-diagram" / "scripts"))  # テストだけ: 作った図を読み戻す

import diagram_cli as cli  # noqa: E402
import diagram_model as reader  # noqa: E402
import excalidraw_core as core  # noqa: E402
import load_drawio  # noqa: E402
import load_excalidraw  # noqa: E402
import lzstring  # noqa: E402

FIXTURES = json.loads((SKILL_DIR / "tests" / "fixtures" / "compressed_samples.json").read_text(encoding="utf-8"))

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



def build(spec: dict) -> tuple[core.Spec, dict]:
    return core.build_from_spec(json.loads(json.dumps(spec)))


def els(doc: dict, kind: str | None = None) -> list[dict]:
    return [e for e in doc["elements"] if kind is None or e["type"] == kind]




class LzStringTests(unittest.TestCase):
    def test_プラグインが実際に保存した圧縮データを展開できる(self) -> None:
        empty = json.loads(lzstring.decompress_from_base64(FIXTURES["empty_drawing"]))
        self.assertEqual((empty["type"], empty["elements"], empty["files"]), ("excalidraw", [], {}))
        one = json.loads(lzstring.decompress_from_base64(FIXTURES["one_rect_one_text"]))
        self.assertEqual(sorted(e["type"] for e in one["elements"]), ["rectangle", "text"])

    def test_改行や空白が混ざっていても読める(self) -> None:
        data = FIXTURES["empty_drawing"]
        chunked = "\n".join(data[i:i + 256] for i in range(0, len(data), 256))
        self.assertEqual(lzstring.decompress_from_base64(chunked), lzstring.decompress_from_base64(data))

    def test_壊れたデータは例外になる(self) -> None:
        with self.assertRaises(lzstring.LZStringError):
            lzstring.decompress_from_base64("!!!not base64!!!")
        broken = FIXTURES["empty_drawing"][:200]
        try:
            text = lzstring.decompress_from_base64(broken)
        except lzstring.LZStringError:
            return
        with self.assertRaises(ValueError):
            json.loads(text)  # 途中までのデータは、JSON として読めない

    def test_空(self) -> None:
        self.assertEqual(lzstring.decompress_from_base64(""), "")
        self.assertEqual(lzstring.decompress_from_base64("  \n"), "")


class TextTests(unittest.TestCase):
    def test_日本語の折り返しは_読み取りでつながる(self) -> None:
        self.assertEqual(reader.flat("在庫あ\nり？"), "在庫あり？")
        self.assertEqual(reader.flat("題名\n\n説明文"), "題名 / 説明文")
        self.assertEqual(reader.flat("手順\n・一つ目\n・二つ目"), "手順 / ・一つ目 / ・二つ目")
        self.assertEqual(reader.flat("Submit\nRequest"), "Submit / Request")
        self.assertEqual(reader.flat("終わり。\n次の文"), "終わり。 / 次の文")
        self.assertEqual(reader.flat(""), "")


class LoadExcalidrawTests(unittest.TestCase):
    def test_書いたものを読み戻せる(self) -> None:
        _, doc = build(SWIM)
        self.assertEqual(load_excalidraw.load_drawing(core.to_obsidian_markdown(doc), "a.excalidraw.md"), doc)
        loaded = load_excalidraw.load_drawing(core.to_excalidraw_json(doc), "a.excalidraw")
        self.assertEqual(loaded["source"], "https://excalidraw.com")
        self.assertEqual(loaded["elements"], doc["elements"])

    def test_圧縮された図を読める(self) -> None:
        text = f"---\nexcalidraw-plugin: parsed\n---\n# Excalidraw Data\n\n## Text Elements\n%%\n## Drawing\n```compressed-json\n{FIXTURES['one_rect_one_text']}\n```\n%%\n"
        doc = load_excalidraw.load_drawing(text, "x.excalidraw.md")
        self.assertEqual(len(doc["elements"]), 2)

    def test_図ではないファイル_壊れたファイル(self) -> None:
        for text, needle in (("# ただのノート\n", "見つかりません"), ("## Drawing\n```json\n{壊れ\n```\n", "JSON"), ("{\"a\": 1}", "elements")):
            with self.assertRaises(load_excalidraw.ReadError) as ctx:
                load_excalidraw.load_drawing(text, "x.excalidraw.md")
            self.assertIn(needle, str(ctx.exception))
        with self.assertRaises(load_excalidraw.ReadError):
            load_excalidraw.load_drawing("## Drawing\n```compressed-json\n!!!\n```\n", "x.excalidraw.md")


class DescribeTests(unittest.TestCase):
    def describe(self, spec: dict, **kw) -> str:
        _, doc = build(spec)
        return reader.describe_text(reader.analyze(doc, "x"), **kw)

    def test_フロー図を読み戻せる(self) -> None:
        text = self.describe(FLOW)
        for label in ("申請を受け付ける", "内容を確認", "問題なし?", "承認する", "差し戻す", "通知メールを送る", "完了"):
            self.assertIn(label, text)
        self.assertIn("タイトル(推定): 申請フロー", text)
        self.assertRegex(text, r"N\d「問題なし\?」 → N\d「承認する」 \(はい\)")
        self.assertRegex(text, r"N\d「問題なし\?」 → N\d「差し戻す」 \(いいえ\)")
        self.assertRegex(text, r"→ N\d「内容を確認」 \(再提出\)")
        self.assertIn("分岐: ", text)
        self.assertIn("戻り(ループ): ", text)
        self.assertIn("始点: N1 / 終点: ", text)
        self.assertIn("[点線]", text)  # d → f の破線
        self.assertIn("ひし形", text)

    def test_見出しと説明は区切られて読める(self) -> None:
        self.assertIn("「内容を確認 / 必須項目の漏れがないか」", self.describe(FLOW))

    def test_スイムレーンのレーンと所属(self) -> None:
        text = self.describe(SWIM)
        self.assertIn("スイムレーン図 (レーンは横長の行", text)
        for lane in ("申請者", "上長", "経理"):
            self.assertRegex(text, rf"R\d「{lane}」\(レーン\)")
        self.assertRegex(text, r"N\d「内容は妥当\?」 ひし形・\S+ \[レーン: R\d「上長」\]")
        self.assertRegex(text, r"\[レーン: R\d「経理」\]")
        text = self.describe({**SWIM, "direction": "TB"})
        self.assertIn("縦長の列", text)

    def test_ツリーとマインドマップ(self) -> None:
        text = self.describe(TREE)
        self.assertIn("箱 13 個 / 矢印 12 本", text)
        self.assertRegex(text, r"N\d「フロントエンド」 — N\d「ログイン画面」")  # 矢じりの無い線
        text = self.describe(MIND)
        self.assertIn("箱 16 個", text)
        self.assertIn("図解の方針", text)

    def test_Mermaid(self) -> None:
        _, doc = build(SWIM)
        mermaid = reader.describe_mermaid(reader.analyze(doc, "x"))
        self.assertTrue(mermaid.startswith("flowchart "))
        self.assertIn('subgraph R', mermaid)
        self.assertRegex(mermaid, r'N\d\{"内容は妥当\?"\}')
        self.assertRegex(mermaid, r'N\d \|?-->\|はい\| N\d|N\d -->\|はい\| N\d')
        self.assertEqual(mermaid.count("-->") + mermaid.count("-.->"), 8)
        _, doc = build(FLOW)
        self.assertIn("-.->", reader.describe_mermaid(reader.analyze(doc, "x")))
        _, doc = build(TREE)
        self.assertIn("---", reader.describe_mermaid(reader.analyze(doc, "x")))

    def test_行数の上限(self) -> None:
        nodes = [{"id": f"n{i}", "label": f"ノード{i}"} for i in range(60)]
        text = self.describe({"type": "flow", "nodes": nodes, "edges": [{"from": f"n{i}", "to": f"n{i + 1}"} for i in range(59)]}, max_lines=40)
        self.assertIn("あと", text.splitlines()[-1])
        self.assertLessEqual(len(text.splitlines()), 41)

    def test_色の名前(self) -> None:
        for hex_color, name in (("#a5d8ff", "青"), ("#b2f2bb", "緑"), ("#ffc9c9", "ピンク"), ("#fff3bf", "黄"), ("#e9ecef", "灰"), ("#ffffff", "白"),
                                ("#1e1e1e", "黒"), ("#ffd8a8", "橙"), ("#d0bfff", "紫"), ("#c3fae8", "緑"), ("transparent", "塗りなし")):
            self.assertEqual(reader.color_name(hex_color), name, hex_color)


def raw_rect(eid: str, x: float, y: float, w: float, h: float, **kw) -> dict:
    return {"id": eid, "type": kw.pop("type", "rectangle"), "x": x, "y": y, "width": w, "height": h, "backgroundColor": kw.pop("fill", "#a5d8ff"), "strokeColor": "#1e1e1e", **kw}


def raw_text(eid: str, text: str, x: float, y: float, w: float = 80, h: float = 24, **kw) -> dict:
    return {"id": eid, "type": "text", "x": x, "y": y, "width": w, "height": h, "text": text, "fontSize": kw.pop("fontSize", 18), **kw}


def raw_arrow(eid: str, x: float, y: float, pts: list[list[float]], **kw) -> dict:
    return {"id": eid, "type": "arrow", "x": x, "y": y, "width": 1, "height": 1, "points": pts, "endArrowhead": kw.pop("end", "arrow"), "startArrowhead": kw.pop("start", None), **kw}


class HandDrawnTests(unittest.TestCase):
    """手で描いた図 (文字が箱に結び付いていない、矢印が結び付いていない) も読める。"""

    def doc(self, *elements: dict) -> dict:
        return {"type": "excalidraw", "version": 2, "elements": list(elements), "appState": {}, "files": {}}

    def test_結び付いていない文字と矢印を_位置から割り当てる(self) -> None:
        doc = self.doc(raw_rect("r1", 0, 0, 200, 80), raw_text("t1", "開始", 70, 30), raw_rect("r2", 0, 200, 200, 80), raw_text("t2", "終了", 70, 230),
                       raw_arrow("a1", 100, 82, [[0, 0], [0, 116]]), raw_text("l1", "完了後", 110, 130))
        text = reader.describe_text(reader.analyze(doc, "x"))
        self.assertRegex(text, r"N1「開始」 .* 位置: 1段目")
        self.assertRegex(text, r"N1「開始」 → N2「終了」 \(完了後\)")

    def test_矢印の向き(self) -> None:
        base = [raw_rect("r1", 0, 0, 100, 60), raw_text("t1", "甲", 40, 20), raw_rect("r2", 300, 0, 100, 60), raw_text("t2", "乙", 340, 20)]
        for kw, expected in (({}, "→"), ({"start": "arrow", "end": None}, "→"), ({"start": "arrow"}, "↔"), ({"end": None}, "—")):
            doc = self.doc(*base, raw_arrow("a", 100, 30, [[0, 0], [200, 0]], **kw))
            text = reader.describe_text(reader.analyze(doc, "x"))
            self.assertIn(expected, text.split("つながり")[1].splitlines()[1], kw)
        # 始点側にだけ矢じりがあるときは、向きを入れ替えて読む
        doc = self.doc(*base, raw_arrow("a", 100, 30, [[0, 0], [200, 0]], start="arrow", end=None))
        self.assertRegex(reader.describe_text(reader.analyze(doc, "x")), r"N2「乙」 → N1「甲」")

    def test_枠(self) -> None:
        doc = self.doc(raw_rect("g", 0, 0, 500, 300, fill="transparent"), raw_text("gt", "Before", 10, 10), raw_rect("a", 30, 60, 150, 60), raw_text("at", "箱A", 60, 80),
                       raw_rect("b", 250, 60, 150, 60), raw_text("bt", "箱B", 280, 80))
        text = reader.describe_text(reader.analyze(doc, "x"))
        self.assertIn("R1「Before」: 含む箱 N1, N2", text)
        self.assertIn("[領域: R1「Before」]", text)

    def test_箱の内側の装飾線は数えない(self) -> None:
        doc = self.doc(raw_rect("r1", 0, 0, 200, 80), raw_text("t1", "処理", 70, 30), {"id": "ln", "type": "line", "x": 20, "y": 0, "width": 0, "height": 80, "points": [[0, 0], [0, 80]]})
        text = reader.describe_text(reader.analyze(doc, "x"))
        self.assertNotIn("つながっていない", text)

    def test_箱につながらない矢印は_注意に出る(self) -> None:
        doc = self.doc(raw_rect("r1", 0, 0, 100, 60), raw_text("t1", "孤立", 20, 20), raw_arrow("a", 500, 500, [[0, 0], [100, 0]]))
        self.assertIn("箱につながっていない矢印・線が 1 本", reader.describe_text(reader.analyze(doc, "x")))

    def test_削除された要素は読まない(self) -> None:
        doc = self.doc(raw_rect("r1", 0, 0, 100, 60), raw_text("t1", "残る", 20, 20), raw_rect("r2", 200, 0, 100, 60, isDeleted=True), raw_text("t2", "消えた", 220, 20, isDeleted=True))
        text = reader.describe_text(reader.analyze(doc, "x"))
        self.assertIn("残る", text)
        self.assertNotIn("消えた", text)

    def test_空の図_画像_手書き(self) -> None:
        dg = reader.analyze(self.doc(), "x")
        self.assertEqual((len(dg.items), dg.links), (0, []))
        doc = self.doc({"id": "i", "type": "image", "x": 0, "y": 0, "width": 100, "height": 100}, {"id": "f", "type": "freedraw", "x": 0, "y": 0, "width": 10, "height": 10})
        text = reader.describe_text(reader.analyze(doc, "x"))
        self.assertIn("画像 1 個 (中身は読めません)", text)
        self.assertIn("手書き 1 個", text)

    def test_外枠の中に並ぶレーン(self) -> None:
        # 実際の図に多い形: 全体を囲む外枠 (プール) の中に、レーンが縦に並ぶ
        doc = self.doc(raw_rect("pool", 0, 0, 700, 420, fill="transparent"), raw_text("pt", "経費精算フロー", 10, 5),
                       raw_rect("l1", 0, 40, 700, 180, fill="transparent"), raw_text("lt1", "申請者", 10, 50),
                       raw_rect("l2", 0, 220, 700, 180, fill="transparent"), raw_text("lt2", "経理", 10, 230),
                       raw_rect("n1", 150, 100, 120, 60), raw_text("nt1", "入力", 180, 120), raw_rect("n2", 400, 280, 120, 60), raw_text("nt2", "支払", 430, 300),
                       raw_arrow("a", 270, 130, [[0, 0], [130, 0], [130, 180]]))
        text = reader.describe_text(reader.analyze(doc, "x"))
        self.assertIn("スイムレーン図", text)
        self.assertRegex(text, r"R\d「申請者」\(レーン\)")
        self.assertRegex(text, r"R\d「経理」\(レーン\)")
        self.assertRegex(text, r"R\d「経費精算フロー」: 含む領域 R\d, R\d")
        self.assertIn("[レーン: ", text)

    def test_レーンの見出しの箱(self) -> None:
        # レーン本体 (大きな枠) の左に、見出しの箱が接している図
        doc = self.doc(raw_rect("h1", 0, 0, 100, 200, fill="#a5d8ff"), raw_text("ht1", "顧客", 30, 90), raw_rect("l1", 100, 0, 600, 200, fill="transparent"),
                       raw_rect("h2", 0, 200, 100, 200, fill="#b2f2bb"), raw_text("ht2", "営業", 30, 290), raw_rect("l2", 100, 200, 600, 200, fill="transparent"),
                       raw_rect("n1", 150, 60, 120, 60), raw_text("nt1", "注文", 170, 80), raw_rect("n2", 350, 260, 120, 60), raw_text("nt2", "受注", 370, 280),
                       raw_arrow("a", 270, 90, [[0, 0], [80, 0], [80, 200]]))
        dg = reader.analyze(doc, "x")
        text = reader.describe_text(dg)
        self.assertIn("箱 2 個", text)  # 見出しの箱は、箱として数えない
        self.assertRegex(text, r"R\d「顧客」\(レーン\)")
        self.assertRegex(text, r"R\d「営業」\(レーン\)")


class ReaderCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name).resolve()
        self.vault = self.tmp / "vault"
        (self.vault / ".obsidian").mkdir(parents=True)
        (self.vault / ".handoff").mkdir()
        (self.vault / "notes").mkdir()

    def run_cli(self, *argv: str, cwd: Path | None = None) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        code = cli.main(list(argv), stdout=out, stderr=err, cwd=cwd or self.vault)
        return code, out.getvalue(), err.getvalue()

    def make_diagram(self, rel: str, spec: dict | None = None) -> Path:
        path = self.vault / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(core.to_obsidian_markdown(build(spec or FLOW)[1]), encoding="utf-8")
        return path

    def test_AIが作った未レビューの図は知らせる(self) -> None:
        # 0.12.0: ハーネスが作った図 (ai_generated) は、人が ai_reviewed を ✓ にするまで、根拠にしない
        path = self.make_diagram("notes/AIの図.excalidraw.md")
        _, out, _ = self.run_cli("describe", "notes/AIの図.excalidraw.md")
        self.assertIn("注意: AI が作った図です (未レビュー)", out.splitlines()[0])
        path.write_text(path.read_text(encoding="utf-8").replace("ai_reviewed: false", "ai_reviewed: true"), encoding="utf-8")
        _, out, _ = self.run_cli("describe", "notes/AIの図.excalidraw.md")
        self.assertNotIn("未レビュー", out)
        text = path.read_text(encoding="utf-8").replace("ai_generated: opencode\nai_reviewed: true\n", "")
        path.write_text(text, encoding="utf-8")
        _, out, _ = self.run_cli("describe", "notes/AIの図.excalidraw.md")
        self.assertNotIn("AI が作った", out)  # 人が描いた図 (印なし) は、これまでどおり

    def test_描画できる図を読む(self) -> None:
        self.make_diagram("notes/申請.excalidraw.md")
        code, out, err = self.run_cli("describe", "notes/申請.excalidraw.md")
        self.assertEqual((code, err), (0, ""))
        self.assertIn("分岐: ", out)
        self.assertIn("図の読み取り: 申請.excalidraw.md", out)

    def test_読める場所の制限(self) -> None:
        outside = self.tmp / "outside.excalidraw.md"
        outside.write_text(core.to_obsidian_markdown(build(FLOW)[1]), encoding="utf-8")
        code, _, err = self.run_cli("describe", str(outside))
        self.assertEqual(code, 2)
        self.assertIn("読んでよい場所の外", err)
        code, _, err = self.run_cli("describe", "../outside.excalidraw.md")
        self.assertEqual(code, 2)

    def test_シンボリックリンクで外を読めない(self) -> None:
        outside = self.tmp / "outside.excalidraw.md"
        outside.write_text(core.to_obsidian_markdown(build(FLOW)[1]), encoding="utf-8")
        try:
            os.symlink(outside, self.vault / "notes" / "link.excalidraw.md")
        except (OSError, NotImplementedError):
            self.skipTest("シンボリックリンクを作れない環境")
        self.assertEqual(self.run_cli("describe", "notes/link.excalidraw.md")[0], 2)

    def test_形式の判断(self) -> None:
        self.assertEqual(cli.detect_format("a.excalidraw.md", ""), "excalidraw")
        self.assertEqual(cli.detect_format("a.drawio.svg", ""), "drawio")
        self.assertEqual(cli.detect_format("a.dio", ""), "drawio")
        self.assertEqual(cli.detect_format("a.md", "## Drawing\n```json\n{}\n```"), "excalidraw")
        self.assertEqual(cli.detect_format("a.xml", "<mxfile></mxfile>"), "drawio")
        self.assertEqual(cli.detect_format("a.svg", '<svg content="x"></svg>'), "drawio")
        with self.assertRaises(cli.CliError):
            cli.detect_format("a.png", "x")

    def test_図ではないファイル(self) -> None:
        (self.vault / "notes" / "memo.md").write_text("# ただのメモ\n", encoding="utf-8")
        code, _, err = self.run_cli("describe", "notes/memo.md")
        self.assertEqual(code, 2)
        self.assertIn("対応していない形式", err)

    def test_圧縮された図を_describeで読む(self) -> None:
        text = f"---\nexcalidraw-plugin: parsed\n---\n\n## Drawing\n```compressed-json\n{FIXTURES['one_rect_one_text']}\n```\n%%\n"
        (self.vault / "notes" / "c.excalidraw.md").write_text(text, encoding="utf-8")
        code, out, _ = self.run_cli("describe", "notes/c.excalidraw.md")
        self.assertEqual(code, 0)
        self.assertIn("箱 1 個", out)

    def test_describeの出力先は_handoffだけ(self) -> None:
        self.make_diagram("notes/a.excalidraw.md")
        code, out, _ = self.run_cli("describe", "notes/a.excalidraw.md", "--out", ".handoff/説明.md")
        self.assertEqual(code, 0)
        self.assertIn("出力: .handoff/説明.md", out)
        self.assertIn("申請を受け付ける", (self.vault / ".handoff" / "説明.md").read_text(encoding="utf-8"))
        self.assertEqual(self.run_cli("describe", "notes/a.excalidraw.md", "--out", ".handoff/説明.md")[0], 2)  # あるものは上書きしない
        self.assertEqual(self.run_cli("describe", "notes/a.excalidraw.md", "--out", "notes/説明.md")[0], 2)
        # パスに .handoff を含んでいても、解いた後に外へ出る指定は拒否 (以前は通っていた)
        for escape in (".handoff/../../escaped.md", ".handoff/../notes/escaped.md", str(self.vault / "notes" / ".handoff" / "x.md"), ".handoff/.."):
            self.assertEqual(self.run_cli("describe", "notes/a.excalidraw.md", "--out", escape)[0], 2, escape)
        self.assertFalse((self.vault.parent / "escaped.md").exists())
        self.assertFalse((self.vault / "notes" / "escaped.md").exists())

    def test_mermaid形式(self) -> None:
        self.make_diagram("notes/a.excalidraw.md")
        code, out, _ = self.run_cli("describe", "notes/a.excalidraw.md", "--format", "mermaid")
        self.assertEqual(code, 0)
        lines = out.splitlines()
        self.assertTrue(lines[0].startswith("%% 注意: AI が作った図です"), "AI の図の注意は、Mermaid の注釈の行")
        self.assertTrue(lines[1].startswith("flowchart TD"))

    def test_linksはノートに埋め込まれた図を探す(self) -> None:
        self.make_diagram("notes/申請.excalidraw.md")
        (self.vault / "notes" / "報告.md").write_text("# 報告\n\n![[申請.excalidraw]]\n\n[[別のノート]] と ![[ない図.excalidraw|300]]\n", encoding="utf-8")
        code, out, _ = self.run_cli("links", "notes/報告.md")
        self.assertEqual(code, 0)
        self.assertIn("図 2 個", out)
        self.assertIn("申請.excalidraw.md: notes/申請.excalidraw.md", out)
        self.assertIn("ない図.excalidraw.md: Vault の中に見つかりません", out)
        (self.vault / "notes" / "なし.md").write_text("図はない\n", encoding="utf-8")
        self.assertIn("ありません", self.run_cli("links", "notes/なし.md")[1])


# ---------------------------------------------------------------------------
# draw.io
# ---------------------------------------------------------------------------

def vertex(cid: str, label: str, x: float, y: float, w: float = 120, h: float = 60, style: str = "rounded=1;whiteSpace=wrap;html=1;", parent: str = "1") -> str:
    return f'<mxCell id="{cid}" value="{html.escape(label, quote=True)}" style="{style}" vertex="1" parent="{parent}"><mxGeometry x="{x}" y="{y}" width="{w}" height="{h}" as="geometry"/></mxCell>'


def edge(cid: str, src: str, dst: str, label: str = "", style: str = "edgeStyle=orthogonalEdgeStyle;html=1;", parent: str = "1", points: str = "") -> str:
    geo = f'<mxGeometry relative="1" as="geometry">{points}</mxGeometry>'
    return f'<mxCell id="{cid}" value="{html.escape(label, quote=True)}" style="{style}" edge="1" parent="{parent}" source="{src}" target="{dst}">{geo}</mxCell>'


def model(cells: str) -> str:
    return f'<mxGraphModel dx="800" dy="600"><root><mxCell id="0"/><mxCell id="1" parent="0"/>{cells}</root></mxGraphModel>'


def compress(xml: str) -> str:
    comp = zlib.compressobj(9, zlib.DEFLATED, -15)
    data = comp.compress(urllib.parse.quote(xml, safe="~()*!.'").encode("utf-8")) + comp.flush()
    return base64.b64encode(data).decode("ascii")


def mxfile(*pages: tuple[str, str], compressed: bool = False) -> str:
    body = "".join(f'<diagram id="p{i}" name="{html.escape(name)}">{compress(xml) if compressed else xml}</diagram>' for i, (name, xml) in enumerate(pages, 1))
    return f'<mxfile host="test">{body}</mxfile>'


def svg(mx: str) -> str:
    return f'<?xml version="1.0" encoding="UTF-8"?>\n<svg xmlns="http://www.w3.org/2000/svg" version="1.1" width="10px" height="10px" content="{html.escape(mx, quote=True)}"><g/></svg>'


FLOW_CELLS = (
    vertex("a", "開始", 40, 40, style="ellipse;whiteSpace=wrap;html=1;")
    + vertex("b", "処理<br>する", 40, 160, style="rounded=1;whiteSpace=wrap;html=1;fillColor=#dae8fc;strokeColor=#6c8ebf;")
    + vertex("c", "判断", 40, 280, style="rhombus;whiteSpace=wrap;html=1;")
    + vertex("d", "完了", 40, 420, style="ellipse;whiteSpace=wrap;html=1;")
    + vertex("e", "やり直し", 260, 290, style="rounded=0;whiteSpace=wrap;html=1;dashed=1;")
    + edge("e1", "a", "b") + edge("e2", "b", "c") + edge("e3", "c", "d", "はい") + edge("e4", "c", "e", "いいえ", style="edgeStyle=orthogonalEdgeStyle;dashed=1;html=1;")
    + edge("e5", "e", "b", "再提出", points='<Array as="points"><mxPoint x="320" y="190"/></Array>')
)


class DrawioTests(unittest.TestCase):
    def describe(self, text: str, name: str = "x.drawio", **kw) -> str:
        out = []
        for page, doc in load_drawio.load_pages(text, name):
            out.append(reader.describe_text(reader.analyze(doc, name + (f" [{page}]" if len(load_drawio.load_pages(text, name)) > 1 else "")), **kw))
        return "\n".join(out)

    def test_フロー図を読める(self) -> None:
        text = self.describe(mxfile(("P", model(FLOW_CELLS))))
        for label in ("開始", "処理する", "判断", "完了", "やり直し"):
            self.assertIn(label, text)
        self.assertRegex(text, r"N\d「判断」 ひし形・\S+")
        self.assertRegex(text, r"N\d「開始」 楕円")
        self.assertRegex(text, r"N\d「判断」 → N\d「完了」 \(はい\)")
        self.assertRegex(text, r"N\d「判断」 → N\d「やり直し」 \(いいえ\) \[点線\]")
        self.assertRegex(text, r"N\d「やり直し」 → N\d「処理する」 \(再提出\)")
        self.assertIn("戻り(ループ): ", text)
        self.assertIn("青: ", text)  # fillColor=#dae8fc

    def test_圧縮_SVG_プレーン_どれでも同じ結果(self) -> None:
        plain = mxfile(("P", model(FLOW_CELLS)))
        results = {
            "plain": self.describe(plain, "a.drawio"),
            "compressed": self.describe(mxfile(("P", model(FLOW_CELLS)), compressed=True), "a.drawio"),
            "svg": self.describe(svg(plain), "a.drawio.svg"),
            "svg圧縮": self.describe(svg(mxfile(("P", model(FLOW_CELLS)), compressed=True)), "a.drawio.svg"),
            "dio": self.describe(plain, "a.dio"),
        }
        bodies = {k: v.split("\n", 1)[1] for k, v in results.items()}  # 1 行目は、ファイル名
        self.assertEqual(len(set(bodies.values())), 1, list(bodies))

    def test_先頭にXMLコメントがあっても読める(self) -> None:
        text = "<!--\n日本語の説明\n-->\n<?xml version=\"1.0\"?>\n" + mxfile(("P", model(FLOW_CELLS)))
        self.assertIn("処理する", self.describe(text.replace('<?xml version="1.0"?>\n', "")))

    def test_mxGraphModelだけのファイル(self) -> None:
        self.assertIn("開始", self.describe(model(FLOW_CELLS)))

    def test_複数ページ(self) -> None:
        text = mxfile(("概要", model(vertex("a", "一枚目", 0, 0))), ("詳細", model(vertex("b", "二枚目", 0, 0))), compressed=True)
        pages = load_drawio.load_pages(text, "x.drawio")
        self.assertEqual([p[0] for p in pages], ["概要", "詳細"])
        self.assertIn("一枚目", reader.describe_text(reader.analyze(pages[0][1], "x")))
        self.assertNotIn("一枚目", reader.describe_text(reader.analyze(pages[1][1], "x")))

    def test_swimlane_子の座標は親の左上が基準(self) -> None:
        cells = (vertex("pool", "経費精算", 40, 40, 800, 400, style="swimlane;horizontal=0;startSize=20;html=1;")
                 + vertex("l1", "申請者", 20, 0, 780, 200, style="swimlane;horizontal=0;startSize=20;html=1;", parent="pool")
                 + vertex("l2", "経理", 20, 200, 780, 200, style="swimlane;horizontal=0;startSize=20;html=1;", parent="pool")
                 + vertex("n1", "入力", 100, 70, parent="l1") + vertex("n2", "支払", 300, 70, parent="l2")
                 + edge("e", "n1", "n2", parent="pool"))
        doc = load_drawio.load_pages(mxfile(("P", model(cells))), "x.drawio")[0][1]
        by_id = {e["id"]: e for e in doc["elements"]}
        self.assertEqual((by_id["n1"]["x"], by_id["n1"]["y"]), (40 + 20 + 100, 40 + 0 + 70))  # pool(40,40) + l1(20,0) + n1(100,70)
        self.assertEqual((by_id["n2"]["x"], by_id["n2"]["y"]), (40 + 20 + 300, 40 + 200 + 70))
        text = reader.describe_text(reader.analyze(doc, "x"))
        self.assertIn("スイムレーン図 (レーンは横長の行", text)
        self.assertRegex(text, r"R\d「申請者」\(レーン\)")
        self.assertRegex(text, r"N\d「入力」 .* \[レーン: R\d「申請者」\]")
        self.assertRegex(text, r"N\d「支払」 .* \[レーン: R\d「経理」\]")
        self.assertRegex(text, r"N\d「入力」 → N\d「支払」")

    def test_HTMLのラベル(self) -> None:
        self.assertEqual(load_drawio.clean_label("修正して<br>再申請"), "修正して\n再申請")
        self.assertEqual(load_drawio.clean_label("<div>一行目</div><div>二行目</div>"), "一行目\n\n二行目")
        self.assertEqual(load_drawio.clean_label("<b>太字</b> &amp; <i>斜体</i>&nbsp;です"), "太字 & 斜体 です")
        self.assertEqual(load_drawio.clean_label("A &lt; B"), "A < B")
        self.assertEqual(load_drawio.clean_label(""), "")
        text = self.describe(mxfile(("P", model(vertex("a", "<div>一行目</div><div>二行目</div>", 0, 0)))))
        self.assertIn("「一行目 / 二行目」", text)

    def test_矢印のラベルは_矢印の子の箱からも読む(self) -> None:
        cells = vertex("a", "甲", 0, 0) + vertex("b", "乙", 300, 0) + edge("e", "a", "b") \
            + '<mxCell id="lab" value="条件A" style="edgeLabel;html=1;" vertex="1" connectable="0" parent="e"><mxGeometry x="-0.1" relative="1" as="geometry"/></mxCell>'
        text = self.describe(mxfile(("P", model(cells))))
        self.assertRegex(text, r"N\d「甲」 → N\d「乙」 \(条件A\)")
        self.assertIn("箱 2 個", text)  # ラベル用の箱は、箱として数えない

    def test_矢じりの種類(self) -> None:
        cells = vertex("a", "甲", 0, 0) + vertex("b", "乙", 300, 0)
        cases = {"endArrow=none;html=1;": "—", "html=1;": "→", "startArrow=classic;endArrow=classic;html=1;": "↔", "startArrow=classic;endArrow=none;html=1;": "→"}
        for style, expected in cases.items():
            text = self.describe(mxfile(("P", model(cells + edge("e", "a", "b", style=style)))))
            line = text.split("つながり")[1].splitlines()[1]
            self.assertIn(expected, line, style)
        text = self.describe(mxfile(("P", model(cells + edge("e", "a", "b", style="startArrow=classic;endArrow=none;html=1;")))))
        self.assertRegex(text, r"N\d「乙」 → N\d「甲」")  # 始点側にだけ矢じり: 向きを入れ替える

    def test_形(self) -> None:
        cells = (vertex("a", "判断1", 0, 0, style="rhombus;html=1;") + vertex("b", "判断2", 200, 0, style="shape=mxgraph.flowchart.decision;html=1;")
                 + vertex("c", "開始", 0, 100, style="shape=mxgraph.flowchart.terminator;html=1;") + vertex("d", "DB", 200, 100, style="shape=cylinder3;html=1;")
                 + vertex("e", "書類", 0, 200, style="shape=mxgraph.flowchart.document;html=1;") + vertex("f", "角丸", 200, 200) + vertex("g", "角", 400, 200, style="rounded=0;html=1;"))
        text = self.describe(mxfile(("P", model(cells))))
        self.assertEqual(text.count("ひし形"), 2)
        self.assertRegex(text, r"N\d「開始」 楕円")
        self.assertRegex(text, r"N\d「DB」 円柱")
        self.assertRegex(text, r"N\d「書類」 書類")
        self.assertRegex(text, r"N\d「角丸」 長方形\(角丸\)")

    def test_文字だけのセルと画像(self) -> None:
        cells = (vertex("a", "箱", 0, 0) + vertex("t", "注記です", 0, 200, 200, 30, style="text;html=1;strokeColor=none;fillColor=none;align=left;")
                 + '<mxCell id="img" value="" style="shape=image;image=data:image/png,AAAA;" vertex="1" parent="1"><mxGeometry x="300" y="0" width="50" height="50" as="geometry"/></mxCell>')
        text = self.describe(mxfile(("P", model(cells))))
        self.assertIn("箱 1 個", text)
        self.assertIn("「注記です」", text)
        self.assertIn("画像 1 個 (中身は読めません)", text)

    def test_objectとUserObjectのラベル(self) -> None:
        cells = ('<object label="属性つき" id="o1"><mxCell style="rounded=1;html=1;" vertex="1" parent="1"><mxGeometry x="0" y="0" width="100" height="50" as="geometry"/></mxCell></object>'
                 '<UserObject label="もう一つ" id="o2"><mxCell style="rounded=1;html=1;" vertex="1" parent="1"><mxGeometry x="300" y="0" width="100" height="50" as="geometry"/></mxCell></UserObject>')
        text = self.describe(mxfile(("P", model(cells + edge("e", "o1", "o2")))))
        self.assertRegex(text, r"N\d「属性つき」 → N\d「もう一つ」")

    def test_つながっていない矢印は_点と近さで割り当てる(self) -> None:
        cells = vertex("a", "甲", 0, 0, 100, 60) + vertex("b", "乙", 300, 0, 100, 60) + (
            '<mxCell id="f" style="html=1;" edge="1" parent="1"><mxGeometry relative="1" as="geometry">'
            '<mxPoint x="102" y="30" as="sourcePoint"/><mxPoint x="298" y="30" as="targetPoint"/></mxGeometry></mxCell>')
        self.assertRegex(self.describe(mxfile(("P", model(cells)))), r"N\d「甲」 → N\d「乙」")

    def test_非表示と空(self) -> None:
        cells = vertex("a", "見える", 0, 0) + '<mxCell id="h" value="隠れる" style="html=1;" vertex="1" visible="0" parent="1"><mxGeometry x="200" y="0" width="100" height="50" as="geometry"/></mxCell>'
        text = self.describe(mxfile(("P", model(cells))))
        self.assertIn("見える", text)
        self.assertNotIn("隠れる", text)
        self.assertIn("箱 0 個", self.describe(mxfile(("P", model("")))))

    def test_壊れたデータ_不正なデータ(self) -> None:
        for text, name, needle in (("hello", "a.drawio", "見つかりません"), ('<svg xmlns="http://www.w3.org/2000/svg"></svg>', "a.drawio.svg", "content"),
                                    ("<mxfile><diagram>!!!壊れ!!!</diagram></mxfile>", "a.drawio", ""), ("<mxfile/>", "a.drawio", "ページ"),
                                    ("<other/>", "a.drawio", "")):
            with self.assertRaises(load_drawio.DrawioError, msg=text):
                load_drawio.load_pages(text, name)

    def test_DTDと実体を拒否する(self) -> None:
        evil = '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "boom">]>' + mxfile(("P", model('<mxCell id="v" value="&a;" style="" vertex="1" parent="1"><mxGeometry width="10" height="10" as="geometry"/></mxCell>')))
        with self.assertRaises(load_drawio.DrawioError) as ctx:
            load_drawio.load_pages(evil, "a.drawio")
        self.assertIn("DTD", str(ctx.exception))
        with self.assertRaises(load_drawio.DrawioError):
            load_drawio.load_pages(svg(evil), "a.drawio.svg")

    def test_展開すると大きすぎる図とセルの数の上限(self) -> None:
        big = mxfile(("P", model(vertex("a", "あ" * 5000, 0, 0))), compressed=True)
        with patch.object(load_drawio, "MAX_INFLATED", 1000):
            with self.assertRaises(load_drawio.DrawioError) as ctx:
                load_drawio.load_pages(big, "a.drawio")
        self.assertIn("大きすぎ", str(ctx.exception))
        cells = "".join(vertex(f"v{i}", f"箱{i}", i * 10, 0) for i in range(20))
        with patch.object(load_drawio, "MAX_CELLS", 10):
            with self.assertRaises(load_drawio.DrawioError):
                load_drawio.load_pages(mxfile(("P", model(cells))), "a.drawio")

    def test_展開は途中で止める_全部を展開してから調べない(self) -> None:
        # zip 爆弾の対策: 展開する量の上限を、展開の呼び出しに渡している (展開し終わってからの検査では、メモリを使い切る)
        limits: list[int] = []
        real = zlib.decompressobj

        class Spy:
            def __init__(self, wbits: int) -> None:
                self.inner = real(wbits)

            def decompress(self, data: bytes, max_length: int = 0) -> bytes:
                limits.append(max_length)
                return self.inner.decompress(data, max_length)

        with patch.object(load_drawio.zlib, "decompressobj", Spy):
            load_drawio.load_pages(mxfile(("P", model(vertex("a", "x", 0, 0))), compressed=True), "a.drawio")
        self.assertEqual(limits, [load_drawio.MAX_INFLATED + 1])

    def test_圧縮されていないだけの_URLエンコードも読める(self) -> None:
        encoded = base64.b64encode(urllib.parse.quote(model(vertex("a", "URLだけ", 0, 0)), safe="").encode()).decode()
        self.assertIn("URLだけ", self.describe(f"<mxfile><diagram name='P'>{encoded}</diagram></mxfile>"))

    def test_Mermaidにできる(self) -> None:
        _, doc = load_drawio.load_pages(mxfile(("P", model(FLOW_CELLS))), "x.drawio")[0]
        mermaid = reader.describe_mermaid(reader.analyze(doc, "x"))
        self.assertTrue(mermaid.startswith("flowchart "))
        self.assertRegex(mermaid, r'N\d\{"判断"\}')
        self.assertIn("-.->", mermaid)  # いいえ (点線)
        self.assertRegex(mermaid, r"-->\|はい\|")


class DrawioCliTests(ReaderCliTests):
    """ReaderCliTests の準備を使って、draw.io のファイルをコマンドで読む。"""

    def put(self, rel: str, text: str) -> str:
        path = self.vault / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return rel

    def test_drawio_svgとdrawioを読む(self) -> None:
        plain = mxfile(("P", model(FLOW_CELLS)), compressed=True)
        for rel, text in (("notes/a.drawio.svg", svg(plain)), ("notes/a.drawio", plain), ("notes/a.dio.svg", svg(plain))):
            code, out, err = self.run_cli("describe", self.put(rel, text))
            self.assertEqual((code, err), (0, ""), rel)
            self.assertIn("(draw.io)", out)
            self.assertIn("分岐: ", out)

    def test_複数ページは_ページごとに読む_指定もできる(self) -> None:
        text = mxfile(("概要", model(vertex("a", "一枚目", 0, 0))), ("詳細", model(vertex("b", "二枚目", 0, 0))))
        self.put("notes/m.drawio", text)
        code, out, _ = self.run_cli("describe", "notes/m.drawio")
        self.assertIn("ページ 1/2「概要」", out)
        self.assertIn("ページ 2/2「詳細」", out)
        _, out, _ = self.run_cli("describe", "notes/m.drawio", "--page", "2")
        self.assertIn("二枚目", out)
        self.assertNotIn("一枚目", out)
        _, out, _ = self.run_cli("describe", "notes/m.drawio", "--page", "概要")
        self.assertIn("一枚目", out)
        code, _, err = self.run_cli("describe", "notes/m.drawio", "--page", "9")
        self.assertEqual(code, 2)
        self.assertIn("あるページ", err)

    def test_壊れたdrawioは分かるメッセージ(self) -> None:
        self.put("notes/bad.drawio", "壊れている")
        code, _, err = self.run_cli("describe", "notes/bad.drawio")
        self.assertEqual(code, 2)
        self.assertIn("mxfile", err)
        self.put("notes/plain.svg", '<svg xmlns="http://www.w3.org/2000/svg"><rect/></svg>')
        self.assertEqual(self.run_cli("describe", "notes/plain.svg")[0], 2)

    def test_linksはdrawioの埋め込みも探す(self) -> None:
        self.put("notes/図.drawio.svg", svg(mxfile(("P", model(FLOW_CELLS)))))
        self.make_diagram("notes/別.excalidraw.md")
        self.put("notes/報告.md", "![[図.drawio.svg]]\n![[別.excalidraw]]\n![[写真.png]]\n[[ただのノート]]\n")
        code, out, _ = self.run_cli("links", "notes/報告.md")
        self.assertEqual(code, 0)
        self.assertIn("図 2 個", out)
        self.assertIn("図.drawio.svg: notes/図.drawio.svg", out)
        self.assertIn("別.excalidraw.md: notes/別.excalidraw.md", out)
        self.assertNotIn("写真", out)

    def test_mermaid形式(self) -> None:
        self.put("notes/a.drawio", mxfile(("P", model(FLOW_CELLS))))
        code, out, _ = self.run_cli("describe", "notes/a.drawio", "--format", "mermaid")
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith("flowchart "))


class SafetyTests(unittest.TestCase):
    def test_ソースに削除_実行_通信が無く_書き込みが限られる(self) -> None:
        banned = (".unlink(", "os.remove", "rmtree", "rmdir", "shutil", "subprocess", "os.system", "urlopen", "socket", "eval(", "exec(", "rename(", "os.replace", "extractall")
        for name in ("diagram_model.py", "load_excalidraw.py", "load_drawio.py", "lzstring.py", "diagram_cli.py"):
            source = (SKILL_DIR / "scripts" / name).read_text(encoding="utf-8")
            for needle in banned:
                self.assertNotIn(needle, source, f"{name} に {needle} があります")
        for name in ("diagram_model.py", "load_excalidraw.py", "load_drawio.py", "lzstring.py"):
            source = (SKILL_DIR / "scripts" / name).read_text(encoding="utf-8")
            self.assertNotIn(".write_text(", source, name)
            self.assertNotIn("open(", source, name)
        cli_source = (SKILL_DIR / "scripts" / "diagram_cli.py").read_text(encoding="utf-8")
        self.assertEqual(cli_source.count(".write_text("), 1, "書き込みは describe --out (.handoff だけ) の 1 か所だけ")

    def test_XMLの解析にはDTDの拒否がある(self) -> None:
        self.assertIn("doctype|entity", (SKILL_DIR / "scripts" / "load_drawio.py").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
