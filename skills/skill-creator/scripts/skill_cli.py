"""スキル (SKILL.md のフォルダ) の雛形を置き、点検するコマンド (スキル skill-creator から呼ぶ)。

  init  : 新しいスキルの雛形を置く (同名があれば止まる。上書きしない)
  check : スキルのフォルダを点検する (読み取り専用)。エラーがあれば終了コード 1

標準ライブラリだけで動く。ファイルを消す機能は無い。
"""
from __future__ import annotations

import argparse
import os
import re
import stat
import sys
from pathlib import Path
from typing import TextIO

NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
NAME_MAX = 64
DESCRIPTION_MAX = 1024  # OpenCode の上限
DESCRIPTION_SOFT = 120  # これを超えたら注意 (説明文は毎回すべての会話で読まれる)
BODY_LINES_SOFT = 300  # これを超えたら注意 (references/ へ分ける)
KNOWN_KEYS = {"name", "description", "license", "compatibility", "metadata"}
PLACEHOLDER = "(ここに"  # 雛形の書きかけの印

SKILL_TEMPLATE = """---
name: {name}
description: (ここに: 何をするか。いつ使うか(ユーザーが言いそうな言葉を 2〜3 個)。80 文字くらいまで)
---

## 前提

{run_line}- (ここに: 使う道具・フォルダ・設定。無ければこの行を消す)

## 守ること

- (ここに: してはいけないこと。理由を短く添える)

## 手順

1. (ここに: 最初にすること。コマンドは、そのまま使える例で書く)
2. (ここに: 次にすること)
3. 報告する: (ここに: 報告に書くこと)

## うまくいかないとき

- (ここに: 出るエラーの文と、そのときにすること。探し回らず、止めて報告する、など)
"""

RUN_LINE = "- 実行: 作業フォルダのまま `<スキルのフォルダ>\\run.bat 引数`(Mac・Linux は `run.sh`)。`cd` しない。使い方は `run.bat --help`。\n"

RUN_BAT = """@echo off
rem Run {module}.py. It needs only the standard library, so it works without the venv too.
rem This file must stay ASCII only (cmd.exe on Japanese Windows may misread other characters).
setlocal
set "PY=%~dp0..\\..\\.venv\\Scripts\\python.exe"
if not exist "%PY%" set "PY=python"
"%PY%" "%~dp0scripts\\{module}.py" %*
exit /b %ERRORLEVEL%
"""

RUN_SH = """#!/bin/sh
# Run {module}.py (Mac / Linux). The Windows version is run.bat. Same arguments.
# It needs only the standard library, so it works without the venv too.
# This file must stay ASCII only, with LF line endings.
here=$(cd "$(dirname "$0")" && pwd -P)  # resolve links: the .venv is next to the real skills folder
PY="$here/../../.venv/bin/python"
if [ ! -x "$PY" ]; then
  # No venv: pick a Python 3.10 or later (macOS /usr/bin/python3 is often 3.9, which the scripts do not support)
  PY=
  for c in python3 python3.14 python3.13 python3.12 python3.11 python3.10 python /opt/homebrew/bin/python3.14 /opt/homebrew/bin/python3.13 /opt/homebrew/bin/python3.12 /usr/local/bin/python3 "$HOME/.local/bin/python3"; do
    if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null; then PY=$c; break; fi
  done
  if [ -z "$PY" ]; then
    echo "Error: Python 3.10 or later was not found. Stop and report this to the user (do not search for Python yourself)." >&2
    exit 1
  fi
fi
exec "$PY" "$here/scripts/{module}.py" "$@"
"""

CLI_TEMPLATE = '''"""{name} のコマンド (スキル {name} から呼ぶ)。標準ライブラリだけで書く。"""
from __future__ import annotations

import argparse
import sys


def cmd_hello(args: argparse.Namespace) -> int:
    # (ここに: 処理を書く。雛形の例なので、要らなければ消す)
    print(f"こんにちは、{{args.who}}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="run.bat", description="{name}")
    sub = parser.add_subparsers(dest="command", required=True)
    hello = sub.add_parser("hello", help="例のコマンド")
    hello.add_argument("who")
    hello.set_defaults(func=cmd_hello)
    return parser


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
'''

TEST_TEMPLATE = '''"""{module} のテスト。実行: python -m unittest discover -s <スキルのフォルダ>/tests -v"""
from __future__ import annotations

import contextlib
import io
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import {module} as cli  # noqa: E402


class HelloTests(unittest.TestCase):
    def test_hello(self) -> None:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(cli.main(["hello", "世界"]), 0)
        self.assertIn("世界", out.getvalue())


if __name__ == "__main__":
    unittest.main()
'''


