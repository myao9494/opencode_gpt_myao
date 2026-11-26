# obsidian-cli(同梱)

公式の Obsidian CLI(`obsidian` コマンド)を使うスキル。**元の手順書は手を加えずに `references/upstream-SKILL.md` に置き、手順書(`SKILL.md`)だけを、ハーネスの決まりに合わせて日本語で書き直した。**

- 出所: https://github.com/obsidianmd/obsidian-skills の `plugins/obsidian/skills/obsidian-cli/SKILL.md`(コミット `48a7ca3`、2026-11-11。リポジトリの先頭は `79e85b7`、2026-11-25 に読んだ)。2026-11 に kepano/obsidian-skills から Obsidian の公式の組織へ移った。作者は Obsidian の CEO の Steph Ango(kepano)
- ライセンス: MIT(`LICENSE`)
- CLI 本体は Obsidian に入っている(インストーラー 1.12.7 以降。公式のヘルプ https://obsidian.md/help/cli 、2026-11-22 に読んだ)。スクリプトは同梱していない

## ハーネスで変えたこと

- 新規作成と本文の修正は、スキル `vault-notes`(CLI の `create`・`append` は使わない)。今日のフォルダ、AI が作った印、バックアップ、リンクとタグの選び方を守るため。
- 名前の変更・移動は、CLI の `rename`・`move` だけで行う(Obsidian がリンクを自動で直す。`alwaysUpdateLinks` を先に確かめる)。整理の手順(計画・影響・確認・報告)を足した。
- 削除・復元・プラグインの導入と削除・公開は使わない。`eval`・`dev:*` は、開発を頼まれたときだけ。
- 以前のスキル `obsidian-run`(Local REST API、許可リスト)と、子 `obsidian`・コマンド `/obsidian-run` は、このスキルに置き換えて削除した(1.1.1)。

## 更新のしかた

1. 元のリポジトリの `skills/obsidian-cli/SKILL.md` の変更点を読む。`references/upstream-SKILL.md` を置き換える。
2. 変更点を、`SKILL.md` に写す(ハーネスの決まりと食い違うものは写さない)。
3. 下の一覧を作り直し、ハーネスのテストを通す。`CHANGELOG.md` に書く。

## 同梱のファイルの一覧(SHA-256)

```
64c64d48361edfe8610016441bf593256ea9b67f133b00f47c55aa29ee878567  LICENSE
28f66289b1cf1d245807766d4dafb6f5f76eadc986fe4a7f224da51f50aa7524  references/upstream-SKILL.md
```
