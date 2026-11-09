"""E1: discover every public ATS job board URL from the Common Crawl columnar index (cc-index-table, parquet).

The CDX query service (index.commoncrawl.org) resets connections from this environment, so this reads the parquet
files on data.commoncrawl.org directly with HTTP range requests: all 300 footers (~20 MB) (the table is
sorted by url_surtkey within each part file), then just the row groups whose surtkey range overlaps a job-board host, and only 2 columns.
Output: raw_sources/H1_job_arbitrage/commoncrawl/<crawl>_ats_urls.jsonl.gz  ({provider, slug, url})
Usage: python3 scripts/cc_ats_discover.py [CC-MAIN-2026-43 ...]
"""
from __future__ import annotations

import gzip
import io
import json
import re
import sys
import time
from pathlib import Path

import pyarrow.parquet as pq
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pipeline_db as pdb  # noqa: E402

BASE = "https://data.commoncrawl.org/"
OUT = pdb.RAW_DIR / "H1_job_arbitrage" / "commoncrawl"
UA = {"User-Agent": "gtm-research-pipeline/3.0 (cc-index research)"}
S = requests.Session()
# surt prefix -> provider; slug extraction regex on the URL
PREFIX = {
    "com,ashbyhq,jobs)/": ("ashby", r"jobs\.ashbyhq\.com/([^/?#]+)"),
    "io,greenhouse,boards)/": ("greenhouse", r"boards\.greenhouse\.io/(?:embed/job_board\?for=)?([^/?#&]+)"),
    "io,greenhouse,job-boards)/": ("greenhouse", r"job-boards\.greenhouse\.io/([^/?#]+)"),
    "io,greenhouse,boards,job-boards)/": ("greenhouse", r"greenhouse\.io/([^/?#]+)"),
    "co,lever,jobs)/": ("lever", r"jobs\.lever\.co/([^/?#]+)"),
    "com,workable,apply)/": ("workable", r"apply\.workable\.com/([^/?#]+)"),
    "com,smartrecruiters,jobs)/": ("smartrecruiters", r"jobs\.smartrecruiters\.com/([^/?#]+)"),
    "com,smartrecruiters,careers)/": ("smartrecruiters", r"careers\.smartrecruiters\.com/([^/?#]+)"),
    "de,personio,jobs,": ("personio", r"https?://([a-z0-9-]+)\.jobs\.personio\.de"),
    "com,personio,jobs,": ("personio_com", r"https?://([a-z0-9-]+)\.jobs\.personio\.com"),
    "com,recruitee,": ("recruitee", r"https?://([a-z0-9-]+)\.recruitee\.com"),
    "com,teamtailor,": ("teamtailor", r"https?://([a-z0-9-]+)\.teamtailor\.com"),
    "hr,breezy,": ("breezy", r"https?://([a-z0-9-]+)\.breezy\.hr"),
}
SKIP = {"www", "api", "app", "jobs", "careers", "embed", "static", "assets", "help", "support", "blog", "status",
        "en", "de", "fr", "admin", "login", "signup", "cdn", "images", "img", "media", "docs", "developers", "favicon.ico",
        "robots.txt", "sitemap.xml", "job_board", "v1", "boards", "_next"}


class RangeFile(io.RawIOBase):
    def __init__(self, url: str):
        self.url, self.pos = url, 0
        for a in range(5):
            try:
                r = S.head(url, headers=UA, timeout=60, allow_redirects=True)
                self.size = int(r.headers["Content-Length"])
                break
            except Exception:  # noqa: BLE001
                time.sleep(2 ** a)
        else:
            raise IOError(f"HEAD failed {url}")
        self.bytes = 0

    def readable(self):
        return True

    def seekable(self):
        return True

    def tell(self):
        return self.pos

    def seek(self, off, whence=0):
        self.pos = off if whence == 0 else (self.pos + off if whence == 1 else self.size + off)
        return self.pos

    def read(self, n=-1):
        if n is None or n < 0:
            n = self.size - self.pos
        if n <= 0 or self.pos >= self.size:
            return b""
        end = min(self.pos + n, self.size) - 1
        for a in range(6):
            try:
                r = S.get(self.url, headers={**UA, "Range": f"bytes={self.pos}-{end}"}, timeout=180)
                if r.status_code in (200, 206):
                    data = r.content
                    break
            except Exception:  # noqa: BLE001
                pass
            time.sleep(2 ** a)
        else:
            raise IOError(f"range read failed {self.url} {self.pos}-{end}")
        self.pos += len(data)
        self.bytes += len(data)
        return data

    def readinto(self, b):
        d = self.read(len(b))
        b[:len(d)] = d
        return len(d)


