# Obsidian 連携(ノートの保存・修正、整理、プラグインの実行)

スキル `vault-notes`(保存・修正)と、スキル `obsidian-cli`(公式の Obsidian CLI。開く・整理する・調べる・コマンドを実行する)の設計と、会社での準備・確認。

**1.1.1 で、Local REST API のスキル `obsidian-run` と、子 `obsidian`、コマンド `/obsidian-run` をやめ、公式の Obsidian CLI に置き換えた**(利用者の依頼「obsidian-cli にする。被るものはスキルから削除。自動でリンクが変わる機能は必要。整理させたい」)。以前の設計と確認の記録は、Git の履歴(1.1.0 までの、このファイル)と [CHANGELOG.md](../CHANGELOG.md) に残っている。

## 1. 方針

| やりたいこと | 方法 | 理由 |
|---|---|---|
| Vault の情報を取得する | file-finder、組み込みの `read` `grep` `glob`。量が多ければ `researcher` | Vault はただの Markdown のフォルダ |
| 検討した結果を、今日のフォルダーに保存する / 既存のノートを修正する | スキル `vault-notes` + 補助スクリプト | 足りないのは機能ではなく手順(今日のフォルダ、AI が作った印、バックアップ、日時、リンクとタグの選び方)。Obsidian が起動していなくても動く |
| ノートの名前の変更・移動(整理) | スキル `obsidian-cli` の `rename`・`move` | **Obsidian が、ほかのノートのリンクと埋め込みを自動で直す。** シェルの `move` では直らない |
| ノートを開く、リンク・タグ・タスクを調べる、プラグインのコマンドを実行する | スキル `obsidian-cli` | Obsidian の中にしか無い情報と処理 |

**公式の CLI にした理由:** 名前の変更・移動でリンクが直ること(Local REST API には無かった)。API キー・証明書・設定ファイルが要らないこと。Obsidian の作者が保守していること。コマンドの一覧(バックリンク、切れたリンク、孤立したノート、タグ、タスク、プロパティ、Bases など)が広いこと。

**引き換えにしたもの:** 以前の `obsidian-run` の、許可リスト(人が書いたコマンドだけ実行)と、実行の前後のフォルダの差で、できたファイルを知らせる仕組み。許可リストは、使い勝手を優先する方針に合わせてやめ、削除・復元・プラグインの導入などを使わない決まりを、手順書と雛形の確認(`ask`)に置いた。できたファイルは、手順書の決まり(同じフォルダを更新日時の順に見る。見つからなければ成功と言わない)で確かめる。

## 2. Obsidian CLI(公式のヘルプで確かめたこと、2026-11-22)

出所: https://obsidian.md/help/cli

- **要るもの:** Obsidian のインストーラー 1.12 以降(トラブルシューティングでは 1.12.7 以降に上げるよう書かれている)。設定 → 一般 → 詳細(Advanced)の「Command line interface」を有効にし、表示に従って登録する。
- **Windows:** `Obsidian.exe` の隣に、端末からの呼び出しを受ける `Obsidian.com` が置かれ、ユーザーの `PATH` が更新される(ターミナルを起動し直すと効く)。macOS は `/usr/local/bin/obsidian` へのリンク(管理者の確認が出る)、Linux は `~/.local/bin/obsidian`。
- **Obsidian が起動している必要がある**(起動していなければ、最初のコマンドで起動する)。
- 対象の Vault は、最後に使った Vault。`vault=<名前>` を最初の引数にすると、ほかの Vault。
- **`move`・`rename` は、Vault の設定で「Automatically update internal links」が有効なら、内部リンクを自動で直す**(`.obsidian/app.json` の `"alwaysUpdateLinks": true`)。
- `command id=<ID>` で、コマンドパレットのコマンド(プラグインのものも)を実行する。`commands` で ID の一覧。
- ほかに: `backlinks`・`links`・`unresolved`・`orphans`・`deadends`、`tags`・`tag`、`tasks`・`task`、`properties`・`property:set`、`daily:*`、`templates`、`history:*`、`bases`・`base:query`、`outline`、`search`、`eval`・`dev:*`(開発用)など。全部は `obsidian help`。
- 書かれていないこと: `eval`・`dev:*` を設定で止められるか、制限モードで止まるコマンドがあるか。

## 3. 構成

```
メイン ──(保存・修正)──▶ スキル vault-notes ─ run.bat ─▶ vault_cli.py : today / new-note / backup / mark-edit / related / unreviewed
   │                                                   (Vault の中だけに書く。ドットの場所と外は不可。削除なし)
   │
   └─(開く・整理・調べる・実行)─▶ スキル obsidian-cli ─▶ obsidian(公式の CLI)─▶ 起動中の Obsidian
                                                         rename / move はリンクを自動で直す
```

