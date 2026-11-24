"""ハーネス全体の整合性テスト (標準ライブラリだけ)。

ファイルの食い違い (ドキュメントと実物、エージェントとスキル、設定の雛形など) を見つけるためのもの。
ファイルを足す・直すたびに、`python run_tests.py` で一緒に動かす。
実行: python -m unittest discover -s tests -v   (リポジトリのルートから)
"""
from __future__ import annotations

import ast
import json
import os
import re
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HANDOFF_BLOCK = (ROOT / "skills" / "create-subagent" / "handoff-block.md").read_text(encoding="utf-8")

# 常に読み込まれる量の上限 (トークンの節約のため、増やすときは意識して変える)
AGENTS_MD_MAX_CHARS = 2200  # 現在は 2,166 文字 (2026-11-24 の試験で、モデルがスキルのスクリプトのソースを読んで時間を使い切ったので、「ソースは読まない」を足して 2,150 から上げた。その前は 2,113 文字 (1.1.1 で、言い回しの重複を削り、2,993 から縮めた (利用者の依頼「よりシンプルに」)。縮めたときに抜けた決まり (推測との区別、スキルの選び方、語の定義、調べられないと答える、文書の変換は自分で、見えたことだけ) を、点検で戻した。その前は 2,993 文字 (1.1.1 の通しの試験で、モデルが環境変数のキーを printf で表示し、ホームの設定を探し回ったので、「キーの値を表示しない」「関係の無い場所を探し回らない」を足して、2,950 から上げた。その前は 2,910 文字。1.1.1 で「人が出たら people の who で必ず引く。無ければ聞いて登録する」の 1 行と、「作業の依頼でも file-finder で探す。grep で代えない」を足して、2,750 から上げた。試験で、作業の依頼では探さず、資料の人を who で引かなかったため。その前は 2,727 文字 (インターネット検索は使えず、社内の Web は認証が要るので browser で読む、と書き直して、2,672 から上げた。その前は 2,672 文字 (最初に無条件でローカルを探す・不明は聞いて 99_knowledge/ に残す、の 2 行を足して、2,481 から上げた。その前は 2,481 文字 (GPT-5.4 mini 版の 1.1.0 で、サブエージェントの節を「基本は自分でやる」に縮めて、2,700 から下げた。その前は 2,661 文字 (GPT-5.4 mini 版の 1.0.0 で、「画像と PDF は read で見られる」「インターネットを使える」に書き換えて、2,550 から上げた。窓が 40 万なので、余裕はある。元のハーネスでは 2,515 文字。0.26.0 で、個人情報の行に「人物ノートの業務の項目は除く」を足し、2,500 から上げた。0.21.0 で「読んでよいか聞かない」を足した)。0.20.0 で、対応表などをプロジェクトのテンプレートへ移して小さくした (共通は、すべての作業に共通することだけ)。日本語のトークン数は、文字数の 1〜2 倍が目安
DESCRIPTIONS_MAX_CHARS = 2800  # エージェント・スキル・コマンドの description の合計 (メインで拒否したスキルは除く。現在は約 2,780 文字 (1.1.1 でスキル skill-creator を足した。その前は約 2,700 文字。1.1.1 で、子 finder・reviewer、スキル python-env・meeting-notes・obsidian-run と /obsidian-run を外し、obsidian-cli を足した)。その前は 3,057 文字。0.28.0 の memory と /remember で 3,080 に上げた。0.26.0 の people と /people で 2,980 に上げた。0.20.0 の python-coding で 2,850 に上げた。約 2,800 トークンで、窓 13 万の 2% 程度)
SAMPLE_CONFIG_ALLOWED_KEYS = {"$schema", "model", "update", "share", "instructions", "experimental", "compaction", "tool_output", "providers", "permissions"}  # OpenCode V2 の書き方。増やすときは公式ドキュメントで確かめる (instructions は 0.28.0。v2.2.6 で ~ と * の展開を確かめた。model・experimental・providers は、GPT-5.4 mini 版の 1.0.0。compaction・tool_output は 1.1.0 (総トークンを減らす)。公開スキーマと、v2.2.6 の `opencode debug config` で確かめた)
MODEL_PROVIDER, MODEL_ID = "azure", "gpt-5.4-mini"  # このハーネスが想定するモデル (Azure の GPT-5.4 mini。画像と PDF を入力できる)
V1_ONLY_KEYS = {"permission", "autoupdate", "agent", "command", "provider", "plugin"}  # V1 の旧名 (V2 の設定に混ぜない)
RESERVED = {"CON", "PRN", "AUX", "NUL"} | {f"COM{i}" for i in range(1, 10)} | {f"LPT{i}" for i in range(1, 10)}


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def split_frontmatter(text: str) -> tuple[str, str]:
    match = re.match(r"---\n(.*?)\n---\n(.*)", text, re.DOTALL)
    if not match:
        raise AssertionError("frontmatter がありません")
    return match.group(1), match.group(2)


def top_level_keys(front: str) -> dict[str, str]:
    return {m.group(1): m.group(2).strip() for m in re.finditer(r"^([A-Za-z_][A-Za-z0-9_-]*):[ \t]*(.*)$", front, re.MULTILINE)}


def indented_block(front: str, key: str) -> list[str]:
    """frontmatter の中の `  key:` の下にある、より深く字下げされた行を返す。"""
    lines = front.splitlines()
    for index, line in enumerate(lines):
        match = re.match(rf'^(\s*)"?{re.escape(key)}"?:\s*$', line)
        if match:
            base = len(match.group(1))
            block = []
            for follow in lines[index + 1:]:
                if follow.strip() and len(follow) - len(follow.lstrip()) <= base:
                    break
                block.append(follow)
            return block
    return []


def permission_rules(front: str) -> list[tuple[str, str, str]]:
    """frontmatter の `permissions:` (V2 の配列) を、(action, resource, effect) の並びで返す。順序は変えない。"""
    rules: list[tuple[str, str, str]] = []
    action = resource = None
    for line in indented_block(front, "permissions"):
        match = re.match(r'^\s*-\s*action:\s*"?([^"\s]+)"?\s*$', line)
        if match:
            action, resource = match.group(1), None
            continue
        match = re.match(r'^\s*resource:\s*(?:"(.*)"|\'(.*)\')\s*$', line)  # `"` を含むパターンは、YAML の ' で囲む
        if match:
            resource = match.group(1) if match.group(1) is not None else match.group(2)
            continue
        match = re.match(r"^\s*effect:\s*(allow|ask|deny)\s*$", line)
        if match:
            assert action is not None and resource is not None, f"permissions の書き方が崩れています: {line!r}"
            rules.append((action, resource, match.group(1)))
            action = resource = None
    return rules


def sample_rules() -> list[tuple[str, str, str]]:
    sample = json.loads(read(ROOT / "sample_opencode.json"))
    return [(r["action"], r["resource"], r["effect"]) for r in sample["permissions"]]


def agent_files() -> list[Path]:
    return sorted((ROOT / "agents").glob("*.md"))


def skill_dirs() -> list[Path]:
    return sorted(p for p in (ROOT / "skills").iterdir() if p.is_dir())


def command_files() -> list[Path]:
    return sorted((ROOT / "commands").glob("*.md"))


def repo_files() -> list[Path]:
    return [Path(dp) / f for dp, _, fs in os.walk(ROOT) for f in fs if ".git" not in Path(dp).parts and f != ".DS_Store" and "__pycache__" not in Path(dp).parts]


