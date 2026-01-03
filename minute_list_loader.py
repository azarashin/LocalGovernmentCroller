import argparse
from concurrent.futures import ThreadPoolExecutor
from itertools import repeat
import json
import random
import sys
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
import re
import time

from city import CityManager
'''
# 概要
自治体ページから議事録候補のURLを抽出する。
自治体ページの一覧はOSSで提供されているものを使用する。

{
  "data_list": [
    {
      "city": "山辺町",
      "parliament_pages": [
        "https://www.town.yamanobe.yamagata.jp/site/gikai/"
      ],
      "lap": 61.36520940018818,
      "minutes_urls": [
        {
          "grand-parent": "https://www.town.yamanobe.yamagata.jp/site/gikai/",
          "link": "https://www.town.yamanobe.yamagata.jp/uploaded/life/15757_38598_misc.pdf",
          "parent": "https://www.town.yamanobe.yamagata.jp/site/gikai/20250901gikaimeibo.html"
        },
        ...,
      }
    }
  ]
}
        
'''


HEADERS = {"User-Agent": "Mozilla/5.0"}

count = 0
max = 0

# ----------------------------------------
# HTML取得
# ----------------------------------------
def fetch_html(url):
    time.sleep(0.5)
    try:
        print(f'-> {url}')
        res = requests.get(url, headers=HEADERS, timeout=10)
        if res.status_code == 200:
            return res.text
    except Exception:
        pass
    print(f'<- {url}')
    return None

# ----------------------------------------
# 同一ドメインのリンク抽出
# ----------------------------------------
def extract_links(base_url, html):
    soup = BeautifulSoup(html, "html.parser")
    links = set()

    for a in soup.find_all("a", href=True):
        href = a["href"]
        full = urljoin(base_url, href)

        # 同一ドメインのみ
        if urlparse(full).netloc == urlparse(base_url).netloc:
            links.add(full)

    return list(links)

# ----------------------------------------
# キーワードフィルタ
# ----------------------------------------
def filter_links(links, keywords):
    pattern = "|".join(keywords)
    return [url for url in links if re.search(pattern, url, re.IGNORECASE)]

# ----------------------------------------
# 議会ページを推測して直接チェック
# ----------------------------------------
def guess_parliament_pages(base_url):
    candidates = [
        "gikai", "shigikai", "assembly", "council",
        "議会", "市議会", "町議会", "村議会"
    ]

    found = []
    for c in candidates:
        test = urljoin(base_url, f"/{c}/")
        try:
            res = requests.head(test, timeout=5)
            if res.status_code < 400:
                found.append(test)
        except:
            pass

    return found

# ----------------------------------------
# メイン処理：議事録抽出
# ----------------------------------------
def extract_minutes(base_url, timeout_sec: float = 60.0):
    global count
    start = time.perf_counter()
    print(f"=== Start: {base_url} ===")

    html = fetch_html(base_url)
    if not html:
        print("× トップページ取得失敗")
        return base_url, [], {}, -1

    # トップページのリンク抽出
    links = extract_links(base_url, html)

    # ----------------------------------------
    # 1. 議会ページ候補（リンク探索）
    # ----------------------------------------
    parliament_keywords = [
        "議会", "市議会", "町議会", "村議会",
        "gikai", "assembly", "shigikai"
    ]
    parliament_pages = filter_links(links, parliament_keywords)

    # ----------------------------------------
    # 2. 議会ページ候補（推測）
    # ----------------------------------------
    guessed = guess_parliament_pages(base_url)
    parliament_pages.extend(guessed)
    parliament_pages = list(set(parliament_pages))