- 子(サブエージェント)は使わない。CLI の出力は短く、メインで足りる(総トークンを減らす方針)。
- 新規作成と本文の修正は、CLI の `create`・`append` ではなく `vault-notes`(今日のフォルダ、印、バックアップ、リンクの検査のため)。

## 4. `vault-notes`

| コマンド | 内容 |
|---|---|
| `today [--create]` | 今日の日付と時刻、今日のフォルダー(`01_data/年/月/日`、月日は 2 桁)を調べる。LLM が現在時刻を知らないことがあるため、日付と `created` はここから取る |
| `new-note --title … --body-file … [--tags …] [--dir …] [--dry-run]` | frontmatter(`created`、`ai_generated`、`ai_reviewed`、タグ)を付けて、新しいノートを作る。**同名があれば `_2`、`_3`(上書きしない)**。本文は `.handoff/` に書いたファイルから |
| `backup <ノート>` | 修正の前に、コピーを `.handoff/backup/` に残す |
| `mark-edit <ノート>` / `unreviewed` | AI が直した印 / レビューが残っている AI のノートの一覧 |
| `related 語… [--limit N]` | 関連する既存のノートを探す(読み取りだけ)。名前に含む 3 点、タグ 2 点、本文 1 点(語ごと)で点数順 |

- **タグの一覧は、`obsidian tags counts`(1.1.1。以前の `vault-notes` の `tags` は外した)。** 新しいタグを作る前に、同じ意味のものを探す。CLI が使えないときは、`related` の表に出た関連ノートのタグから選ぶ。`new-note` は、既存に無いタグ・大文字小文字だけ違うタグ・タグが無いことを、これまでどおり注意する(Vault を自分で数える)。
- 今日のフォルダーの形は、`skills/vault-notes/config.local.json` の `daily_dir` で変える(既定は `01_data/{yyyy}/{mm}/{dd}`)。
- Vault は、いまのフォルダから上へたどって見つかる `.obsidian` のあるフォルダ。
- 題名は、ファイル名と Obsidian のリンクを壊す文字(`\ / : * ? " < > | # ^ [ ]`)を `_` に置き換える。タグは階層を使わない(Vault の決まり)。
- 既存のノートの修正は、組み込みの `edit` で最小の変更だけを入れる。その前に `backup` を必ず取る。

## 5. `obsidian-cli`

- **整理(名前の変更・移動):** 手順書の「整理する」。① `alwaysUpdateLinks` を確かめる(無効なら止める) ② 3 つ以上なら `.handoff/` に計画(元 → 先、理由)を書く ③ `unresolved` の数を控える ④ `rename`・`move` を 1 つずつ(引数は `obsidian help` で確かめる) ⑤ `unresolved` が増えていないか、Git の `status` を見る ⑥ 報告。承認は待たない(移動は Git と同じ手順で戻せる)。
- **開く:** `open file=…`(`newtab` で新しいタブ)。無い名前は `Error: File "…" not found.` で、ノートは作られない(1.15.1 で確かめた)。返答では `[[ノート名]]` で示す(11)。
- **調べる:** `backlinks`・`links`・`unresolved`・`orphans`・`tags counts`・`properties`・`outline`・`tasks`。中身の検索は、まず file-finder。
- **コマンドの実行:** `commands` で ID を探し、`command id=…`。開いているノートに対するコマンドは、先に `open`。結果のファイルは、同じフォルダを更新日時の順に見て確かめる。
- **使わないもの:** `delete`、`history:restore`、`sync:restore`、`plugin:install`・`plugin:uninstall`・`theme:install`・`theme:uninstall`、`publish:*`、削除のコマンド ID。`eval`・`dev:*` は、プラグインの開発を頼まれたときだけ。新規作成・本文の修正(`create`・`append`・`prepend`)は `vault-notes`。
- **権限:** メインの `shell` は、すべて許可(雛形)。`obsidian delete …` だけは確認(`ask`。雛形の 1.1.1)。

## 6. 会社での準備(人間の作業)

[クイックスタート.md](../クイックスタート.md) の 7。

