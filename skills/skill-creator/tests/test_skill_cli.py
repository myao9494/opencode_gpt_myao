"""skill_cli のテスト。実行: python -m unittest discover -s skills/skill-creator/tests -v   (リポジトリのルートから)"""
from __future__ import annotations

import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_DIR / "scripts"))

import skill_cli as cli  # noqa: E402


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.work = Path(self._tmp.name).resolve()
        self._cwd = os.getcwd()
        os.chdir(self.work)
        self.addCleanup(os.chdir, self._cwd)

    def run_cli(self, *argv: str) -> tuple[int, str]:
        out = io.StringIO()
        code = cli.main(list(argv), out)
        return code, out.getvalue()

    def fill(self, folder: Path) -> None:
        """雛形の書きかけを埋める (人やモデルが書いた後の状態にする)。"""
        path = folder / "SKILL.md"
        lines = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("description:"):
                line = "description: 見積の費用を集計する。「費用をまとめて」のとき"
            lines.append(line.replace(cli.PLACEHOLDER, "(").replace("(:", "(例:"))
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")


class InitTests(Base):
    def test_ほかのフォルダではopencode_skillsに置く(self) -> None:
        code, out = self.run_cli("init", "cost-report")
        self.assertEqual(code, 0, out)
        self.assertTrue((self.work / ".opencode" / "skills" / "cost-report" / "SKILL.md").is_file())
        self.assertFalse((self.work / ".opencode" / "skills" / "cost-report" / "run.bat").exists())

    def test_ハーネスのリポジトリではskillsに置く(self) -> None:
        (self.work / "skills").mkdir()
        (self.work / "agents").mkdir()
        (self.work / "AGENTS.md").write_text("x", encoding="utf-8")
        code, _ = self.run_cli("init", "cost-report")
        self.assertEqual(code, 0)
        self.assertTrue((self.work / "skills" / "cost-report" / "SKILL.md").is_file())

    def test_同名があれば上書きしない(self) -> None:
        self.run_cli("init", "cost-report")
        skill = self.work / ".opencode" / "skills" / "cost-report" / "SKILL.md"
        skill.write_text("人が書いた", encoding="utf-8")
        code, out = self.run_cli("init", "cost-report", "--script")
        self.assertEqual(code, 1)
        self.assertIn("すでにあります", out)
        self.assertEqual(skill.read_text(encoding="utf-8"), "人が書いた")

    def test_使えない名前は止まる(self) -> None:
        for name in ["Cost", "cost_report", "a-", "a--b", "見積"]:
            code, out = self.run_cli("init", name)
            self.assertEqual(code, 1, name)
            self.assertIn("使えません", out)
        self.assertFalse((self.work / ".opencode").exists())

    def test_scriptつきの雛形は点検とテストを通る(self) -> None:
        code, _ = self.run_cli("init", "cost-report", "--script", "--dir", "s")
        self.assertEqual(code, 0)
        folder = self.work / "s" / "cost-report"
        for rel in ["run.bat", "run.sh", "scripts/cost_report_cli.py", "tests/test_cost_report.py"]:
            self.assertTrue((folder / rel).is_file(), rel)
        self.assertTrue((folder / "run.bat").read_bytes().isascii())
        self.assertNotIn(b"\r\n", (folder / "run.sh").read_bytes())
        if os.name == "posix":
            self.assertTrue(os.access(folder / "run.sh", os.X_OK))
        # 書きかけのままでは、エラーになる
        code, out = self.run_cli("check", str(folder))
        self.assertEqual(code, 1)
        self.assertIn("雛形", out)
        self.fill(folder)
        code, out = self.run_cli("check", str(folder))
        self.assertEqual(code, 0, out)
        self.assertIn("問題なし", out)
        # 雛形のテストがそのまま通る
        result = subprocess.run([sys.executable, "-B", "-m", "unittest", "discover", "-s", str(folder / "tests")],
                                capture_output=True, text=True, encoding="utf-8", env={**os.environ, "PYTHONUTF8": "1"})
        self.assertEqual(result.returncode, 0, result.stderr)
        if os.name == "posix":
            result = subprocess.run([str(folder / "run.sh"), "hello", "a"], capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(result.returncode, 0, result.stderr)


class CheckTests(Base):
    def make(self, front: str, body: str = "## 手順\n\n1. する\n", name: str = "demo") -> Path:
        folder = self.work / name
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "SKILL.md").write_text(f"---\n{front}\n---\n\n{body}", encoding="utf-8")
        return folder

    def test_正しいスキルは問題なし(self) -> None:
        folder = self.make("name: demo\ndescription: 例を示す。「例を見せて」のとき")
        code, out = self.run_cli("check", str(folder))
        self.assertEqual(code, 0, out)

    def test_名前と説明の誤り(self) -> None:
        cases = [
            ("description: 説明", "name がありません"),
            ("name: other\ndescription: 説明", "フォルダ名「demo」と違います"),
            ("name: Demo\ndescription: 説明", "使えません"),
            ("name: demo", "description がありません"),
            ("name: demo\ndescription: " + "あ" * 1100, "上限"),
        ]
        for front, expected in cases:
            code, out = self.run_cli("check", str(self.make(front)))
            self.assertEqual(code, 1, front)
            self.assertIn(expected, out, front)

    def test_長い説明と知らないキーは注意(self) -> None:
        folder = self.make("name: demo\ndescription: " + "あ" * 200 + "\nallowed-tools: x")
        code, out = self.run_cli("check", str(folder))
        self.assertEqual(code, 0, out)
        self.assertIn("注意: description が 200 文字", out)
        self.assertIn("allowed-tools", out)

    def test_frontmatterが無い_本文が無い(self) -> None:
        folder = self.work / "demo"
        folder.mkdir()
        (folder / "SKILL.md").write_text("# 見出しだけ\n", encoding="utf-8")
        code, out = self.run_cli("check", str(folder))
        self.assertEqual(code, 1)
        self.assertIn("frontmatter", out)
        code, out = self.run_cli("check", str(self.make("name: demo\ndescription: 説明", body="")))
        self.assertIn("本文がありません", out)

    def test_本文が指すファイルが無い(self) -> None:
        folder = self.make("name: demo\ndescription: 説明", body="詳しくは `references/detail.md` を読む。`references/<名前>.md` は例。\n")
        code, out = self.run_cli("check", str(folder))
        self.assertEqual(code, 1)
        self.assertIn("references/detail.md がありません", out)
        self.assertNotIn("<名前>", out)
        (folder / "references").mkdir()
        (folder / "references" / "detail.md").write_text("x", encoding="utf-8")
        code, out = self.run_cli("check", str(folder))
        self.assertEqual(code, 0, out)

    def test_run_batとrun_shの誤り(self) -> None:
        folder = self.make("name: demo\ndescription: 説明", body="実行は run.bat。\n")
        (folder / "scripts").mkdir()
        (folder / "scripts" / "a.py").write_text("print(1)\n", encoding="utf-8")
        (folder / "run.bat").write_text('@echo off\nrem 日本語\n"python" "%~dp0scripts\\a.py" %*\n', encoding="utf-8")
        (folder / "run.sh").write_bytes(b'#!/bin/sh\r\nexec python3 "$here/scripts/b.py" "$@"\r\n')
        code, out = self.run_cli("check", str(folder))
        self.assertEqual(code, 1)
        for expected in ["run.bat に ASCII 以外", "CRLF", "scripts/b.py がありません", "違うスクリプト", "run.sh の案内がありません"]:
            self.assertIn(expected, out)
        if os.name == "posix":
            self.assertIn("実行権限", out)

    def test_フォルダやSKILL_mdが無い(self) -> None:
        code, out = self.run_cli("check", str(self.work / "nothing"))
        self.assertEqual(code, 1)
        self.assertIn("フォルダがありません", out)
        (self.work / "empty").mkdir()
        code, out = self.run_cli("check", str(self.work / "empty"))
        self.assertIn("SKILL.md がありません", out)


class SelfTests(unittest.TestCase):
    def test_このスキル自身が点検を通る(self) -> None:
        errors, _ = cli.check_folder(SKILL_DIR)
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
