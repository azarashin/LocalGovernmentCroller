# LocalGovernmentCroller <!-- omit from toc -->

- [1. 自治体サイトのURLについて](#1-自治体サイトのurlについて)
- [2. 使い方](#2-使い方)
  - [2.1. 議事録のURL一覧候補を取得する](#21-議事録のurl一覧候補を取得する)
    - [2.1.1. 出力データ仕様](#211-出力データ仕様)
      - [2.1.1.1. データ全体](#2111-データ全体)
      - [2.1.1.2. data\_list の要素](#2112-data_list-の要素)
      - [2.1.1.3. minutes\_urls の要素](#2113-minutes_urls-の要素)
  - [2.2. 議事録のリンク元サイトを特定する](#22-議事録のリンク元サイトを特定する)
    - [2.2.1. 出力データ仕様](#221-出力データ仕様)
      - [2.2.1.1. データ全体](#2211-データ全体)
  - [議事録をクローリングで収集する](#議事録をクローリングで収集する)


## 1. 自治体サイトのURLについて

下記から取得しています。

https://github.com/kebhr/localgovlistjp

## 2. 使い方

### 2.1. 議事録のURL一覧候補を取得する

localgovlistjp ディレクトリはいかにlocalgov_utf8_lf.csv が格納されていることが前提です。

```bash
$ python minute_list_loader.py --help

議事録URL抽出（サンプル骨組み）

options:
  -h, --help            show this help message and exit
  --workers WORKERS     マルチスレッドのワーカー数。デフォルト: 20
  --timeout-sec TIMEOUT_SEC
                        タイムアウトする時間（秒）。デフォルト: 60
  --output OUTPUT       出力ファイル名。デフォルト: ./data/minute_list.json
  --log LOG             ログファイル名。デフォルト: ./log/minute_list.txt


```

```bash
$ python minute_list_loader.py

```

上記を実行すると下記のようなjson ファイルが生成されます。

```json

{
  "data_list": [
    {
      "city": "美里町",
      "lap": 35.54459900013171,
      "minutes_urls": [],
      "parliament_pages": [
        "https://www.town.kumamoto-misato.lg.jp/gyosei/gikai/index.html"
      ],
      "prefecture": "熊本県",
      "url": "https://www.town.kumamoto-misato.lg.jp/"
    },
    {
      "city": "壬生町",
      "lap": 61.91181969991885,
      "minutes_urls": [
        {
          "grand-parent": "http://www.town.mibu.tochigi.jp/category/bunya/gyoseimachizukuri/gikai/",
          "link": "http://www.town.mibu.tochigi.jp/docs/2015022400149/file_contents/R07reaf.pdf",
          "parent": "http://www.town.mibu.tochigi.jp/docs/2015022400149//"
        },
        {
          "grand-parent": "http://www.town.mibu.tochigi.jp/category/bunya/gyoseimachizukuri/gikai/",
          "link": "http://www.town.mibu.tochigi.jp/docs/2020040900014/file_contents/r7.pdf",
          "parent": "http://www.town.mibu.tochigi.jp/docs/2020040900014/"
        },
        ...
      ]
    },
    ...
  ], 
  "total_lap": 100.5112590000499
}
```

#### 2.1.1. 出力データ仕様

##### 2.1.1.1. データ全体

| キー名 | 概要 |
| --- | --- |
| data_list | 各都市の議事録情報 |
| total_lap | 抽出に要した全時間(秒) |

##### 2.1.1.2. data_list の要素

| キー名 | 概要 |
| --- | --- |
| prefecture | 都道府県名 |
| city | 都市名 |
| lap | その都市の議事録リストを抽出するのに要した時間（秒）timeout-sec を超えている場合はタイムアウトにより探索が打ち切られた。 |
| minutes_urls | 議事録のURL関連情報の一覧 |
| parliament_pages | 議会のURL一覧 |

##### 2.1.1.3. minutes_urls の要素

| キー名 | 概要 |
| --- | --- |
| link | 議事録と思われるファイルへのリンク（議事録かどうかは別途検証が必要） |
| parent | 議事録と思われるファイルが掲載されているサイトのURL |
| grand-parent | parent が掲載されているサイトのURL |


### 2.2. 議事録のリンク元サイトを特定する

minute_list_loader.py の出力ファイルが生成されていることが前提です。

```bash
$ python minute_site_finder.py --help
usage: minute_site_finder.py [-h] [--input INPUT] [--output OUTPUT] [--log LOG]

議事録サイト数分布分析

options:
  -h, --help       show this help message and exit
  --input INPUT    入力ファイル名。デフォルト: ./data/minute_list.json
  --output OUTPUT  出力ファイル名。デフォルト: ./data/minute_site_list.json
  --log LOG        ログファイル名。デフォルト: ./log/minute_site_list.txt
```

```bash
$ python minute_site_finder.py
```

上記を実行すると下記のようなjson ファイルが生成されます。

```json
[
  {
    "city": "壬生町",
    "grand_parent": {
      "http://www.town.mibu.tochigi.jp/category/bunya/gyoseimachizukuri/gikai/": 1
    },
    "parent": {
      "http://www.town.mibu.tochigi.jp/docs/2015022400149//": 1
    },
    "prefecture": "栃木県"
  },
  {
    "city": "壬生町",
    "grand_parent": {
      "http://www.town.mibu.tochigi.jp/category/bunya/gyoseimachizukuri/gikai/": 2
    },
    "parent": {
      "http://www.town.mibu.tochigi.jp/docs/2015022400149//": 1,
      "http://www.town.mibu.tochigi.jp/docs/2020040900014/": 1
    },
    "prefecture": "栃木県"
  },
  ...
]
```


#### 2.2.1. 出力データ仕様

##### 2.2.1.1. データ全体

議事録と思われるファイルのことを「議事録」と表記します。

| キー名 | 概要 |
| --- | --- |
| prefecture | 都道府県名 |
| city | 都市名 |
| parent | 「議事録が掲載されているサイトのURLと、含まれていた議事録の数」の一覧 |
| grand-parent | 「parent が掲載されているサイトのURLと、含まれていた議事録の数」の一覧 |


### 議事録をクローリングで収集する

```bash
$ python minute_finder.py --help
usage: minute_finder.py [-h] [--input INPUT] [--outdir OUTDIR] [--manifest MANIFEST] [--overwrite-manifest]
                        [--threshold THRESHOLD] [--max-depth MAX_DEPTH] [--max-pages MAX_PAGES]
                        [--delay DELAY] [--timeout TIMEOUT] [--no-download] [--resume] [--no-resume]
                        [--skip-completed-seeds] [--no-skip-completed-seeds] [--force-crawl]
                        [--force-download] [--respect-robots] [--no-respect-robots] [--same-domain-only]        
                        [--same-path-prefix-only] [--user-agent USER_AGENT] [--keywords KEYWORDS]
                        [--file-exts FILE_EXTS] [--url-hints URL_HINTS] [--report-dir REPORT_DIR]
                        [--workers WORKERS]

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
  --no-download         ダウンロードせずリンク収集のみ
  --resume              manifest を見て再開（デフォルトON）
  --no-resume           resume を無効化（毎回最初から）
  --skip-completed-seeds
                        manifest上で完了済みseedはスキップ（デフォルトON）
  --no-skip-completed-seeds
                        完了済みseedも再クロールする（seed単位のスキップ無効）
  --force-crawl         完了済みseedでも強制的にクロール（= seedスキップを無視）
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

```bash
$ python minute_finder.py
```

使い方例
1) parent が合計 5 件以上なら parent、未満なら grand_parent（デフォルト）
python minute_extractor.py --input data/minute_link_list.json

2) 閾値を 20 にしたい／ドメイン外に出ない（推奨）
python minute_extractor.py \
  --input data/minute_link_list.json \
  --threshold 20 \
  --same-domain-only

3) ダウンロードせず「議事録リンク収集」だけ（軽量）
python minute_extractor.py --no-download --same-domain-only

出力

data/minutes_out/{都道府県}/{市町}/files/ … PDF等の保存先

data/minutes_out/{都道府県}/{市町}/pages/ … 巡回したHTMLの保存先（--no-download だと作りません）

data/minutes_out/manifest.jsonl … 収集・保存したリンク/ファイル/エラーのログ（JSONL）

実運用で効く調整ポイント（最初に触るならここ）

--threshold：parent を採用する閾値

--keywords：議事録判定キーワード（「会議結果」等も自治体により有効）

--max-depth / --max-pages：カテゴリページ→一覧→個別…の段数に合わせる

--same-domain-only：基本は ON 推奨（無駄な外部リンク巡回を防止）