class AgentTests(unittest.TestCase):
    def test_エージェントの形(self) -> None:
        self.assertTrue(agent_files())
        for path in agent_files():
            front, body = split_frontmatter(read(path))
            keys = top_level_keys(front)
            self.assertTrue(keys.get("description"), f"{path.name}: description がありません")
            self.assertEqual(keys.get("mode"), "subagent", f"{path.name}: mode は subagent")
            self.assertTrue(re.fullmatch(r"\d+", keys.get("steps", "")), f"{path.name}: steps (繰り返しの上限) がありません")
            self.assertLessEqual(int(keys["steps"]), 50, f"{path.name}: steps が大きすぎます")
            self.assertTrue(re.fullmatch(r"[a-z][a-z0-9-]*", path.stem), f"{path.name}: 名前は英小文字とハイフン")
            self.assertTrue(permission_rules(front), f"{path.name}: permissions (V2 の配列) がありません")

    def test_読み取り専用の権限が_handoffにだけ書ける形(self) -> None:
        for path in agent_files():
            front, _ = split_frontmatter(read(path))
            edits = [(resource, effect) for action, resource, effect in permission_rules(front) if action == "edit"]
            # 最後に一致したルールが勝つので、「すべて拒否」が先、「.handoff だけ許可」が後
            self.assertEqual(edits, [("*", "deny"), (".handoff/**", "allow")], f"{path.name}: edit が「すべて拒否 → .handoff だけ許可」の形ではありません")

    def test_結果の返し方のブロックが原本と一致する(self) -> None:
        for path in agent_files():
            _, body = split_frontmatter(read(path))
            self.assertIn("## 結果の返し方(必ず守る)", body, path.name)
            block = body[body.index("## 結果の返し方(必ず守る)"):]
            self.assertEqual(block.strip(), HANDOFF_BLOCK.strip(), f"{path.name}: 結果の返し方が原本 (skills/create-subagent/handoff-block.md) と違います")

    def test_使うスキルとrun_batが実在する(self) -> None:
        for path in agent_files():
            front, _ = split_frontmatter(read(path))
            rules = permission_rules(front)
            for action, resource, effect in rules:
                if action == "skill" and effect == "allow":
                    self.assertTrue((ROOT / "skills" / resource / "SKILL.md").exists(), f"{path.name}: スキル {resource} がありません")
            for action, resource, _ in rules:
                if action == "shell":
                    for name, ext in re.findall(r"\*([a-z0-9-]+)\*run\.(bat|sh)", resource):
                        self.assertTrue((ROOT / "skills" / name / f"run.{ext}").exists(), f"{path.name}: skills/{name}/run.{ext} がありません")

    def test_shellは既定で禁止か_絞られている(self) -> None:
        for path in agent_files():
            front, _ = split_frontmatter(read(path))
            shells = [(resource, effect) for action, resource, effect in permission_rules(front) if action == "shell"]
            # 最後に一致したルールが勝つので、「すべて拒否」は先頭に置く (後ろに置くと、許可を打ち消してしまう)
            self.assertTrue(shells and shells[0] == ("*", "deny"), f"{path.name}: shell の先頭が「すべて拒否」ではありません")

    def test_MCPは使わない_ブラウザはスキルで操作する(self) -> None:
        # 0.30.0: 会社で MCP がうまく動かなかったので、MCP をやめた。ブラウザは、スキル browser-session (run.bat) で操作する
        self.assertFalse((ROOT / "mcp").exists(), "mcp/ は、0.30.0 で外した")
        self.assertFalse((ROOT / "docs" / "mcp.md").exists())
        sample = json.loads(read(ROOT / "sample_opencode.json"))
        self.assertNotIn("mcp", sample, "sample_opencode.json に mcp の項目を戻さない")
        for path in agent_files():
            front, _ = split_frontmatter(read(path))
            for action, _, _ in permission_rules(front):
                self.assertFalse(action.startswith("playwright_"), f"{path.name}: MCP のツールの権限 {action} が残っています")
        front, body = split_frontmatter(read(ROOT / "agents" / "browser.md"))
        self.assertIn("browser-session", front)
        self.assertNotIn("browser_navigate", body)

    def test_V1の旧名が残っていない(self) -> None:
        # V2 は、1 つのファイルの中で新旧の書き方が混ざると、旧名の項目を無視する。旧名を残さない
        for path in agent_files():
            front, _ = split_frontmatter(read(path))
            self.assertNotRegex(front, r"(?m)^permission:", f"{path.name}: V1 の permission: が残っています (permissions: の配列にする)")
            self.assertNotRegex(front, r"(?m)^\s+bash:", f"{path.name}: V1 の bash: が残っています (action: shell にする)")
        for path in command_files():
            front, _ = split_frontmatter(read(path))
            self.assertNotRegex(front, r"(?m)^subtask:", f"{path.name}: V1 の subtask: が残っています (subagent: にする)")
        sample = json.loads(read(ROOT / "sample_opencode.json"))
        self.assertFalse(set(sample) & V1_ONLY_KEYS, f"設定の雛形に V1 の旧名があります: {set(sample) & V1_ONLY_KEYS}")
        for rule in sample["permissions"]:
            self.assertNotIn(rule["action"], {"bash", "task", "write", "patch"}, f"V1 の action 名が残っています: {rule}")

    def test_子セッションで動くコマンドはsubagentを使う(self) -> None:
        for path in command_files():
            front, _ = split_frontmatter(read(path))
            keys = top_level_keys(front)
            if "subagent" in keys:
                self.assertEqual(keys["subagent"], "true", f"{path.name}: subagent は true だけを使う")
                self.assertIn("agent", keys, f"{path.name}: 子セッションで動かすなら agent を指定する")


class SkillAndCommandTests(unittest.TestCase):
    def test_スキルの形(self) -> None:
        for directory in skill_dirs():
            skill = directory / "SKILL.md"
            self.assertTrue(skill.exists(), f"{directory.name}: SKILL.md がありません")
            front, _ = split_frontmatter(read(skill))
            keys = top_level_keys(front)
            self.assertEqual(keys.get("name"), directory.name, f"{directory.name}: name がフォルダ名と違います")
            self.assertRegex(directory.name, r"^[a-z0-9]+(-[a-z0-9]+)*$")
            self.assertTrue(keys.get("description"), f"{directory.name}: description がありません")
            self.assertLessEqual(len(keys["description"]), 1024, f"{directory.name}: description が 1024 文字を超えています")

    def test_scriptsのあるスキルにはrun_batがある(self) -> None:
        for directory in skill_dirs():
            if (directory / "scripts").is_dir():
                self.assertTrue((directory / "run.bat").exists(), f"{directory.name}: run.bat がありません")
                self.assertTrue((directory / "tests").is_dir(), f"{directory.name}: tests/ がありません")

    def test_コマンドの形と参照するスキル(self) -> None:
        for path in command_files():
            front, body = split_frontmatter(read(path))
            keys = top_level_keys(front)
            self.assertTrue(keys.get("description"), f"{path.name}: description がありません")
            if "agent" in keys:
                self.assertTrue((ROOT / "agents" / f"{keys['agent']}.md").exists(), f"{path.name}: agent {keys['agent']} がありません")
            for name in re.findall(r"スキル `([a-z0-9-]+)`", body):
                self.assertTrue((ROOT / "skills" / name / "SKILL.md").exists(), f"{path.name}: スキル {name} がありません")