class SkillError(Exception):
    pass


def default_parent(work: Path) -> Path:
    """ハーネスのリポジトリ (skills/ と agents/ と AGENTS.md がある) なら skills/、ほかは .opencode/skills/。"""
    if (work / "skills").is_dir() and (work / "agents").is_dir() and (work / "AGENTS.md").is_file():
        return work / "skills"
    return work / ".opencode" / "skills"


def module_name(name: str) -> str:
    return name.replace("-", "_") + "_cli"


def write_new(path: Path, text: str, *, executable: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "x", encoding="utf-8", newline="\n") as f:  # "x": 既にあれば失敗 (上書きしない)
        f.write(text)
    if executable:
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def cmd_init(args: argparse.Namespace, out: TextIO) -> int:
    name = args.name
    if not NAME_RE.match(name) or len(name) > NAME_MAX:
        raise SkillError(f"名前「{name}」は使えません。英小文字・数字・ハイフンで、{NAME_MAX} 文字まで(例: cost-report)")
    parent = Path(args.dir).resolve() if args.dir else default_parent(Path.cwd().resolve())
    folder = parent / name
    if folder.exists():
        raise SkillError(f"すでにあります: {folder}(上書きしません。別の名前にするか、既存のスキルを直してください)")
    module = module_name(name)
    created = [folder / "SKILL.md"]
    write_new(folder / "SKILL.md", SKILL_TEMPLATE.format(name=name, run_line=RUN_LINE if args.script else ""))
    if args.script:
        files = [
            (folder / "run.bat", RUN_BAT.format(module=module), False),
            (folder / "run.sh", RUN_SH.format(module=module), True),
            (folder / "scripts" / f"{module}.py", CLI_TEMPLATE.format(name=name), False),
            (folder / "tests" / f"test_{name.replace('-', '_')}.py", TEST_TEMPLATE.format(module=module), False),
        ]
        for path, text, executable in files:
            write_new(path, text, executable=executable)
            created.append(path)
    print(f"作成: {folder}", file=out)
    for path in created:
        print(f"  {path.relative_to(folder).as_posix()}", file=out)
    print("次に: SKILL.md の「(ここに」をすべて書き換え、check で点検する。", file=out)
    return 0


def split_frontmatter(text: str) -> tuple[str | None, str]:
    text = text.lstrip("﻿")
    if not text.startswith("---"):
        return None, text
    lines = text.splitlines()
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return "\n".join(lines[1:i]), "\n".join(lines[i + 1 :])
    return None, text


def top_level_keys(front: str) -> dict[str, str]:
    keys: dict[str, str] = {}
    for line in front.splitlines():
        m = re.match(r"^([A-Za-z_][\w-]*):\s*(.*)$", line)
        if m:
            keys[m.group(1)] = m.group(2).strip().strip("\"'")
    return keys


def is_ascii(data: bytes) -> bool:
    try:
        data.decode("ascii")
        return True
    except UnicodeDecodeError:
        return False


def check_folder(folder: Path) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    notes: list[str] = []
    skill = folder / "SKILL.md"
    if not folder.is_dir():
        return [f"フォルダがありません: {folder}"], notes
    if not skill.is_file():
        return ["SKILL.md がありません(スキルのフォルダには必須)"], notes
    text = skill.read_text(encoding="utf-8")
    front, body = split_frontmatter(text)
    if front is None:
        errors.append("SKILL.md の先頭に frontmatter(--- で挟んだ name と description)がありません")
        front = ""
    keys = top_level_keys(front)
    name = keys.get("name", "")
    description = keys.get("description", "")
    if not name:
        errors.append("frontmatter に name がありません")
    elif not NAME_RE.match(name) or len(name) > NAME_MAX:
        errors.append(f"name「{name}」は使えません(英小文字・数字・ハイフン、{NAME_MAX} 文字まで)")
    elif name != folder.name:
        errors.append(f"name「{name}」がフォルダ名「{folder.name}」と違います(同じにする)")
    if not description:
        errors.append("frontmatter に description がありません(いつ使うかを書く。モデルはこれを見て使うかを決める)")
    elif len(description) > DESCRIPTION_MAX:
        errors.append(f"description が {len(description)} 文字です(上限 {DESCRIPTION_MAX})")
    elif len(description) > DESCRIPTION_SOFT:
        notes.append(f"description が {len(description)} 文字です。毎回すべての会話で読まれるので、{DESCRIPTION_SOFT} 文字くらいまでに縮める")
    for key in sorted(set(keys) - KNOWN_KEYS):
        notes.append(f"frontmatter の「{key}」は、OpenCode では使われません")
    if PLACEHOLDER in text:
        count = text.count(PLACEHOLDER)
        errors.append(f"雛形の「{PLACEHOLDER}…)」が {count} か所残っています(書き換えるか、行を消す)")
    body_lines = [line for line in body.splitlines()]
    if not body.strip():
        errors.append("本文がありません(手順を書く)")
    elif len(body_lines) > BODY_LINES_SOFT:
        notes.append(f"本文が {len(body_lines)} 行です。{BODY_LINES_SOFT} 行を超えたら、細部を references/ に分け、本文から「〜のときだけ読む」で指す")
    for ref in sorted(set(re.findall(r"(?<![\w./-])((?:references|scripts|assets)/[\w./-]+\.\w+)", body))):
        if "<" in ref or "*" in ref:
            continue
        if not (folder / ref).exists():
            errors.append(f"本文が指す {ref} がありません")
    errors_run, notes_run = check_run_files(folder, body)
    return errors + errors_run, notes + notes_run


