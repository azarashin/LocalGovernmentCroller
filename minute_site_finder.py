'''
{
  "data_list": [
    {
      "city": "永平寺町",
      "lap": 5.655498699983582,
      "minutes_urls": [],
      "parliament_pages": [],
      "prefecture": "福井県",
      "url": "https://www.town.eiheiji.lg.jp/"
    },
    {
      "city": "共和町",
      "lap": 7.19101499998942,
      "minutes_urls": [],
      "parliament_pages": [],
      "prefecture": "北海道",
      "url": "https://www.town.kyowa.hokkaido.jp/"
    },
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

'''


import argparse
import json
import sys


def run(path_input: str, path_output: str, path_log: str):
    print(path_input)
    with open(path_input, "r", encoding="utf-8") as f:
        input = json.load(f)
    data_list = input["data_list"]
    result = []
    for data in data_list:
        prefecture = data["prefecture"]
        city = data["city"]
        grand_parent_map = {}
        parent_map = {}
        minutes_urls = data["minutes_urls"]
        for minutes_url in minutes_urls:
            grand_parent = minutes_url["grand-parent"] if "grand-parent" in minutes_url else None
            parent = minutes_url["parent"]
            if grand_parent is not None:
                if not grand_parent in grand_parent_map:
                    grand_parent_map[grand_parent] = 0
                grand_parent_map[grand_parent] += 1
            if not parent in parent_map:
                parent_map[parent] = 0
            parent_map[parent] += 1
            result.append({
                "prefecture": prefecture, 
                "city": city, 
                "grand_parent": {d:grand_parent_map[d] for d in grand_parent_map}, 
                "parent": {d:parent_map[d] for d in parent_map}, 
            })
    with open(path_output, 'w', encoding='utf-8') as f:
        f.write(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))

def main() -> int:
    parser = argparse.ArgumentParser(
        description="議事録サイト数分布分析"
    )
    default_input = './data/minute_list.json'
    default_output = './data/minute_site_list.json'
    default_log = './log/minute_site_list.txt'
    parser.add_argument(
        "--input",
        type=str,
        default=default_input,
        help=f"入力ファイル名。デフォルト: {default_input}",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=default_output,
        help=f"出力ファイル名。デフォルト: {default_output}",
    )
    parser.add_argument(
        "--log",
        type=str,
        default=default_log,
        help=f"ログファイル名。デフォルト: {default_log}",
    )

    args = parser.parse_args()
    run(args.input, args.output, args.log)

if __name__ == "__main__":
    raise SystemExit(main())