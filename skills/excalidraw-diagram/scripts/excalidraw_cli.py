"""Excalidraw の図を作る・読む・点検するコマンド (スキル excalidraw-diagram から呼ぶ)。

  build    : 仕様 (JSON) から、図 (.excalidraw.md / .excalidraw) を作る。同名があれば上書きせず、_2 を付ける
  check    : 図の点検 (重なり、矢印の接続、文字のはみ出し、ID など)。読み取り専用

図を読む (画像を見なくても分かる文章にする) 機能は、スキル diagram-reader にある。
読めるのは、いまのフォルダ、または Vault (上へたどって見つかる .obsidian のあるフォルダ) の中だけ。
書き込むのは build の出力だけ (Vault の中で、ドットで始まらない場所)。削除はしない。
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

import excalidraw_check as checker  # noqa: E402
import excalidraw_core as core  # noqa: E402

MAX_FILE_BYTES = 30 * 1024 * 1024
RESERVED = {"CON", "PRN", "AUX", "NUL"} | {f"COM{i}" for i in range(1, 10)} | {f"LPT{i}" for i in range(1, 10)}


class CliError(Exception):
    pass


def find_vault(start: Path) -> Path | None:
    current = start.resolve()
    for candidate in [current, *current.parents]:
        if (candidate / ".obsidian").is_dir():
            return candidate
    return None


def is_within(path: Path, root: Path) -> bool:
    a, b = os.path.normcase(str(path)), os.path.normcase(str(root))
    return a == b or a.startswith(b.rstrip("\\/") + os.sep)


def allowed_roots(cwd: Path) -> list[Path]:
    roots = [cwd.resolve()]
    vault = find_vault(cwd)
    if vault:
        roots.append(vault.resolve())
    return roots


def resolve_input(raw: str, cwd: Path) -> Path:
    path = Path(raw)
    path = path if path.is_absolute() else cwd / path
    if not path.is_file():
        raise CliError(f"ファイルがありません: {raw}")
    resolved = path.resolve()
    if not any(is_within(resolved, root) for root in allowed_roots(cwd)):
        raise CliError(f"'{raw}' は、読んでよい場所の外です (いまのフォルダと、Vault の中だけ)。回避せずに、そのまま報告してください")
    if resolved.stat().st_size > MAX_FILE_BYTES:
        raise CliError("ファイルが大きすぎます")
    return resolved


def safe_stem(name: str) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|\x00-\x1f#^\[\]]', "_", name).strip(" .")[:80].rstrip(" .") or "diagram"
    return "_" + cleaned if cleaned.split(".")[0].upper() in RESERVED else cleaned


def check_output_path(raw: str, cwd: Path) -> Path:
    name = Path(raw).name
    ext = ".excalidraw.md" if name.endswith(".excalidraw.md") else ".excalidraw" if name.endswith(".excalidraw") else None
    if ext is None:
        raise CliError("--out は、.excalidraw.md (Obsidian) か .excalidraw で終わる名前にしてください")
    path = Path(raw)
    path = path if path.is_absolute() else cwd / path
    root = (find_vault(cwd) or cwd).resolve()
    parent = Path(os.path.realpath(path.parent))
    if not is_within(parent, root):
        raise CliError("--out は、Vault (Vault が無ければ、いまのフォルダ) の中にしてください")
    if any(part.startswith(".") for part in parent.relative_to(root).parts):
        raise CliError("--out に、ドットで始まる場所は使えません (.obsidian、.handoff など)")
    return parent / (safe_stem(name[: -len(ext)]) + ext)


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    suffix = ".excalidraw.md" if path.name.endswith(".excalidraw.md") else ".excalidraw"
    stem = path.name[: -len(suffix)]
    number = 2
    while (path.parent / f"{stem}_{number}{suffix}").exists():
        number += 1
    return path.parent / f"{stem}_{number}{suffix}"


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def cmd_build(args: argparse.Namespace, cwd: Path, out: TextIO) -> int:
    spec_path = resolve_input(args.spec, cwd)
    try:
        raw = json.loads(read_text(spec_path))
    except ValueError as exc:
        raise CliError(f"仕様の JSON を読めません: {exc}") from exc
    try:
        spec, doc = core.build_from_spec(raw)
    except core.SpecError as exc:
        raise CliError(f"仕様が正しくありません: {exc}") from exc
    errors, warns = checker.check_doc(doc)
    target = check_output_path(args.out, cwd)
    final = unique_path(target)
    markdown = final.name.endswith(".excalidraw.md")
    content = core.to_obsidian_markdown(doc) if markdown else core.to_excalidraw_json(doc)
    xs = [e["x"] for e in doc["elements"]]
    ys = [e["y"] for e in doc["elements"]]
    xe = [e["x"] + e["width"] for e in doc["elements"]]
    ye = [e["y"] + e["height"] for e in doc["elements"]]
    root = (find_vault(cwd) or cwd).resolve()
    shown = final.relative_to(root).as_posix()
    if args.dry_run:
        out.write(f"[試行] 作る図: {shown} (種類 {spec.kind} / 箱 {len(spec.nodes)} 個 / 矢印 {len(spec.edges)} 本 / 要素 {len(doc['elements'])} 個 / 大きさ {int(max(xe) - min(xs))}x{int(max(ye) - min(ys))})\n")
    else:
        final.parent.mkdir(parents=True, exist_ok=True)
        final.write_text(content, encoding="utf-8", newline="\n")
        out.write(f"作りました: {shown}\n  種類 {spec.kind} / 箱 {len(spec.nodes)} 個 / 矢印 {len(spec.edges)} 本 / 要素 {len(doc['elements'])} 個 / 大きさ {int(max(xe) - min(xs))}x{int(max(ye) - min(ys))}\n")
        if final != target:
            out.write(f"注意: 同じ名前の図があったため、'{final.name}' にしました (上書きしていません)\n")
        if markdown:
            out.write(f"ノートへの埋め込み: ![[{final.name[:-3]}]]\n")
    for line in errors:
        out.write(f"エラー: {line}\n")
    for line in warns[:20]:
        out.write(f"注意: {line}\n")
    if len(warns) > 20:
        out.write(f"注意: ほか {len(warns) - 20} 件\n")
    if args.dry_run:
        out.write("実際には何も作っていません\n")
    return 4 if errors else 0


def cmd_check(args: argparse.Namespace, cwd: Path, out: TextIO) -> int:
    path = resolve_input(args.file, cwd)
    try:
        doc = checker.load_plain(read_text(path), path.name)
    except checker.CheckError as exc:
        raise CliError(str(exc)) from exc
    errors, warns = checker.check_doc(doc)
    count = len([e for e in doc["elements"] if isinstance(e, dict) and not e.get("isDeleted")])
    out.write(f"点検: {path.name} (要素 {count} 個) — エラー {len(errors)} 件 / 注意 {len(warns)} 件\n")
    for line in errors[:30]:
        out.write(f"エラー: {line}\n")
    for line in warns[:30]:
        out.write(f"注意: {line}\n")
    if len(errors) + len(warns) > 60:
        out.write("… (多いので省略)\n")
    return 4 if errors else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="excalidraw_cli", description="Excalidraw の図を作る・読む・点検する (削除はしない)")
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build", help="仕様 (JSON) から図を作る")
    build.add_argument("spec", help="仕様の JSON ファイル (.handoff/ など)")
    build.add_argument("--out", required=True, help="作る図のパス (.excalidraw.md か .excalidraw)。同名があれば _2 を付ける")
    build.add_argument("--dry-run", action="store_true", help="作らずに、仕様の検査と点検だけする")
    check = sub.add_parser("check", help="図の点検")
    check.add_argument("file")
    return parser


def main(argv: list[str] | None = None, stdout: TextIO | None = None, stderr: TextIO | None = None, cwd: Path | None = None) -> int:
    out = stdout or sys.stdout
    err = stderr or sys.stderr
    if stdout is None:
        for stream in (sys.stdout, sys.stderr):
            if hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding="utf-8")  # Windows の標準出力の文字化けを防ぐ
    args = build_parser().parse_args(argv)
    work = (cwd or Path.cwd()).resolve()
    try:
        if args.command == "build":
            return cmd_build(args, work, out)
        return cmd_check(args, work, out)
    except CliError as exc:
        err.write(f"エラー: {exc}\n")
        return 2
    except OSError as exc:
        err.write(f"エラー: ファイルを読み書きできません (他のアプリで開いている可能性があります): {exc}\n")
        return 2


if __name__ == "__main__":
    sys.exit(main())
