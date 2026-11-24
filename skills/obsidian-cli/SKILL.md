---
name: obsidian-cli
description: 公式の Obsidian CLI で、ノートを開く・名前の変更と移動(リンクが自動で直る)・リンクとタグを調べる・プラグインのコマンドを実行する手順。Vault の整理に使う
---

公式の Obsidian CLI(`obsidian` コマンド)の手順。引数は `obsidian help <コマンド>` で分かる(推測で書かない)。プラグインやテーマの開発を頼まれたときだけ、`references/upstream-SKILL.md`(英語の元の手順書)を読む。

## 前提

- Obsidian 1.12.7 以降で、設定 → 一般 → 詳細の「コマンドラインインターフェース」が有効なこと(人の作業)。Obsidian が起動している必要がある。
- 対象は最後に使った Vault。ほかの Vault は、最初の引数に `vault=<名前>`。
- 書き方: `名前=値`(空白があれば `"` で囲む)、フラグは値なし。ファイルは `file=<名前>`(リンクと同じ解決)か `path=<Vault からのパス>`。
- **成否は出力で判断する**(失敗しても終了コードは 0)。`Error:` で始まれば失敗。成功は `Renamed: 元 -> 先`、`Moved: 元 -> 先`、`Opened: パス`。
- 「Command line interface is not enabled」「`obsidian` が見つからない」「unable to find Obsidian」は、止めて報告する(有効化・起動は人)。シェルの `move` や、アプリの中の実行ファイルを探して使うことで代えない(別の Vault に対して動くことがある)。
- コマンドが返ってこないときは、Obsidian の画面に確認が出たまま。繰り返さず、止めて報告する。

## 守ること

- ノートの新規作成と本文の修正は、スキル `vault-notes`。CLI の `create`・`append`・`prepend` は使わない(frontmatter の値 1 つだけなら `property:set` でよい。先に `vault-notes` の `backup`)。
- **名前の変更・移動は、必ず CLI で**(下の「整理する」)。シェルや `git mv` で動かすと、リンクが直らない。
- **消さない。** `delete`、`history:restore`、`sync:restore`、`plugin:install`・`plugin:uninstall`・`theme:install`・`theme:uninstall`、`publish:*`、削除のプラグインのコマンドは使わない(人が行う)。
- `eval`・`dev:*` は、プラグインやテーマの開発を頼まれたときだけ。`.obsidian/` の中は書き換えない。

## ノートを開く

```
obsidian open file="報告書"
obsidian open path="01_data/2026/11/07/報告書.md"
```

- 無い名前は `Error: File "…" not found.`(何も作られない)。`vault-notes` の `related` か file-finder で探し、`path=` で開き直す。同名が複数あるときも `path=`。新しいタブは `newtab`。
- 返答では、ノートを `[[ノート名]]` で書く(同名があればフォルダつき)。

## 整理する(名前の変更・移動)

Obsidian が、ほかのノートのリンクと埋め込みを自動で直す。承認は待たない。

1. Vault の `.obsidian/app.json` が `"alwaysUpdateLinks": true` か確かめる。違えば動かさず、設定を頼む。
2. 3 つ以上動かすときは、`.handoff/obsidian_整理_話題.md` に「元 → 先、理由」の表を書く。Vault の `AGENTS.md` のフォルダの決まりに合わせ、分からないものは動かさず「要確認」にする。
3. 動かす前に、`obsidian unresolved total`(切れたリンクの数)を控える。
4. 1 つずつ動かす。`Error:` が出たら、そこで止めて報告する。

   ```
   obsidian rename file="見積の件" name="見積_足場"
   obsidian move file="締付メモ" to="01_data/手順"
   obsidian move path="01_data/000_適宜移す/構成図.png" to="01_data/画像/構成図_A橋.png"
   ```

   - `rename` の `name=` は拡張子なしでよい。`move` の `to=` は、フォルダか、新しい名前つきのパス。
   - **移動先のフォルダが無いと失敗する。** 先にシェルで作る(Windows は `mkdir "01_data\手順"`、Mac は `mkdir -p "01_data/手順"`)。
   - 添付ファイル(画像・PDF など)も、この方法で動かす。
5. 確かめる: `obsidian unresolved total` が増えていないこと、`obsidian file path=新しいパス` で見つかること。Vault が Git にあれば、`git status` で変更と出たノートだけが、リンクを書き換えられたノート(移動だけなら、名前だけのリンク `[[見積の件]]` は書き換わらない)。
6. 報告する: 動かしたもの(元 → 先)、リンクを書き換えられたノートの数(確かめた数。0 なら 0)、切れたリンクの数の前後、要確認のもの。

- 名前の変更・移動だけでは中身は変わらないので、`vault-notes` の `backup` と `mark-edit` は付けない。
- 動かした後、元のフォルダが空になったか `obsidian files` などで確かめ、空なら消さずに報告する(確かめずに「空」と書かない)。

## 調べる(読み取りだけ)

```
obsidian backlinks file="報告書"
obsidian links file="報告書"
obsidian unresolved
obsidian orphans
obsidian tags counts sort=count
obsidian properties file="報告書"
obsidian outline file="報告書"
obsidian tasks todo
```

- 中身の検索は file-finder。CLI の `search` は、Obsidian の検索結果が要るときだけ。一覧が長いときは、先に `total` で件数を見る。

## プラグインのコマンドを実行する

```
obsidian commands
obsidian open file="報告書"
obsidian command id=obsidian-pandoc:pandoc-export-html
```

- ID は `commands` の一覧から選ぶ。合うものが無ければ、実行せず報告する(推測した ID を試さない)。
- 「現在のノートを〜」のコマンドは、先に `open` で対象を開く。
- 結果は返らないことが多い。できたファイルを、元のノートのフォルダで更新日時の新しい順に見て確かめる。見つからなければ「実行したが、結果は確認できなかった」と報告する。
