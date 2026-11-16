# 出典とライセンス

このスキルは、次のスキルを土台にし、ほかの公開スキルを参考にして作った。

## 土台にしたもの (MIT ライセンス)

**axtonliu/axton-obsidian-visual-skills** の `excalidraw-diagram` — https://github.com/axtonliu/axton-obsidian-visual-skills
Copyright (c) 2025 Axton Liu (MIT License。全文は [LICENSE-axton-obsidian-visual-skills.txt](LICENSE-axton-obsidian-visual-skills.txt))

そこから取り入れたもの:

- 配色の役割(塗りの色の hex と、使い分けの意味)
- デザインのルール(フォントサイズの下限、線の太さ、余白、角丸、最小の箱の大きさ、絵文字を使わない)
- Obsidian の `.excalidraw.md` の出力形式(frontmatter、警告文、`## Drawing` の ```json フェンス、`%%`)
- 矢印の結合、箱の中の文字(`containerId`)の考え方
- `frameId`、`index`、`versionNonce`、`rawText` を出さないという注意

元のスキルは、LLM が座標を直接書く方式。このスキルは、LLM は図の仕様(ノードと矢印)だけを書き、座標・箱の大きさ・矢印の経路・日本語の幅は、スクリプトが計算する方式に変えた。コードは、すべて新しく書いた(元のスキルには、スクリプトが無い)。

## 参考にしたもの (考え方だけ。コードは取り入れていない)

- github/awesome-copilot の `excalidraw-diagram-generator` — スイムレーン図の構成(レーンの見出しと本体、ステップの置き方)、テンプレートの種類
- coleam00/excalidraw-diagram-skill — 図で主張を見せるという設計の考え方(描画の確認は、このスキルでは Obsidian の PNG 化で行う)

## 圧縮データの展開

`scripts/lzstring.py` は、lz-string (MIT License, Copyright (c) pieroxy) の decompressFromBase64 の処理を、Python で書き直したもの。Obsidian の Excalidraw プラグインが、図を圧縮して保存するため。
