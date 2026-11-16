"""インタビュー (計画や考えを、質問で詰める) の記録を作り、終わってよいかを検査するコマンド (スキル interview から呼ぶ)。

  init  : .handoff/interview_<話題>.md に、記録のひな形を作る
  check : 記録を検査する (読み取りだけ)。終わってよいときだけ、終了コード 0

検査すること:
  - 目的・成功の条件・やらないことが書かれているか
  - 「決まったこと」の決めた人が、ユーザーか (AI が自分で決めたことになっていないか)
  - 「未決」が残っていないか
  - 「調べた事実」に出典があるか
  - 「仮定」が残っていないか (残すなら、ユーザーが了承したと書く)
  - 最後の確認 (ユーザーが、共通の理解になったと確かめた) に印があるか
書くのは .handoff/ の中だけ。上書きはしない。
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import TextIO

TEMPLATE = """# インタビュー: {topic}

この記録が、インタビューの正本。会話が長くなったら、ここを読み直す。各回の後に更新する。

## 目的(なぜやるか。ユーザーの言葉で)

## 成功の条件(何ができたら成功か)

## やらないこと・失敗の条件

## 決まったこと(決めた人は、必ずユーザー)

<!-- 例: - D1: 対象は J1 と J2 の接合部だけ(決めた人: ユーザー、第 1 回) -->

## 未決(次の回で聞く)

<!-- 例: - Q3: 報告の形式(前提: D1) -->

## 仮定(ユーザーに確かめていないこと)

<!-- 例: - A1: 基準は第 3 版(理由: フォルダにそれしかない)。了承された仮定は「(ユーザーが了承)」を付ける -->

## 調べた事実(聞かずに調べたこと。出典つき)

<!-- 例: - F1: 許容応力は 80 N/mm²(出典: 社内設計基準_抜粋.md 3.1) -->

## 最後の確認

- [ ] ユーザーが「これで共通の理解になった」と確かめた
"""

SECTIONS = {
    "purpose": "目的", "success": "成功の条件", "nongoals": "やらないこと", "decided": "決まったこと",
    "open": "未決", "assumptions": "仮定", "facts": "調べた事実", "confirm": "最後の確認",
}


class InterviewError(Exception):
    pass


def safe_topic(topic: str) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|\x00-\x1f\s]+', "_", topic).strip("._")[:60]
    if not cleaned:
        raise InterviewError("話題が空です")
    return cleaned


def parse(text: str) -> dict[str, list[str]]:
    """見出し (## …) ごとの、中身の行 (コメントと空行を除く)。"""
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    found: dict[str, list[str]] = {}
    current = None
    for line in text.splitlines():
        if line.startswith("## "):
            current = next((key for key, word in SECTIONS.items() if line[3:].strip().startswith(word)), None)
            if current:
                found[current] = []
            continue
        if current and line.strip():
            found[current].append(line.strip())
    return found


def items(lines: list[str]) -> list[str]:
    return [re.sub(r"^[-*]\s+", "", l).strip() for l in lines if re.match(r"^[-*]\s+\S", l)]


def check(text: str) -> tuple[list[str], list[str], dict[str, int]]:
    sections = parse(text)
    problems: list[str] = []
    notes: list[str] = []
    missing_sections = [word for key, word in SECTIONS.items() if key not in sections]
    if missing_sections:
        problems.append("見出しがありません: " + "、".join(missing_sections) + " (init のひな形の見出しを残す)")
    for key in ("purpose", "success", "nongoals"):
        if key in sections and not sections[key]:
            problems.append(f"「{SECTIONS[key]}」が空です (ユーザーに聞く。決まっていないなら、未決に入れる)")
    decided = items(sections.get("decided", []))
    for item in decided:
        who = re.search(r"決めた人[:：]\s*([^、,)）]+)", item)
        if not who:
            problems.append(f"決めた人がありません: {item[:60]} (「決めた人: ユーザー」と書く)")
        elif "ユーザー" not in who.group(1):
            problems.append(f"ユーザー以外が決めています: {item[:60]} (決定はユーザーのもの。提案として未決に戻し、ユーザーに聞く)")
    open_items = items(sections.get("open", []))
    if open_items:
        problems.append(f"未決が {len(open_items)} 件あります: " + " / ".join(i[:40] for i in open_items[:5]))
    assumptions = [a for a in items(sections.get("assumptions", [])) if "ユーザーが了承" not in a]
    if assumptions:
        problems.append(f"確かめていない仮定が {len(assumptions)} 件: " + " / ".join(a[:40] for a in assumptions[:5])
                        + " (ユーザーに確かめる。了承されたら「(ユーザーが了承)」を付ける)")
    for fact in items(sections.get("facts", [])):
        if not re.search(r"出典[:：]\s*\S", fact):
            problems.append(f"出典の無い事実: {fact[:60]} (調べた場所を書く。調べていないなら、仮定に移す)")
    confirmed = any(re.match(r"^[-*]\s+\[[xX]\]", line) for line in sections.get("confirm", []))
    if not confirmed:
        notes.append("最後の確認に、まだ印がありません (未決が 0 件になったら、まとめを示し、ユーザーが確かめたら [x] にする)")
    counts = {"決まったこと": len(decided), "未決": len(open_items), "仮定": len(assumptions), "事実": len(items(sections.get("facts", [])))}
    return problems, notes, counts


def resolve(raw: str, cwd: Path) -> Path:
    path = Path(raw)
    path = path if path.is_absolute() else cwd / path
    if not path.is_file():
        raise InterviewError(f"記録がありません: {raw} (init で作る)")
    return path


def main(argv: list[str] | None = None, stdout: TextIO | None = None, stderr: TextIO | None = None, cwd: Path | None = None) -> int:
    out = stdout or sys.stdout
    err = stderr or sys.stderr
    if stdout is None:
        for stream in (sys.stdout, sys.stderr):
            if hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding="utf-8")  # Windows の標準出力の文字化けを防ぐ
    parser = argparse.ArgumentParser(prog="interview_cli", description="インタビューの記録を作り、終わってよいかを検査する")
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init", help="記録のひな形を .handoff/ に作る")
    init.add_argument("topic")
    check_p = sub.add_parser("check", help="記録を検査する (終わってよいときだけ 0)")
    check_p.add_argument("file")
    args = parser.parse_args(argv)
    work = (cwd or Path.cwd()).resolve()
    try:
        if args.command == "init":
            target = work / ".handoff" / f"interview_{safe_topic(args.topic)}.md"
            if target.exists():
                out.write(f"すでにあります: {target.relative_to(work).as_posix()} (続きから。読み直して、未決から聞く)\n")
                return 0
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(TEMPLATE.format(topic=args.topic), encoding="utf-8", newline="\n")
            out.write(f"作りました: {target.relative_to(work).as_posix()}\n")
            return 0
        path = resolve(args.file, work)
        problems, notes, counts = check(path.read_text(encoding="utf-8-sig"))
        summary = "、".join(f"{k} {v}" for k, v in counts.items())
        done = not problems and not notes
        out.write(f"記録: {path.name} ({summary})\n結果: {'終わってよい' if done else 'まだ終わらない'}\n")
        out.write("".join(f"- {p}\n" for p in problems))
        out.write("".join(f"- {n}\n" for n in notes))
        if not problems and notes:
            out.write("未決は 0 件。まとめをユーザーに示し、確認を得る\n")
        return 0 if done else 1
    except InterviewError as exc:
        err.write(f"エラー: {exc}\n")
        return 2
    except OSError as exc:
        err.write(f"エラー: {exc}\n")
        return 2


if __name__ == "__main__":
    sys.exit(main())