#    print(f"議会ページ候補: {len(parliament_pages)} 件")

    # ----------------------------------------
    # 3. 議事録ページ抽出
    # ----------------------------------------
    minutes_keywords = [
        "議事録", "会議録", "会議資料", "本会議",
        "minutes", "minutes", "kaigiroku",
        "pdf"
    ]

    minutes_urls = {}

    for gpage in parliament_pages:
        html2 = fetch_html(gpage)
        if not html2:
            continue

        # 議会ページのリンク抽出
        links2 = extract_links(gpage, html2)

        # 議事録候補
        candidates = filter_links(links2, minutes_keywords)
        for c in candidates:
            minutes_urls[c] = {"parent": gpage, "link": c}
            end = time.perf_counter()
            if end - start > timeout_sec:
                global count
                count += 1 
                print(f'{count}/{max} (timeout)')
                return base_url, list(parliament_pages), minutes_urls, end - start

        # さらに1階層深く探索
        for l2 in links2:
            html3 = fetch_html(l2)
            if not html3:
                continue
            links3 = extract_links(l2, html3)
            candidates2 = filter_links(links3, minutes_keywords)
            for c in candidates2:
                minutes_urls[c] = {"grand-parent": gpage, "parent": l2, "link": c}
                end = time.perf_counter()
                if end - start > timeout_sec:
                    count += 1 
                    print(f'{count}/{max} (timeout)')
                    return base_url, list(parliament_pages), minutes_urls, end - start

#    print(f"議事録候補URL: {len(minutes_urls)} 件")

    # マルチスレッドで動かすとカウンタが壊れるかもしれないが、
    # 目安程度に把握できればよいのでスレッドセーフじゃない状態で足していく
    count += 1 
    print(f'{count}/{max}')
    end = time.perf_counter()
    return base_url, list(parliament_pages), minutes_urls, end - start

def run(max_workers: int, timeout_sec: float, path_output: str, path_log: str):
    city_manager = CityManager()
    random.shuffle(city_manager.data_list) # 同じ県に連続アクセスしないよう順番を入れ替えて適度に分散させる
    global max
    max = len(city_manager.data_list)
    start = time.perf_counter()
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        urls = [d.url for d in city_manager.data_list]
        results = list(executor.map(extract_minutes, urls, repeat(timeout_sec)))
    end = time.perf_counter()

    json_list = []
    with open(path_log, 'w', encoding='utf-8') as f:
        for url, parliament_pages, minutes_urls, lap in results:
            city = city_manager.map[url]
            
            f.write(f"=== 都道府県: {city.prefecture}, 市町村: {city.city} ===")
            f.write(f"URL: {url}")
            f.write(f"Lap: {lap}秒")
            f.write(f"議会ページ候補: {len(parliament_pages)} 件")
            f.write(f"議事録候補URL: {len(minutes_urls.keys())} 件")
            f.write("\n=== 抽出された議事録URL ===\n")
            for u in minutes_urls.values():
                f.write(f'{u["parent"]}\n\t-> {u["link"]}\n')
            json_list.append({
                "prefecture": city.prefecture, 
                "city": city.city, 
                "url": city.url, 
                "parliament_pages": parliament_pages, 
                "minutes_urls": list(minutes_urls.values()), 
                "lap": lap
            })
    
    json_body = {
        "data_list": json_list, 
        "total_lap": end - start
    }
    with open(path_output, 'w', encoding='utf-8') as f:
        f.write(json.dumps(json_body, ensure_ascii=False, indent=2, sort_keys=True))

def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="議事録URL抽出（サンプル骨組み）"
    )
    default_workers = 20
    default_timeout = 60
    default_data = './data/minute_list.json'
    default_log = './log/minute_list.txt'
    parser.add_argument(
        "--workers",
        type=int,
        default=default_workers,
        help=f"マルチスレッドのワーカー数。デフォルト: {default_workers}",
    )
    parser.add_argument(
        "--timeout-sec",
        type=float,
        default=default_timeout,
        help=f"タイムアウトする時間（秒）。デフォルト: {default_timeout}",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=default_data,
        help=f"出力ファイル名。デフォルト: {default_data}",
    )
    parser.add_argument(
        "--log",
        type=str,
        default=default_log,
        help=f"ログファイル名。デフォルト: {default_log}",
    )

    args = parser.parse_args(argv)
    run(args.workers, args.timeout_sec, args.output, args.log)

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))