def surt_range(md, col: int):
    """(min, max) url_surtkey over the whole file, from row-group statistics."""
    lo = hi = None
    for i in range(md.num_row_groups):
        st = md.row_group(i).column(col).statistics
        if st is None or not st.has_min_max:
            continue
        mn, mx = st.min, st.max
        mn = mn.decode() if isinstance(mn, bytes) else mn
        mx = mx.decode() if isinstance(mx, bytes) else mx
        lo = mn if lo is None or mn < lo else lo
        hi = mx if hi is None or mx > hi else hi
    return lo, hi


def main() -> None:
    crawls = sys.argv[1:] or ["CC-MAIN-2026-43"]
    OUT.mkdir(parents=True, exist_ok=True)
    for crawl in crawls:
        paths = gzip.decompress(S.get(f"{BASE}crawl-data/{crawl}/cc-index-table.paths.gz", headers=UA, timeout=60).content)
        files = [p for p in paths.decode().split() if "subset=warc" in p]
        cache: dict[int, tuple] = {}

        def meta(i):
            if i not in cache:
                f = RangeFile(BASE + files[i])
                pf = pq.ParquetFile(f)
                col = pf.schema_arrow.get_field_index("url_surtkey")
                cache[i] = (pf, f, col, surt_range(pf.metadata, col))
            return cache[i]

        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(16) as ex:  # part files are each sorted, but not ordered among themselves
            list(ex.map(meta, range(len(files))))
        want_files = {i for i in range(len(files)) for pre in PREFIX
                      if meta(i)[3][0] and meta(i)[3][0] <= pre + "\uffff" and meta(i)[3][1] >= pre}
        print(f"{crawl}: {len(files)} files, footers read {len(cache)}, matching files {sorted(want_files)}",
              file=sys.stderr)
        seen, n_bytes = {}, 0
        for i in sorted(want_files):
            pf, f, col, _ = meta(i)
            md = pf.metadata
            for g in range(md.num_row_groups):
                st = md.row_group(g).column(col).statistics
                mn = st.min.decode() if isinstance(st.min, bytes) else st.min
                mx = st.max.decode() if isinstance(st.max, bytes) else st.max
                hit = [p for p in PREFIX if mn <= p + "￿" and mx >= p]
                if not hit:
                    continue
                t = pf.read_row_group(g, columns=["url_surtkey", "url"])
                for s, u in zip(t.column(0).to_pylist(), t.column(1).to_pylist()):
                    for p in hit:
                        if s.startswith(p):
                            prov, rx = PREFIX[p]
                            m = re.search(rx, u, re.I)
                            if m:
                                slug = m.group(1).lower()
                                if slug not in SKIP and len(slug) >= 2 and "." not in slug[-5:]:
                                    seen.setdefault((prov.replace("_com", ""), slug), u)
                            break
            n_bytes += f.bytes
            print(f"  file {i}: {len(seen)} boards so far, {n_bytes / 1e6:.0f} MB read", file=sys.stderr)
        with gzip.open(OUT / f"{crawl}_ats_urls.jsonl.gz", "wt") as fo:
            for (prov, slug), u in sorted(seen.items()):
                fo.write(json.dumps({"provider": prov, "slug": slug, "url": u}) + "\n")
        from collections import Counter
        print(json.dumps({"crawl": crawl, "boards": len(seen), "by_provider": Counter(p for p, _ in seen)}, indent=1))


if __name__ == "__main__":
    main()
