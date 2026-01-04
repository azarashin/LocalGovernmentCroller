#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
minutes_finder.py

root
|- 都道府県名
    |- 市町村名
        |- 議事録かもしれないファイル

上記ディレクトリを走査して「議事録と思われる」ファイルを抽出し、JSON/CSVで出力します。

対応（任意）:
- .txt / .md / .csv / .json / .log など: そのままテキストとして判定
- .pdf: pypdf が入っていればテキスト抽出して判定
- .docx: python-docx が入っていればテキスト抽出して判定
- .html/.htm: BeautifulSoup が入っていればテキスト抽出して判定（無ければ簡易抽出）

依存を入れない場合でも、テキスト系ファイルだけは動きます。
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
from dataclasses import dataclass, asdict
from pathlib import Path
import time
from typing import Dict, List, Optional, Tuple


# --------------------------
# オプション依存（入っていれば使う）
# --------------------------
def _try_import_pypdf():
    try:
        from pypdf import PdfReader  # type: ignore
        return PdfReader
    except Exception:
        return None


def _try_import_docx():
    try:
        import docx  # type: ignore
        return docx
    except Exception:
        return None


def _try_import_bs4():
    try:
        from bs4 import BeautifulSoup  # type: ignore
        return BeautifulSoup
    except Exception:
        return None


# --------------------------
# データ構造
# --------------------------
@dataclass
class Candidate:
    prefecture: str
    municipality: str
    rel_path: str
    ext: str
    size_bytes: int
    score: int
    matched: List[str]
    filename_hit: List[str]
    snippet: str


# --------------------------
# 議事録スコアリング（ルールベース）
# --------------------------
MINUTES_KEYWORDS: Dict[str, int] = {
    "議事録": 25,
    "会議録": 22,
    "議事要旨": 18,
    "会議要旨": 18,
    "会議概要": 14,
    "議事次第": 14,
    "次第": 8,
    "開催日時": 12,
    "開催日": 8,
    "日時": 4,
    "場所": 5,
    "会場": 5,
    "出席者": 12,
    "欠席者": 8,
    "委員": 7,
    "事務局": 6,
    "議題": 10,
    "報告事項": 8,
    "審議": 8,
    "協議": 7,
    "決定": 7,
    "議決": 10,
    "承認": 8,
    "質疑": 9,
    "意見": 6,
    "資料": 4,
    "配布資料": 6,
    "傍聴": 6,
    "公開": 3,
    "非公開": 3,
    "要旨": 6,
    "概要": 4,
}

NEGATIVE_KEYWORDS: Dict[str, int] = {
    "請求書": -25,
    "見積書": -25,
    "納品書": -25,
    "領収書": -25,
    "仕様書": -15,
    "設計書": -15,
    "契約書": -20,
    "提案書": -12,
    "マニュアル": -10,
    "求人": -12,
    "履歴書": -20,
    "請求": -12,
}

# ファイル名に含まれると強い手掛かりになるパターン
FILENAME_PATTERNS: List[Tuple[re.Pattern, int, str]] = [
    (re.compile(r"(議事録|会議録|議事要旨|会議要旨)"), 18, "jp_minutes_word"),
    (re.compile(r"(minutes|minute|gijiroku|kaigiroku)", re.IGNORECASE), 12, "en_minutes_word"),
    (re.compile(r"(令和|平成|昭和)\d+年"), 4, "era_year"),
    (re.compile(r"\d{4}[./-]\d{1,2}[./-]\d{1,2}"), 4, "date_like"),
    (re.compile(r"(第\d+回)"), 5, "meeting_count"),
]

# 本文側の「見出しっぽい」行（議事録にありがち）
HEADING_HINTS: List[Tuple[re.Pattern, int, str]] = [
    (re.compile(r"^\s*(1[.)]|２[.)]|2[.)]|Ⅰ|Ⅱ|III|第\d+)\s*"), 2, "outline_like"),
    (re.compile(r"^\s*(議題|報告事項|審議事項|協議事項|議事)\s*[:：]?\s*$"), 6, "section_heading"),
    (re.compile(r"^\s*(開催日時|日時|場所|出席者|欠席者)\s*[:：]"), 6, "metadata_line"),
]


def _normalize_text(text: str) -> str:
    # 連続空白を軽く整理
    text = text.replace("\u3000", " ")
    text = re.sub(r"[ \t]+", " ", text)
    return text


