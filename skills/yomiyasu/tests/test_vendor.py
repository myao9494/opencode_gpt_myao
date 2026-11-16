"""同梱の yomiyasu のテスト (ハーネスが足したもの)。実行: python -m unittest discover -s skills/yomiyasu/tests -v

- 同梱のファイルが、VENDOR.md の一覧 (SHA-256) と同じか (うっかりの編集を見つける)
- 入口 (scripts/run_yomiyasu.py) が、同梱のスクリプトを呼び、記録を作業フォルダの .handoff/yomiyasu に置くか
- 同梱のスクリプトが、通信のモジュールを使っていないか

更新のとき: python3 skills/yomiyasu/tests/test_vendor.py --manifest で、一覧の行を出す。
"""
from __future__ import annotations

import hashlib
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parents[1]
HARNESS_FILES = {"run.bat", "run.sh", "VENDOR.md", "SKILL.md", "scripts/run_yomiyasu.py"}  # SKILL.md はハーネスの要約版 (元は references/upstream-SKILL.md)


def vendored_files() -> list[str]:
    files = []
    for path in SKILL_DIR.rglob("*"):
        rel = path.relative_to(SKILL_DIR).as_posix()
        if path.is_file() and rel not in HARNESS_FILES and not rel.startswith("tests/") and "__pycache__" not in rel:
            files.append(rel)
    return sorted(files)


def manifest_lines() -> list[str]:
    return [f"{hashlib.sha256((SKILL_DIR / f).read_bytes()).hexdigest()}  {f}" for f in vendored_files()]


class VendorTests(unittest.TestCase):
    def test_同梱のファイルが一覧と同じ(self) -> None:
        text = (SKILL_DIR / "VENDOR.md").read_text(encoding="utf-8")
        listed = re.findall(r"(?m)^([0-9a-f]{64})  (\S.*)$", text)
        self.assertTrue(listed, "VENDOR.md に一覧がありません")
        self.assertEqual([f"{h}  {p}" for h, p in listed], manifest_lines(), "同梱のファイルが、VENDOR.md の一覧と違います (編集したか、足りない・余計なファイル)")

    def test_版とライセンス_要約版は元を指す(self) -> None:
        upstream = (SKILL_DIR / "references" / "upstream-SKILL.md").read_text(encoding="utf-8")
        self.assertIn('version: "2.4.0"', upstream)
        skill = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("references/upstream-SKILL.md", skill)
        self.assertLess(len(skill), 8000, "要約版の手順書が長くなっています (社内 LLM の窓のため、短く保つ)")
        for ref in set(re.findall(r"`(references/[^`<>]+?\.(?:md|txt))`", skill)):
            self.assertTrue((SKILL_DIR / ref).is_file(), f"要約版が指す {ref} がありません")
        self.assertIn("v2.4.0", (SKILL_DIR / "VENDOR.md").read_text(encoding="utf-8"))
        self.assertIn("MIT License", (SKILL_DIR / "LICENSE").read_text(encoding="utf-8"))

    def test_同梱のスクリプトは通信しない(self) -> None:
        for path in (SKILL_DIR / "scripts").glob("*.py"):
            source = path.read_text(encoding="utf-8")
            for needle in ("import socket", "import urllib", "from urllib", "import http", "from http", "import requests", "import ftplib", "import smtplib", "webbrowser"):
                self.assertNotIn(needle, source, f"{path.name}: {needle}")


class EntryTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.cwd = Path(self._tmp.name).resolve()
        (self.cwd / "元.md").write_text("# 手順\n\n作業者は、トルクレンチを用いて締付を実施する事が可能です。\n", encoding="utf-8")
        (self.cwd / "最終稿.md").write_text("# 手順\n\n作業者は、トルクレンチで締め付けることができます。\n", encoding="utf-8")

    def run_entry(self, *args: str) -> subprocess.CompletedProcess:
        env = {k: v for k, v in os.environ.items() if k != "YOMIYASU_STATE_DIR"}
        return subprocess.run([sys.executable, "-B", str(SKILL_DIR / "scripts" / "run_yomiyasu.py"), *args], cwd=self.cwd,
                              capture_output=True, text=True, encoding="utf-8", env=env, timeout=120)

    def test_知らない道具は使い方を出す(self) -> None:
        result = self.run_entry("bogus")
        self.assertEqual(result.returncode, 2)
        self.assertIn("使い方", result.stderr)

    def test_lintとcheckが動き_記録は_handoffの中(self) -> None:
        lint = self.run_entry("lint", "元.md")
        self.assertEqual(lint.returncode, 0, lint.stdout + lint.stderr)
        self.assertIn("検査を実行しました", lint.stdout)
        check = self.run_entry("check", "run", "--source", "元.md", "--final", "最終稿.md", "--stance=説明")
        self.assertIn("yomiyasu-check: executed", check.stdout, check.stdout + check.stderr)
        self.assertTrue((self.cwd / ".handoff" / "yomiyasu" / "state.json").is_file())
        self.assertTrue((self.cwd / ".handoff" / "yomiyasu" / ".gitignore").is_file())
        self.assertFalse((self.cwd / ".yomiyasu").exists())  # yomiyasu の既定の場所には書かない
        verify = self.run_entry("check", "verify", "--final", "最終稿.md")
        self.assertIn("ハッシュが記録と合いました", verify.stdout, verify.stdout + verify.stderr)


if __name__ == "__main__":
    if sys.argv[1:] == ["--manifest"]:
        print("\n".join(manifest_lines()))
    else:
        unittest.main()
