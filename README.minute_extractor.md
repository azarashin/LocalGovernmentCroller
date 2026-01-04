# minute_extractor.py 使い方ガイド

`minute_site_finder.py` が出力した JSON（例: `data/minute_site_list.json`）を入力にして、  
**議事録っぽいリンクの収集**と（任意で）**議事録本体ファイルのダウンロード**を行うクローラです。

重要ポイント：
- **seed（起点URL＝一覧ページ）**が更新されている場合、`seed_done` 済みでも **再クロール**できます（デフォルトON）
- ただし議事録本体（PDF等）は **DL済みなら再DLしない**（resume時）
- さらに `--no-download-files` で **本体DLを完全に止める**ことも可能

---

## 1. 前提（入力ファイル）

入力は `minute_site_finder.py` の出力 JSON です。

- 既定：`data/minute_link_list.json`

各要素は概ね次の形を想定しています：

- `prefecture`: 都道府県名
- `city`: 市区町村名
- `parent`: `{ URL: 議事録数, ... }`
- `grand_parent`: `{ URL: 議事録数, ... }`

---

## 2. 最短実行（推奨）

### 2.1 robots尊重 + 同一ドメインのみ + 8スレッド
```bash
python minute_extractor.py --same-domain-only --workers 8
````

---

## 3. 出力先と生成物

### 3.1 出力先（デフォルト）

* `--outdir` 既定：`data/minutes_out`

### 3.2 主な生成物

* `data/minutes_out/<都道府県>/<市区町村>/pages/`

  * 取得したHTML（`--no-download` を付けない場合）
* `data/minutes_out/<都道府県>/<市区町村>/files/`

  * ダウンロードした議事録本体ファイル（PDF等、条件に合うリンク）
* `data/minutes_out/manifest.jsonl`

  * 実行ログ（JSONL）
* `data/minutes_out/reports/`

  * robots で禁止されたURL集計レポート

---

## 4. robots 禁止URLレポート（実行終了時に自動出力）

デフォルトでは `outdir/reports/` に以下が出ます：

* `robots_disallow_urls.jsonl`

  * 禁止URLの全件リスト（prefecture/city/netloc/path_prefix/url）
* `robots_disallow_summary.json`

  * 上位集計（市区町村別 / ドメイン別 / パス先頭別）
* `robots_disallow_by_city.csv`
* `robots_disallow_by_domain.csv`
* `robots_disallow_by_path_prefix.csv`

---

## 5. よく使う実行例

### 5.1 入力ファイルを指定

```bash
python minute_extractor.py \
  --input data/minute_link_list.json \
  --same-domain-only \
  --workers 8
```

### 5.2 ダウンロードせず、リンク収集＋manifestだけ（高速）

```bash
python minute_extractor.py \
  --no-download \
  --same-domain-only \
  --workers 16
```

### 5.3 本体ファイルはダウンロードしない（リンク収集＋ページ保存はする）

「一覧は追従したいがPDFは落としたくない」場合。

```bash
python minute_extractor.py \
  --no-download-files \
  --same-domain-only \
  --workers 8
```

### 5.4 robots を無視（非推奨。必要時のみ）

```bash
python minute_extractor.py \
  --no-respect-robots \
  --same-domain-only \
  --workers 8
```

### 5.5 parent / grand_parent の採用基準（threshold）

`parent` の議事録数合計が `--threshold` 以上なら `parent` のURLを seed（起点）として優先します。

```bash
python minute_extractor.py \
  --threshold 10 \
  --same-domain-only \
  --workers 8
```

### 5.6 クロール範囲を狭める（起点パス配下のみ）

関係ないページに広がりやすい場合。

```bash
python minute_extractor.py \
  --same-domain-only \
  --same-path-prefix-only \
  --workers 8
```

### 5.7 深さ・ページ数を調整

```bash
python minute_extractor.py \
  --max-depth 1 \
  --max-pages 80 \
  --same-domain-only \
  --workers 8
```

### 5.8 丁寧にアクセス（間隔を広げる）

`--delay` は最小間隔。robots の `Crawl-delay` があれば「大きい方」を採用します。

```bash
python minute_extractor.py \
  --delay 1.5 \
  --same-domain-only \
  --workers 8
```

### 5.9 レポート出力先を変える

```bash
python minute_extractor.py \
  --report-dir data/reports \
  --same-domain-only \
  --workers 8