class ConfigTests(unittest.TestCase):
    def test_設定の雛形は想定のキーだけ(self) -> None:
        sample = json.loads(read(ROOT / "sample_opencode.json"))
        extra = set(sample) - SAMPLE_CONFIG_ALLOWED_KEYS
        self.assertFalse(extra, f"想定外のキーがあります (公式ドキュメントで確かめてから増やす): {extra}")

    def test_権限は_使い勝手優先で_確認は取り返しのつかない操作だけ(self) -> None:
        # GPT-5.4 mini 版の 1.0.0 (利用者の依頼「ローカルで使うので、セキュリティは気にしなくてよい。使い勝手を優先」):
        # 拒否 (deny) とポリシーを置かない。確認 (ask) は、取り返しのつかない shell の操作だけ (消す、未コミットの変更を捨てる、履歴を書き換える、push、ディスクの初期化)
        sample = json.loads(read(ROOT / "sample_opencode.json"))
        rules = sample_rules()
        self.assertNotIn("policies", sample, "ポリシー (上書きできない拒否) は置かない")
        self.assertFalse([r for r in rules if r[2] == "deny"], "メインに拒否のルールは置かない")
        self.assertFalse([r for r in rules if r[2] == "ask" and r[0] != "shell"], "確認は、shell の操作だけ")
        for action in ("read", "edit", "skill", "webfetch", "websearch", "external_directory", "shell"):
            self.assertIn((action, "*", "allow"), rules)
        # 最後に一致したものが勝つ。「すべて許可」が先頭で、確認はその後ろ
        shells = [(resource, effect) for action, resource, effect in rules if action == "shell"]
        self.assertEqual(shells[0], ("*", "allow"))
        self.assertTrue(all(effect == "ask" for _, effect in shells[1:]))

    def test_総トークンを減らす設定(self) -> None:
        # GPT-5.4 mini 版の 1.1.0 (利用者の依頼「総トークン数を減らす。無理にサブエージェントを使わない。重いものだけ分ける」)
        sample = json.loads(read(ROOT / "sample_opencode.json"))
        model = sample["providers"][MODEL_PROVIDER]["models"][MODEL_ID]
        # 思考の深さは low (思考も出力のトークン)。難しい作業だけ、/models で high を選ぶ
        self.assertEqual(model["settings"]["reasoningEffort"], "low")
        # 自動圧縮を早める。発動点は min(入力の上限, 窓) - buffer (v2.2.6 のコードで確認)。27.2 万 × 40% ≈ 10.9 万で圧縮する
        self.assertEqual(sample["compaction"], {"buffer": "60%"})
        self.assertRegex(sample["compaction"]["buffer"], r"^([1-9]\d?(\.\d+)?)%$", "スキーマの形 (1%〜99%)")
        # 道具の出力を短く切る (既定は 2,000 行・50 KiB。超えた分はファイルに残り、必要なら読める)
        self.assertEqual(sample["tool_output"], {"max_lines": 1000, "max_bytes": 30720})
        # 子に任せるのは重い作業だけ。軽いコマンドは、メインで動かす (子の起動には毎回、数千トークンかかる)
        for name in ("summarize", "read-diagram", "convert-doc"):
            front, _ = split_frontmatter(read(ROOT / "commands" / f"{name}.md"))
            self.assertNotIn("subagent", top_level_keys(front), f"/{name} は、メインで動かす")
        for name in ("polish", "diagram"):  # 1.1.1: /obsidian-run は、公式の Obsidian CLI (スキル obsidian-cli、メインで使う) に置き換えて削除した
            front, _ = split_frontmatter(read(ROOT / "commands" / f"{name}.md"))
            self.assertEqual(top_level_keys(front).get("subagent"), "true", f"/{name} は、重いので子で動かす")
        text = read(ROOT / "AGENTS.md")
        self.assertIn("基本は自分でやる", text)
        self.assertNotIn("3 つ以上の資料", text, "古い委任の基準が残っています")

    def test_プロフィールは毎回読まれ_Gitに入らない(self) -> None:
        # 0.28.0: memory/profile.md (会社の PC にだけ置く) を instructions で毎回読ませる
        sample = json.loads(read(ROOT / "sample_opencode.json"))
        self.assertIn("~/.config/opencode/memory/*.md", sample.get("instructions", []))
        self.assertIn("/memory/", read(ROOT / ".gitignore").splitlines(), ".gitignore に /memory/ がありません (個人の情報を Git に入れない)")
        template = read(ROOT / "templates" / "memory" / "profile.md")
        self.assertLessEqual(len(template), 2000, f"プロフィールの雛形が {len(template)} 文字です (上限 2,000。毎回読まれるため)")
        self.assertIn("2,000", read(ROOT / "skills" / "memory" / "SKILL.md"), "スキル memory に上限の文字数がありません")

    def test_雛形に秘密の値や会社ごとの値が無い(self) -> None:
        # キーは環境変数 AZURE_API_KEY、リソース名は AZURE_RESOURCE_NAME で渡す。デプロイ名 (modelID) を変えるときは、Git に入らない opencode.json に書く
        text = read(ROOT / "sample_opencode.json")
        self.assertNotRegex(text, r"(?i)apikey|api_key|password|secret|token|sk-[A-Za-z0-9]{10,}")
        self.assertNotRegex(text, r"(?i)resourceName|baseURL|modelID|\.azure\.com|\{env:|\{file:", "リソース名・接続先・デプロイ名は、雛形に書かない")
        self.assertNotIn('"provider"', text)

    def test_モデルはAzureのGPT_5_4_miniで_画像を入力できる(self) -> None:
        sample = json.loads(read(ROOT / "sample_opencode.json"))
        self.assertEqual(sample["model"], f"{MODEL_PROVIDER}/{MODEL_ID}")
        self.assertEqual(list(sample["providers"]), [MODEL_PROVIDER], "雛形に書くプロバイダーは、Azure だけ")
        self.assertEqual(set(sample["providers"][MODEL_PROVIDER]), {"models"}, "プロバイダーには、モデルの定義だけを書く (接続先は環境変数)")
        model = sample["providers"][MODEL_PROVIDER]["models"][MODEL_ID]
        # 画像と PDF を入力できる前提 (AGENTS.md とスキルが、read で画像を見る手順を書いている)
        self.assertEqual(model["capabilities"]["input"], ["text", "image", "pdf"])
        # limit は必ず書く (省略すると、カタログに無いモデルは 20 万・3.2 万と仮定される)。値は models.dev の azure/gpt-5.4-mini (2026-11-16 に確認)
        self.assertEqual(model["limit"], {"context": 400000, "input": 272000, "output": 128000})
        self.assertIn(model["settings"]["reasoningEffort"], {"low", "medium", "high"})
        # 画像を見られる、と指示にも書いてある (設定と指示が食い違うと、画像を開かない・開けないのに開こうとする)
        self.assertRegex(read(ROOT / "AGENTS.md"), r"画像.{0,40}`read` で開いて見られる")
        for path in (ROOT / "AGENTS.md", *agent_files(), *[d / "SKILL.md" for d in skill_dirs() if d.name not in VENDORED_SKILLS], *command_files()):
            self.assertNotRegex(read(path), r"画像(を|は|として)見られない|画像を見なくても", f"{path.relative_to(ROOT)}: 画像を見られない前提の文が残っています")

    def test_会社ごとの設定はリポジトリに入れない(self) -> None:
        # 置いてあってよい (会社の PC と、リンクで試す Mac では、ここに置く) が、Git の管理には入れない
        import subprocess
        found = [p for p in repo_files() if "node_modules" not in p.parts and p.name in ("opencode.json", "opencode.jsonc", "config.local.json")]
        for path in found:
            ignored = subprocess.run(["git", "check-ignore", "-q", str(path)], cwd=ROOT, capture_output=True)
            if ignored.returncode == 128:
                self.skipTest("git が使えない (Git の管理下ではない)")
            self.assertEqual(ignored.returncode, 0, f"{path.relative_to(ROOT)} は会社側だけに置くファイルです。Git の管理から外してください (.gitignore)")
        tracked = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
        if tracked.returncode == 0:
            for forbidden in ("opencode.json", "opencode.jsonc", "config.local.json"):
                self.assertFalse([l for l in tracked.stdout.splitlines() if l.rsplit("/", 1)[-1] == forbidden], f"{forbidden} が Git の管理に入っています")
        ignore = read(ROOT / ".gitignore")
        for needed in ("opencode.json", "*.local.json", ".venv/"):
            self.assertIn(needed, ignore, f".gitignore に {needed} がありません")

    def test_設定の雛形のスキルのパターンは実在する(self) -> None:
        # 0.31.0: スキルの書き込み (Outlook、タスク、ブラウザの送信) の確認と、試行 (--dry-run) の許可は外した。shell は既定で許可。残るスキルのパターンは、実在を確かめるだけ
        for pattern, value in [(resource, effect) for action, resource, effect in sample_rules() if action == "shell"]:
            if "run.bat" not in pattern and "run.sh" not in pattern:
                continue
            name = re.search(r"\*([a-z0-9-]+)\*run\.(bat|sh)", pattern)
            self.assertTrue(name and (ROOT / "skills" / name.group(1) / f"run.{name.group(2)}").exists(), f"{pattern}: 対応するスキルの run.bat / run.sh がありません")
            self.assertNotEqual(value, "ask", f"{pattern}: スキルのコマンドは、確認なしで動かす (0.31.0)")

    def test_掃除のコマンドはサブエージェントに持たせない(self) -> None:
        # .handoff の掃除 (sweep) は、人間の承認を得てメインが行う。子が勝手に動かせないようにする
        for path in agent_files():
            self.assertNotIn("handoff-clean", read(path), f"{path.name}: handoff-clean を使わせないでください")

    def test_requirements_inの全パッケージがlockに固定されている(self) -> None:
        wanted = [re.match(r"[A-Za-z0-9_.-]+", line).group(0).lower().replace("_", "-") for line in read(ROOT / "requirements.in").splitlines() if line.strip() and not line.startswith("#")]
        pinned = {name.lower().replace("_", "-") for name in re.findall(r"(?m)^([A-Za-z0-9_.-]+)==", read(ROOT / "requirements.lock"))}
        self.assertTrue(wanted)
        self.assertFalse(set(wanted) - pinned, f"requirements.lock に固定されていません: {set(wanted) - pinned}")

    def test_AGENTS_mdの大きさ(self) -> None:
        size = len(read(ROOT / "AGENTS.md"))
        self.assertLessEqual(size, AGENTS_MD_MAX_CHARS, f"AGENTS.md が {size} 文字です (上限 {AGENTS_MD_MAX_CHARS})。毎回読み込まれるので、増やすときは意識して上限を変える")

    def test_テンプレートの対応表は実在のスキルとエージェントを指す(self) -> None:
        # 0.17.0: 「会話でスキルやエージェントを使ってくれない」への対策。0.20.0 で、共通 AGENTS.md から、一般資料と Obsidian のテンプレートへ移した
        # (コーディングでは、社内の資料の検索より、コーディングのスキルを使う)。表の名前が古くなると、モデルが探して迷う
        names = {p.stem for p in agent_files()} | {d.name for d in skill_dirs()}
        for path in (ROOT / "templates" / "project" / "document" / "AGENTS.md", ROOT / "templates" / "obsidian" / "AGENTS.md"):
            section = read(path).split("## スキルとエージェントを先に使う", 1)[1].split("\n## ", 1)[0]
            self.assertIn("迷ったら使う", section, path)
            self.assertRegex(section, r"答える・作業する前にスキル `file-finder`")  # 1.1.0: 総トークンを減らすため、探すのはメインがスキルで行う (1.1.1: 作業の依頼でも)
            for name in re.findall(r"`([a-z][a-z-]+)`", section):
                if name not in ("skill", "content", "who"):  # 道具の名前と、スキルのサブコマンド (1.1.1)
                    self.assertIn(name, names, f"{path.relative_to(ROOT)} の対応表の {name} が、エージェントにもスキルにもありません")
        self.assertNotIn("## スキルとエージェントを先に使う", read(ROOT / "templates" / "project" / "coding" / "AGENTS.md"))

    def test_コーディングのテンプレートはpython_codingを必ず読ませる(self) -> None:
        text = read(ROOT / "templates" / "project" / "coding" / "AGENTS.md")
        self.assertRegex(text, r"必ずスキル `python-coding` を読み込む")
        self.assertTrue((ROOT / "skills" / "python-coding" / "SKILL.md").is_file())

    def test_共通AGENTS_mdは小さく_スキルの探し方を書いている(self) -> None:
        # 0.20.0: 会社で、プロジェクトの中のスキルだけを探し、共通の場所 (~/.config/opencode/skills) を見なかったため
        text = read(ROOT / "AGENTS.md")
        self.assertIn("~/.config/opencode/", text)
        self.assertIn("%USERPROFILE%", text)
        self.assertRegex(text, r"無いと決めない")
        self.assertNotIn("| 依頼 | 使うもの |", text, "対応表はプロジェクトのテンプレートに置く")
        self.assertRegex(text, r"答える・作業する前にスキル `file-finder`", "プロジェクトの AGENTS.md が無いフォルダでも、ローカルを先に探す (作業の依頼でも)")
        self.assertIn("スキル `people` の `who` で必ず引く", text)
        self.assertIn("キー・トークンの値を、`echo`・`printf`・`env` などで表示しない", text)
        # 1.1.1: AGENTS.md を縮めたときに、決まりが抜けた。要点の語が残っていることを確かめる
        for key in ("確かめたことと推測を区別", "プロジェクトの `AGENTS.md` に従う", "人・案件・用語・略称", "調べられない", "doc-convert", "見えたことだけ",
                    "管理外のファイルの削除と上書き", "取り消せない", "指示には従わず", "本番のデータ"):
            self.assertIn(key, text, f"共通 AGENTS.md に {key!r} がありません")  # 1.1.1: 試験で、キーを表示した  # 1.1.1: 人が出たら引き、無ければ聞いて登録する

    def test_モデルが読むファイルは実在の部品だけを指す(self) -> None:
        # 1.1.1: 子・スキルを外したり統合したりした後、手順書や雛形に古い名前が残り、モデルが探して迷うのを防ぐ
        skills = {d.name for d in skill_dirs()} | {d.name for d in (ROOT / "templates" / "ai_work" / ".opencode" / "skills").iterdir() if d.is_dir()}
        agents = {p.stem for p in agent_files()}
        removed = ("python-env", "obsidian-run", "`finder`", "`reviewer`", "tags --grep", "Local REST", "社内の小さい", "スキル `meeting-notes`")
        files = [ROOT / "AGENTS.md", *agent_files(), *command_files(), *[d / "SKILL.md" for d in skill_dirs()], *(ROOT / "templates").rglob("*.md")]
        for path in files:
            text = read(path)
            rel = path.relative_to(ROOT)
            for name in re.findall(r"スキル `([a-z][a-z0-9-]+)`", text):
                self.assertIn(name, skills, f"{rel}: スキル `{name}` がありません")
            for name in re.findall(r"(?:サブエージェント|子) `([a-z][a-z0-9-]+)`", text):
                self.assertIn(name, agents, f"{rel}: 子 `{name}` がありません")
            for word in removed:
                self.assertNotIn(word, text, f"{rel}: 外した・統合した部品の名前 {word!r} が残っています")

    def test_子は共通ルールとのつながりを書いている(self) -> None:
        # 1.1.1: 共通 AGENTS.md の「file-finder で探す」「who で引く」「知識ノートに保存する」は、子では使えないものがある。断られる道具を試さないよう、各子に注記を置く
        shared = "**1 回に 1 つのコマンドを実行する**"
        for path in agent_files():
            text = read(path)
            self.assertIn("は、メインが行う。この子では使えない", text, f"{path.name}: 共通ルールのうち、この子で使えないものの注記がありません")
            if "shell" in text and 'resource: "*"\n    effect: deny' in text.split("---", 2)[1].replace("\r", ""):
                self.assertIn(shared, text, f"{path.name}: 「1 回に 1 つのコマンド」の段落がありません (つなぐと全体が拒否される)")

    def test_節の参照は実在の見出しを指す(self) -> None:
        # 1.1.1: 「スキル `x` の「節」」「下の「節」」が、実在する見出しや太字を指すこと (節の名前を変えたときに、参照が切れないように)
        def anchors(path: Path) -> list[str]:
            text = read(path)
            return [re.sub(r"[`*]", "", h) for h in re.findall(r"^#{1,4} (.+)$", text, re.M) + re.findall(r"\*\*([^*]+)\*\*", text)]
        skill_md = {d.name: d / "SKILL.md" for d in skill_dirs()}
        files = [ROOT / "AGENTS.md", *agent_files(), *command_files(), *skill_md.values(), *(ROOT / "templates").rglob("*.md")]
        for path in files:
            text = read(path)
            for name, section in re.findall(r"`([a-z][a-z0-9-]+)`(?: の|の)「([^」]{2,40})」", text):
                if name in skill_md:
                    target = re.sub(r"[`*]", "", section)
                    self.assertTrue(any(target in a for a in anchors(skill_md[name])), f"{path.relative_to(ROOT)}: `{name}` の「{section}」がありません")
            own = anchors(path)
            for section in re.findall(r"(?:下|上)の「([^」]{2,40})」", text):
                target = re.sub(r"[`*]", "", section)
                self.assertTrue(any(target in a for a in own), f"{path.relative_to(ROOT)}: 「{section}」がありません")

    def test_文書の節と版の参照は実在する(self) -> None:
        # 1.1.1: クイックスタートの節を足して番号がずれたときなどに、参照が切れないようにする
        def heads(path: Path, pattern: str) -> set[str]:
            return set(re.findall(pattern, read(path), re.M))
        targets = {
            r"README(?:\.md)?(?:\]\([^)]*\))?\s*の\s*(6\.\d+)": heads(ROOT / "README.md", r"^### (6\.\d+) "),
            r"クイックスタート(?:\.md)?(?:\]\([^)]*\))?\s*の\s*(\d+(?:\.\d+)?)": heads(ROOT / "クイックスタート.md", r"^## (\d+)\. ") | heads(ROOT / "クイックスタート.md", r"^### (\d+\.\d+) "),
            r"company-checklist(?:\.md)?(?:\]\([^)]*\))?\s*の\s*(\d+)": heads(ROOT / "docs" / "company-checklist.md", r"^## (\d+)\. "),
            r"obsidian\.md(?:\]\([^)]*\))?\s*の\s*(\d+)": heads(ROOT / "docs" / "obsidian.md", r"^## (\d+)\. "),
            r"CHANGELOG(?:\.md)?(?:\]\([^)]*\))?\s*の\s*(\d+\.\d+\.\d+)": heads(ROOT / "CHANGELOG.md", r"^## (\d+\.\d+\.\d+) "),
        }
        for path in [p for p in ROOT.rglob("*.md") if ".git" not in p.parts and "references" not in p.parts]:
            text = read(path)
            for pattern, found in targets.items():
                for ref in re.findall(pattern, text):
                    self.assertIn(ref, found, f"{path.relative_to(ROOT)}: 参照先 {ref} がありません ({pattern[:20]}…)")

    def test_AGENTS_mdはCLAUDE_mdを最初に読むと書いている(self) -> None:
        # 以前 Claude Code で開発したリポジトリ用。OpenCode は AGENTS.md があると CLAUDE.md を読み込まないことがある
        text = read(ROOT / "AGENTS.md")
        self.assertRegex(text, r"`CLAUDE\.md`.{0,80}最初に読", "共通 AGENTS.md に、CLAUDE.md を最初に読む規則がありません")
        self.assertIn("共通ルールを優先", text, "CLAUDE.md と共通ルールが食い違うときの優先順位がありません")

    def test_常に見える説明文の合計(self) -> None:
        # メインで拒否したスキルは、メインの一覧に出ない (OpenCode v2.2.6 で確かめた) ので数えない (今は無い)
        hidden = {resource for action, resource, effect in sample_rules() if action == "skill" and effect == "deny" and "*" not in resource}
        total = 0
        for path in agent_files() + [d / "SKILL.md" for d in skill_dirs() if d.name not in hidden] + command_files():
            front, _ = split_frontmatter(read(path))
            total += len(top_level_keys(front).get("description", ""))
        self.assertLessEqual(total, DESCRIPTIONS_MAX_CHARS, f"description の合計が {total} 文字です (上限 {DESCRIPTIONS_MAX_CHARS})")

    def test_requirements_lockはハッシュ付きで接続先を含まない(self) -> None:
        lock = read(ROOT / "requirements.lock")
        self.assertNotRegex(lock, r"(?m)^\s*--(extra-)?index-url")
        pins = re.findall(r"(?m)^([A-Za-z0-9_.-]+)==", lock)
        self.assertTrue(pins)
        for name in pins:
            block = re.search(rf"(?ms)^{re.escape(name)}==.*?(?=^\S|\Z)", lock).group(0)
            self.assertIn("--hash=sha256:", block, f"{name} にハッシュがありません")


