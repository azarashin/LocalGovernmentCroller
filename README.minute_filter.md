# minute_filter.py 使い方（議事録候補抽出ツール）

指定した `root` ディレクトリ配下を走査して、**議事録っぽい**ファイルをスコアリングし、候補リストを **JSON または CSV** で出力します。

想定ディレクトリ構造：

```

root
|- 都道府県名
|- 市町村名
|- 議事録かもしれないファイル

````

- `root/都道府県名/市町村名/` 配下にあるファイルを対象にします
- 市町村配下にサブフォルダがあっても `rglob("*")` で再帰探索します


---

## 1. 実行方法

### 基本（JSON出力、閾値30以上のみ）
```bash
python minute_filter.py --root "D:\data\root" --out minutes_candidates.json
````

### CSVで出力（Excelで確認しやすい）

```bash
python minute_filter.py --root "D:\data\root" --out minutes_candidates.csv
```

### 閾値未満も含めて全件を出力（スコア確認・調整用）

```bash
python minute_filter.py --root "D:\data\root" --out all_scored.json --include-low
```

### 対象拡張子を限定（例：PDFとTXTだけ）

```bash
python minute_filter.py --root "D:\data\root" --ext-allow ".pdf,.txt" --out minutes.json
```

### 除外拡張子を指定（例：画像を除外）

```bash
python minute_filter.py --root "D:\data\root" --ext-deny ".jpg,.png,.gif" --out minutes.json
```

---

## 2. オプション一覧

| オプション             | 必須 |                     デフォルト | 説明                               |
| ----------------- | -: | ------------------------: | -------------------------------- |
| `--root`          |   |`./data/minutes_out` | 走査対象の root ディレクトリ（都道府県フォルダが並ぶ場所） |
| `--out`           |    | `./data/minutes_candidates.json` | 出力ファイル名（`.json` or `.csv`）       |
| `--threshold`     |    |                      `30` | 議事録とみなすスコア閾値                     |
| `--include-low`   |    |                        なし | 閾値未満も含めて全件を出力（スコア付き）             |
| `--max-bytes`     |    |                 `2000000` | テキスト抽出の最大バイト（テキスト系、HTML等）        |
| `--max-pdf-pages` |    |                       `8` | PDF抽出の最大ページ数（先頭から）               |
| `--min-size`      |    |                       `1` | 無視する最小ファイルサイズ（bytes）             |
| `--ext-allow`     |    |                         空 | 許可拡張子（カンマ区切り）。空なら全て対象            |
| `--ext-deny`      |    |                         空 | 除外拡張子（カンマ区切り）。空なら除外なし            |

---

## 3. 判定ロジックの概要（ざっくり）

* ファイル名のヒント（例：`議事録`, `会議録`, `minutes`, 日付っぽい文字列 など）
* 本文のキーワード（例：`議題`, `出席者`, `開催日時`, `議決`, `質疑` など）
* 議事録の見出しっぽい行（`議題:` や `開催日時:` のようなメタ情報行）
* 逆に議事録ではない可能性が高い単語（例：`請求書`, `見積書`, `契約書` など）は減点

これらの合計点が `--threshold` 以上なら「議事録候補」として採用します。

---

## 4. 対応ファイル形式（テキスト抽出）

### 依存なしで対応（そのまま読める）

* `.txt`, `.md`, `.csv`, `.tsv`, `.json`, `.log`, `.xml`, `.yaml`, `.yml`, `.html`, `.htm`

### 任意依存を入れると対応（入ってなければスキップ/弱判定）

* `.pdf`（`pypdf` がある場合のみテキスト抽出）
* `.docx`（`python-docx` がある場合のみテキスト抽出）
* `.html/.htm` は `beautifulsoup4` があればより良い抽出（無ければ簡易タグ除去）

依存を入れる場合：

```bash
pip install pypdf python-docx beautifulsoup4
```

---

## 5. 出力フォーマット

### 5.1 JSON出力フォーマット（`.json`）

出力JSONのトップレベル構造：

