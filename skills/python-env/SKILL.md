---
name: python-env
description: Python のスクリプトを実行する、パッケージを追加する、仮想環境(.venv)を作るときの手順。pip install を実行する前に使う
---

## 前提

- 会社の PC では、Python が使え、`pip install` も使える。接続先は PyPI に直接(ミラーではない)。
- インターネットを使える。Node.js のパッケージ(`npm install`・`npx`)も使える(このスキルは、Python の手順だけを扱う)。

## 仮想環境(`.venv`)

1. プロジェクト直下に `.venv/` があるか確認する。なければ作る。

   ```
   python -m venv .venv
   ```

2. 実行は、有効化せず、venv の Python を直接指定する。
   - Windows: `.venv\Scripts\python スクリプト名.py`
   - Mac: `.venv/bin/python スクリプト名.py`
3. `.venv/` が `.gitignore` に入っていることを確認する。なければ足す。
4. 作れない事情があるときは、システムの Python に入れる前に、ユーザーに確認する。

### ハーネス自身のスクリプトの `.venv`

Outlook やファイル検索のスキルのスクリプトは、ハーネスのフォルダ(`.config\opencode\`)直下の `.venv` で動く(各スキルの `run.bat` がここを探す)。ここは、プロジェクトの `.venv` とは別。作り方は、ハーネスのフォルダで次のとおり。パッケージは `requirements.lock` にあるものだけを入れる。

```
python -m venv .venv
.venv\Scripts\python -m pip install --require-hashes -r requirements.lock
```

## パッケージを追加する

1. 標準ライブラリで足りないかを、先に考える。
2. 入れてよいのは、次のどちらかだけ。
   - プロジェクトの `requirements.txt`(または `requirements.in`)にすでにあるパッケージ
   - ユーザーが名前を指定したパッケージ

   自分で思いついた名前は入れない。実在しない名前や、似た名前の別のパッケージを入れてしまう恐れがある。
3. 上の 2 つに合うなら、確認なしに入れる(入れたパッケージ名とバージョンは、報告に書く)。
4. バージョンを指定して、venv の中に入れる。

   ```
   .venv\Scripts\python -m pip install パッケージ名==バージョン
   ```

5. `requirements.in`(または `requirements.txt`)に、名前とバージョンを書き足す。
6. 接続先(`--index-url`、`--extra-index-url`)をコマンドで指定しない。接続先は、会社の PC の設定(`pip.ini` や環境変数 `PIP_INDEX_URL`)に従う。

## 守ること

- システムの Python にパッケージを入れない。
- `requirements` に書かずに、パッケージを入れない。
- できなかったこと(接続できなかった、など)は、そのまま報告する。別の方法で回避しようとしない。