# ---------------------------------------------------------------------------
# 権限の判定を再現して、実際のコマンドで確かめる
# ---------------------------------------------------------------------------
# OpenCode v2.2.6 の判定 (2026-11-07 に、Mac のバイナリの中のコードを読んで写した。公式の permissions ページとも一致):
#   - ルールは「基本 → グローバル (opencode.json) → エージェント」の順に連結し、最後に一致したものが勝つ。一致しなければ ask
#   - パターンは全体一致。`*` は `/` や改行を含む任意の文字列、`?` は 1 文字。`\` は `/` に直してから比べる
#   - パターンが ` *` で終わるときは、引数なしにも一致する
#   - shell は、複合コマンド (&&、;、|) を部分ごとに判定する。リダイレクト (`>`) は、その部分の文字列に含まれたまま
# OpenCode の版を上げたら、公式の permissions ページと見比べて、ここを直す。
BASE_RULES = [("*", "*", "allow"), ("read", "*.env", "ask"), ("read", "*.env.*", "ask"), ("read", "*.env.example", "allow")]


def opencode_match(value: str, pattern: str) -> bool:
    regex = re.sub(r"[.+^${}()|\[\]\\]", lambda m: "\\" + m.group(0), pattern.replace("\\", "/")).replace("*", ".*").replace("?", ".")
    if regex.endswith(" .*"):
        regex = regex[:-3] + "( .*)?"
    return re.fullmatch(regex, value.replace("\\", "/"), re.DOTALL) is not None


HOME = "/Users/u"  # 判定の例で使うホーム (~ の展開先)


def expand_home(action: str, pattern: str) -> str:
    """OpenCode は、external_directory・read・edit のパターンの ~ を、設定の読み込み時にホームへ展開する。"""
    if action in ("external_directory", "read", "edit") and (pattern == "~" or pattern.startswith("~/")):
        return HOME + pattern[1:]
    return pattern


def decide(agent: str | None, action: str, resource: str) -> str:
    rules = [(a, expand_home(a, r), e) for a, r, e in BASE_RULES + sample_rules()]
    if agent:
        front, _ = split_frontmatter(read(ROOT / "agents" / f"{agent}.md"))
        rules += permission_rules(front)
    effect = "ask"
    for rule_action, rule_resource, rule_effect in rules:
        if opencode_match(action, rule_action) and opencode_match(resource, rule_resource):
            effect = rule_effect
    return effect


SKILLS_WIN = r"C:\Users\u\.config\opencode\skills"
SKILLS_MAC = "/Users/u/.config/opencode/skills"