def check_run_files(folder: Path, body: str) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    notes: list[str] = []
    bat, sh = folder / "run.bat", folder / "run.sh"
    scripts: dict[str, str] = {}
    for path in (bat, sh):
        if not path.exists():
            continue
        raw = path.read_bytes()
        if not is_ascii(raw):
            errors.append(f"{path.name} に ASCII 以外の文字があります(日本語の Windows の cmd.exe が読み違える。コメントも英語にする)")
        m = re.search(r"scripts[\\/]([\w-]+\.py)", raw.decode("utf-8", "replace"))
        if m:
            scripts[path.name] = m.group(1)
            if not (folder / "scripts" / m.group(1)).is_file():
                errors.append(f"{path.name} が呼ぶ scripts/{m.group(1)} がありません")
        else:
            errors.append(f"{path.name} が scripts/ の Python を呼んでいません")
    if sh.exists():
        raw = sh.read_bytes()
        if b"\r\n" in raw:
            errors.append("run.sh の改行が CRLF です(LF にする。CRLF だと Mac・Linux で動かない)")
        if b"exec " not in raw:
            notes.append("run.sh が exec で呼んでいません(終了コードが伝わらないことがある)")
        if os.name == "posix" and not os.access(sh, os.X_OK):
            errors.append("run.sh に実行権限がありません(chmod +x run.sh)")
        if "run.sh" not in body:
            notes.append("本文に run.sh の案内がありません(「Mac・Linux は run.sh」と書く)")
    if bat.exists() and not sh.exists():
        notes.append("run.bat だけで run.sh がありません(Windows 専用なら、そのままでよい)")
    if sh.exists() and not bat.exists():
        errors.append("run.sh だけで run.bat がありません(会社の Windows で使えない)")
    if len(set(scripts.values())) > 1:
        errors.append(f"run.bat と run.sh が違うスクリプトを呼んでいます: {scripts}")
    if (folder / "scripts").is_dir() and not bat.exists():
        notes.append("scripts/ があるのに run.bat がありません(入口を run.bat・run.sh にすると、許可と手順が揃う)")
    return errors, notes


def cmd_check(args: argparse.Namespace, out: TextIO) -> int:
    folder = Path(args.folder).resolve()
    errors, notes = check_folder(folder)
    print(f"点検: {folder}", file=out)
    for message in errors:
        print(f"エラー: {message}", file=out)
    for message in notes:
        print(f"注意: {message}", file=out)
    if not errors and not notes:
        print("問題なし", file=out)
    else:
        print(f"エラー {len(errors)} 件、注意 {len(notes)} 件", file=out)
    return 1 if errors else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="run.bat", description="スキルの雛形を置く (init)・点検する (check)")
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init", help="新しいスキルの雛形を置く (上書きしない)")
    init.add_argument("name", help="スキルの名前 (英小文字・数字・ハイフン。例: cost-report)")
    init.add_argument("--script", action="store_true", help="run.bat・run.sh・scripts/・tests/ も置く")
    init.add_argument("--dir", help="置く場所 (既定: ハーネスのリポジトリなら skills/、ほかは .opencode/skills/)")
    init.set_defaults(func=cmd_init)
    check = sub.add_parser("check", help="スキルのフォルダを点検する (読み取り専用)")
    check.add_argument("folder", help="スキルのフォルダ (SKILL.md がある場所)")
    check.set_defaults(func=cmd_check)
    return parser


def main(argv: list[str] | None = None, out: TextIO | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    out = out or sys.stdout
    args = build_parser().parse_args(argv)
    try:
        return args.func(args, out)
    except SkillError as exc:
        print(f"エラー: {exc}", file=out)
        return 1


if __name__ == "__main__":
    sys.exit(main())
