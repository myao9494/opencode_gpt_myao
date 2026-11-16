"""ハーネスの入口: run.bat lint|check|diff [引数] で、同梱の yomiyasu のスクリプトを動かす (ハーネスが足したファイル。yomiyasu の本体ではない)。

  lint  : scripts/yomiyasu_lint.py   (原文・最終稿の候補を出す)
  check : scripts/yomiyasu_check.py  (run で lint と diff をまとめて実行し記録する / verify で最終稿を照合する)
  diff  : scripts/yomiyasu_diff.py

記録の置き場 (YOMIYASU_STATE_DIR) が決まっていなければ、作業フォルダの .handoff/yomiyasu にする
(yomiyasu の既定は Git の最上位の .yomiyasu/。Vault やプロジェクトを汚さないため)。
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOOLS = {"lint": "yomiyasu_lint.py", "check": "yomiyasu_check.py", "diff": "yomiyasu_diff.py"}


def main(argv: list[str]) -> int:
    usage = "usage: run.bat lint|check|diff [yomiyasu の引数]   (例: run.bat check run --source 元.md --final 最終稿.md。Mac・Linux は run.sh)\n"
    if argv[:1] in (["--help"], ["-h"]):
        sys.stdout.write(usage)
        return 0
    if not argv or argv[0] not in TOOLS:
        sys.stderr.write("使い方: " + usage)
        return 2
    env = dict(os.environ)
    if not env.get("YOMIYASU_STATE_DIR", "").strip():
        env["YOMIYASU_STATE_DIR"] = str(Path.cwd() / ".handoff" / "yomiyasu")
    env.setdefault("PYTHONIOENCODING", "utf-8")  # Windows の標準出力の文字化けを防ぐ
    command = [sys.executable, "-B", str(HERE / TOOLS[argv[0]]), *argv[1:]]
    return subprocess.call(command, env=env)  # noqa: S603 (同梱のスクリプトを、決まった引数の形で呼ぶだけ)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