class PermissionBehaviorTests(unittest.TestCase):
    """権限の「形」ではなく、「このコマンドが許可・確認・拒否のどれになるか」を確かめる。"""

    def check(self, agent: str | None, cases: list[tuple[str, str]], action: str = "shell") -> None:
        for resource, expected in cases:
            with self.subTest(agent=agent or "メイン", resource=resource):
                self.assertEqual(decide(agent, action, resource), expected, f"{agent or 'メイン'}: {resource}")

    def test_判定の写しが公式の例どおり(self) -> None:
        self.assertTrue(opencode_match("git status", "git status *"))  # 末尾の ` *` は、引数なしにも一致
        self.assertTrue(opencode_match("git status --short", "git status *"))
        self.assertFalse(opencode_match("git statusx", "git status *"))
        self.assertTrue(opencode_match(r"C:\a\b.md", "C:/a/*"))  # `\` は `/` として比べる
        self.assertTrue(opencode_match("src/a/b.ts", "src/*"))  # `*` は `/` を含む
        self.assertFalse(opencode_match("xgit status", "git status*"))  # 全体一致

    def test_メイン(self) -> None:
        w = SKILLS_WIN
        self.check(None, [
            # 0.16.0: 既定で許可 (Git で戻せる)。書き込み・スクリプトの実行・リダイレクトも確認しない
            ("git status", "allow"), ("git diff --stat", "allow"), ("dir", "allow"), ("python -m pytest", "allow"), ("mkdir -p .handoff", "allow"),
            ("git add -A", "allow"), ('git commit -m "x"', "allow"), ("move a.md b.md", "allow"), (r"type nul > src\main.py", "allow"),
            (rf"{w}\vault-notes\run.bat today", "allow"),
            (rf"{w}\diagram-reader\run.bat describe 図.excalidraw.md", "allow"),
            (rf"{w}\vault-notes\run.bat new-note --title a --body-file .handoff/d.md --dry-run", "allow"),
            (f"{SKILLS_MAC}/project-init/run.sh plan /Users/u/work/p", "allow"),
            # 0.13.0: 計算の検査、文書の検査とつなぎ (.handoff の中だけに書く)、レビュー待ちの一覧、AI が直した印 (未レビューにするだけ)
            (rf"{w}\calc-check\run.bat check .handoff\calc_a.json", "allow"), (f"{SKILLS_MAC}/calc-check/run.sh template", "allow"),
            (rf"{w}\doc-writing\run.bat init 手順書", "allow"), (rf"{w}\doc-writing\run.bat join .handoff\doc_手順書", "allow"),
            (rf"{w}\vault-notes\run.bat unreviewed", "allow"), (rf"{w}\vault-notes\run.bat mark-edit 01_data\a.md", "allow"),
            # 0.25.0: 関連ノートの検索と既存のタグの一覧 (読み取りだけ)
            (rf"{w}\vault-notes\run.bat related 銘柄分析 優待", "allow"), (rf"{w}\vault-notes\run.bat tags --grep 株", "allow"),
            (rf"{w}\calc-check\run.bat check a.json > b.md", "allow"),
            (rf"{w}\browser-session\run.bat open https://intranet/a", "allow"), (rf"{w}\browser-session\run.bat snapshot", "allow"),
            (rf"{w}\browser-session\run.bat run .handoff\browser\01.py", "allow"), (rf"{w}\browser-session\run.bat close", "allow"),
            (rf"{w}\vault-notes\run.bat new-note --title a --body-file .handoff/d.md", "allow"),
            (rf"{w}\excalidraw-diagram\run.bat build s.json --out 図.excalidraw.md", "allow"),
            (rf"{w}\handoff-clean\run.bat sweep --older-than-days 7", "allow"), (rf"{w}\project-init\run.bat apply C:\Work\p", "allow"),
            (rf'"{w}\vault-notes\run.bat" today', "allow"),
            # 確認: Git で戻せない (消す、未コミットの変更を捨てる、push)、パッケージを入れる、送信を許すブラウザの実行
            ("rm -rf build", "ask"), ("del /q a.txt", "ask"), ("rd /s /q build", "ask"), ("Remove-Item -Recurse build", "ask"),
            ("git push origin main", "ask"), ("git reset --hard HEAD~1", "ask"), ("git clean -fd", "ask"), ("git checkout -- src/a.py", "ask"),
            ("git checkout .", "ask"), ("git restore src/a.py", "ask"), ("git stash drop", "ask"), ("git branch -D topic", "ask"),
            (r".venv\Scripts\python -m pip install pypdf==7.0.0", "allow"), ("pip uninstall pypdf", "allow"),
            (rf"{w}\browser-session\run.bat run a.py --allow-submit", "allow"),
            ("git checkout main", "allow"), ("git branch topic", "allow"), ("git stash", "allow"),
            # GPT-5.4 mini 版の 1.0.0: 拒否は無い。強制 push とディスクの操作は確認、外への通信と npm は許可
            ("git push --force origin main", "ask"), ("git push -f", "ask"), ("npx some-tool", "allow"), ("npm install left-pad", "allow"),
            ("curl http://example.com", "allow"), ("Invoke-WebRequest http://example.com", "allow"), ("format D:", "ask"),
        ])

    def test_外への通信とパッケージの導入は_確認なしで動く(self) -> None:
        # GPT-5.4 mini 版の 1.0.0: Web の取得・検索、curl、パッケージの導入、作業フォルダの外、.env は、確認しない
        self.check(None, [("https://example.com/a", "allow")], action="webfetch")
        self.check(None, [("opencode azure", "allow")], action="websearch")
        self.check(None, [
            ("curl http://example.com", "allow"), ("wget http://example.com/a.zip", "allow"), ("Invoke-WebRequest http://example.com", "allow"),
            ("npm install left-pad", "allow"), ("npx some-tool", "allow"), ("pnpm add left-pad", "allow"), ("bunx some-tool", "allow"), ("brew install jq", "allow"),
            ("pip install requests", "allow"), ("uv add requests", "allow"), ("ssh host", "allow"), ("git clone https://example.com/a.git", "allow"), ("git pull", "allow"),
            ("gh pr view 1", "allow"), ("docker compose up -d", "allow"), ("make test", "allow"),
        ])
        # 確認が残るもの: 取り返しのつかない操作
        self.check(None, [("rm -rf build", "ask"), ("git push origin main", "ask"), ("git push --force origin main", "ask"), ("git reset --hard HEAD~1", "ask"), ("diskpart", "ask")])
        # 1.1.1: 公式の Obsidian CLI。整理 (名前の変更・移動) と調べるのは確認なし。削除だけ確認 (手順書でも使わない)
        self.check(None, [('obsidian move file="報告書" to=01_data/2026', "allow"), ('obsidian rename file="報告書" name="報告書_改"', "allow"),
                          ("obsidian unresolved total", "allow"), ('obsidian vault="仕事" backlinks file="報告書"', "allow"),
                          ('obsidian delete file="報告書"', "ask"), ('obsidian vault="仕事" delete path="a.md"', "ask"), ("obsidian delete", "ask")])
        # 画像と PDF は、read で開ける
        self.check(None, [("docs/図.png", "allow"), (".handoff/browser/画面.png", "allow"), ("資料/仕様書.pdf", "allow")], action="read")

    def test_yomiyasuはメインとpolishが使える(self) -> None:
        # 0.15.0: 手順書を要約版 (約 4,700 文字) にしたので、短い文はメインでも推敲する。長い文書は polish に
        # 0.16.0: スキルの読み込みは、メインでは確認しない (明示的に許可。会社の既定が確認でも効くように)
        self.check(None, [("yomiyasu", "allow"), ("vault-notes", "allow"), ("browser-session", "allow")], action="skill")
        self.assertIn(("skill", "*", "allow"), sample_rules())
        self.check("polish", [("yomiyasu", "allow"), ("vault-notes", "deny")], action="skill")
        y = rf"{SKILLS_WIN}\yomiyasu\run.bat"
        self.check(None, [(f"{y} lint .handoff/a.md", "allow"), (f"{y} check run --source .handoff/a.md --final .handoff/b.md", "allow"),
                          (f"{y} check verify --final .handoff/b.md", "allow"), (f"{SKILLS_MAC}/yomiyasu/run.sh diff a.md b.md", "allow"),
                          (f'"{y}" lint a.md', "allow"), (f"{y} lint a.md > b.txt", "allow")])
        self.check("polish", [(f"{y} check run --source .handoff/a.md --final .handoff/polish_a.md", "allow"), (f"{SKILLS_MAC}/yomiyasu/run.sh lint a.md", "allow"),
                              (f"{y} lint a.md > b.txt", "deny"), ("python3 x.py", "deny")])

    def test_メインのread_edit_MCP(self) -> None:
        self.check(None, [(".env", "allow"), ("config/.env.local", "allow"), (".env.example", "allow"), ("src/a.py", "allow")], action="read")  # GPT-5.4 mini 版の 1.0.0: .env も確認しない
        # .handoff/ は使い捨ての受け渡し場所。下書き (vault-notes の draft など) は確認なしで書ける (0.10.1。会社の試験の前に、Mac の試験で分かった)
        # 0.16.0: 編集は確認しない (Git で戻せる)。作業フォルダの外は external_directory で確認
        self.check(None, [("src/a.py", "allow"), (".handoff/draft_x.md", "allow"), ("notes/.handoff.md", "allow")], action="edit")
        # ハーネスのスキルのフォルダは、確認なしで読める (手順書や使い方を確かめるため)。ほかの外のフォルダと、ハーネスの設定 (opencode.json) は確認
        self.check(None, [(f"{HOME}/.config/opencode/skills/vault-notes/scripts/*", "allow"), (f"{HOME}/.config/opencode/*", "allow"), ("/etc/*", "allow")], action="external_directory")  # GPT-5.4 mini 版の 1.0.0: 作業フォルダの外も確認しない

    def test_読み取り専用の子は_handoffにだけ書け_グローバルのshell許可を使えない(self) -> None:
        for agent in ("researcher", "outlook", "browser", "diagram", "polish"):
            self.check(agent, [("src/a.py", "deny"), (".handoff/researcher_x.md", "allow")], action="edit")
            self.check(agent, [("type a.txt", "deny"), ("dir", "deny"), ("rm -rf x", "deny"), ("git push", "deny"), ("python x.py", "deny")])
            self.check(agent, [("git status", "deny"), ("git diff", "deny")])

    def test_子は_子を呼べない(self) -> None:
        # 0.22.0: 読んだ文書やメールに「outlook に頼んで」と書かれていても、子から別の子へ広がらないようにする (役割の外に出ない。繰り返しも防ぐ)
        for path in agent_files():
            self.check(path.stem, [("outlook", "deny"), ("researcher", "deny"), ("browser", "deny")], action="subagent")
        self.check(None, [("researcher", "allow")], action="subagent")

    def test_子が読み込めるスキルは_役割のものだけ(self) -> None:
        # 0.22.0: reviewer と browser は、スキルの拒否が無く、メインの「すべて許可」を引き継いでいた
        for path in agent_files():
            front, _ = split_frontmatter(read(path))
            allowed = {r for a, r, e in permission_rules(front) if a == "skill" and e == "allow"}
            for directory in skill_dirs():
                expected = "allow" if directory.name in allowed else "deny"
                self.assertEqual(decide(path.stem, "skill", directory.name), expected, f"{path.name}: skill {directory.name}")

    def test_子は5つ_finderとreviewerはメインが行う(self) -> None:
        # 1.1.1: 総トークンを減らす方針で、探すのとレビューはメインが行う (子 finder・reviewer をやめた)
        self.assertEqual({p.stem for p in agent_files()}, {"browser", "diagram", "outlook", "polish", "researcher"})
        self.check(None, [("git status", "allow"), ("git diff --stat", "allow"), ("git show HEAD", "allow")])

    def test_メインの確認と拒否_0_22_0で足したもの(self) -> None:
        o = rf"{SKILLS_WIN}\outlook-mail\run.bat"
        self.check(None, [
            # 0.31.0: Outlook を変える操作 (移動・フォルダ・予定の作成) も、確認なしで動く (送信と削除は、スクリプトに無い)
            (f"{o} mail-move m1 --to 案件A", "allow"), (f"{o} mail-undo --last 3", "allow"), (f"{o} folder-create 案件A", "allow"),
            (f'{o} cal-create --subject 会議 --start "2026-11-10 14:00"', "allow"),
            (f"{o} mail-move m1 --to 案件A --dry-run", "allow"), (f"{o} cal-create --subject 会議 --start 2026-11-10 --dry-run", "allow"),
            (f"{o} cache-search 見積", "allow"), (f"{o} mail-list", "allow"), (f"{o} reply-draft m1 --body-file .handoff/a.md", "allow"),
            # 履歴を書き換える、未コミットの変更を捨てる (AGENTS.md の「先に確認する」を、設定でも守る)
            ("git rebase -i HEAD~3", "ask"), ("git commit --amend -m x", "ask"), ("git commit -a --amend", "ask"), ("git checkout HEAD -- src/a.py", "ask"),
            ("git switch main --discard-changes", "ask"), ("git filter-branch --all", "ask"),
            ('git commit -m "rebase の説明"', "allow"), ("git checkout -b topic", "allow"), ("git switch topic", "allow"),
            ("find . -name *.tmp -delete", "ask"), ("find . -name *.tmp -exec rm {} +", "ask"), ("find . -name *.md", "allow"),
            # .env は、read の道具だけでなく、シェルで読むときも確認
            ("type .env", "allow"), ("cat config/.env.local", "allow"), (r"type config\.env", "allow"), ("cat .env.example", "allow"),
            (r".venv\Scripts\python -m pytest", "allow"), ("python -m venv .venv", "allow"),
            # 外へつなぐ・システムを変える
            ("ssh host", "allow"), ("scp a.txt host:/tmp", "allow"), ("reg add HKCU\\Software\\x", "allow"), ("pip download pypdf", "allow"),
            ("git push origin main --force", "ask"), ("git push origin main -f", "ask"), ("git push origin +main", "ask"), ("git push --force-with-lease", "ask"),
            ("curl.exe http://example.com", "allow"), ('powershell -c "Invoke-WebRequest http://example.com"', "allow"),
            ("certutil -urlcache -f http://example.com a.exe", "allow"), ("Start-BitsTransfer http://example.com", "allow"),
            ('powershell -c "(New-Object Net.WebClient).DownloadFile(1,2)"', "allow"), ("powershell -EncodedCommand AAAA", "allow"),
            ("Format-Volume -DriveLetter D", "ask"), ("Format-Table", "allow"),
        ])
        self.check(None, [("server.pem", "allow"), ("keys/id_rsa", "allow"), ("certs/a.key", "allow"), ("docs/keyboard.md", "allow")], action="read")
        self.check(None, [(".env", "allow"), ("config/.env.local", "allow"), (".env.example", "allow"), ("src/a.py", "allow")], action="edit")

    def test_app_ecosystem(self) -> None:
        a = rf"{SKILLS_WIN}\app-ecosystem\run.bat"
        self.check(None, [
            (f"{a} status", "allow"), (f"{a} tasks --due-within 7", "allow"), (f"{a} task 123", "allow"),
            (f"{a} diff a.pdf b.pdf --page-a 2", "allow"), (f"{a} links C:\\Work\\a.md --q 見積", "allow"), (f"{a} recent", "allow"), (f"{a} review 01_data/資料.md", "allow"),
            (f"{SKILLS_MAC}/app-ecosystem/run.sh tasks", "allow"),
            # 0.31.0: 書き込みも確認なしで動く (--dry-run は要らない)
            (f"{a} task-add --title 提出 --start 2026-11-10", "allow"), (f"{a} task-update 123 --progress 80", "allow"),
            (f"{a} task-link 123 --note 01_data/まとめ【123】.md", "allow"), (f"{a} task-note 123", "allow"),
            (f"{a} task-add --title 提出 --start 2026-11-10 --with-note", "allow"),
            (f"{a} tasks > .handoff/t.txt", "allow"),
        ])

    def test_outlook(self) -> None:
        o = rf"{SKILLS_WIN}\outlook-mail\run.bat"
        self.check("outlook", [
            (f"{o} check", "allow"), (f"{o} mail-list --since 2026-11-01 --limit 20", "allow"),
            (f"{o} mail-get m1a2b3c --out .handoff/outlook_本文.md", "allow"), (f"{o} folder-list --depth 3", "allow"),
            (f'"{o}" mail-list', "allow"),
            # 0.21.0: キャッシュ (読むのはキャッシュから。書くのは、キャッシュのファイルだけ)
            (f"{o} cache-search 見積 --since 2026-11-01", "allow"), (f"{o} cache-show m1a2b3c m4d5e6f --no-quote", "allow"),
            (f"{o} cache-sync --full", "allow"), (f"{o} cache-status", "allow"),
            (f'{o} cal-create --subject "cache-search の確認" --start "2026-11-10 14:00"', "allow"),
            (f"{o} cache-show m1 > C:\\Users\\u\\Desktop\\mail.txt", "deny"),
            (f"{o} reply-draft m1 --body-file .handoff/outlook_返信.md", "allow"),  # 0.16.0: 下書きを作るだけ (送らない)
            (f"{o} reply-draft m1 --body-file .handoff/outlook_返信.md --dry-run", "allow"),
            (f"{o} mail-move m1 m2 --to 案件A/2026", "allow"), (f"{o} mail-move m1 --to 案件A --dry-run", "allow"),
            (f"{o} mail-undo --last 5", "allow"), (f"{o} folder-create 案件A/2026", "allow"),
            (f'{o} cal-create --subject "folder-list の確認" --start "2026-11-10 14:00"', "allow"),
            # リダイレクトで .handoff の外に書かない (書くのは --out)
            (f"{o} mail-get m1 > C:\\Users\\u\\Desktop\\mail.txt", "deny"),
        ])
        # 0.26.0: 登場人物は、読む (who) だけ。人物ノートを書くのは、ユーザーの承認の後のメイン
        p = rf"{SKILLS_WIN}\people\run.bat"
        self.check("outlook", [
            (f"{p} who --mail m1a2b3c", "allow"), (f"{p} who 山田 taro@abc.co.jp", "allow"), (f'"{p}" who --self', "allow"),
            (f"{p} who reset@abc.co.jp new_user@abc.co.jp memo@abc.co.jp", "allow"),
            (f"{p} who memo", "allow"), (f"{p} who --mail m1a2b3c set new", "allow"),  # 引数の語は、new・set・memo でも読むだけ
            (f'"{p}" new "山田 太郎"', "deny"), (f'"{p}" memo 山田 "x"', "deny"),
            (f"{p} who 山田 & {p} set 山田 --role 窓口", "deny"),  # つないで書いても、書き込みは拒否
            (f'{p} new "山田 太郎" --email t@a.jp', "deny"), (f"{p} set 山田 --role 窓口", "deny"),
            (f'{p} memo 山田 "who の確認"', "deny"), (f"{p} candidates", "deny"),
            (f"{p} who 山田 > C:\\Users\\u\\Desktop\\who.txt", "deny"),
        ])

    def test_researcher(self) -> None:
        self.check("researcher", [
            (rf"{SKILLS_WIN}\doc-convert\run.bat convert 見積.xlsx", "allow"), (f"{SKILLS_MAC}/diagram-reader/run.sh describe a.drawio", "allow"),
            (rf"{SKILLS_WIN}\doc-convert\run.bat convert 見積.xlsx > src\a.py", "deny"),
            (rf"{SKILLS_WIN}\outlook-mail\run.bat mail-list", "deny"),
        ])
        # 0.21.0: 調査を 1 回で終わらせる。researcher も、探して (content / names)、作業フォルダの外のテキストを読める (read)
        self.check("researcher", [
            (rf"{SKILLS_WIN}\file-finder\run.bat content 見積 金額", "allow"), (f"{SKILLS_MAC}/file-finder/run.sh read /Users/u/Docs/a.md", "allow"),
            (rf"{SKILLS_WIN}\file-finder\run.bat read C:\Docs\a.md > b.md", "deny"),
        ])
        self.check("researcher", [("file-finder", "allow"), ("doc-convert", "allow"), ("outlook-mail", "deny")], action="skill")

    def test_調査は1回で答えまで出す(self) -> None:
        # 0.21.0: 「検索して、ファイルだけ出して、読んでいいかと聞かれる」への対策。指示から、聞き返して止まる書き方が戻らないようにする
        self.assertIn("読んでよいか聞かない", read(ROOT / "AGENTS.md"))
        for name in ("researcher", "outlook"):
            body = read(ROOT / "agents" / f"{name}.md")
            self.assertNotRegex(body, r"(調べ|探さ)ずに、(何を教えてほしいかを返答で)?質問", f"{name}: 読み取りの依頼で、質問して止まる書き方が残っています")
        self.assertIn("探す → 読む → 答える", read(ROOT / "agents" / "researcher.md"))
        self.assertIn("cache-search", read(ROOT / "agents" / "outlook.md"))
        for path in (ROOT / "templates" / "project" / "document" / "AGENTS.md", ROOT / "templates" / "obsidian" / "AGENTS.md"):
            self.assertIn("調査は、1 回で答えまで出す", read(path), path)

    def test_diagram(self) -> None:
        e = rf"{SKILLS_WIN}\excalidraw-diagram\run.bat"
        self.check("diagram", [
            (f"{e} build .handoff/diagram_a.json --out 図_a.excalidraw.md --dry-run", "allow"),
            (f"{e} build .handoff/diagram_a.json --out 図_a.excalidraw.md", "allow"),  # 0.16.0: 同名は上書きしない。Git で戻せる
            (f"{e} check 図_a.excalidraw.md", "allow"), (rf"{SKILLS_WIN}\diagram-reader\run.bat describe 図_a.excalidraw.md", "allow"),
        ])

    def test_browserはbrowser_sessionだけを使える(self) -> None:
        w, m = rf"{SKILLS_WIN}\browser-session\run.bat", f"{SKILLS_MAC}/browser-session/run.sh"
        self.check("browser", [
            (f"{w} start", "allow"), (f"{w} status", "allow"), (f"{w} open https://intranet/a", "allow"), (f"{w} text --selector main", "allow"),
            (f"{w} snapshot", "allow"), (f"{w} run .handoff/browser/01.py", "allow"), (f"{w} close", "allow"), (f'"{w}" tabs', "allow"),
            (f"{m} open https://intranet/a", "allow"), (f"{m} snapshot", "allow"),
            (f"{w} run .handoff/browser/01.py --allow-submit", "allow"), (f"{m} run a.py --allow-submit", "allow"),
            ("dir", "deny"), ("python x.py", "deny"), ("curl http://example.com", "deny"), ("git status", "deny"),
        ])
        self.check("browser", [("browser-session", "allow"), ("vault-notes", "deny"), ("outlook-mail", "deny")], action="skill")


