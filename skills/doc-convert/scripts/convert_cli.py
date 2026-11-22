"""文書 (Word / Excel / PowerPoint / PDF / CSV) を Markdown にするコマンド (スキル doc-convert から呼ぶ)。

  convert : Markdown に変換して、.handoff/converted/ に保存する。画面には、保存先と目次だけを出す
  info    : 変換せずに、構成 (ページ数・シート名・スライド数) だけを調べる
  check   : 設定と、PDF に必要な pypdf の確認

読んでよいファイルは、作業フォルダの中と、設定 (config.local.json) の roots の中だけ。書くのは .handoff/converted/ だけ。
元のファイルは変更しない。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import TextIO

sys.path.insert(0, str(Path(__file__).resolve().parent))

import convert_core as core  # noqa: E402

SKILL_DIR = Path(__file__).resolve().parents[1]
OUT_SUBDIR = Path(".handoff") / "converted"
RESERVED = {"CON", "PRN", "AUX", "NUL"} | {f"COM{i}" for i in range(1, 10)} | {f"LPT{i}" for i in range(1, 10)}


class ScopeError(core.ConvertError):
    pass


def load_roots(config_path: Path | None = None) -> list[str]:
    """読んでよい場所 (作業フォルダのほかに)。doc-convert の設定がなければ、file-finder の設定を使う。"""
    candidates = [config_path] if config_path else [SKILL_DIR / "config.local.json", SKILL_DIR.parent / "file-finder" / "config.local.json"]
    for candidate in candidates:
        if candidate and candidate.is_file():
            try:
                data = json.loads(candidate.read_text(encoding="utf-8-sig"))
            except (OSError, ValueError) as exc:
                raise core.ConvertError(f"設定ファイルを読めません: {candidate}: {exc}") from exc
            roots = data.get("roots", [])
            if not isinstance(roots, list) or not all(isinstance(r, str) for r in roots):
                raise core.ConvertError(f"設定ファイルの roots は、文字列の配列にしてください: {candidate}")
            return [r for r in roots if r.strip()]
    return []


def norm(path: str | Path) -> str:
    return os.path.normcase(os.path.realpath(str(path)))


def is_within(path: str, root: str) -> bool:
    path, root = norm(path), norm(root)
    return path == root or path.startswith(root.rstrip("\\/") + os.sep)


def check_input(raw: str, cwd: Path, roots: list[str]) -> Path:
    path = Path(raw)
    if not path.is_absolute():
        path = cwd / path
    if not path.exists():
        raise core.ConvertError(f"ファイルがありません: {raw}")
    allowed = [str(cwd), *roots]
    if not any(is_within(str(path), root) for root in allowed):
        hint = "スキルのフォルダに cd していたら、作業フォルダに戻り、run.bat (run.sh) をフルパスで実行し直す。" if (cwd / "scripts" / "convert_cli.py").is_file() else ""
        raise ScopeError(f"'{raw}' は、読んでよい場所の外です (作業フォルダ {cwd} と、設定の roots の中だけ)。{hint}それ以外は、回避せずに、そのまま報告してください")
    return path


def safe_name(name: str) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", name).strip(" .")
    cleaned = cleaned[:80].rstrip(" .") or "document"
    if cleaned.split(".")[0].upper() in RESERVED:
        cleaned = "_" + cleaned
    return cleaned


def unique_path(directory: Path, filename: str) -> Path:
    candidate = directory / filename
    stem, suffix = filename[: -len(".md")], ".md"
    number = 2
    while candidate.exists():
        candidate = directory / f"{stem}_{number}{suffix}"
        number += 1
    return candidate


def build_options(args: argparse.Namespace) -> core.Options:
    options = core.Options(max_rows=args.max_rows, include_hidden=args.include_hidden, pages=args.pages)
    if args.sheets:
        options.sheets = [s for s in args.sheets.split(",") if s.strip()]
    return options


def cmd_convert(args: argparse.Namespace, cwd: Path, roots: list[str], out: TextIO) -> int:
    source = check_input(args.file, cwd, roots)
    result = core.convert_file(source, build_options(args))
    directory = cwd / OUT_SUBDIR
    directory.mkdir(parents=True, exist_ok=True)
    target = unique_path(directory, safe_name(source.name) + ".md")
    header = f"<!-- 変換元: {source} / 変換: doc-convert (自動) / 元のファイルは変更していません -->\n\n"
    text = header + result.markdown
    target.write_text(text, encoding="utf-8", newline="\n")
    shown = target.relative_to(cwd).as_posix()
    out.write(f"変換しました: {shown}\n")
    out.write(f"種類: {result.kind} / {result.units:,} {result.unit_label} / {len(text.splitlines()):,} 行 / {len(text):,} 文字\n")
    headings = core.outline(text)
    if headings:
        out.write("目次 (行番号: 見出し。必要な部分だけ read の offset / limit で読む):\n")
        for number, line in headings:
            out.write(f"  {number}: {line}\n")
    for note in result.notes:
        out.write(f"注意: {note}\n")
    if len(result.markdown.strip()) == 0:
        out.write("注意: 変換結果が空です\n")
        return 4
    return 0


def cmd_info(args: argparse.Namespace, cwd: Path, roots: list[str], out: TextIO) -> int:
    source = check_input(args.file, cwd, roots)
    for line in core.inspect_file(source):
        out.write(line + "\n")
    return 0


def cmd_check(cwd: Path, roots: list[str], out: TextIO) -> int:
    out.write(f"Python: {sys.version.split()[0]}\n")
    out.write("読んでよい場所: 作業フォルダ (" + str(cwd) + ")" + "".join(f", {r}" for r in roots) + "\n")
    if not roots:
        out.write("  (roots は未設定: 作業フォルダの外のファイルは読めません。必要なら config.local.json を作る)\n")
    try:
        import pypdf  # noqa: F401
        out.write(f"pypdf: あり ({pypdf.__version__}) → PDF を変換できます\n")
        status = 0
    except ImportError:
        out.write("pypdf: なし → PDF は変換できません (Word / Excel / PowerPoint / CSV は可)\n")
        out.write("  入れ方: ハーネスのフォルダで .venv\\Scripts\\python -m pip install --require-hashes -r requirements.lock\n")
        status = 3
    out.write("対応: " + ", ".join(core.SUPPORTED) + "\n")
    out.write("未対応: " + ", ".join(core.LEGACY) + " (Office で開いて新しい形式に保存し直す)\n")
    return status


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="convert_cli", description="文書を Markdown にする (読み取り専用。書くのは .handoff/converted/ だけ)")
    sub = parser.add_subparsers(dest="command", required=True)
    convert = sub.add_parser("convert", help="Markdown にして .handoff/converted/ に保存する")
    convert.add_argument("file")
    convert.add_argument("--pages", help="PDF: 変換するページ (例: 1-5,8)")
    convert.add_argument("--sheets", help="Excel: 変換するシート名 (カンマ区切り)")
    convert.add_argument("--include-hidden", action="store_true", help="Excel: 非表示のシートも変換する")
    convert.add_argument("--max-rows", type=int, default=500, help="表 1 つあたりの最大行数 (既定 500)")
    info = sub.add_parser("info", help="構成 (ページ数・シート名・スライド数) だけを調べる")
    info.add_argument("file")
    sub.add_parser("check", help="設定と pypdf の確認")
    return parser


def main(argv: list[str] | None = None, stdout: TextIO | None = None, stderr: TextIO | None = None, cwd: Path | None = None, config_path: Path | None = None) -> int:
    out = stdout or sys.stdout
    err = stderr or sys.stderr
    if stdout is None:
        for stream in (sys.stdout, sys.stderr):
            if hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding="utf-8")  # Windows の標準出力の文字化けを防ぐ
    args = build_parser().parse_args(argv)
    work = (cwd or Path.cwd()).resolve()
    try:
        roots = load_roots(config_path)
        if args.command == "convert":
            if args.max_rows < 1 or args.max_rows > 5000:
                raise core.ConvertError("--max-rows は 1〜5000 にしてください")
            return cmd_convert(args, work, roots, out)
        if args.command == "info":
            return cmd_info(args, work, roots, out)
        return cmd_check(work, roots, out)
    except core.ConvertError as exc:
        err.write(f"エラー: {exc}\n")
        return 3 if isinstance(exc, ScopeError) else 2
    except OSError as exc:  # 他のアプリ (Word など) が開いていて読めない、書き込めない、など
        err.write(f"エラー: ファイルを読み書きできません (他のアプリで開いている可能性があります): {exc}\n")
        return 2


if __name__ == "__main__":
    sys.exit(main())
