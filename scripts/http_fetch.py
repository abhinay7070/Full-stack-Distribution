"""Polite, cached, parallel HTTP for live sources (ATS APIs, HN Algolia, EDGAR, RSS, homepages, DNS).

- One shared requests.Session per thread, retries with backoff on 429/5xx.
- Per-host minimum interval (RATE) so no host sees bursts.
- On-disk cache under .cache/http/ (gitignored) keyed by URL: re-runs are deterministic and offline-capable;
  pass max_age (seconds) to refresh.
- fetch_many() runs a thread pool and returns {url: Resp}.
Generic client name only (no personal data in headers).
"""
from __future__ import annotations

import gzip
import hashlib
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import requests

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / ".cache" / "http"
UA = "Mozilla/5.0 (compatible; gtm-research-pipeline/2.0; +https://github.com)"
SEC_UA = "gtm-research-pipeline research-bot admin@gtm-research.invalid"  # SEC asks for a declared UA
RATE = {  # min seconds between requests per host
    "api.ashbyhq.com": 0.07, "boards-api.greenhouse.io": 0.07, "api.lever.co": 0.1, "apply.workable.com": 1.2,
    "hn.algolia.com": 0.1, "efts.sec.gov": 0.15, "www.sec.gov": 0.15, "dns.google": 0.004, "trustmrr.com": 0.5,
    "autocomplete.clearbit.com": 0.15, "api.smartrecruiters.com": 0.1,
}
DEFAULT_RATE = 0.0
_locks: dict[str, threading.Lock] = {}
_last: dict[str, float] = {}
_glock = threading.Lock()
_tls = threading.local()


@dataclass
class Resp:
    url: str
    status: int
    text: str
    final_url: str = ""
    from_cache: bool = False

    def json(self):
        return json.loads(self.text)


def _session() -> requests.Session:
    s = getattr(_tls, "s", None)
    if s is None:
        s = requests.Session()
        _tls.s = s
    return s


def _key(url: str) -> Path:
    h = hashlib.sha1(url.encode()).hexdigest()
    return CACHE / h[:2] / f"{h}.json.gz"


def _throttle(host: str) -> None:
    gap = RATE.get(host, DEFAULT_RATE)
    if not gap:
        return
    with _glock:
        lk = _locks.setdefault(host, threading.Lock())
    with lk:
        wait = _last.get(host, 0) + gap - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last[host] = time.monotonic()


def fetch(url: str, max_age: float | None = None, timeout: float = 20, retries: int = 3,
          headers: dict | None = None, max_bytes: int = 3_000_000, cache_errors: bool = True) -> Resp:
    p = _key(url)
    if p.exists() and (max_age is None or time.time() - p.stat().st_mtime < max_age):
        try:
            d = json.loads(gzip.decompress(p.read_bytes()))
            return Resp(url, d["status"], d["text"], d.get("final_url", ""), True)
        except Exception:
            pass
    host = urlparse(url).hostname or ""
    hdr = {"User-Agent": SEC_UA if host.endswith("sec.gov") else UA, "Accept-Encoding": "gzip, deflate"}
    hdr.update(headers or {})
    status, text, final = 0, "", ""
    for attempt in range(retries + 1):
        _throttle(host)
        try:
            r = _session().get(url, headers=hdr, timeout=timeout, stream=True, allow_redirects=True)
            raw = r.raw.read(max_bytes, decode_content=True) if r.raw else b""
            r.close()
            status, final = r.status_code, r.url
            text = raw.decode(r.encoding or "utf-8", "replace") if raw else ""
            if status in (429, 500, 502, 503, 504) and attempt < retries:
                ra = r.headers.get("Retry-After")
                time.sleep(min(float(ra), 60) if ra and ra.isdigit() else 2 ** (attempt + 1))
                continue
            break
        except Exception as e:  # noqa: BLE001  (requests, urllib3 read timeouts mid-body, socket errors)
            status, text = -1, f"{type(e).__name__}: {e}"[:300]
            if attempt < retries:
                time.sleep(2 ** attempt)
    if status > 0 and (cache_errors or status < 400):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(gzip.compress(json.dumps({"url": url, "status": status, "text": text,
                                                 "final_url": final, "fetched_at": time.time()}).encode()))
    return Resp(url, status, text, final, False)


def fetch_many(urls, workers: int = 16, **kw) -> dict[str, Resp]:
    urls = list(dict.fromkeys(urls))
    out: dict[str, Resp] = {}
    with ThreadPoolExecutor(workers) as ex:
        for u, r in zip(urls, ex.map(lambda u: fetch(u, **kw), urls)):
            out[u] = r
    return out