def score_minutes(text: str, filename: str) -> Tuple[int, List[str], List[str], str]:
    """
    戻り値:
      score, matched_keywords, filename_hits, snippet
    """
    matched: List[str] = []
    filename_hit: List[str] = []

    score = 0
    text_n = _normalize_text(text)
    # 長すぎると重いので先頭中心に評価（ただしキーワードは全文も軽く見る）
    head = text_n[:20000]
    lines = head.splitlines()

    # ファイル名パターン
    for pat, w, tag in FILENAME_PATTERNS:
        if pat.search(filename):
            score += w
            filename_hit.append(tag)

    # キーワード（本文）
    for kw, w in MINUTES_KEYWORDS.items():
        if kw in head:
            score += w
            matched.append(kw)

    for kw, w in NEGATIVE_KEYWORDS.items():
        if kw in head:
            score += w
            matched.append(f"NEG:{kw}")

    # 見出しっぽい行のヒント
    for line in lines[:300]:
        for pat, w, tag in HEADING_HINTS:
            if pat.search(line):
                score += w
                if tag not in matched:
                    matched.append(tag)

    # スニペット（それっぽい行を優先）
    snippet = ""
    prefer_lines = []
    for line in lines:
        if any(k in line for k in ("議事録", "会議録", "議事要旨", "会議要旨", "開催日時", "出席者", "議題", "議事")):
            s = line.strip()
            if s:
                prefer_lines.append(s)
        if len(prefer_lines) >= 5:
            break
    if prefer_lines:
        snippet = " / ".join(prefer_lines)[:300]
    else:
        snippet = re.sub(r"\s+", " ", head.strip())[:300]

    # それっぽい最低限の長さ補正（短すぎるメモを弾く）
    if len(head) < 400:
        score -= 6

    return score, matched, filename_hit, snippet


# --------------------------
# ファイル読み込み（拡張子別）
# --------------------------
TEXT_EXTS = {
    ".txt", ".md", ".csv", ".tsv", ".json", ".log", ".xml", ".yaml", ".yml", ".html", ".htm"
}
PDF_EXTS = {".pdf"}
DOCX_EXTS = {".docx"}


def read_text_file(path: Path, max_bytes: int) -> str:
    # バイナリ混入を避けつつ読む
    data = path.read_bytes()[:max_bytes]
    # まず utf-8 を試し、ダメなら cp932/shift_jis 系を試す
    for enc in ("utf-8", "utf-8-sig", "cp932", "shift_jis", "euc_jp"):
        try:
            return data.decode(enc, errors="replace")
        except Exception:
            continue
    return data.decode("utf-8", errors="replace")


def read_pdf(path: Path, max_pages: int) -> str:
    PdfReader = _try_import_pypdf()
    if PdfReader is None:
        return ""
    try:
        reader = PdfReader(str(path))
        texts: List[str] = []
        n = min(len(reader.pages), max_pages)
        for i in range(n):
            page = reader.pages[i]
            t = page.extract_text() or ""
            if t:
                texts.append(t)
        return "\n".join(texts)
    except Exception:
        return ""


def read_docx(path: Path) -> str:
    docx = _try_import_docx()
    if docx is None:
        return ""
    try:
        d = docx.Document(str(path))
        paras = [p.text for p in d.paragraphs if p.text]
        return "\n".join(paras)
    except Exception:
        return ""


def read_html(path: Path, max_bytes: int) -> str:
    raw = read_text_file(path, max_bytes=max_bytes)
    BeautifulSoup = _try_import_bs4()
    if BeautifulSoup is not None:
        try:
            soup = BeautifulSoup(raw, "html.parser")
            return soup.get_text("\n")
        except Exception:
            pass
    # 依存なし簡易版（タグ除去）
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", raw)
    text = re.sub(r"(?is)<br\s*/?>", "\n", text)
    text = re.sub(r"(?is)</p\s*>", "\n", text)
    text = re.sub(r"(?is)<.*?>", " ", text)
    text = re.sub(r"[ \t]+", " ", text)
    return text


def extract_text(path: Path, max_bytes: int, max_pdf_pages: int) -> str:
    ext = path.suffix.lower()
    if ext in PDF_EXTS:
        return read_pdf(path, max_pages=max_pdf_pages)
    if ext in DOCX_EXTS:
        return read_docx(path)
    if ext in {".html", ".htm"}:
        return read_html(path, max_bytes=max_bytes)
    if ext in TEXT_EXTS:
        return read_text_file(path, max_bytes=max_bytes)
    return ""


