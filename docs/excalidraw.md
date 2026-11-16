# Excalidraw の図(作る・点検する)

スキル `excalidraw-diagram` とサブエージェント `diagram` の設計。使い方は [skills/excalidraw-diagram/SKILL.md](../skills/excalidraw-diagram/SKILL.md)。図を**読む**機能は、別のスキル `diagram-reader`([diagram-reader.md](diagram-reader.md))に分けた(Excalidraw と draw.io の両方を読める)。

## 1. 目的と、最初に決めたこと

- Markdown ノートの図解は、Excalidraw で作る(Obsidian の Excalidraw プラグイン。この Vault は 2.29.1)。
- **元のハーネスの LLM は、画像を認識できなかった(ビジョンが切られていた)。** そのため、図を「見る」代わりに、図の中身(箱・文字・矢印・レーン)を**文章にして読む**ことを、作ることと同じ重さの機能にした。Excalidraw は中身が構造化されたデータなので、これができる。画像(PNG)の図には、できない。**GPT-5.4 mini 版では、モデルは画像を見られるが、この設計はそのまま使う**(`.excalidraw.md` は画像ではない。構造から読む方が正確)。作った図の見た目(重なり、はみ出し)を、モデルが画像で点検することは、図を画像にする手段が無いので、まだできない。
- 作る側は、LLM が座標つきの JSON を直接書く方式(既存のスキル)ではなく、**図の仕様(ノードと矢印)だけを書かせ、座標・大きさ・矢印・日本語の幅はスクリプトが計算する**方式にした。

| | LLM が JSON を直接書く | 仕様だけ書く(採用) |
|---|---|---|
| LLM の出力 | この Vault の既存の図で 約 1 万文字 | 仕様 約 1 千文字(目安) |
| 座標・日本語の幅 | LLM が手計算 | スクリプトが計算 |
| 矢印の接続 | 切れやすい(この Vault の既存の図は、21 本中 5 本だけが箱に結び付いていた) | 必ず両端を箱に結び付ける |
| 検証 | 目で見るだけ | 機械で点検できる |

## 2. 構成

```
スキル excalidraw-diagram
  scripts/excalidraw_core.py   仕様 → 図 (配置・要素・出力。flow / swimlane / tree / mindmap)
  scripts/excalidraw_check.py  図の点検
  scripts/excalidraw_cli.py    コマンド: build / check
  NOTICE.md、LICENSE-…txt      出典と MIT ライセンス (axtonliu 版)

スキル diagram-reader          図を読む (Excalidraw・draw.io)。docs/diagram-reader.md

agents/diagram.md              図を作る子 (build は確認つき。読み戻しは diagram-reader)
commands/diagram.md            /diagram 内容       (子 diagram で作る)
```

- 標準ライブラリだけ(`.venv` が無くても、PATH の `python` で動く)。
- 読めるのは、いまのフォルダと Vault の中だけ。書き込むのは `build` の出力(Vault の中で、ドットで始まらない場所。同名は上書きせず `_2`)と、`describe --out`(`.handoff/` の中だけ)。削除はしない(テストが検査)。

## 3. 作る(`build`)

- **種類:** `flow`(層に分けて配置。分岐、合流、戻る矢印、層を飛ばす矢印)、`swimlane`(レーンごとに担当を分ける。横向き・縦向き)、`tree`(親を子の真ん中に置く)、`mindmap`(根を中央に、枝を左右へ。枝ごとに色が変わる)。
- **配置のしくみ(flow):** 循環を除いて層を決め(最長経路)、層を飛ぶ矢印には中継点を入れ、重心法で並べ替え、隣の層の中心に寄せる。矢印は、箱の縁から縁へ、直角に折れる経路(`elbow`)または直線(`straight`)。ひし形から複数の矢印が出るときは、左右の頂点からも出す。戻る矢印は、箱を横切らない側(左右または上下)を選んで、横を回り込む。
- **日本語:** 全角は 1em、半角の英数字は約 0.6em で幅を見積もり、折り返す(行頭に来てはいけない記号は、前の行にぶら下げる)。見出し(`label`)と説明(`detail`)の間には、空行を入れる(読み取り側が、区切りと分かる)。
- **形:** 角丸の長方形が既定。**ひし形(分岐)は、角を丸めない。** 楕円は開始・終了。
- **配色とルール:** axtonliu 版の配色の意味(青=入力、緑=成功、橙=警告、赤=エラー、黄=判断…)、フォントサイズの下限、余白、絵文字を使わない、を取り入れた。出力は、`frameId`・`index`・`versionNonce`・`rawText` を出さない(同じ作者の注意)。フォントの既定は、この Vault の既存の図に合わせて Virgil(`virgil`)。
- **矢印のラベル:** Excalidraw は、矢印の文字を、点の数が奇数なら真ん中の点、偶数なら真ん中の線分の中点に置く。それと同じ位置にしてある(同じ線上に点を足して調整する)。
- 点検(`check`)に、重なり、矢印が箱を横切る、結合の欠け、文字のはみ出し、小さすぎる文字、絵文字を含む。`build` は、作ったあとに自動で点検して「注意」を出す。`--dry-run` なら、何も作らずに検査できる。

## 4. 読む(別のスキル)

図を読む機能(`describe`、`links`。構造のデータを文章にする)は、**スキル `diagram-reader`** に分けた。Excalidraw だけでなく draw.io も読めるため、形式ではなく目的でまとめた。設計は [diagram-reader.md](diagram-reader.md)。このスキルは、作ることと点検だけ。作った図の読み戻しには、`diagram-reader` の `describe` を使う。

## 5. 確認できたこと(自宅の Mac)

- **図の読み取り**(この Vault の既存の図の読み込み。実物のスイムレーン、フローチャートを含む)は、`diagram-reader` で確認した([diagram-reader.md](diagram-reader.md) の 5)。
- **自分で作った図を、読み戻して確かめた**(4 種類 × 向き)。
- **Obsidian で描画して、目で確認した。** この Vault の「HTMLで出力(人間用)」は、Excalidraw の埋め込みを PNG にする。それを API から実行して(`docs/obsidian.md`)、PNG を取り出して見た。日本語、色、矢印の頭、ラベル、レーン、ツリー、マインドマップが、意図どおりに描画された。見て分かった崩れ(分岐ラベルが頂点に重なる、戻る矢印が箱を横切る、ひし形の角が丸い)は、直した。

## 6. 限界と、確認できていないこと

- **自動配置の限界:** ノードが約 30 個を超える複雑な図、矢印が交差する図は、人が手直しする前提。サブグラフ(入れ子の枠)、シーケンス図、ER 図、関係図は、対応していない。
- **見た目は、LLM には確認できない。** 点検は構造だけ。最終確認は、Obsidian で人が行う。
- **既存の図の編集はしない。** 読んで、新しい図として作り直す。
- **まだ確認していないこと:** 会社の Windows での動作、会社の Obsidian・Excalidraw プラグインの版の違い、会社の LLM が、作った図を読み戻して(`diagram-reader`)確かめられるか。会社での確認は [company-checklist.md](company-checklist.md) の 13。

## 7. 出典

土台: axtonliu/axton-obsidian-visual-skills の `excalidraw-diagram`(MIT)。配色の意味、デザインのルール、Obsidian の出力形式を取り入れた。スイムレーンの構成は awesome-copilot のテンプレート、設計の考え方は coleam00/excalidraw-diagram-skill を参考にした(コードは取り入れていない)。詳しくは [NOTICE.md](../skills/excalidraw-diagram/NOTICE.md)。