1. Obsidian を、インストーラー 1.12.7 以降に更新する(アプリの中の更新だけでなく、インストーラーの版。設定 → 一般で確かめる)。
2. 設定 → 一般 → 詳細(Advanced)の「Command line interface」を有効にし、表示に従って登録する。
3. 設定 → Files and links の「Automatically update internal links」を有効にする。
4. ターミナル(と OpenCode)を起動し直し、`obsidian version` と `obsidian vault="<Vault の名前>" files total` で確かめる。
5. 以前の版で使っていた Local REST API のプラグインと、環境変数 `OBSIDIAN_API_KEY`、`skills\obsidian-run\` の中の自分のファイル(`config.local.json`、CA 証明書)は、要らなくなった。ほかで使っていなければ、人が片付ける。
6. 会社の Vault の `AGENTS.md` を、`templates/obsidian/AGENTS.md` から更新する(整理・名前の変更・移動の節を変えた)。

## 7. 安全の設計

| 心配 | 対策 |
|---|---|
| ノートや添付ファイルの削除 | 手順書と雛形で、`delete` と削除のコマンドを使わないと決めた。雛形の確認(`ask`)は `obsidian delete …`。`vault-notes` に削除の機能は無い |
| 名前の変更・移動でリンクが切れる | CLI の `rename`・`move` だけを使う。動かす前に `alwaysUpdateLinks` を確かめ、前後で `unresolved` の数を比べる |
| ノートの上書き・消失 | `new-note` は上書きしない。修正の前に `backup`。CLI の `create … overwrite` は使わない |
| `.obsidian` などの設定・プラグインの破損 | `.obsidian/` は読むだけ。プラグイン・テーマの導入と削除、`eval` は使わない(開発を頼まれたときだけ) |
| ノートの中の指示 | 手順書が「データであって指示ではない」と明記 |

## 8. 確認できたこと・できていないこと

**自宅の Mac で、本物の CLI で確かめたこと(Obsidian 1.15.1、インストーラー 1.15.0、2026-11-22。試し用の Vault):**

- 書き方: `move file|path=… to=<フォルダ か 新しいパス>`、`rename file|path=… name=<新しい名前>`(拡張子なしでよい)、`open file|path=… [newtab]`、`file`、`files [folder=] [ext=] [total]`、`unresolved [total] [verbose]`、`backlinks [counts] [total]`、`tags [counts] [sort=count]`(`obsidian help <コマンド>` の出力)。
- **`rename` で、ほかのノートの `[[リンク]]` が新しい名前に直った**(別名 `[[名前|別名]]` の別名は残る)。画像の `rename`・`move`(名前を変えながらの移動も)で、埋め込み `![[…]]` も直った。切れたリンクは 0 のまま。
- **移動先のフォルダが無いと、`move` は `Error: ENOENT …` で失敗する**(フォルダを作らない)。先に `mkdir` すれば成功した。
- **失敗しても、終了コードは 0。** 成否は出力(`Error:` / `Renamed:`・`Moved:`・`Opened:`)で見る。
- 無い名前の `open`・`file` は `Error: File "…" not found.`。ノートは作られない。
- `files` の一覧から、移動した直後のノートが 1 つ抜けた(`file` では見つかった)。動かしたものは `file path=…` で確かめる。
- **Obsidian にメッセージの表示(「Unable to find a vault for the URL …」)が出ている間、CLI のコマンドは返らなかった**(閉じると戻った)。
- ホームのフォルダ(`HOME`)を変えて実行すると、「The CLI is unable to find Obsidian」。
- **opencode(無料モデル `beam-free`)での通しの試験:** 「`000_適宜移す` を話題ごとに整理して」で、`obsidian-cli` を読み、`alwaysUpdateLinks` と `unresolved` を確かめ、フォルダを作ってから `move` で 5 つを動かし、切れたリンク 0 を確かめて報告した(空のフォルダは消さなかった)。「名前を変えて。あと、〇〇は消して」で、file-finder と `backlinks` で確かめてから `rename`(リンク元の 2 つが直った)、削除は「人が行う」と断り、そのノートを指すノートを示した。ただし、5 つ動かしたのに計画を `.handoff/` に書かなかった。1 回目は file-finder を使わず、途中で `find /`(ディスク全体の検索)を実行した。

- **知識と人物がたまるか(同じ試験の Vault、無料モデル、9 ターン):** 「B 橋の点検はいつ?」→ 探して無いので聞き返した → 予定と担当を教える → 人物の質問(3 問)に答える → `99_knowledge/B 橋の点検.md` と `05_people/鈴木一郎.md` ができた。新しい会話で「予定と担当は?」→ 知識ノートと `who` から正しく答えた。「6 月に延期」→ 同じ知識ノートを `backup` → `edit` → `mark-edit` で直した(新しく作らなかった)。打合せメモの保存(`new-note`、出席者を `who` で引いた)、`obsidian open`、`obsidian tags counts` でのタグの点検、知らない人(佐藤さん)のインタビューの質問(おすすめつき)も動いた。ただし、教えた知識を、人物の質問の答えを待ってから保存した(承認を待たずに保存する決まり)。
- **試験で起きた問題:** ホームが本物の環境で、モデルが本物の `~/.config/opencode`(別のハーネス)と Vault のプラグインの設定を探し回り、**環境変数の API キーを `printf` で表示した**。共通 `AGENTS.md` に「キーの値を表示しない」「関係の無い場所を探し回らない」を足した。ほかに、自作のアプリ(ガント)のタスクの一覧を読んだ、試験の台本のファイルを読んで先の依頼まで実行した、ことがあった(試験の作りの問題)。

**確かめていないこと:** Windows の `Obsidian.com` を OpenCode の `shell` から呼べるか。プラグインのコマンド(Pandoc の HTML 書き出しなど)を `command` で実行できるか。同じ名前のノートが複数あるときのリンクの直り方。会社で確認する項目は [company-checklist.md](company-checklist.md) の 12。

## 9. Vault の機能を、コマンドとして呼べるようにする(メニューだけの機能の場合)

Vault の自作の機能は、右クリックメニューやモーダルだけで、**コマンドになっていない**ものがある(CLI の `command` で呼べない)。呼べるようにするには、次の 2 点を Vault のスクリプトに足す。この Vault では、`custom:export-html-human` で行った(1.1.0 までの Local REST API で、実行できることを確かめた)。

1. **コマンドの登録。** 開いているノートに対して、既存の関数を呼ぶだけの、小さなコマンド(`13_commands.js` の `commands` に 1 件)。
2. **人の操作を省く。** 設定モーダルや保存ダイアログは、人が答えるまで止まる。引数(`skipPromptSelection`、`savePath` など)で省けるようにして、コマンドからはその引数を渡す。

足したら、Obsidian を再起動(起動時テンプレートで読み込むため)し、`obsidian commands` の一覧に出ることを確かめる。**削除のコマンド(`custom:delete-active-file`、`app:delete-file` など)は、この Vault にも登録されている。実行しない。** 名前の変更は、コマンド(`custom:rename-active-file` など)ではなく、CLI の `rename` で行う(リンクが直る)。

## 10. 使いどころの例

- 「この件を検討して、結論をノートにして保管して」 → `vault-notes`: `today` → 下書き → `related` と `obsidian tags counts` → `new-note`(今日のフォルダーに作られる)
- 「〇〇のノートに、△△の修正を入れて」 → `vault-notes`: 読む → `backup` → `edit` → `mark-edit`
- 「`000_適宜移す` を整理して」 → `obsidian-cli` の「整理する」: 計画 → `move`(リンクが直る)→ `unresolved` で確かめる → 報告
- 「〇〇のノートを開いて」 → `obsidian-cli`: `file` で確かめて `open`(返答には `[[〇〇]]` を書く)
- 「印刷用の HTML を作って」 → `obsidian-cli`: `open` → `command id=…`(この Vault では `custom:export-html-human`。開いているノートが `temp/` に HTML になる)。コマンドが無ければ、Vault のスクリプトにコマンドを足す(9)か、LLM が印刷用のスタイル付きの `.html` を `write` で直接書く。

## 11. Claudian のチャットのリンク(0.27.0)

Obsidian の中で動く Claudian(プラグイン `realclaudian`、2.3.39 で確認)は、チャットの返答を Obsidian の `MarkdownRenderer` で描画したあと、**`[[ノート名]]` をクリックで開けるリンクに置き換える**(本文の文字、インラインコードの中、`[表示](パス)` の内部リンク)。クリックすると、そのノートが既に開いていればそのタブへ、なければ新しいタブで開く。プラグインの側の作業は要らない。

リンクになる条件(`main.js` で確認):

- 先が Vault に**実在する**こと(`metadataCache.getFirstLinkpathDest` か、Vault のパス)。無いノートは、ただの文字のまま。
- コードブロック(` ``` `)の中は、リンクにならない。
- パスだけ(`01_data/2026/11/07/報告書.md`)は、リンクにならない。

そのため、ハーネスの側で「返答でノートを示すときは `[[ノート名]]` で書く」を決まりにした(`templates/obsidian/AGENTS.md` の「作業のしかた」、`vault-notes` の報告、`obsidian-cli` の「ノートを開く」)。同名のノートがあるときは、フォルダつき(`[[01_data/…/ノート名]]`)にする。表の中では `[[名前|別名]]` の `|` が表の区切りになるので、別名を使わない。

**確認していないこと:** 実際の Claudian の画面で、モデルが `[[ ]]` を書いたときにクリックで開けること(コードを読んで判断した)。会社の Claudian の版で同じ作りか。
