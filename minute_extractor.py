#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
minute_extractor_v4_1_parallel.py  (v4.1)

目的
- minute_site_finder.py の出力JSONを入力に、議事録っぽいリンクを収集・（任意で）DL
- seed（起点URL）単位でマルチスレッド並列化（--workers）
- robots.txt を尊重（デフォルトON）
- ドメイン単位レート制限で Crawl-delay / --delay を守る
- manifest.jsonl を利用して resume / seed skip / download skip が可能
- robots により禁止されたURLを最後に集計してレポート出力

v4.1 追加仕様（重要）
- 「seed（一覧サイト）が更新されている場合は、完了済みseedでも再クロール」できる（デフォルトON）
  - ETag / Last-Modified / HTML本文sha1 を manifest に保存し、差分があれば再クロール
- ただし「議事録本体のダウンロードはスキップさせたい」を満たすため、
  - 既にDL済みの本体ファイルは再DLしない（従来の resume 挙動）
  - さらに `--no-download-files` で本体DLを完全無効化し、リンク収集＋（任意で）HTML保存のみも可能

依存: 標準ライブラリのみ
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
import time
from collections import Counter, defaultdict, deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from threading import Lock
from typing import Dict, List, Optional, Set, Tuple
from urllib import robotparser
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urldefrag, urlparse
from urllib.request import Request, urlopen


# -----------------------------
# Defaults
# -----------------------------

DEFAULT_KEYWORDS = [
    "議事録", "会議録", "会議資料", "会議結果", "会議概要", "審議会",
    "委員会", "本会議", "定例会", "臨時会", "会議", "録",
    "令和", "平成", "議会", "会期", "質疑", "答弁",
]

DEFAULT_FILE_EXTS = {
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".csv",
    ".txt", ".zip",
}

DEFAULT_URL_HINTS = [
    "giji", "gijiroku", "kaigi", "minutes", "meeting", "gikai", "iin",
    "shingikai", "kaigiroku",
]


# -----------------------------
# Utils
# -----------------------------

JST = timezone(timedelta(hours=9))


def now_iso_jst() -> str:
    return datetime.now(JST).isoformat(timespec="seconds")


def safe_name(s: str) -> str:
    s = (s or "").strip()
    s = re.sub(r"[\\/:*?\"<>|]+", "_", s)
    s = re.sub(r"\s+", " ", s)
    return s[:80] if len(s) > 80 else s


def normalize_url(url: str) -> str:
    url = (url or "").strip()
    url, _frag = urldefrag(url)
    return url