VENDORED_SKILLS = {"yomiyasu"}  # 手を加えずに同梱したスキル。ハーネスの案内は、SKILL.md でなく VENDOR.md に書く
WINDOWS_ONLY_SKILLS = {"outlook-mail"}  # COM は Windows 専用なので、run.sh を作らない (file-finder は、Mac でも全文検索だけは動く)


def sh_skills() -> list[str]:
    return sorted(d.name for d in skill_dirs() if (d / "run.sh").exists())


class MacLauncherTests(unittest.TestCase):
    """Mac / Linux 用の run.sh (Windows 用の run.bat と同じ引数・同じスクリプト)。"""

    def test_run_shの形(self) -> None:
        self.assertTrue(sh_skills())
        for name in sh_skills():
            raw = (ROOT / "skills" / name / "run.sh").read_bytes()
            raw.decode("ascii")  # 日本語を書かない
            self.assertTrue(raw.startswith(b"#!/bin/sh\n"), f"{name}: 先頭は #!/bin/sh")
            self.assertNotIn(b"\r", raw, f"{name}: 改行は LF だけ (CRLF だと sh が動かない)")
            text = raw.decode("ascii")
            self.assertIn('exec "$PY"', text, f"{name}: exec で終了コードを引き継ぐ")
            self.assertIn('"$@"', text, f"{name}: 引数をそのまま渡す")
            self.assertIn(".venv/bin/python", text)
            self.assertIn("pwd -P", text, f"{name}: リンクをたどった実際の場所で .venv を探す (Mac は部品ごとのリンクで置くため)")
            if os.name == "posix":
                self.assertTrue(os.access(ROOT / "skills" / name / "run.sh", os.X_OK), f"{name}: run.sh に実行権限がありません (chmod +x)")

    def test_run_shはrun_batと同じスクリプトを呼ぶ(self) -> None:
        for name in sh_skills():
            self.assertTrue((ROOT / "skills" / name / "run.bat").exists(), f"{name}: run.bat のないスキルに run.sh だけがあります")
            bat = re.search(r"scripts\\([a-z_]+\.py)", read(ROOT / "skills" / name / "run.bat"))
            sh = re.search(r"scripts/([a-z_]+\.py)", read(ROOT / "skills" / name / "run.sh"))
            self.assertTrue(bat and sh and bat.group(1) == sh.group(1), f"{name}: run.bat と run.sh が違うスクリプトを呼んでいます")

    def test_Windows専用のスキルにはrun_shを作らない_ほかには作る(self) -> None:
        for name in WINDOWS_ONLY_SKILLS:
            self.assertFalse((ROOT / "skills" / name / "run.sh").exists(), f"{name}: Windows 専用 (COM / Everything) なので run.sh は作らない")
        for directory in skill_dirs():
            if (directory / "run.bat").exists() and directory.name not in WINDOWS_ONLY_SKILLS:
                self.assertTrue((directory / "run.sh").exists(), f"{directory.name}: run.sh がありません (Windows 専用なら WINDOWS_ONLY_SKILLS に入れる)")

    def test_手順書がrun_shを案内している(self) -> None:
        for name in sh_skills():
            guide = "VENDOR.md" if name in VENDORED_SKILLS else "SKILL.md"
            self.assertIn("run.sh", read(ROOT / "skills" / name / guide), f"{name}: {guide} に run.sh の案内がありません")

    def test_許可パターンはrun_batとrun_shで同じ効果(self) -> None:
        # run.bat のルールの直後に、同じ効果の run.sh のルールがあること (順序が変わると、最後に一致したルールが勝つ前提が崩れる)
        shared = set(sh_skills())

        def check(label: str, rules: list[tuple[str, str, str]]) -> None:
            for i, (action, resource, effect) in enumerate(rules):
                match = re.match(r"\*([a-z0-9-]+)\*run\.bat", resource)
                if action == "shell" and match and match.group(1) in shared:
                    expected = ("shell", resource.replace("run.bat", "run.sh"), effect)
                    self.assertTrue(i + 1 < len(rules) and rules[i + 1] == expected, f"{label}: {resource} の直後に {expected} がありません")
                if action == "shell" and "run.sh" in resource:
                    previous = rules[i - 1] if i else None
                    self.assertEqual(previous, ("shell", resource.replace("run.sh", "run.bat"), effect), f"{label}: {resource} の直前に run.bat 版がありません")

        check("sample_opencode.json", sample_rules())
        for path in agent_files():
            front, _ = split_frontmatter(read(path))
            check(path.name, permission_rules(front))

    def test_run_shが実際に動き_リンク経由でも動く(self) -> None:
        if os.name != "posix":
            self.skipTest("POSIX のみ")
        import subprocess
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            link = Path(tmp) / "opencode"
            os.symlink(ROOT, link)  # ~/.config/opencode をリンクで置く使い方
            for name in sh_skills():
                for launcher in (ROOT / "skills" / name / "run.sh", link / "skills" / name / "run.sh"):
                    done = subprocess.run(["sh", str(launcher), "--help"], capture_output=True, text=True, timeout=60)
                    self.assertEqual(done.returncode, 0, f"{launcher}: {done.stderr}")
                    self.assertIn("usage", done.stdout.lower(), str(launcher))
            bad = subprocess.run(["sh", str(link / "skills" / "project-init" / "run.sh"), "check", str(Path(tmp) / "none")], capture_output=True, text=True, timeout=60)
            self.assertEqual(bad.returncode, 2, "スクリプトの終了コードが引き継がれていません")

    def test_gitattributesでshはLF(self) -> None:
        self.assertIn("*.sh text eol=lf", read(ROOT / ".gitattributes"))


