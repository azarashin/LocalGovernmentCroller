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

