#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
minute_filter.py (Hybrid + text export)
- PDFのみ ProcessPoolExecutor で並列処理
- それ以外はメインプロセスで順次処理
- 議事録判定( score >= threshold )のものは抽出テキストを個別 .txt として出力

出力例（text-out-dir 指定時）:
text_out/
  東京都/
    渋谷区/
      元ファイル名.txt   (元が pdf/docx/txt/html などでも .txt に)
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import time
import multiprocessing
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Set


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

FILENAME_PATTERNS: List[Tuple[re.Pattern, int, str]] = [
    (re.compile(r"(議事録|会議録|議事要旨|会議要旨)"), 18, "jp_minutes_word"),
    (re.compile(r"(minutes|minute|gijiroku|kaigiroku)", re.IGNORECASE), 12, "en_minutes_word"),
    (re.compile(r"(令和|平成|昭和)\d+年"), 4, "era_year"),
    (re.compile(r"\d{4}[./-]\d{1,2}[./-]\d{1,2}"), 4, "date_like"),
    (re.compile(r"(第\d+回)"), 5, "meeting_count"),
]

HEADING_HINTS: List[Tuple[re.Pattern, int, str]] = [
    (re.compile(r"^\s*(1[.)]|２[.)]|2[.)]|Ⅰ|Ⅱ|III|第\d+)\s*"), 2, "outline_like"),
    (re.compile(r"^\s*(議題|報告事項|審議事項|協議事項|議事)\s*[:：]?\s*$"), 6, "section_heading"),
    (re.compile(r"^\s*(開催日時|日時|場所|出席者|欠席者)\s*[:：]"), 6, "metadata_line"),
]


def _normalize_text(text: str) -> str:
    text = text.replace("\u3000", " ")
    text = re.sub(r"[ \t]+", " ", text)
    return text


def score_minutes(text: str, filename: str) -> Tuple[int, List[str], List[str], str]:
    matched: List[str] = []
    filename_hit: List[str] = []
    score = 0

    head = _normalize_text(text)[:20000]
    lines = head.splitlines()

    for pat, w, tag in FILENAME_PATTERNS:
        if pat.search(filename):
            score += w
            filename_hit.append(tag)

    for kw, w in MINUTES_KEYWORDS.items():
        if kw in head:
            score += w
            matched.append(kw)

    for kw, w in NEGATIVE_KEYWORDS.items():
        if kw in head:
            score += w
            matched.append(f"NEG:{kw}")

    for line in lines[:300]:
        for pat, w, tag in HEADING_HINTS:
            if pat.search(line):
                score += w
                if tag not in matched:
                    matched.append(tag)

    prefer_lines: List[str] = []
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

    if len(head) < 400:
        score -= 6

    return score, matched, filename_hit, snippet


# --------------------------
# 抽出テキストの個別出力
# --------------------------
def write_extracted_text(
    text_out_dir: Path,
    rel_path: str,
    text: str,
    encoding: str = "utf-8",
) -> str:
    """
    rel_path（例: 東京都/渋谷区/foo.pdf）を保ったまま
    text_out_dir/東京都/渋谷区/foo.txt に出力して、出力先パス文字列を返す
    """
    rel_txt = Path(rel_path).with_suffix(".txt")
    out_path = (text_out_dir / rel_txt).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(text, encoding=encoding, errors="replace")
    return str(out_path)


# --------------------------
# ファイル読み込み（拡張子別）
# --------------------------
TEXT_EXTS = {
    ".txt", ".md", ".csv", ".tsv", ".json", ".log", ".xml", ".yaml", ".yml", ".html", ".htm"
}
PDF_EXTS = {".pdf"}
DOCX_EXTS = {".docx"}


def read_text_file(path: Path, max_bytes: int) -> str:
    data = path.read_bytes()[:max_bytes]
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
            t = reader.pages[i].extract_text() or ""
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
    items: List[Tuple[str, str, Path]] = []
    if not root.exists():
        return items

    for pref_dir in sorted([p for p in root.iterdir() if p.is_dir()]):
        pref = pref_dir.name
        for muni_dir in sorted([m for m in pref_dir.iterdir() if m.is_dir()]):
            muni = muni_dir.name
            for f in sorted([x for x in muni_dir.rglob("*") if x.is_file()]):
                items.append((pref, muni, f))
    return items


