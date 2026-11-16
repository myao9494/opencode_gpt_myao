"""作業フォルダの .handoff/ を掃除するコマンド (スキル handoff-clean から呼ぶ)。

  list  : 何がどれだけあるかを調べる (読み取り専用)。古いファイルの一覧つき
  sweep : 古いファイルを .handoff/_trash/<日時>/ へ「移動」する (削除はしない)

ファイルを削除する機能はない。退避したものを消すのは、人間 (エクスプローラーで _trash を削除する)。
対象は、作業フォルダ直下の .handoff/ の中だけ。_trash の中、シンボリックリンク、フォルダには触らない。
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import stat
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

BASE_NAME = ".handoff"
TRASH_NAME = "_trash"
DEFAULT_DAYS = 7
MAX_SWEEP_FILES = 1000
LIST_LINES = 30


class CleanError(Exception):
    pass


@dataclass
class Entry:
    path: Path
    rel: str  # .handoff からの相対パス (/ 区切り)
    size: int
    age_days: float


@dataclass
class Scan:
    entries: list[Entry]
    skipped_links: int
    trash_files: int
    trash_bytes: int


def find_base(cwd: Path) -> Path | None:
    base = cwd / BASE_NAME
    if not base.exists() and not base.is_symlink():
        return None
    if base.is_symlink():
        raise CleanError(f"{BASE_NAME} がシンボリックリンクです。安全のため、何もしません")
    if not base.is_dir():
        raise CleanError(f"{BASE_NAME} がフォルダではありません")
    if os.path.realpath(base) != os.path.join(os.path.realpath(cwd), BASE_NAME):
        raise CleanError(f"{BASE_NAME} が作業フォルダの外を指しています。安全のため、何もしません")
    return base


def human_size(size: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size} B"


def scan(base: Path, now: float) -> Scan:
    entries: list[Entry] = []
    skipped = 0
    trash_files = trash_bytes = 0
    for directory, dirnames, filenames in os.walk(base, followlinks=False):
        here = Path(directory)
        keep = []
        for name in dirnames:
            child = here / name
            if here == base and name == TRASH_NAME:
                for sub_dir, _, sub_files in os.walk(child, followlinks=False):
                    for sub in sub_files:
                        try:
                            trash_bytes += (Path(sub_dir) / sub).lstat().st_size
                            trash_files += 1
                        except OSError:
                            pass
            elif child.is_symlink():
                skipped += 1
            else:
                keep.append(name)
        dirnames[:] = keep
        for name in filenames:
            path = here / name
            try:
                info = path.lstat()
            except OSError:
                continue
            if stat.S_ISLNK(info.st_mode):
                skipped += 1
                continue
            if not stat.S_ISREG(info.st_mode):
                continue
            entries.append(Entry(path, path.relative_to(base).as_posix(), info.st_size, max(0.0, (now - info.st_mtime) / 86400)))
    entries.sort(key=lambda e: (-e.age_days, e.rel))
    return Scan(entries, skipped, trash_files, trash_bytes)


def gitignore_warning(cwd: Path) -> str | None:
    """git の管理下で、.handoff が .gitignore に無いときだけ警告する。"""
    if not (cwd / ".git").exists():
        return None
    ignore = cwd / ".gitignore"
    lines = []
    if ignore.is_file():
        lines = [line.strip() for line in ignore.read_text(encoding="utf-8", errors="replace").splitlines()]
    if any(line in (".handoff", ".handoff/", "/.handoff", "/.handoff/", ".handoff/*") for line in lines):
        return None
    return ".gitignore に .handoff/ が入っていません。入れてください (中身をリポジトリに入れないため)"


def select(entries: list[Entry], days: int | None) -> list[Entry]:
    return list(entries) if days is None else [e for e in entries if e.age_days >= days]


def top_folder(rel: str) -> str:
    return rel.split("/", 1)[0] if "/" in rel else "(直下)"


def cmd_list(cwd: Path, days: int, now: float, out: TextIO) -> int:
    base = find_base(cwd)
    if base is None:
        out.write(f"{BASE_NAME}/ はありません。掃除するものはありません\n")
        return 0
    result = scan(base, now)
    total = sum(e.size for e in result.entries)
    out.write(f"{BASE_NAME}: {len(result.entries)} ファイル / {human_size(total)}")
    out.write(f" (退避済み {TRASH_NAME}: {result.trash_files} ファイル / {human_size(result.trash_bytes)})\n")
    buckets = [("今日", 0, 1), ("7日未満", 1, 7), ("30日未満", 7, 30), ("30日以上", 30, float("inf"))]
    out.write("古さ: " + " / ".join(f"{label} {sum(1 for e in result.entries if low <= e.age_days < high)}" for label, low, high in buckets) + "\n")
    folders: dict[str, int] = {}
    for entry in result.entries:
        folders[top_folder(entry.rel)] = folders.get(top_folder(entry.rel), 0) + 1
    if folders:
        out.write("場所: " + " / ".join(f"{name} {count}" for name, count in sorted(folders.items())) + "\n")
    old = select(result.entries, days)
    out.write(f"{days} 日以上前のもの: {len(old)} ファイル / {human_size(sum(e.size for e in old))}\n")
    for entry in old[:LIST_LINES]:
        out.write(f"  {int(entry.age_days):>4} 日  {human_size(entry.size):>9}  {entry.rel}\n")
    if len(old) > LIST_LINES:
        out.write(f"  … ほか {len(old) - LIST_LINES} ファイル\n")
    if result.skipped_links:
        out.write(f"注意: シンボリックリンク {result.skipped_links} 個は、対象外です\n")
    warning = gitignore_warning(cwd)
    if warning:
        out.write(f"警告: {warning}\n")
    if old:
        out.write(f"掃除するには: sweep --older-than-days {days}\n")
    return 0


def unique_dir(parent: Path, name: str) -> Path:
    candidate = parent / name
    number = 2
    while candidate.exists():
        candidate = parent / f"{name}_{number}"
        number += 1
    return candidate


def cmd_sweep(cwd: Path, days: int | None, dry_run: bool, now: float, out: TextIO, stamp: str | None = None) -> int:
    base = find_base(cwd)
    if base is None:
        out.write(f"{BASE_NAME}/ はありません。掃除するものはありません\n")
        return 0
    result = scan(base, now)
    chosen = select(result.entries, days)
    label = "すべて" if days is None else f"{days} 日以上前の"
    if not chosen:
        out.write(f"{label}ファイルはありません。何も移動しませんでした\n")
        return 0
    over = max(0, len(chosen) - MAX_SWEEP_FILES)
    chosen = chosen[:MAX_SWEEP_FILES]
    size = sum(e.size for e in chosen)
    if dry_run:
        out.write(f"[試行] {label}ファイル {len(chosen)} 個 ({human_size(size)}) を {TRASH_NAME}/ へ移動します。実際には何も移動していません\n")
        for entry in chosen[:LIST_LINES]:
            out.write(f"  {int(entry.age_days):>4} 日  {human_size(entry.size):>9}  {entry.rel}\n")
        if len(chosen) > LIST_LINES:
            out.write(f"  … ほか {len(chosen) - LIST_LINES} ファイル\n")
        if over:
            out.write(f"注意: 一度に移動できるのは {MAX_SWEEP_FILES} 個までです (残り {over} 個)\n")
        return 0
    stamp = stamp or dt.datetime.fromtimestamp(now).strftime("%Y%m%d-%H%M%S")
    trash = base / TRASH_NAME
    batch = unique_dir(trash, stamp)
    moved: list[tuple[Entry, Path]] = []
    failed: list[tuple[Entry, str]] = []
    for entry in chosen:
        destination = batch / entry.rel
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                raise FileExistsError("移動先にすでにファイルがあります")
            entry.path.rename(destination)
            moved.append((entry, destination))
        except OSError as exc:
            failed.append((entry, str(exc)))
    if moved:
        manifest = trash / f"{batch.name}.manifest.txt"
        lines = [f"{entry.path}\t->\t{destination}" for entry, destination in moved]
        manifest.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    out.write(f"移動しました: {len(moved)} ファイル / {human_size(sum(e.size for e, _ in moved))} → {BASE_NAME}/{TRASH_NAME}/{batch.name}/\n")
    out.write("削除はしていません。戻すときは、そこから元の場所へ移してください。不要なら、人間が _trash を削除します\n")
    if moved:
        out.write(f"移動の記録: {BASE_NAME}/{TRASH_NAME}/{batch.name}.manifest.txt\n")
    for entry, reason in failed[:10]:
        out.write(f"移動できませんでした: {entry.rel} ({reason})\n")
    if len(failed) > 10:
        out.write(f"… ほか {len(failed) - 10} 個\n")
    if over:
        out.write(f"注意: 一度に移動できるのは {MAX_SWEEP_FILES} 個までです。残り {over} 個は、もう一度 sweep してください\n")
    return 0 if not failed else 4


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="handoff_cli", description=".handoff/ の掃除 (削除はしない。_trash へ移動するだけ)")
    sub = parser.add_subparsers(dest="command", required=True)
    listing = sub.add_parser("list", help="中身と、古いファイルの一覧 (読み取り専用)")
    listing.add_argument("--older-than-days", type=int, default=DEFAULT_DAYS, help=f"何日以上前を古いとするか (既定 {DEFAULT_DAYS})")
    sweep = sub.add_parser("sweep", help="古いファイルを _trash へ移動する")
    group = sweep.add_mutually_exclusive_group(required=True)
    group.add_argument("--older-than-days", type=int, help="この日数以上前のファイルを移動する (1 以上)")
    group.add_argument("--all", action="store_true", help="_trash 以外のすべてを移動する")
    sweep.add_argument("--dry-run", action="store_true", help="移動せずに、対象だけを表示する")
    return parser


def main(argv: list[str] | None = None, stdout: TextIO | None = None, stderr: TextIO | None = None, cwd: Path | None = None, now: float | None = None) -> int:
    out = stdout or sys.stdout
    err = stderr or sys.stderr
    if stdout is None:
        for stream in (sys.stdout, sys.stderr):
            if hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding="utf-8")  # Windows の標準出力の文字化けを防ぐ
    args = build_parser().parse_args(argv)
    work = (cwd or Path.cwd()).resolve()
    moment = time.time() if now is None else now
    try:
        if args.command == "list":
            if args.older_than_days < 0:
                raise CleanError("--older-than-days は 0 以上にしてください")
            return cmd_list(work, args.older_than_days, moment, out)
        if not args.all and args.older_than_days < 1:
            raise CleanError("--older-than-days は 1 以上にしてください (すべてを移動するときは --all)")
        return cmd_sweep(work, None if args.all else args.older_than_days, args.dry_run, moment, out)
    except CleanError as exc:
        err.write(f"エラー: {exc}\n")
        return 2


if __name__ == "__main__":
    sys.exit(main())
