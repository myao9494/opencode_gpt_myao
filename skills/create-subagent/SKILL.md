---
name: create-subagent
description: サブエージェント(agents/)を作る・直す手順(要否の判断、最小の権限、返し方の定型)。作成や改修を頼まれたときに使う
---

## 前提

- **置き場所は、作業フォルダの `.opencode/agents/エージェント名.md`**(そのフォルダの中だけで使える)。作業フォルダがこのハーネスのリポジトリ(直下に `skills/`・`agents/`・`AGENTS.md` がある)のときだけ、`agents/エージェント名.md`。**スキルや子が置かれている共通の設定のフォルダ(`skill_files` のパスの上の `agents/`、`.config/opencode`)には書かない**(すべての作業に効く本番の設定。人が写す)。ファイル名がエージェント名になる。名前は英小文字とハイフンにする(例: `doc-checker`)。
- 結果の返し方の約束の原本は、同じフォルダの `handoff-block.md` にある。各エージェントのファイルには、その写しを入れる。
- 目的は、メインエージェントのコンテキストを節約しながら、良い答えを得ること。子セッションの中間の読み取りや検索の結果は、メインに入らない。メインに入るのは、返答だけ。

## 手順

1. **本当にサブエージェントが必要かを判断する。** 子セッションの起動には固定のコスト(システムプロンプトなどの読み込み)がかかる。次のどれかに当てはまるときだけ作る。
   - 読む量が多い作業(10 以上のファイル、Office の分析、画像 4 枚以上、長い PDF)で、結果が要約で足りる(総トークンを減らすため、軽い作業には作らない)
   - 互いに独立していて、並行して進められる調査
   - 権限を絞って、安全に任せたい作業

   1〜2 ファイルを読むだけの作業や、ユーザーと確認しながら進める作業は、メインが自分でやる。すでにあるエージェント(`researcher`: 調査、`outlook`: メール・予定、`browser`: Web ページ、`polish`: 長い文書の推敲、`diagram`: 図)と、メインがスキルで行う方法(探すのは `file-finder`、レビューは Git の差分を読む)で足りないかも、先に確認する。
2. **目的を 1 つに絞る。** 「何を調べて、何を返すか」を 1 文で書く。2 つ以上あるなら、エージェントを分ける。
3. **frontmatter を書く。**
   - `description`: 必須。「どんなときにメインがこのエージェントを呼ぶか」と「何を返すか」を書く。メインは、これを見て呼ぶかどうかを決める。
   - `mode: subagent`
   - `steps`: 繰り返しの上限。20〜25 を目安にする。上限に達すると、ツールが使えなくなり、まとめを書いて終わる。モデルが同じ操作を繰り返して、トークンを使い続けるのを防ぐ。
   - `permissions`: 必要最小限にする(下の「権限の雛形」を使う)。OpenCode V2 の書き方で、`action`・`resource`・`effect` の配列。**最後に一致したルールが勝つ**ので、広いルール(`"*"` の拒否)を先に、例外を後に書く。`permission:`・`bash:` は V1 の旧名なので使わない(1 つのファイルに新旧を混ぜない)。
4. **本文を書く。** 次の順にする。
   - 役割(1〜2 行)
   - 手順(番号つき。調べる順序、見る範囲)
   - 守ること(やってはいけないこと、確認できなかったときの扱い)
5. **返し方の約束を入れる。** `handoff-block.md` の中身を、本文の最後に、そのまま写す。
6. **動作を確認する。** 作ったエージェントは、OpenCode を起動し直すまで呼べない。自分で `opencode run` を入れ子に起動して試さず、次の確認を人に頼む(報告に書く)。確認できなかったものは、そう書く。
   - メインから呼ぶと、サブエージェントが動く。
   - 短い結果のとき、返答に直接書かれ、ファイルが作られない。
   - 長い結果のとき、`.handoff/` に指定の形のファイルができ、返答が「結論・根拠・確信度・未確認・詳細」の形になっている。
   - 禁止した操作(許可していないファイルの書き換えなど)が、拒否される。

## 権限の雛形

読み取り専用(調べて報告するだけのエージェント):

```
permissions:
  - action: edit
    resource: "*"
    effect: deny
  - action: edit
    resource: ".handoff/**"
    effect: allow
  - action: shell
    resource: "*"
    effect: deny
```

読み取り専用で、読み取りのコマンドだけ使う:

```
permissions:
  - action: edit
    resource: "*"
    effect: deny
  - action: edit
    resource: ".handoff/**"
    effect: allow
  - action: shell
    resource: "*"
    effect: deny
  - action: shell
    resource: "git status *"
    effect: allow
  - action: shell
    resource: "git diff *"
    effect: allow
  - action: shell
    resource: "git log *"
    effect: allow
```

(`git diff*` のように空白なしで書くと、外部のツールを起動する `git difftool` にも一致する。末尾の ` *` は、引数なしにも一致する)

スクリプトを使うエージェント(スキルと `run.bat` を入口にする。`outlook`、`researcher` が例):

```
permissions:
  - action: edit
    resource: "*"
    effect: deny
  - action: edit
    resource: ".handoff/**"
    effect: allow
  - action: skill
    resource: "*"
    effect: deny
  - action: skill
    resource: "スキル名"
    effect: allow
  - action: shell
    resource: "*"
    effect: deny
  - action: shell
    resource: "*スキル名*run.bat*"
    effect: allow
  - action: shell
    resource: "*>*"
    effect: deny
```

`ask` は、原則として使わない(0.31.0)。Outlook やタスクの書き込みも `allow` でよい(送信と削除は、スクリプトに無い)。本番のデータベースのように、本当に戻せないものだけ `ask` にする。`ask` を使うときは、読み取りの `allow` の後ろに置く(最後に一致したものが勝つ。リダイレクト `*>*` の `deny` は最後)。注意:

- パターンは、コマンド全体のどこに一致してもよい。`ask` を先に書くと、後ろの `allow`(`*mail-list*` など)が、引数の値(件名やファイル名)に一致して、確認を打ち消す。
- リダイレクト(`> ファイル`)は、許可したコマンドでも、どこにでも書けてしまう。子は `--out`(`.handoff/` の中だけ)で書く。
- 作ったら、`tests/test_harness.py` の `PermissionBehaviorTests` に、許可・確認・拒否になるべきコマンドを足す(判定を再現して確かめる)。

ブラウザを操作するエージェント(`browser` が例): MCP は使わない。スキル `browser-session` だけを `skill` で許可し、`shell` は先頭を「すべて拒否」にして、`run.bat`・`run.sh` だけを許可する。送信・申請・削除のボタンは、スキルの見張りが止める。

ファイルを書き換えるエージェントは、原則として作らない。必要なときは、書き換える範囲を明確にして、人間に確認する。

## 守ること

- 返し方の約束の原本(`handoff-block.md`)を変えたときは、`agents/` の全ファイルの該当ブロックも同じ内容に直す。`結果の返し方` で検索して探す。
- 1 つのエージェントに、広い権限を与えない。
- 機密情報(キー、パスワード、個人情報)を、`.handoff/` に書かせない。