# --------------------------
# 共通フィルタ
# --------------------------
def passes_filters(
    path: Path,
    min_size: int,
    allow_exts: Set[str],
    deny_exts: Set[str],
) -> Optional[os.stat_result]:
    try:
        st = path.stat()
    except Exception:
        return None

    if st.st_size < min_size:
        return None

    ext = path.suffix.lower()
    if allow_exts and ext not in allow_exts:
        return None
    if deny_exts and ext in deny_exts:
        return None

    return st


# --------------------------
# メインプロセスで1ファイル処理（非PDF）
# --------------------------
def process_one_file_in_main(
    pref: str,
    muni: str,
    path: Path,
    root: Path,
    threshold: int,
    include_low: bool,
    max_bytes: int,
    max_pdf_pages: int,
    min_size: int,
    allow_exts: Set[str],
    deny_exts: Set[str],
    text_out_dir: Optional[Path],
    text_out_encoding: str,
) -> Optional[Candidate]:
    st = passes_filters(path, min_size=min_size, allow_exts=allow_exts, deny_exts=deny_exts)
    if st is None:
        return None

    ext = path.suffix.lower()
    text = extract_text(path, max_bytes=max_bytes, max_pdf_pages=max_pdf_pages) or ""
    score, matched, filename_hit, snippet = score_minutes(text, filename=path.name)

    # listへ入れるか
    if (not include_low) and (score < threshold):
        return None

    rel = str(path.relative_to(root))

    # テキスト出力は「議事録判定（>=threshold）」のみ
    if text_out_dir is not None and score >= threshold:
        write_extracted_text(text_out_dir, rel_path=rel, text=text, encoding=text_out_encoding)

    return Candidate(
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


# --------------------------
# PDF専用（プロセスで実行）
#  - テキスト出力もワーカー側で行う（巨大テキストを親へ返さない）
# --------------------------
def process_one_pdf_in_worker(
    pref: str,
    muni: str,
    path_str: str,
    root_str: str,
    threshold: int,
    include_low: bool,
    max_pdf_pages: int,
    min_size: int,
    allow_exts: Set[str],
    deny_exts: Set[str],
    text_out_dir_str: str,
    text_out_encoding: str,
) -> Optional[Candidate]:
    path = Path(path_str)
    root = Path(root_str)

    st = passes_filters(path, min_size=min_size, allow_exts=allow_exts, deny_exts=deny_exts)
    if st is None:
        return None

    ext = path.suffix.lower()
    # PDFテキスト抽出
    text = read_pdf(path, max_pages=max_pdf_pages) or ""
    score, matched, filename_hit, snippet = score_minutes(text, filename=path.name)

    # include_low の挙動はリスト用
    if (not include_low) and (score < threshold):
        return None

    try:
        rel = str(path.relative_to(root))
    except Exception:
        rel = os.path.relpath(path_str, root_str)

    # テキスト出力は「議事録判定（>=threshold）」のみ
    if text_out_dir_str and score >= threshold:
        text_out_dir = Path(text_out_dir_str)
        write_extracted_text(text_out_dir, rel_path=rel, text=text, encoding=text_out_encoding)

    return Candidate(
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


def main():
    ap = argparse.ArgumentParser(description="議事録っぽいファイル抽出（Hybrid: PDFのみProcessPool）+ 個別テキスト出力")
    ap.add_argument("--root", default="./data/minutes_out", help="root ディレクトリ（都道府県フォルダが並ぶ場所）")
    ap.add_argument("--out", default="./data/minutes_candidates.json", help="出力ファイル（.json または .csv）")
    ap.add_argument("--threshold", type=int, default=30, help="議事録とみなすスコア閾値（デフォルト: 30）")
    ap.add_argument("--max-bytes", type=int, default=2_000_000, help="テキスト抽出の最大バイト（デフォルト: 2,000,000）")
    ap.add_argument("--max-pdf-pages", type=int, default=8, help="PDF抽出の最大ページ数（デフォルト: 8）")
    ap.add_argument("--min-size", type=int, default=1, help="無視する最小ファイルサイズ（bytes）")
    ap.add_argument("--ext-allow", default="", help="許可する拡張子のカンマ区切り（例: .pdf,.txt,.docx）。空なら全て対象")
    ap.add_argument("--ext-deny", default="", help="除外する拡張子のカンマ区切り（例: .jpg,.png）。空なら除外なし")
    ap.add_argument("--include-low", action="store_true", help="閾値未満も含めて全件出す（score付き）")
    ap.add_argument("--workers", type=int, default=(os.cpu_count() or 4), help="PDF用ProcessPoolの並列数（デフォルト: CPUコア数）")

    # ★追加：議事録判定されたファイルの抽出テキストを個別出力
    ap.add_argument("--text-out-dir", default="", help="議事録判定されたファイルの抽出テキスト出力先ディレクトリ（空なら出力しない）")
    ap.add_argument("--text-out-encoding", default="utf-8", help="個別テキスト出力のエンコーディング（デフォルト: utf-8）")

    args = ap.parse_args()

    root = Path(args.root).resolve()
    out = Path(args.out).resolve()

    allow = {e.strip().lower() for e in args.ext_allow.split(",") if e.strip()}
    deny = {e.strip().lower() for e in args.ext_deny.split(",") if e.strip()}

    text_out_dir: Optional[Path] = None
    if args.text_out_dir.strip():
        text_out_dir = Path(args.text_out_dir).resolve()
        text_out_dir.mkdir(parents=True, exist_ok=True)

    files = iter_candidate_files(root)
    total = len(files)

    pdf_jobs: List[Tuple[str, str, Path]] = []
    main_jobs: List[Tuple[str, str, Path]] = []
    for pref, muni, path in files:
        if path.suffix.lower() == ".pdf":
            pdf_jobs.append((pref, muni, path))
        else:
            main_jobs.append((pref, muni, path))

    start = time.perf_counter()
    candidates: List[Candidate] = []
    done = 0

    # 1) 非PDFはメインで順次
    for pref, muni, path in main_jobs:
        done += 1
        print(f"{done} / {total}", end="\r")
        res = process_one_file_in_main(
            pref=pref,
            muni=muni,
            path=path,
            root=root,
            threshold=args.threshold,
            include_low=args.include_low,
            max_bytes=args.max_bytes,
            max_pdf_pages=args.max_pdf_pages,
            min_size=args.min_size,
            allow_exts=allow,
            deny_exts=deny,
            text_out_dir=text_out_dir,
            text_out_encoding=args.text_out_encoding,
        )
        if res is not None:
            candidates.append(res)

    # 2) PDFだけProcessPoolで並列
    if pdf_jobs:
        with ProcessPoolExecutor(max_workers=args.workers) as ex:
            futures = [
                ex.submit(
                    process_one_pdf_in_worker,
                    pref,
                    muni,
                    str(path),
                    str(root),
                    args.threshold,
                    args.include_low,
                    args.max_pdf_pages,
                    args.min_size,
                    allow,
                    deny,
                    str(text_out_dir) if text_out_dir is not None else "",
                    args.text_out_encoding,
                )
                for pref, muni, path in pdf_jobs
            ]

            for fut in as_completed(futures):
                done += 1
                print(f"{done} / {total}", end="\r")
                try:
                    res = fut.result()
                except Exception:
                    res = None
                if res is not None:
                    candidates.append(res)

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

    print(f"[OK] root={root}")
    print(f"[OK] out={out}")
    print(f"[OK] extracted(listed)={len(candidates)} files")
    print(f"[OK] text_out_dir={text_out_dir if text_out_dir is not None else '(disabled)'}")
    if candidates:
        print("[TOP 10]")
        for c in candidates[:10]:
            print(f"  score={c.score:3d}  {c.rel_path}")

    end = time.perf_counter()
    print(f"lap: {end - start}sec")
    print(f"pdf_jobs={len(pdf_jobs)}, main_jobs={len(main_jobs)}, workers={args.workers}")


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
