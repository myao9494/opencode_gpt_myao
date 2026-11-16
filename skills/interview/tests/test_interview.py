"""interview_cli のテスト。実行: python -m unittest discover -s skills/interview/tests -v   (リポジトリのルートから)"""
from __future__ import annotations

import io
import sys
import tempfile
import unittest
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_DIR / "scripts"))

import interview_cli as cli  # noqa: E402

DONE = """# インタビュー: 締付の手順書

## 目的(なぜやるか。ユーザーの言葉で)
- 新人が一人で締付をできるようにしたい

## 成功の条件(何ができたら成功か)
- 責任者がレビューして承認できる

## やらないこと・失敗の条件
- 規格の解説は書かない

## 決まったこと(決めた人は、必ずユーザー)
- D1: 対象は M16 と M22(決めた人: ユーザー、第 1 回)
- D2: 印刷して使う(決めた人: ユーザー。AI の提案を了承、第 1 回)

## 未決(次の回で聞く)

## 仮定(ユーザーに確かめていないこと)
- A1: 作業標準は WS-12 が最新(ユーザーが了承)

## 調べた事実(聞かずに調べたこと。出典つき)
- F1: M16 の締付トルクは 25 N·m(出典: 作業標準_ボルト締付.md 3)

## 最後の確認
- [x] ユーザーが「これで共通の理解になった」と確かめた
"""


class InterviewTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.cwd = Path(self._tmp.name).resolve()

    def run_cli(self, *argv: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        code = cli.main(list(argv), stdout=out, stderr=err, cwd=self.cwd)
        return code, out.getvalue(), err.getvalue()

    def write(self, text: str) -> str:
        (self.cwd / ".handoff").mkdir(exist_ok=True)
        (self.cwd / ".handoff" / "interview_x.md").write_text(text, encoding="utf-8")
        return ".handoff/interview_x.md"

    def test_initはひな形を作り_上書きしない_ひな形のままでは終わらない(self) -> None:
        code, out, _ = self.run_cli("init", "締付の 手順書")
        self.assertEqual(code, 0)
        path = self.cwd / ".handoff" / "interview_締付の_手順書.md"
        self.assertIn("## 決まったこと", path.read_text(encoding="utf-8"))
        path.write_text(path.read_text(encoding="utf-8") + "メモ\n", encoding="utf-8")
        code, out, _ = self.run_cli("init", "締付の 手順書")
        self.assertIn("すでにあります", out)
        self.assertTrue(path.read_text(encoding="utf-8").endswith("メモ\n"))
        code, out, _ = self.run_cli("check", ".handoff/interview_締付の_手順書.md")
        self.assertEqual(code, 1)
        self.assertIn("「目的」が空です", out)
        self.assertNotIn("D1", out)  # ひな形のコメントの例は数えない

    def test_そろえば終わってよい(self) -> None:
        code, out, _ = self.run_cli("check", self.write(DONE))
        self.assertEqual(code, 0, out)
        self.assertIn("結果: 終わってよい", out)
        self.assertIn("決まったこと 2、未決 0、仮定 0、事実 1", out)

    def test_AIが決めた_未決_仮定_出典なし_確認なし(self) -> None:
        text = (DONE.replace("(決めた人: ユーザー、第 1 回)", "(決めた人: AI、第 1 回)")
                .replace("## 未決(次の回で聞く)\n", "## 未決(次の回で聞く)\n- Q3: 置き場所\n")
                .replace("(ユーザーが了承)", "")
                .replace("(出典: 作業標準_ボルト締付.md 3)", "")
                .replace("- [x]", "- [ ]"))
        code, out, _ = self.run_cli("check", self.write(text))
        self.assertEqual(code, 1)
        for needle in ("ユーザー以外が決めています: D1", "未決が 1 件あります: Q3: 置き場所", "確かめていない仮定が 1 件",
                       "出典の無い事実: F1", "最後の確認に、まだ印がありません"):
            self.assertIn(needle, out)

    def test_決めた人の無い決定と_見出しの欠け(self) -> None:
        code, out, _ = self.run_cli("check", self.write(DONE.replace("(決めた人: ユーザー、第 1 回)", "").replace("## 最後の確認\n- [x] ユーザーが「これで共通の理解になった」と確かめた\n", "")))
        self.assertEqual(code, 1)
        self.assertIn("決めた人がありません: D1", out)
        self.assertIn("見出しがありません: 最後の確認", out)

    def test_確認だけが残るときの案内と_無いファイル(self) -> None:
        code, out, _ = self.run_cli("check", self.write(DONE.replace("- [x]", "- [ ]")))
        self.assertEqual(code, 1)
        self.assertIn("未決は 0 件。まとめをユーザーに示し、確認を得る", out)
        self.assertEqual(self.run_cli("check", "無い.md")[0], 2)

    def test_削除_実行_通信をしない(self) -> None:
        source = (SKILL_DIR / "scripts" / "interview_cli.py").read_text(encoding="utf-8")
        for needle in (".unlink(", "rmtree", "subprocess", "os.system", "urlopen", "socket", "eval(", "exec("):
            self.assertNotIn(needle, source)


if __name__ == "__main__":
    unittest.main()