```

---

## 6. 再実行（resume）と「一覧更新追従」の挙動

### 6.1 デフォルト挙動（重要）

* **resume ON（デフォルト）**
* **完了seedスキップ ON（デフォルト）**
* **seed更新チェック ON（デフォルト）**

つまり：

1. 過去に `seed_done` 済みの seed でも、まず「一覧ページが更新されたか」を確認

   * 変化なし → seed をスキップ
   * 変化あり → seed を再クロール
2. 議事録本体ファイル（PDF等）は、過去に `downloaded_file` 記録があるものは **再DLしない**
   （`--force-download` を付けたときだけ再DL）

### 6.2 seed更新チェックを無効化（常にseedをスキップ）

```bash
python minute_extractor.py --no-recheck-seeds --same-domain-only --workers 8
```

### 6.3 完了seedも強制クロール（一覧更新判定を無視して全部回す）

```bash
python minute_extractor.py --force-crawl --same-domain-only --workers 8
```

### 6.4 DL済みでも強制再ダウンロード

```bash
python minute_extractor.py --force-download --same-domain-only --workers 8
```

### 6.5 完全に最初からやり直す（manifestも作り直す）

```bash
python minute_extractor.py --overwrite-manifest --no-resume --same-domain-only --workers 8
```

---

## 7. オプション要点一覧

* 入出力

  * `--input` 入力JSON（既定: `data/minute_link_list.json`）
  * `--outdir` 保存先（既定: `data/minutes_out`）
  * `--manifest` manifest出力（既定: `data/minutes_out/manifest.jsonl`）
  * `--report-dir` robotsレポート先（既定: `outdir/reports`）
  * `--overwrite-manifest` manifestを空にしてやり直し

* 収集ロジック

  * `--threshold` parent優先判定（既定: 5）
  * `--max-depth` 巡回深さ（既定: 2）
  * `--max-pages` seedごとの最大ページ取得数（既定: 200）

* 保存/ダウンロード

  * `--no-download` pages/files を一切保存しない（リンク収集のみ）
  * `--no-download-files` 本体ファイルDLだけ止める（ページ保存は可能）
  * `--force-download` DL済みでも再DL

* アクセス制御

  * `--delay` 最小間隔（既定: 0.5）
  * `--timeout` タイムアウト（既定: 20）
  * `--same-domain-only` 同一ドメインのみ巡回（推奨）
  * `--same-path-prefix-only` 起点パス配下のみ巡回（厳しめ）

* robots

  * `--respect-robots` 尊重（デフォルト）
  * `--no-respect-robots` 無視（非推奨）

* 再開/seed制御

  * `--resume` 再開（デフォルト）
  * `--no-resume` 再開しない
  * `--skip-completed-seeds` 完了seedスキップ（デフォルト）
  * `--no-skip-completed-seeds` 完了seedも処理
  * `--force-crawl` 完了seedでも強制クロール
  * `--recheck-seeds` 一覧更新をチェックして必要なら再クロール（デフォルト）
  * `--no-recheck-seeds` 一覧更新チェックを無効化

* 並列

  * `--workers` seed並列数（既定: 8）

---

## 8. 推奨運用パターン

### 8.1 「一覧は更新されれば追従」＋「PDF等は落としたくない」

```bash
python minute_extractor.py --same-domain-only --workers 8 --no-download-files
```

### 8.2 「一覧は更新されれば追従」＋「新規PDFだけ落としたい」

```bash
python minute_extractor.py --same-domain-only --workers 8
```




## 9. --help 実行結果

```bash
usage: minute_finder.py [-h] [--input INPUT] [--outdir OUTDIR] [--manifest MANIFEST] [--overwrite-manifest] [--threshold THRESHOLD] [--max-depth MAX_DEPTH] [--max-pages MAX_PAGES] [--delay DELAY] [--timeout TIMEOUT] [--no-download]
                        [--no-download-files] [--resume] [--no-resume] [--skip-completed-seeds] [--no-skip-completed-seeds] [--force-crawl] [--recheck-seeds] [--no-recheck-seeds] [--force-download] [--respect-robots] [--no-respect-robots]
                        [--same-domain-only] [--same-path-prefix-only] [--user-agent USER_AGENT] [--keywords KEYWORDS] [--file-exts FILE_EXTS] [--url-hints URL_HINTS] [--report-dir REPORT_DIR] [--workers WORKERS]

options:
  -h, --help            show this help message and exit
  --input INPUT         minute_site_finder.py の出力JSON
  --outdir OUTDIR       保存先ディレクトリ
  --manifest MANIFEST   結果ログ(JSONL)
  --overwrite-manifest  manifest を作り直す（追記しない）
  --threshold THRESHOLD
                        parentの議事録数合計がこの値以上ならparent優先
  --max-depth MAX_DEPTH
                        巡回深さ（0=起点ページのみ）
  --max-pages MAX_PAGES
                        起点URLごとの最大取得ページ数
  --delay DELAY         最小リクエスト間隔（秒）。robotsのCrawl-delayがあれば大きい方
  --timeout TIMEOUT     HTTPタイムアウト（秒）
  --no-download         保存（pages/files）を一切行わず、リンク収集＋manifest記録のみ
  --no-download-files   議事録本体（pdf/doc等）のダウンロードを行わない（ページ保存は可）
  --resume              manifest を見て再開（デフォルトON）
  --no-resume           resume を無効化（毎回最初から）
  --skip-completed-seeds
                        manifest上で完了済みseedはスキップ（デフォルトON）
  --no-skip-completed-seeds
                        完了済みseedも処理（seed単位のスキップ無効）
  --force-crawl         完了済みseedでも強制的にクロール（seedスキップ無視）
  --recheck-seeds       seed_done でも seed(一覧)が更新されていれば再クロール（デフォルトON）
  --no-recheck-seeds    seed_done は更新チェックせず常にスキップ
  --force-download      DL済みでも強制的に再ダウンロードする
  --respect-robots      robots.txt を尊重する（デフォルト）
  --no-respect-robots   robots.txt を無視する
  --same-domain-only    起点と同一ドメインのみ巡回（推奨）
  --same-path-prefix-only
                        起点URLのパス配下のみ巡回（厳しめ）
  --user-agent USER_AGENT
                        User-Agent
  --keywords KEYWORDS   議事録判定キーワード（カンマ区切り）
  --file-exts FILE_EXTS
                        ファイル拡張子（カンマ区切り）
  --url-hints URL_HINTS
                        URLヒント語（カンマ区切り）
  --report-dir REPORT_DIR
                        レポート出力先（空なら outdir/reports）
  --workers WORKERS     seed並列実行数（スレッド数）
```