def sha1_hex(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()


def sha1_bytes(b: bytes) -> str:
    return hashlib.sha1(b).hexdigest()


def sum_counts(d: Dict[str, int]) -> int:
    return int(sum(int(v) for v in (d or {}).values()))


def is_probably_binary(content_type: str) -> bool:
    ct = (content_type or "").lower()
    return (
        "application/pdf" in ct
        or "application/msword" in ct
        or "application/vnd" in ct
        or "application/zip" in ct
        or "octet-stream" in ct
    )


def guess_ext_from_content_type(content_type: str) -> Optional[str]:
    ct = (content_type or "").lower()
    if "application/pdf" in ct:
        return ".pdf"
    if "application/zip" in ct:
        return ".zip"
    if "msword" in ct:
        return ".doc"
    if "officedocument.wordprocessingml" in ct:
        return ".docx"
    if "officedocument.spreadsheetml" in ct:
        return ".xlsx"
    if "officedocument.presentationml" in ct:
        return ".pptx"
    if "text/plain" in ct:
        return ".txt"
    if "text/csv" in ct:
        return ".csv"
    return None


def looks_like_minutes_link(
    url: str,
    anchor_text: str,
    keywords: List[str],
    file_exts: Set[str],
    url_hints: List[str],
) -> bool:
    u = (url or "").lower()
    t = (anchor_text or "").strip()

    for ext in file_exts:
        if u.endswith(ext):
            return True
    if any(h in u for h in url_hints):
        return True
    if any(k in t for k in keywords):
        return True
    if any(k in url for k in keywords):
        return True
    return False


def path_prefix(url: str) -> str:
    p = urlparse(url).path or "/"
    p = p if p.startswith("/") else "/" + p
    parts = [x for x in p.split("/") if x]
    if not parts:
        return "/"
    return f"/{parts[0]}/"


# -----------------------------
# Thread-safe manifest writer
# -----------------------------

class ManifestWriter:
    def __init__(self, manifest_path: Path) -> None:
        self.manifest_path = manifest_path
        self._lock = Lock()

    def write(self, obj: dict) -> None:
        line = json.dumps(obj, ensure_ascii=False)
        self.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            with self.manifest_path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")


# -----------------------------
# Robots disallow report
# -----------------------------

@dataclass(frozen=True)
class RobotsDisallowEntry:
    prefecture: str
    city: str
    url: str
    netloc: str
    path_prefix: str


class RobotsDisallowReport:
    def __init__(self) -> None:
        self._lock = Lock()
        self._seen: Set[str] = set()
        self.entries: List[RobotsDisallowEntry] = []

    def add(self, prefecture: str, city: str, url: str) -> None:
        url = normalize_url(url)
        key = f"{prefecture}|{city}|{url}"
        p = urlparse(url)
        entry = RobotsDisallowEntry(
            prefecture=prefecture,
            city=city,
            url=url,
            netloc=p.netloc,
            path_prefix=path_prefix(url),
        )
        with self._lock:
            if key in self._seen:
                return
            self._seen.add(key)
            self.entries.append(entry)

    def summary(self) -> dict:
        with self._lock:
            entries = list(self.entries)
        by_city = Counter((e.prefecture, e.city) for e in entries)
        by_domain = Counter(e.netloc for e in entries)
        by_prefix = Counter((e.netloc, e.path_prefix) for e in entries)

        top_city = [{"prefecture": p, "city": c, "count": n} for (p, c), n in by_city.most_common(50)]
        top_domain = [{"netloc": d, "count": n} for d, n in by_domain.most_common(50)]
        top_prefix = [{"netloc": d, "path_prefix": pref, "count": n} for (d, pref), n in by_prefix.most_common(50)]

        return {
            "generated_at": now_iso_jst(),
            "robots_disallow_total": len(entries),
            "top_by_city": top_city,
            "top_by_domain": top_domain,
            "top_by_path_prefix": top_prefix,
        }


def write_robots_reports(report: RobotsDisallowReport, report_dir: Path) -> None:
    report_dir.mkdir(parents=True, exist_ok=True)

    with report._lock:
        entries = list(report.entries)

    urls_jsonl = report_dir / "robots_disallow_urls.jsonl"
    with urls_jsonl.open("w", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps({
                "prefecture": e.prefecture,
                "city": e.city,
                "netloc": e.netloc,
                "path_prefix": e.path_prefix,
                "url": e.url,
            }, ensure_ascii=False) + "\n")

    summary_json = report_dir / "robots_disallow_summary.json"
    summary_json.write_text(json.dumps(report.summary(), ensure_ascii=False, indent=2), encoding="utf-8")

    by_city = Counter((e.prefecture, e.city) for e in entries)
    city_csv = report_dir / "robots_disallow_by_city.csv"
    with city_csv.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["prefecture", "city", "count"])
        for (p, c), n in by_city.most_common():
            w.writerow([p, c, n])

    by_domain = Counter(e.netloc for e in entries)
    domain_csv = report_dir / "robots_disallow_by_domain.csv"
    with domain_csv.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["netloc", "count"])
        for d, n in by_domain.most_common():
            w.writerow([d, n])

    by_prefix = Counter((e.netloc, e.path_prefix) for e in entries)
    prefix_csv = report_dir / "robots_disallow_by_path_prefix.csv"
    with prefix_csv.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["netloc", "path_prefix", "count"])
        for (d, pref), n in by_prefix.most_common():
            w.writerow([d, pref, n])


# -----------------------------
# HTML link extractor
# -----------------------------

class LinkExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._current_href: Optional[str] = None
        self._current_text_parts: List[str] = []
        self.links: List[Tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        if tag.lower() == "a":
            href = None
            for k, v in attrs:
                if k.lower() == "href" and v:
                    href = v
                    break
            self._current_href = href
            self._current_text_parts = []

    def handle_data(self, data: str) -> None:
        if self._current_href is not None and data:
            self._current_text_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a" and self._current_href is not None:
            text = "".join(self._current_text_parts).strip()
            href = (self._current_href or "").strip()
            if href:
                self.links.append((href, text))
            self._current_href = None
            self._current_text_parts = []


# -----------------------------
# Fetch / Save
# -----------------------------

@dataclass
class FetchResult:
    url: str
    final_url: str
    status: int
    content_type: str
    body: bytes
    headers: dict


def fetch_url(url: str, timeout: int, user_agent: str, extra_headers: Optional[dict] = None) -> FetchResult:
    headers = {"User-Agent": user_agent}
    if extra_headers:
        headers.update(extra_headers)
    req = Request(url, headers=headers)
    with urlopen(req, timeout=timeout) as resp:
        final_url = resp.geturl()
        status = getattr(resp, "status", 200)
        ct = resp.headers.get("Content-Type", "") or ""
        body = resp.read()
        # copy headers to plain dict
        h = {k: v for (k, v) in resp.headers.items()}
        return FetchResult(url=url, final_url=final_url, status=status, content_type=ct, body=body, headers=h)


def save_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


# -----------------------------
# Robots manager (thread-safe cache)
# -----------------------------

class RobotsManager:
    def __init__(self, timeout_sec: int, user_agent: str, manifest: ManifestWriter) -> None:
        self.timeout_sec = timeout_sec
        self.user_agent = user_agent
        self.manifest = manifest
        self._cache: Dict[str, robotparser.RobotFileParser] = {}
        self._cache_fail: Set[str] = set()
        self._lock = Lock()

    def _robots_url(self, any_url: str) -> str:
        p = urlparse(any_url)
        scheme = p.scheme or "https"
        return f"{scheme}://{p.netloc}/robots.txt"

    def _get_parser(self, any_url: str) -> Optional[robotparser.RobotFileParser]:
        p = urlparse(any_url)
        netloc = p.netloc
        if not netloc:
            return None

        with self._lock:
            if netloc in self._cache:
                return self._cache[netloc]
            if netloc in self._cache_fail:
                return None

        robots_url = self._robots_url(any_url)
        rp = robotparser.RobotFileParser()
        rp.set_url(robots_url)

        try:
            res = fetch_url(robots_url, timeout=self.timeout_sec, user_agent=self.user_agent)
            text = res.body.decode("utf-8", errors="replace")
            rp.parse(text.splitlines())

            with self._lock:
                self._cache[netloc] = rp

            self.manifest.write({
                "ts": now_iso_jst(),
                "event": "robots_loaded",
                "netloc": netloc,
                "robots_url": robots_url,
            })
            return rp

        except Exception as e:
            with self._lock:
                self._cache_fail.add(netloc)

            self.manifest.write({
                "ts": now_iso_jst(),
                "event": "robots_load_failed_allow_all",
                "netloc": netloc,
                "robots_url": robots_url,
                "error": repr(e),
            })
            return None

    def can_fetch(self, url: str) -> bool:
        url = normalize_url(url)
        rp = self._get_parser(url)
        if rp is None:
            return True
        try:
            return bool(rp.can_fetch(self.user_agent, url))
        except Exception:
            return True

    def crawl_delay(self, url: str) -> Optional[float]:
        url = normalize_url(url)
        rp = self._get_parser(url)
        if rp is None:
            return None
        try:
            d = rp.crawl_delay(self.user_agent)
            if d is None:
                return None
            return float(d)
        except Exception:
            return None


# -----------------------------
# Domain rate limiter (thread-safe)
# -----------------------------

class DomainRateLimiter:
    """
    同一ドメインへのアクセス間隔を制御する。
    複数スレッドでも「次にアクセスして良い時刻」を共有して守る。
    """
    def __init__(self) -> None:
        self._lock = Lock()
        self._next_allowed: Dict[str, float] = {}  # netloc -> epoch sec

    def wait(self, url: str, delay_sec: float) -> None:
        netloc = urlparse(url).netloc or ""
        if not netloc or delay_sec <= 0:
            return
        now = time.time()
        with self._lock:
            nxt = self._next_allowed.get(netloc, now)
            sleep_for = max(0.0, nxt - now)
            base = max(now, nxt)
            self._next_allowed[netloc] = base + delay_sec
        if sleep_for > 0:
            time.sleep(sleep_for)


# -----------------------------
# Manifest cache (resume)
# -----------------------------

@dataclass
class ManifestCache:
    downloaded_file_urls: Set[str]
    saved_page_urls: Set[str]
    completed_seeds: Set[str]
    seed_meta: Dict[str, dict]  # seed_url -> {etag,last_modified,content_sha1}


def load_manifest_cache(manifest_path: Path) -> ManifestCache:
    downloaded: Set[str] = set()
    saved_pages: Set[str] = set()
    completed_seeds: Set[str] = set()
    seed_meta: Dict[str, dict] = {}

    if not manifest_path.exists():
        return ManifestCache(downloaded, saved_pages, completed_seeds, seed_meta)

    with manifest_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue

            ev = obj.get("event")
            if ev == "downloaded_file":
                u = obj.get("file_url")
                if isinstance(u, str) and u:
                    downloaded.add(normalize_url(u))

            elif ev == "saved_page":
                u = obj.get("page_url")
                if isinstance(u, str) and u:
                    saved_pages.add(normalize_url(u))

            elif ev == "seed_done":
                u = obj.get("seed_url")
                if isinstance(u, str) and u:
                    completed_seeds.add(normalize_url(u))

            elif ev == "seed_state":
                su = obj.get("seed_url")
                if isinstance(su, str) and su:
                    seed_meta[normalize_url(su)] = {
                        "etag": obj.get("etag") or "",
                        "last_modified": obj.get("last_modified") or "",
                        "content_sha1": obj.get("content_sha1") or "",
                    }

    return ManifestCache(downloaded, saved_pages, completed_seeds, seed_meta)


# -----------------------------
# Crawl config
# -----------------------------

@dataclass
class CrawlConfig:
    max_depth: int
    max_pages: int
    delay_sec: float
    timeout_sec: int
    user_agent: str
    keywords: List[str]
    file_exts: Set[str]
    url_hints: List[str]
    same_domain_only: bool
    same_path_prefix_only: bool
    respect_robots: bool


def effective_delay(cfg: CrawlConfig, robots: Optional[RobotsManager], url: str) -> float:
    d = float(cfg.delay_sec)
    if cfg.respect_robots and robots is not None:
        rd = robots.crawl_delay(url)
        if rd is not None:
            d = max(d, float(rd))
    return d


# -----------------------------
# Shared state (thread-safe)
# -----------------------------

@dataclass
class SharedState:
    manifest: ManifestWriter
    cache: ManifestCache
    cache_lock: Lock
    robots_report: RobotsDisallowReport
    limiter: DomainRateLimiter
    robots: Optional[RobotsManager]


# -----------------------------
# Seed update check (ETag / Last-Modified / body sha1)
# -----------------------------

def fetch_seed_state(url: str, timeout: int, user_agent: str, prev_meta: Optional[dict]) -> Tuple[bool, dict]:
    """
    seed（一覧ページ）が更新されているか確認する。
    戻り値: (changed, new_meta)

    - 可能なら If-None-Match / If-Modified-Since を使う（304なら変更なし）
    - 200の場合は body の sha1 でも比較（ヘッダが無いサイトでも検知しやすい）
    """
    headers = {}
    if prev_meta:
        et = (prev_meta.get("etag") or "").strip()
        lm = (prev_meta.get("last_modified") or "").strip()
        if et:
            headers["If-None-Match"] = et
        if lm:
            headers["If-Modified-Since"] = lm

    try:
        res = fetch_url(url, timeout=timeout, user_agent=user_agent, extra_headers=headers)
        etag = (res.headers.get("ETag", "") or "").strip()
        last_mod = (res.headers.get("Last-Modified", "") or "").strip()
        content_sha1 = sha1_bytes(res.body)

        new_meta = {"etag": etag, "last_modified": last_mod, "content_sha1": content_sha1}

        if not prev_meta:
            return True, new_meta

        old_sha1 = (prev_meta.get("content_sha1") or "").strip()
        if old_sha1 and old_sha1 == content_sha1:
            return False, new_meta

        old_et = (prev_meta.get("etag") or "").strip()
        old_lm = (prev_meta.get("last_modified") or "").strip()
        if old_et and etag and old_et == etag:
            return False, new_meta
        if old_lm and last_mod and old_lm == last_mod:
            return False, new_meta

        return True, new_meta

    except HTTPError as e:
        if getattr(e, "code", None) == 304:
            return False, prev_meta or {"etag": "", "last_modified": "", "content_sha1": ""}
        # 失敗時は安全側（更新あり扱い）
        return True, prev_meta or {"etag": "", "last_modified": "", "content_sha1": ""}

    except Exception:
        return True, prev_meta or {"etag": "", "last_modified": "", "content_sha1": ""}


# -----------------------------
# Crawl and collect (sequential inside seed)
# -----------------------------

def crawl_and_collect(
    start_url: str,
    cfg: CrawlConfig,
    out_dir: Path,
    state: SharedState,
    prefecture: str,
    city: str,
    save_pages: bool,
    download_files: bool,
    resume: bool,
    force_download: bool,
) -> List[str]:
    start_url = normalize_url(start_url)
    parsed0 = urlparse(start_url)
    base_netloc = parsed0.netloc
    base_prefix = parsed0.path.rstrip("/") + "/"

    visited_pages: Set[str] = set()
    queued: List[Tuple[str, int]] = [(start_url, 0)]
    pages_fetched = 0

    found_minutes: List[str] = []
    found_set: Set[str] = set()

    while queued and pages_fetched < cfg.max_pages:
        url, depth = queued.pop(0)
        url = normalize_url(url)
        if url in visited_pages:
            continue
        visited_pages.add(url)

        pu = urlparse(url)

        if cfg.same_domain_only and pu.netloc and pu.netloc != base_netloc:
            continue

        if cfg.same_path_prefix_only and pu.path and not pu.path.startswith(base_prefix) and pu.path != parsed0.path:
            continue

        # robots
        if cfg.respect_robots and state.robots is not None:
            if not state.robots.can_fetch(url):
                state.robots_report.add(prefecture, city, url)
                state.manifest.write({
                    "ts": now_iso_jst(),
                    "prefecture": prefecture,
                    "city": city,
                    "event": "robots_disallow",
                    "url": url,
                })
                continue

        # rate limit (domain + crawl-delay)
        state.limiter.wait(url, effective_delay(cfg, state.robots, url))

        try:
            res = fetch_url(url, timeout=cfg.timeout_sec, user_agent=cfg.user_agent)
            pages_fetched += 1
        except HTTPError as e:
            state.manifest.write({
                "ts": now_iso_jst(),
                "prefecture": prefecture,
                "city": city,
                "event": "fetch_error",
                "url": url,
                "error": f"HTTPError {e.code}",
            })
            continue
        except URLError as e:
            state.manifest.write({
                "ts": now_iso_jst(),
                "prefecture": prefecture,
                "city": city,
                "event": "fetch_error",
                "url": url,
                "error": f"URLError {getattr(e, 'reason', repr(e))}",
            })
            continue
        except Exception as e:
            state.manifest.write({
                "ts": now_iso_jst(),
                "prefecture": prefecture,
                "city": city,
                "event": "fetch_error",
                "url": url,
                "error": repr(e),
            })
            continue

        final_page_url = normalize_url(res.final_url)
        ct = (res.content_type or "").lower()

        # binary direct hit (pdf etc)
        if is_probably_binary(ct):
            file_final = normalize_url(res.final_url)
            if file_final not in found_set:
                found_set.add(file_final)
                found_minutes.append(file_final)

            if download_files:
                with state.cache_lock:
                    already = resume and (not force_download) and (file_final in state.cache.downloaded_file_urls)
                if already:
                    state.manifest.write({
                        "ts": now_iso_jst(),
                        "prefecture": prefecture,
                        "city": city,
                        "event": "skip_download_already_done",
                        "file_url": file_final,
                    })
                else:
                    ext = guess_ext_from_content_type(ct) or Path(urlparse(file_final).path).suffix or ".bin"
                    fname = f"{sha1_hex(file_final)}{ext}"
                    save_path = out_dir / safe_name(prefecture) / safe_name(city) / "files" / fname
                    save_bytes(save_path, res.body)
                    with state.cache_lock:
                        state.cache.downloaded_file_urls.add(file_final)

                    state.manifest.write({
                        "ts": now_iso_jst(),
                        "prefecture": prefecture,
                        "city": city,
                        "event": "downloaded_file",
                        "source_page": url,
                        "file_url": file_final,
                        "content_type": res.content_type,
                        "path": str(save_path),
                    })
            continue

        # html
        body = res.body
        html = body.decode("utf-8", errors="replace")

        # save page
        if save_pages:
            with state.cache_lock:
                already = resume and (final_page_url in state.cache.saved_page_urls)
            if already:
                state.manifest.write({
                    "ts": now_iso_jst(),
                    "prefecture": prefecture,
                    "city": city,
                    "event": "skip_save_page_already_done",
                    "page_url": final_page_url,
                })
            else:
                page_fname = f"{sha1_hex(final_page_url)}.html"
                page_path = out_dir / safe_name(prefecture) / safe_name(city) / "pages" / page_fname
                save_bytes(page_path, body)
                with state.cache_lock:
                    state.cache.saved_page_urls.add(final_page_url)

                state.manifest.write({
                    "ts": now_iso_jst(),
                    "prefecture": prefecture,
                    "city": city,
                    "event": "saved_page",
                    "page_url": final_page_url,
                    "path": str(page_path),
                    "content_type": res.content_type,
                })

        # extract links
        parser = LinkExtractor()
        try:
            parser.feed(html)
        except Exception:
            continue

        for href, text in parser.links:
            abs_url = normalize_url(urljoin(final_page_url, href))
            if not abs_url:
                continue
            low = abs_url.lower()
            if low.startswith(("mailto:", "javascript:", "tel:")):
                continue

            p2 = urlparse(abs_url)
            if cfg.same_domain_only and p2.netloc and p2.netloc != base_netloc:
                continue

            # minutes-like
            if looks_like_minutes_link(abs_url, text, cfg.keywords, cfg.file_exts, cfg.url_hints):
                if abs_url not in found_set:
                    found_set.add(abs_url)
                    found_minutes.append(abs_url)
                    state.manifest.write({
                        "ts": now_iso_jst(),
                        "prefecture": prefecture,
                        "city": city,
                        "event": "found_minutes_link",
                        "source_page": final_page_url,
                        "link_url": abs_url,
                        "anchor_text": text,
                    })

                # download file if enabled
                if download_files:
                    ext = Path(p2.path).suffix.lower()
                    if ext in cfg.file_exts:
                        # robots check for file url
                        if cfg.respect_robots and state.robots is not None:
                            if not state.robots.can_fetch(abs_url):
                                state.robots_report.add(prefecture, city, abs_url)
                                state.manifest.write({
                                    "ts": now_iso_jst(),
                                    "prefecture": prefecture,
                                    "city": city,
                                    "event": "robots_disallow",
                                    "url": abs_url,
                                })
                                continue

                        with state.cache_lock:
                            already = resume and (not force_download) and (abs_url in state.cache.downloaded_file_urls)
                        if already:
                            state.manifest.write({
                                "ts": now_iso_jst(),
                                "prefecture": prefecture,
                                "city": city,
                                "event": "skip_download_already_done",
                                "file_url": abs_url,
                            })
                            continue

                        # rate limit for file
                        state.limiter.wait(abs_url, effective_delay(cfg, state.robots, abs_url))

                        try:
                            fres = fetch_url(abs_url, timeout=cfg.timeout_sec, user_agent=cfg.user_agent)
                            fct = (fres.content_type or "").lower()
                            file_final = normalize_url(fres.final_url)

                            with state.cache_lock:
                                already2 = resume and (not force_download) and (file_final in state.cache.downloaded_file_urls)
                            if already2:
                                state.manifest.write({
                                    "ts": now_iso_jst(),
                                    "prefecture": prefecture,
                                    "city": city,
                                    "event": "skip_download_already_done",
                                    "file_url": file_final,
                                })
                                continue

                            ext2 = Path(urlparse(file_final).path).suffix.lower()
                            if not ext2:
                                ext2 = guess_ext_from_content_type(fct) or ext or ".bin"

                            fname = f"{sha1_hex(file_final)}{ext2}"
                            save_path = out_dir / safe_name(prefecture) / safe_name(city) / "files" / fname
                            save_bytes(save_path, fres.body)
                            with state.cache_lock:
                                state.cache.downloaded_file_urls.add(file_final)

                            state.manifest.write({
                                "ts": now_iso_jst(),
                                "prefecture": prefecture,
                                "city": city,
                                "event": "downloaded_file",
                                "source_page": final_page_url,
                                "file_url": file_final,
                                "content_type": fres.content_type,
                                "path": str(save_path),
                            })
                        except Exception as e:
                            state.manifest.write({
                                "ts": now_iso_jst(),
                                "prefecture": prefecture,
                                "city": city,
                                "event": "download_error",
                                "source_page": final_page_url,
                                "file_url": abs_url,
                                "error": repr(e),
                            })
                continue

            # enqueue for crawl
            if depth < cfg.max_depth:
                if cfg.same_path_prefix_only and p2.path and not p2.path.startswith(base_prefix) and p2.path != parsed0.path:
                    continue
                if abs_url not in visited_pages:
                    queued.append((abs_url, depth + 1))

    return found_minutes


# -----------------------------
# seed selection
# -----------------------------

def choose_seed_urls(record: dict, threshold: int) -> Tuple[str, Dict[str, int]]:
    parent = record.get("parent") or {}
    grand_parent = record.get("grand_parent") or {}
    parent_total = sum_counts(parent)
    if parent_total >= threshold and parent:
        return "parent", parent
    return "grand_parent", grand_parent

def round_robin_by_netloc(tasks):
    buckets = defaultdict(deque)
    for t in tasks:
        seed = t[3]
        netloc = urlparse(seed).netloc or ""
        buckets[netloc].append(t)

    ordered = []
    keys = [k for k in buckets.keys() if k]
    # 空netlocがあるなら最後にまとめて
    empty = buckets.get("", deque())

    # ラウンドロビン
    while keys:
        next_keys = []
        for k in keys:
            if buckets[k]:
                ordered.append(buckets[k].popleft())
            if buckets[k]:
                next_keys.append(k)
        keys = next_keys

    # netlocが空のものがあれば最後に
    while empty:
        ordered.append(empty.popleft())

    return ordered

# -----------------------------
# main (parallel)
# -----------------------------

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="data/minute_site_list.json", help="minute_site_finder.py の出力JSON")
    ap.add_argument("--outdir", default="data/minutes_out", help="保存先ディレクトリ")
    ap.add_argument("--manifest", default="data/minutes_out/manifest.jsonl", help="結果ログ(JSONL)")
    ap.add_argument("--overwrite-manifest", action="store_true", help="manifest を作り直す（追記しない）")

    ap.add_argument("--threshold", type=int, default=5, help="parentの議事録数合計がこの値以上ならparent優先")
    ap.add_argument("--max-depth", type=int, default=2, help="巡回深さ（0=起点ページのみ）")
    ap.add_argument("--max-pages", type=int, default=200, help="起点URLごとの最大取得ページ数")
    ap.add_argument("--delay", type=float, default=0.5, help="最小リクエスト間隔（秒）。robotsのCrawl-delayがあれば大きい方")
    ap.add_argument("--timeout", type=int, default=20, help="HTTPタイムアウト（秒）")

    # saving/downloading
    ap.add_argument("--no-download", action="store_true",
                    help="保存（pages/files）を一切行わず、リンク収集＋manifest記録のみ")
    ap.add_argument("--no-download-files", action="store_true",
                    help="議事録本体（pdf/doc等）のダウンロードを行わない（ページ保存は可）")

    # resume
    ap.add_argument("--resume", action="store_true", default=True, help="manifest を見て再開（デフォルトON）")
    ap.add_argument("--no-resume", action="store_true", help="resume を無効化（毎回最初から）")

    # seed skip
    ap.add_argument("--skip-completed-seeds", action="store_true", default=True,
                    help="manifest上で完了済みseedはスキップ（デフォルトON）")
    ap.add_argument("--no-skip-completed-seeds", action="store_true",
                    help="完了済みseedも処理（seed単位のスキップ無効）")
    ap.add_argument("--force-crawl", action="store_true",
                    help="完了済みseedでも強制的にクロール（seedスキップ無視）")

    # seed recheck (v4.1)
    ap.add_argument("--recheck-seeds", action="store_true", default=True,
                    help="seed_done でも seed(一覧)が更新されていれば再クロール（デフォルトON）")
    ap.add_argument("--no-recheck-seeds", action="store_true",
                    help="seed_done は更新チェックせず常にスキップ")

    # download
    ap.add_argument("--force-download", action="store_true",
                    help="DL済みでも強制的に再ダウンロードする")

    # robots
    ap.add_argument("--respect-robots", dest="respect_robots", action="store_true",
                    help="robots.txt を尊重する（デフォルト）")
    ap.add_argument("--no-respect-robots", dest="respect_robots", action="store_false",
                    help="robots.txt を無視する")
    ap.set_defaults(respect_robots=True)

    # crawl scope
    ap.add_argument("--same-domain-only", action="store_true", help="起点と同一ドメインのみ巡回（推奨）")
    ap.add_argument("--same-path-prefix-only", action="store_true", help="起点URLのパス配下のみ巡回（厳しめ）")

    # identification / heuristics
    ap.add_argument("--user-agent", default="MinuteExtractorBot/4.1 (+local script)", help="User-Agent")
    ap.add_argument("--keywords", default=",".join(DEFAULT_KEYWORDS), help="議事録判定キーワード（カンマ区切り）")
    ap.add_argument("--file-exts", default=",".join(sorted(DEFAULT_FILE_EXTS)), help="ファイル拡張子（カンマ区切り）")
    ap.add_argument("--url-hints", default=",".join(DEFAULT_URL_HINTS), help="URLヒント語（カンマ区切り）")

    # report
    ap.add_argument("--report-dir", default="", help="レポート出力先（空なら outdir/reports）")

    # parallel
    ap.add_argument("--workers", type=int, default=8, help="seed並列実行数（スレッド数）")

    args = ap.parse_args()

    in_path = Path(args.input)
    out_dir = Path(args.outdir)
    manifest_path = Path(args.manifest)

    if args.overwrite_manifest:
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text("", encoding="utf-8")

    if not in_path.exists():
        print(f"[ERROR] input not found: {in_path}", file=sys.stderr)
        return 2

    records = json.loads(in_path.read_text(encoding="utf-8"))

    # flags
    resume = args.resume and (not args.no_resume)
    skip_completed_seeds = (
        args.skip_completed_seeds
        and (not args.no_skip_completed_seeds)
        and (not args.force_crawl)
    )
    recheck_seeds = args.recheck_seeds and (not args.no_recheck_seeds)

    # saving/download flags
    # --no-download なら pages/files 共に保存しない
    save_pages = (not args.no_download)
    # files は no-download か no-download-files で抑止
    download_files = (not args.no_download) and (not args.no_download_files)

    cfg = CrawlConfig(
        max_depth=args.max_depth,
        max_pages=args.max_pages,
        delay_sec=args.delay,
        timeout_sec=args.timeout,
        user_agent=args.user_agent,
        keywords=[k.strip() for k in args.keywords.split(",") if k.strip()],
        file_exts={e.strip().lower() for e in args.file_exts.split(",") if e.strip()},
        url_hints=[h.strip().lower() for h in args.url_hints.split(",") if h.strip()],
        same_domain_only=bool(args.same_domain_only),
        same_path_prefix_only=bool(args.same_path_prefix_only),
        respect_robots=bool(args.respect_robots),
    )

    manifest = ManifestWriter(manifest_path)
    cache = load_manifest_cache(manifest_path) if resume else ManifestCache(set(), set(), set(), {})
    cache_lock = Lock()

    robots_report = RobotsDisallowReport()
    limiter = DomainRateLimiter()
    robots = RobotsManager(timeout_sec=cfg.timeout_sec, user_agent=cfg.user_agent, manifest=manifest) if cfg.respect_robots else None

    state = SharedState(
        manifest=manifest,
        cache=cache,
        cache_lock=cache_lock,
        robots_report=robots_report,
        limiter=limiter,
        robots=robots,
    )

    manifest.write({
        "ts": now_iso_jst(),
        "event": "start",
        "input": str(in_path),
        "outdir": str(out_dir),
        "threshold": args.threshold,
        "resume": resume,
        "skip_completed_seeds": skip_completed_seeds,
        "recheck_seeds": recheck_seeds,
        "force_download": args.force_download,
        "force_crawl": args.force_crawl,
        "respect_robots": cfg.respect_robots,
        "parallel_workers": int(args.workers),
        "save_pages": save_pages,
        "download_files": download_files,
        "config": {
            "max_depth": cfg.max_depth,
            "max_pages": cfg.max_pages,
            "delay_sec": cfg.delay_sec,
            "timeout_sec": cfg.timeout_sec,
            "same_domain_only": cfg.same_domain_only,
            "same_path_prefix_only": cfg.same_path_prefix_only,
        }
    })

    # build seed tasks
    tasks: List[Tuple[str, str, str, str]] = []  # (prefecture, city, mode, seed_url)
    for rec in records:
        prefecture = rec.get("prefecture", "")
        city = rec.get("city", "")
        if not prefecture or not city:
            continue

        mode, url_map = choose_seed_urls(rec, threshold=args.threshold)
        seed_urls = [normalize_url(u) for u in url_map.keys() if u]

        manifest.write({
            "ts": now_iso_jst(),
            "event": "city_start",
            "prefecture": prefecture,
            "city": city,
            "mode": mode,
            "seed_count": len(seed_urls),
            "parent_total": sum_counts(rec.get("parent") or {}),
            "grand_parent_total": sum_counts(rec.get("grand_parent") or {}),
        })

        if not seed_urls:
            manifest.write({
                "ts": now_iso_jst(),
                "event": "city_skip_no_seed",
                "prefecture": prefecture,
                "city": city,
            })
            continue

        for seed in seed_urls:
            tasks.append((prefecture, city, mode, seed))

    tasks = round_robin_by_netloc(tasks)

    # counters
    total_found_links = 0
    skipped_seed_count = 0
    counters_lock = Lock()

    def process_seed(prefecture: str, city: str, mode: str, seed: str) -> Tuple[int, bool]:
        nonlocal skipped_seed_count
        seed = normalize_url(seed)

        # skip logic with recheck
        if skip_completed_seeds:
            with state.cache_lock:
                done = (seed in state.cache.completed_seeds)
                prev_meta = state.cache.seed_meta.get(seed)

            if done:
                # force-crawl already handled in skip_completed_seeds flag calculation
                if recheck_seeds:
                    # robots before checking
                    if cfg.respect_robots and state.robots is not None:
                        if not state.robots.can_fetch(seed):
                            state.manifest.write({
                                "ts": now_iso_jst(),
                                "event": "skip_seed_already_done_robots_disallow",
                                "prefecture": prefecture,
                                "city": city,
                                "mode": mode,
                                "seed_url": seed,
                            })
                            with counters_lock:
                                skipped_seed_count += 1
                            return (0, True)

                    # rate limit for seed check
                    state.limiter.wait(seed, effective_delay(cfg, state.robots, seed))
                    changed, new_meta = fetch_seed_state(seed, cfg.timeout_sec, cfg.user_agent, prev_meta)

                    # write & cache seed_state
                    state.manifest.write({
                        "ts": now_iso_jst(),
                        "event": "seed_state",
                        "seed_url": seed,
                        "etag": new_meta.get("etag", ""),
                        "last_modified": new_meta.get("last_modified", ""),
                        "content_sha1": new_meta.get("content_sha1", ""),
                    })
                    with state.cache_lock:
                        state.cache.seed_meta[seed] = new_meta

                    if not changed:
                        state.manifest.write({
                            "ts": now_iso_jst(),
                            "event": "skip_seed_already_done_not_modified",
                            "prefecture": prefecture,
                            "city": city,
                            "mode": mode,
                            "seed_url": seed,
                        })
                        with counters_lock:
                            skipped_seed_count += 1
                        return (0, True)

                    state.manifest.write({
                        "ts": now_iso_jst(),
                        "event": "seed_changed_re_crawl",
                        "prefecture": prefecture,
                        "city": city,
                        "mode": mode,
                        "seed_url": seed,
                    })
                else:
                    state.manifest.write({
                        "ts": now_iso_jst(),
                        "event": "skip_seed_already_done",
                        "prefecture": prefecture,
                        "city": city,
                        "mode": mode,
                        "seed_url": seed,
                    })
                    with counters_lock:
                        skipped_seed_count += 1
                    return (0, True)

        found = crawl_and_collect(
            start_url=seed,
            cfg=cfg,
            out_dir=out_dir,
            state=state,
            prefecture=prefecture,
            city=city,
            save_pages=save_pages,
            download_files=download_files,
            resume=resume,
            force_download=args.force_download,
        )

        state.manifest.write({
            "ts": now_iso_jst(),
            "event": "seed_done",
            "prefecture": prefecture,
            "city": city,
            "mode": mode,
            "seed_url": seed,
            "found_count": len(found),
        })

        with state.cache_lock:
            state.cache.completed_seeds.add(seed)

        return (len(found), False)

    # run parallel
    with ThreadPoolExecutor(max_workers=max(1, int(args.workers))) as ex:
        futures = [ex.submit(process_seed, p, c, m, s) for (p, c, m, s) in tasks]
        for fut in as_completed(futures):
            try:
                cnt, _skipped = fut.result()
                with counters_lock:
                    total_found_links += cnt
            except Exception as e:
                manifest.write({
                    "ts": now_iso_jst(),
                    "event": "seed_task_exception",
                    "error": repr(e),
                })

    # robots report
    report_dir = Path(args.report_dir) if args.report_dir else (out_dir / "reports")
    write_robots_reports(robots_report, report_dir)

    manifest.write({
        "ts": now_iso_jst(),
        "event": "robots_report_written",
        "report_dir": str(report_dir),
        "robots_disallow_total": len(robots_report.entries),
    })

    manifest.write({
        "ts": now_iso_jst(),
        "event": "done",
        "total_found_links": total_found_links,
        "skipped_seed_count": skipped_seed_count,
        "robots_disallow_total": len(robots_report.entries),
        "save_pages": save_pages,
        "download_files": download_files,
        "recheck_seeds": recheck_seeds,
    })

    print(f"[DONE] total_found_links={total_found_links} skipped_seed_count={skipped_seed_count}")
    print(f"[ROBOTS_DISALLOW] {len(robots_report.entries)} (reports: {report_dir})")
    print(f"[MANIFEST] {manifest_path}")
    print(f"[OUTDIR] {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