# --------------------------
# 走査
# --------------------------
def iter_candidate_files(root: Path) -> List[Tuple[str, str, Path]]:
    """
    root/都道府県/市町村/ファイル を収集
    """
    items: List[Tuple[str, str, Path]] = []
    if not root.exists():
        return items

    for pref_dir in sorted([p for p in root.iterdir() if p.is_dir()]):
        pref = pref_dir.name
        for muni_dir in sorted([m for m in pref_dir.iterdir() if m.is_dir()]):
            muni = muni_dir.name
            for f in sorted([x for x in muni_dir.rglob("*") if x.is_file()]):  # 市町村配下は更にサブフォルダがあってもOK
                items.append((pref, muni, f))
    return items


def main():
    ap = argparse.ArgumentParser(description="議事録っぽいファイルを抽出してリスト化する")
    ap.add_argument("--root", default="./data/minutes_out", help="root ディレクトリ（都道府県フォルダが並ぶ場所）")
    ap.add_argument("--out", default="./data/minutes_candidates.json", help="出力ファイル（.json または .csv）")
    ap.add_argument("--threshold", type=int, default=30, help="議事録とみなすスコア閾値（デフォルト: 30）")
    ap.add_argument("--max-bytes", type=int, default=2_000_000, help="テキスト抽出の最大バイト（デフォルト: 2,000,000）")
    ap.add_argument("--max-pdf-pages", type=int, default=8, help="PDF抽出の最大ページ数（デフォルト: 8）")
    ap.add_argument("--min-size", type=int, default=1, help="無視する最小ファイルサイズ（bytes）")
    ap.add_argument("--ext-allow", default="", help="許可する拡張子のカンマ区切り（例: .pdf,.txt,.docx）。空なら全て対象")
    ap.add_argument("--ext-deny", default="", help="除外する拡張子のカンマ区切り（例: .jpg,.png）。空なら除外なし")
    ap.add_argument("--include-low", action="store_true", help="閾値未満も含めて全件出す（score付き）")
    args = ap.parse_args()

    root = Path(args.root).resolve()
    out = Path(args.out).resolve()

    allow = {e.strip().lower() for e in args.ext_allow.split(",") if e.strip()}
    deny = {e.strip().lower() for e in args.ext_deny.split(",") if e.strip()}

    candidates: List[Candidate] = []
    files = iter_candidate_files(root)
    
    max = len(files)
    count = 0
    start = time.perf_counter()

    for pref, muni, path in files:
        count += 1
        print(f'{count} / {max}', end='\r')
        try:
            st = path.stat()
        except Exception:
            continue

        if st.st_size < args.min_size:
            continue

        ext = path.suffix.lower()
        if allow and ext not in allow:
            continue
        if deny and ext in deny:
            continue

        text = extract_text(path, max_bytes=args.max_bytes, max_pdf_pages=args.max_pdf_pages)
        # テキスト抽出できない拡張子は、ファイル名のみで軽く判定
        if not text:
            text = ""

        score, matched, filename_hit, snippet = score_minutes(text, filename=path.name)

        rel = str(path.relative_to(root))
        c = Candidate(
            prefecture=pref,
            municipality=muni,
            rel_path=rel,
            ext=ext,
            size_bytes=st.st_size,
            score=score,
            matched=matched,
            filename_hit=filename_hit,
            snippet=snippet,
        )
        if args.include_low or score >= args.threshold:
            candidates.append(c)

    print()
    # スコア降順
    candidates.sort(key=lambda x: x.score, reverse=True)

    out.parent.mkdir(parents=True, exist_ok=True)

    if out.suffix.lower() == ".csv":
        with out.open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow([
                "prefecture", "municipality", "rel_path", "ext", "size_bytes",
                "score", "filename_hit", "matched", "snippet"
            ])
            for c in candidates:
                w.writerow([
                    c.prefecture, c.municipality, c.rel_path, c.ext, c.size_bytes,
                    c.score, "|".join(c.filename_hit), "|".join(c.matched), c.snippet
                ])
    else:
        payload = {
            "root": str(root),
            "threshold": args.threshold,
            "count": len(candidates),
            "items": [asdict(c) for c in candidates],
        }
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    # ざっくり統計を標準出力
    print(f"[OK] root={root}")
    print(f"[OK] out={out}")
    print(f"[OK] extracted={len(candidates)} files")
    if candidates:
        print("[TOP 10]")
        for c in candidates[:10]:
            print(f"  score={c.score:3d}  {c.rel_path}")
    end = time.perf_counter()
    print(f'lap: {end - start}sec')


if __name__ == "__main__":
    main()
