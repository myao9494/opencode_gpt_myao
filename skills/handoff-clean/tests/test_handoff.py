"""handoff_cli のテスト。実行: python -m unittest discover -s skills/handoff-clean/tests -v   (リポジトリのルートから)"""
from __future__ import annotations

import io
import os
import sys
import tempfile
import unittest
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_DIR / "scripts"))

import handoff_cli as cli  # noqa: E402

NOW = 1_800_000_000.0  # 固定の「いま」
DAY = 86400


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.work = Path(self._tmp.name).resolve() / "work"
        self.work.mkdir()
        self.base = self.work / ".handoff"

    def make(self, rel: str, days: float, content: str = "x") -> Path:
        path = self.base / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        os.utime(path, (NOW - days * DAY, NOW - days * DAY))
        return path

    def run_cli(self, *argv: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        code = cli.main(list(argv), stdout=out, stderr=err, cwd=self.work, now=NOW)
        return code, out.getvalue(), err.getvalue()

    def files(self) -> set[str]:
        return {p.relative_to(self.base).as_posix() for p in self.base.rglob("*") if p.is_file()}


class ListTests(Base):
    def test_ない場合(self) -> None:
        code, out, _ = self.run_cli("list")
        self.assertEqual(code, 0)
        self.assertIn("はありません", out)
        self.assertFalse(self.base.exists())  # 作らない

    def test_件数_古さ_場所_古いものの一覧(self) -> None:
        self.make("researcher_a.md", 0.2)
        self.make("researcher_b.md", 3)
        self.make("playwright/p1.md", 10, "y" * 2048)
        self.make("playwright/p2.md", 40)
        self.make("converted/報告.docx.md", 8)
        self.make("_trash/20260101-000000/old.md", 100)
        code, out, _ = self.run_cli("list")
        self.assertEqual(code, 0)
        self.assertIn(".handoff: 5 ファイル", out)
        self.assertIn("退避済み _trash: 1 ファイル", out)
        self.assertIn("古さ: 今日 1 / 7日未満 1 / 30日未満 2 / 30日以上 1", out)
        self.assertIn("場所: (直下) 2 / converted 1 / playwright 2", out)
        self.assertIn("7 日以上前のもの: 3 ファイル", out)
        self.assertRegex(out, r"  40 日 +\d+ B  playwright/p2\.md")
        self.assertLess(out.index("p2.md"), out.index("p1.md"))  # 古い順
        self.assertNotIn("researcher_b.md", out)
        self.assertNotIn("old.md", out)  # _trash は一覧に出さない
        self.assertIn("sweep --older-than-days 7", out)

    def test_何も変更しない(self) -> None:
        self.make("a.md", 50)
        before = {p: p.stat().st_mtime for p in self.base.rglob("*") if p.is_file()}
        self.run_cli("list")
        self.assertEqual(before, {p: p.stat().st_mtime for p in self.base.rglob("*") if p.is_file()})
        self.assertEqual(self.files(), {"a.md"})

    def test_一覧は長くなりすぎない(self) -> None:
        for i in range(100):
            self.make(f"f{i:03}.md", 20 + i)
        _, out, _ = self.run_cli("list")
        self.assertIn("… ほか 70 ファイル", out)
        self.assertLess(len(out.splitlines()), 45)

    def test_gitignoreの警告(self) -> None:
        self.make("a.md", 1)
        self.assertNotIn("警告", self.run_cli("list")[1])  # git の管理下でなければ言わない
        (self.work / ".git").mkdir()
        self.assertIn("警告: .gitignore に .handoff/", self.run_cli("list")[1])
        (self.work / ".gitignore").write_text("node_modules/\n.handoff/\n", encoding="utf-8")
        self.assertNotIn("警告", self.run_cli("list")[1])

    def test_日数の指定(self) -> None:
        self.make("a.md", 2)
        self.assertIn("1 日以上前のもの: 1", self.run_cli("list", "--older-than-days", "1")[1])
        self.assertEqual(self.run_cli("list", "--older-than-days", "-1")[0], 2)


class SweepTests(Base):
    def test_古いものだけ_trashへ移動し_削除しない(self) -> None:
        old = self.make("playwright/old.md", 20, "古い")
        self.make("new.md", 1, "新しい")
        code, out, err = self.run_cli("sweep", "--older-than-days", "7")
        self.assertEqual((code, err), (0, ""))
        self.assertIn("移動しました: 1 ファイル", out)
        self.assertIn("削除はしていません", out)
        self.assertFalse(old.exists())
        stamp_dirs = list((self.base / "_trash").iterdir())
        batch = next(p for p in stamp_dirs if p.is_dir())
        self.assertEqual((batch / "playwright" / "old.md").read_text(encoding="utf-8"), "古い")  # 中身は同じ
        self.assertTrue((self.base / "new.md").exists())
        self.assertIn(f"{batch.name}.manifest.txt", out)
        manifest = (self.base / "_trash" / f"{batch.name}.manifest.txt").read_text(encoding="utf-8")
        self.assertIn(str(old), manifest)
        self.assertIn(str(batch / "playwright" / "old.md"), manifest)

    def test_ファイルの総数と内容が保たれる(self) -> None:
        for i in range(12):
            self.make(f"d{i % 3}/f{i}.md", 10 + i, f"内容{i}")
        before = {p.name: p.read_text(encoding="utf-8") for p in self.base.rglob("*.md")}
        self.run_cli("sweep", "--older-than-days", "1")
        after = {p.name: p.read_text(encoding="utf-8") for p in self.base.rglob("*.md")}
        self.assertEqual(before, after)

    def test_dry_runは何も動かさない(self) -> None:
        self.make("a.md", 20)
        code, out, _ = self.run_cli("sweep", "--older-than-days", "7", "--dry-run")
        self.assertEqual(code, 0)
        self.assertIn("[試行]", out)
        self.assertIn("実際には何も移動していません", out)
        self.assertEqual(self.files(), {"a.md"})
        self.assertFalse((self.base / "_trash").exists())

    def test_all(self) -> None:
        self.make("a.md", 0)
        self.make("b/c.md", 5)
        self.make("_trash/x/keep.md", 5)
        self.run_cli("sweep", "--all")
        remaining = {f for f in self.files() if not f.startswith("_trash/")}
        self.assertEqual(remaining, set())
        self.assertTrue((self.base / "_trash" / "x" / "keep.md").exists())  # すでに退避したものは動かさない
        self.assertFalse(any(f.count("_trash") > 1 for f in self.files()))  # _trash の中に _trash を作らない

    def test_trashの中は対象外で_入れ子にならない(self) -> None:
        self.make("_trash/20260101-000000/old.md", 200)
        code, out, _ = self.run_cli("sweep", "--older-than-days", "7")
        self.assertIn("ファイルはありません", out)
        self.assertEqual(self.files(), {"_trash/20260101-000000/old.md"})

    def test_日数の指定が無い_0以下はエラー(self) -> None:
        self.make("a.md", 20)
        with self.assertRaises(SystemExit):
            self.run_cli("sweep")  # --older-than-days か --all が必要
        self.assertEqual(self.run_cli("sweep", "--older-than-days", "0")[0], 2)
        self.assertEqual(self.files(), {"a.md"})

    def test_同じ秒に2回掃除しても上書きしない(self) -> None:
        self.make("a.md", 20, "一回目")
        self.run_cli("sweep", "--older-than-days", "7")
        self.make("a.md", 20, "二回目")
        self.run_cli("sweep", "--older-than-days", "7")
        contents = sorted(p.read_text(encoding="utf-8") for p in (self.base / "_trash").rglob("a.md"))
        self.assertEqual(contents, ["一回目", "二回目"])

    def test_移動先に同名があっても上書きしない(self) -> None:
        self.make("a.md", 20, "元")
        batch = self.base / "_trash" / "FIXED"
        batch.mkdir(parents=True)
        (batch / "a.md").write_text("既存", encoding="utf-8")
        out = io.StringIO()
        code = cli.cmd_sweep(self.work, 7, False, NOW, out, stamp="FIXED")
        # 同じ名前の退避先は使わず、別の名前 (FIXED_2) に移す
        self.assertEqual(code, 0)
        self.assertEqual((batch / "a.md").read_text(encoding="utf-8"), "既存")
        self.assertEqual((self.base / "_trash" / "FIXED_2" / "a.md").read_text(encoding="utf-8"), "元")

    def test_使用中などで動かせないものは残して報告(self) -> None:
        a = self.make("a.md", 20)
        self.make("b.md", 20)
        original = Path.rename

        def flaky(self_: Path, target):  # type: ignore[no-untyped-def]
            if self_.name == "a.md":
                raise PermissionError("使用中です")
            return original(self_, target)

        Path.rename = flaky  # type: ignore[method-assign]
        try:
            code, out, _ = self.run_cli("sweep", "--older-than-days", "7")
        finally:
            Path.rename = original  # type: ignore[method-assign]
        self.assertEqual(code, 4)
        self.assertIn("移動できませんでした: a.md", out)
        self.assertTrue(a.exists())
        self.assertFalse((self.base / "b.md").exists())

    def test_多すぎるときは上限で止めて知らせる(self) -> None:
        original = cli.MAX_SWEEP_FILES
        cli.MAX_SWEEP_FILES = 3
        try:
            for i in range(5):
                self.make(f"f{i}.md", 20 + i)
            _, out, _ = self.run_cli("sweep", "--older-than-days", "7")
        finally:
            cli.MAX_SWEEP_FILES = original
        self.assertIn("残り 2 個", out)
        self.assertEqual(len([f for f in self.files() if not f.startswith("_trash/") and not f.endswith(".manifest.txt")]), 2)

    def test_シンボリックリンクは動かさない(self) -> None:
        target = self.work / "実体.txt"
        target.write_text("大事", encoding="utf-8")
        os.utime(target, (NOW - 50 * DAY, NOW - 50 * DAY))
        self.base.mkdir()
        try:
            os.symlink(target, self.base / "link.txt")
            os.symlink(self.work, self.base / "dirlink")
        except (OSError, NotImplementedError):
            self.skipTest("シンボリックリンクを作れない環境")
        _, out, _ = self.run_cli("sweep", "--all")
        self.assertTrue((self.base / "link.txt").is_symlink())
        self.assertTrue((self.base / "dirlink").is_symlink())
        self.assertEqual(target.read_text(encoding="utf-8"), "大事")
        self.assertIn("ファイルはありません", out)
        self.assertIn("シンボリックリンク 2 個", self.run_cli("list")[1])

    def test_handoff自体がシンボリックリンクなら何もしない(self) -> None:
        elsewhere = self.work.parent / "elsewhere"
        elsewhere.mkdir()
        (elsewhere / "a.md").write_text("外", encoding="utf-8")
        os.utime(elsewhere / "a.md", (NOW - 50 * DAY, NOW - 50 * DAY))
        try:
            os.symlink(elsewhere, self.base)
        except (OSError, NotImplementedError):
            self.skipTest("シンボリックリンクを作れない環境")
        for argv in (("sweep", "--all"), ("list",)):
            code, _, err = self.run_cli(*argv)
            self.assertEqual(code, 2)
            self.assertIn("シンボリックリンク", err)
        self.assertTrue((elsewhere / "a.md").exists())


class SafetyTests(unittest.TestCase):
    def test_ソースに削除の機能が無い(self) -> None:
        source = (SKILL_DIR / "scripts" / "handoff_cli.py").read_text(encoding="utf-8")
        for needle in (".unlink(", "os.remove", "os.unlink", "rmtree", "rmdir", "shutil", "subprocess", "os.system", "urlopen", "socket", "eval(", "exec(", ".replace(Path", "os.replace", "send2trash"):
            self.assertNotIn(needle, source, f"handoff_cli.py に {needle} があります")
        self.assertEqual(source.count(".rename("), 1, "移動は rename の 1 か所だけ")
        self.assertEqual(source.count(".write_text("), 1, "書き込みは移動の記録の 1 か所だけ")


if __name__ == "__main__":
    unittest.main()