class FileSystemTests(unittest.TestCase):
    def test_batはASCIIだけ(self) -> None:
        for path in repo_files():
            if path.suffix == ".bat" and "node_modules" not in path.parts:
                path.read_bytes().decode("ascii")

    def test_Windowsで使えない名前_長すぎるパス_シンボリックリンクが無い(self) -> None:
        bad = re.compile(r'[<>:"|?*\\\x00-\x1f]')
        for dirpath, dirnames, filenames in os.walk(ROOT):
            dirnames[:] = [d for d in dirnames if d not in (".git", ".venv")]  # .venv は Git の管理外 (会社へ運ばない)
            for name in dirnames + filenames:
                full = Path(dirpath) / name
                rel = full.relative_to(ROOT).as_posix()
                self.assertFalse(full.is_symlink(), f"シンボリックリンク: {rel}")
                self.assertFalse(bad.search(name), f"使えない文字: {rel}")
                self.assertNotIn(name.split(".")[0].upper(), RESERVED, f"予約名: {rel}")
                self.assertEqual(name, name.rstrip(" ."), f"末尾の空白/ピリオド: {rel}")
                self.assertLessEqual(len(rel), 156, f"パスが 156 文字を超えています: {rel}")

    def test_大文字小文字だけが違う名前が無い(self) -> None:
        seen: dict[str, str] = {}
        for path in repo_files():
            rel = path.relative_to(ROOT).as_posix()
            self.assertNotIn(rel.lower(), seen, f"大文字小文字だけが違う: {rel} と {seen.get(rel.lower())}")
            seen[rel.lower()] = rel