```json
{
  "root": "走査したrootの絶対パス",
  "threshold": 30,
  "count": 123,
  "items": [
    {
      "prefecture": "都道府県名",
      "municipality": "市町村名",
      "path": "ファイルの絶対パス",
      "rel_path": "rootからの相対パス",
      "ext": ".pdf",
      "size_bytes": 123456,
      "score": 45,
      "matched": ["議事録", "出席者", "議題", "metadata_line"],
      "filename_hit": ["jp_minutes_word", "date_like"],
      "snippet": "判定に使われたそれっぽい行の抜粋（最大約300文字）"
    }
  ]
}
```

各フィールドの意味：

| フィールド       | 型      | 内容                   |
| ----------- | ------ | -------------------- |
| `root`      | string | 走査したrootディレクトリ（絶対パス） |
| `threshold` | number | 判定に使ったスコア閾値          |
| `count`     | number | 出力された件数（itemsの数）     |
| `items`     | array  | 候補ファイルの配列            |

`items[*]` の要素：

| フィールド          | 型             | 内容                                              |
| -------------- | ------------- | ----------------------------------------------- |
| `prefecture`   | string        | 都道府県フォルダ名                                       |
| `municipality` | string        | 市町村フォルダ名                                        |
| `path`         | string        | ファイルの絶対パス                                       |
| `rel_path`     | string        | rootからの相対パス（例：`東京都/渋谷区/xxx.pdf`）                |
| `ext`          | string        | 拡張子（小文字）                                        |
| `size_bytes`   | number        | ファイルサイズ（bytes）                                  |
| `score`        | number        | 議事録らしさスコア（大きいほど議事録っぽい）                          |
| `matched`      | array[string] | 本文由来の一致ワードや見出しヒント（`NEG:請求書` のような減点要素も含む）        |
| `filename_hit` | array[string] | ファイル名由来のヒット種別（例：`jp_minutes_word`, `date_like`） |
| `snippet`      | string        | 検出根拠の抜粋（確認用）                                    |

> `--include-low` を付けない場合、`score >= threshold` のものだけ `items` に入ります。
> `--include-low` を付けた場合、スコアに関わらず全件が `items` に入ります（ただしサイズや拡張子フィルタには従います）。

---

### 5.2 CSV出力フォーマット（`.csv`）

ヘッダ行：

```csv
prefecture,municipality,rel_path,ext,size_bytes,score,filename_hit,matched,snippet
```

各列の意味：

| 列              | 内容           |        |
| -------------- | ------------ | ------ |
| `prefecture`   | 都道府県フォルダ名    |        |
| `municipality` | 市町村フォルダ名     |        |
| `rel_path`     | rootからの相対パス  |        |
| `ext`          | 拡張子          |        |
| `size_bytes`   | サイズ（bytes）   |        |
| `score`        | 議事録らしさスコア    |        |
| `filename_hit` | ファイル名ヒット種別（` | ` 区切り） |
| `matched`      | 本文ヒット語/ヒント（` | ` 区切り） |
| `snippet`      | 抜粋（最大約300文字） |        |

---

## 6. 実行時の標準出力（ログ）

実行すると、以下のように結果サマリが標準出力に出ます。

* 走査root
* 出力先
* 抽出件数
* スコア上位10件（`[TOP 10]`）

例：

```
[OK] root=D:\data\root
[OK] out=D:\out\minutes_candidates.json
[OK] extracted=42 files
[TOP 10]
  score= 78  東京都/渋谷区/令和6年度_第3回_○○委員会_議事録.pdf
  ...
```

---

## 7. よくある調整ポイント

* 抽出が多すぎる：`--threshold` を上げる（例：40〜60）
* 抽出が少なすぎる：`--threshold` を下げる（例：20〜25）
* PDFがほとんど抽出されない：`pypdf` を入れる、`--max-pdf-pages` を増やす
* まず全件のスコア分布を見たい：`--include-low` で全件出力してExcel/JSONで確認

---

```
```