class DocumentTests(unittest.TestCase):
    def docs(self) -> list[Path]:
        return [ROOT / "README.md", ROOT / "CHANGELOG.md", *sorted((ROOT / "docs").glob("*.md"))]

    def test_ドキュメント内の相対リンクが実在する(self) -> None:
        for doc in self.docs():
            for target in re.findall(r"\]\(([^)#\s]+\.md)(?:#[^)]*)?\)", read(doc)):
                if target.startswith("http"):
                    continue
                self.assertTrue((doc.parent / target).exists(), f"{doc.name}: リンク先がありません: {target}")

    def test_READMEに全部品の名前がある(self) -> None:
        readme = read(ROOT / "README.md")
        for path in agent_files():
            self.assertIn(f"`{path.stem}`", readme, f"README に agents/{path.name} の説明がありません")
        for directory in skill_dirs():
            self.assertIn(f"`{directory.name}`", readme, f"README に skills/{directory.name} の説明がありません")
        for path in command_files():
            self.assertIn(f"`/{path.stem}`", readme, f"README に commands/{path.name} の説明がありません")
        for doc in sorted((ROOT / "docs").glob("*.md")):
            self.assertIn(f"docs/{doc.name}", readme, f"README に docs/{doc.name} へのリンクがありません")

    def test_古い表現が残っていない(self) -> None:
        stale = ["結果の受け渡し(必ず守る)", "送信・削除・移動はできない", "送信・削除・移動は実装しない", "要約: (3 行以内)", "メールの整理は案 → 承認 → 実行"]
        for doc in self.docs() + agent_files() + [d / "SKILL.md" for d in skill_dirs()] + [ROOT / "AGENTS.md"]:
            text = read(doc)
            for phrase in stale:
                self.assertNotIn(phrase, text, f"{doc.name}: 古い表現が残っています: {phrase}")

    def model_and_docs(self) -> list[Path]:
        paths = [*self.docs(), ROOT / "AGENTS.md", ROOT / "クイックスタート.md", *agent_files(), *command_files()]
        paths += [d / "SKILL.md" for d in skill_dirs()] + sorted((ROOT / "templates").rglob("*.md"))
        return paths

    def test_JSONとPythonの例は文法どおり(self) -> None:
        # 断片(キーだけ、配列の要素だけ)は、括弧で囲めば読めること。【 】の雛形は除く。
        for doc in self.model_and_docs():
            text = read(doc)
            for match in re.finditer(r"```(json|jsonc|python)\n(.*?)```", text, re.S):
                lang, body = match.group(1), textwrap.dedent(match.group(2))
                where = f"{doc.relative_to(ROOT)}:{text[: match.start()].count(chr(10)) + 1}"
                if "【" in body:
                    continue
                if lang == "python":
                    ast.parse(body)
                    continue
                body = re.sub(r"(?m)^\s*//.*$|\s//\s.*$", "", body).strip().rstrip(",")
                for wrapped in (body, "{" + body + "}", "[" + body + "]"):
                    try:
                        json.loads(wrapped)
                        break
                    except json.JSONDecodeError:
                        pass
                else:
                    self.fail(f"{where}: JSON の例が読めません")

    # Obsidian CLI 1.15.1 の `obsidian help` で実在を確かめたコマンド。新しく書くときは、help で確かめてから足す。
    OBSIDIAN_COMMANDS = {
        "append", "backlinks", "command", "commands", "create", "delete", "eval", "file", "files", "help",
        "history:restore", "links", "move", "open", "orphans", "outline", "plugin:install", "plugin:uninstall",
        "prepend", "properties", "property:set", "rename", "search", "sync:restore", "tags", "tasks",
        "theme:install", "theme:uninstall", "unresolved", "version",
    }

    def test_Obsidian_CLIの例は確かめたコマンドだけ(self) -> None:
        for doc in self.model_and_docs():
            for line in read(doc).splitlines():
                for part in line.split("`"):
                    m = re.match(r"\s*(?:\d+\.\s+)?obsidian (?:vault=(?:\"[^\"]*\"|\S+) )?([a-z][a-z:-]*)", part)
                    if m:
                        self.assertIn(m.group(1), self.OBSIDIAN_COMMANDS, f"{doc.relative_to(ROOT)}: obsidian {m.group(1)}")

    def test_現在の手順に消した部品が出てこない(self) -> None:
        # 経緯(CHANGELOG、README の 6、docs の経緯の行)は除く。モデルが読むものと、人が手順に使うもの。
        removed = ["obsidian-run", "python-env", "agents/finder", "agents/reviewer", "子 `finder`", "子 `reviewer`", "OBSIDIAN_API_KEY"]
        paths = [ROOT / "AGENTS.md", *agent_files(), *command_files(), *[d / "SKILL.md" for d in skill_dirs()]]
        paths += sorted((ROOT / "templates").rglob("*.md")) + [ROOT / "sample_opencode.json"]
        for path in paths:
            text = read(path)
            for name in removed:
                self.assertNotIn(name, text, f"{path.relative_to(ROOT)}: 消した部品 {name} が残っています")

    def test_変更履歴に現在の版がある(self) -> None:
        self.assertRegex(read(ROOT / "CHANGELOG.md"), r"(?m)^## \d+\.\d+\.\d+")


class AiWorkTemplateTests(unittest.TestCase):
    """templates/ai_work/ (ランチャーの AI_work 用のひな形)。人がコピーするので、名前が古くなると、置いた先で迷う。"""

    AI_WORK = ROOT / "templates" / "ai_work"

    def local_skills(self) -> list[Path]:
        return sorted(p for p in (self.AI_WORK / ".opencode" / "skills").iterdir() if p.is_dir())

    def known_names(self) -> set[str]:
        return {p.stem for p in agent_files()} | {d.name for d in skill_dirs()} | {d.name for d in self.local_skills()}

    def test_スキルの名前はフォルダ名と同じで_OpenCodeの決まりに合う(self) -> None:
        harness = {d.name for d in skill_dirs()}
        for folder in self.local_skills():
            head = read(folder / "SKILL.md").split("\n---", 1)[0]
            self.assertIn(f"\nname: {folder.name}\n", head, folder)
            self.assertRegex(head, r"\ndescription: \S")
            self.assertRegex(folder.name, r"^[a-z0-9]+(-[a-z0-9]+)*$")
            self.assertNotIn(folder.name, harness, f"{folder.name} がハーネスのスキルと同じ名前です")
            self.assertTrue((folder / "run.bat").is_file() and (folder / "run.sh").is_file(), folder)

    def test_AGENTS_mdの表は実在のスキルとエージェントを指す(self) -> None:
        text = read(self.AI_WORK / "AGENTS.md")
        section = text.split("## 使うハーネスのスキル", 1)[1].split("\n## ", 1)[0]
        for name in re.findall(r"`([a-z][a-z-]+)`", section):
            self.assertIn(name, self.known_names(), f"templates/ai_work/AGENTS.md の {name} が見つかりません")

    def test_ランチャーのコマンドの例はランチャーの決まりに合い_実在のスキルを指す(self) -> None:
        commands = json.loads(read(self.AI_WORK / "launcher_slash_commands.sample.json"))
        names = [c["name"] for c in commands]
        self.assertEqual(len(names), len({n.lower() for n in names}), "名前が重複しています (大文字小文字は区別しない)")
        for command in commands:
            self.assertEqual(set(command), {"name", "description", "prompt"})
            self.assertRegex(command["name"], r"^[^\s/]+$")
            self.assertTrue(command["prompt"].strip())
            for skill in re.findall(r"スキル `([a-z][a-z-]+)`", command["prompt"]):
                self.assertIn(skill, self.known_names(), f"/{command['name']} の {skill}")
        for path in (self.AI_WORK / ".opencode" / "commands").glob("*.md"):
            self.assertIn(path.stem, names, f".opencode/commands/{path.name} が、ランチャーの例にありません")
        readme = read(self.AI_WORK / "README.md")
        for name in names:
            self.assertIn(f"/{name}", readme, f"README.md に /{name} がありません")

    def test_一覧に見本の自動化が載っている(self) -> None:
        index = read(self.AI_WORK / "自動化一覧.md")
        for folder in self.local_skills():
            if folder.name != "automation-builder":
                self.assertIn(f"`{folder.name}`", index)


if __name__ == "__main__":
    unittest.main()
