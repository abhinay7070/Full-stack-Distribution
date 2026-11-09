"""Live ATS sweep: every known public job board (Ashby, Greenhouse, Lever, Workable, Recruitee, Breezy, Teamtailor).

Usage: python3 scripts/ats_sweep.py [--providers ashby,greenhouse,...] [--max-postings 60]
Board slugs come from
  1 open-ats-feed index (18,164 live boards; raw_sources/H1_job_arbitrage/open_ats_feed) with <= --max-postings
  2 ATS links inside HN 'Who is hiring' posts (.cache/hn_hiring_posts.jsonl)
  3 inference for every universe domain (domain label / brand core) on Ashby, Greenhouse, Lever
Every fetched board is normalized to {provider, slug, company_name, jobs[{id,title,department,location,posted_at,
url,comp,description}]} and written to raw_sources/H1_job_arbitrage/ats_live/<provider>.jsonl.gz (boards with >= 1
job; descriptions capped at 6,000 chars). A board index (status per slug) goes to ats_live/index.json.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import html
import io
import json
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import http_fetch as hf  # noqa: E402
import pipeline_db as pdb  # noqa: E402
import resolve_domains as rd  # noqa: E402

RAW = pdb.RAW_DIR / "H1_job_arbitrage"
LIVE = RAW / "ats_live"
TAG = re.compile(r"<[^>]+>")


def txt(h: str | None) -> str:
    return re.sub(r"\s+", " ", html.unescape(TAG.sub(" ", html.unescape(h or "")))).strip()


def iso(v) -> str | None:
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return datetime.fromtimestamp(v / 1000 if v > 1e11 else v, tz=timezone.utc).date().isoformat()
    m = re.match(r"(\d{4}-\d\d-\d\d)", str(v))
    if m:
        return m.group(1)
    try:
        from email.utils import parsedate_to_datetime
        return parsedate_to_datetime(str(v)).date().isoformat()
    except Exception:  # noqa: BLE001
        return None


URL = {
    "ashby": "https://api.ashbyhq.com/posting-api/job-board/{s}?includeCompensation=true",
    "greenhouse": "https://boards-api.greenhouse.io/v1/boards/{s}/jobs?content=true",
    "lever": "https://api.lever.co/v0/postings/{s}?mode=json",
    "workable": "https://apply.workable.com/api/v1/widget/accounts/{s}?details=true",
    "recruitee": "https://{s}.recruitee.com/api/offers/",
    "breezy": "https://{s}.breezy.hr/json",
    "teamtailor": "https://{s}.teamtailor.com/jobs.rss",
    "smartrecruiters": "https://api.smartrecruiters.com/v1/companies/{s}/postings?limit=100",
    "personio": "https://{s}.jobs.personio.de/xml?language=en",
}


def parse(provider: str, slug: str, r: hf.Resp) -> dict | None:
    if r.status != 200 or not r.text:
        return None
    jobs, name = [], None
    try:
        if provider == "ashby":
            d = r.json()
            for j in d.get("jobs") or []:
                comp = (j.get("compensation") or {}).get("compensationTierSummary")
                jobs.append({"id": j.get("id"), "title": j.get("title"), "department": j.get("department") or j.get("team"),
                             "location": j.get("location"), "posted_at": iso(j.get("publishedAt")),
                             "url": j.get("jobUrl"), "comp": comp, "description": (j.get("descriptionPlain") or "")[:6000],
                             "remote": j.get("isRemote")})
        elif provider == "greenhouse":
            d = r.json()
            for j in d.get("jobs") or []:
                jobs.append({"id": j.get("id"), "title": j.get("title"),
                             "department": ", ".join(x["name"] for x in j.get("departments") or []),
                             "location": (j.get("location") or {}).get("name"),
                             "posted_at": iso(j.get("first_published") or j.get("updated_at")),
                             "url": j.get("absolute_url"), "comp": None, "description": txt(j.get("content"))[:6000]})
                name = name or j.get("company_name")
        elif provider == "lever":
            d = r.json()
            if not isinstance(d, list):
                return None
            for j in d:
                c = j.get("categories") or {}
                sr = j.get("salaryRange") or {}
                jobs.append({"id": j.get("id"), "title": j.get("text"), "department": c.get("team") or c.get("department"),
                             "location": c.get("location"), "posted_at": iso(j.get("createdAt")),
                             "url": j.get("hostedUrl"),
                             "comp": f"{sr.get('currency', '')} {sr.get('min')}-{sr.get('max')} {sr.get('interval', '')}"
                             if sr.get("min") else None,
                             "description": f"{j.get('descriptionPlain') or ''} {j.get('additionalPlain') or ''}"[:6000]})
        elif provider == "workable":
            d = r.json()
            name = d.get("name")
            for j in d.get("jobs") or []:
                jobs.append({"id": j.get("shortcode"), "title": j.get("title"), "department": j.get("department"),
                             "location": ", ".join(x for x in (j.get("city"), j.get("country")) if x),
                             "posted_at": iso(j.get("published_on") or j.get("created_at")), "url": j.get("url"),
                             "comp": None, "description": txt(j.get("description"))[:6000]})
        elif provider == "recruitee":
            d = r.json()
            for j in d.get("offers") or []:
                name = name or j.get("company_name")
                jobs.append({"id": j.get("id"), "title": j.get("title"), "department": j.get("department"),
                             "location": j.get("location") or ", ".join(x for x in (j.get("city"), j.get("country")) if x),
                             "posted_at": iso(j.get("published_at") or j.get("created_at")),
                             "url": j.get("careers_url"), "comp": None,
                             "description": txt(j.get("description"))[:6000]})
        elif provider == "breezy":
            d = r.json()
            if not isinstance(d, list):
                return None
            for j in d:
                name = name or (j.get("company") or {}).get("name")
                loc = j.get("location") or {}
                jobs.append({"id": j.get("id"), "title": j.get("name"), "department": j.get("department"),
                             "location": loc.get("name") if isinstance(loc, dict) else str(loc),
                             "posted_at": iso(j.get("published_date")), "url": j.get("url"), "comp": None,
                             "description": ""})
        elif provider == "teamtailor":
            root = ET.fromstring(r.text.encode())
            ch = root.find("channel")
            name = (ch.findtext("title") or "").strip() if ch is not None else None
            for it in (ch.findall("item") if ch is not None else []):
                jobs.append({"id": it.findtext("guid"), "title": it.findtext("title"), "department": None,
                             "location": " ".join(x.text or "" for x in it.iter() if x.tag.endswith("city")),
                             "posted_at": iso(it.findtext("pubDate")), "url": it.findtext("link"), "comp": None,
                             "description": txt(it.findtext("description"))[:6000]})
        elif provider == "smartrecruiters":
            d = r.json()
            for j in d.get("content") or []:
                name = name or (j.get("company") or {}).get("name")
                loc = j.get("location") or {}
                jobs.append({"id": j.get("id"), "title": j.get("name"),
                             "department": (j.get("department") or {}).get("label"),
                             "location": loc.get("fullLocation"), "posted_at": iso(j.get("releasedDate")),
                             "url": f"https://jobs.smartrecruiters.com/{slug}/{j.get('id')}", "comp": None,
                             "description": " ".join(x for x in ((j.get("industry") or {}).get("label"),
                                                                 (j.get("function") or {}).get("label")) if x),
                             "remote": loc.get("remote")})
        elif provider == "personio":
            if "<workzag-jobs" not in r.text[:500]:
                return None
            root = ET.fromstring(r.text.encode())
            for it in root.findall("position"):
                name = name or (it.findtext("subcompany") or "").strip() or None
                desc = " ".join(txt(v.findtext("value")) for v in it.iter("jobDescription"))
                jobs.append({"id": it.findtext("id"), "title": it.findtext("name"), "department": it.findtext("department"),
                             "location": it.findtext("office"), "posted_at": iso(it.findtext("createdAt")),
                             "url": f"https://{slug}.jobs.personio.de/job/{it.findtext('id')}", "comp": None,
                             "description": desc[:6000]})
    except (ValueError, ET.ParseError, AttributeError, TypeError):
        return None
    return {"provider": provider, "slug": slug, "company_name": name, "jobs": jobs}


def split_large(path: Path, limit: int = 45_000_000) -> list[Path]:
    """Keep every raw file under GitHub's limits: split <prov>.jsonl.gz into <prov>.pN.jsonl.gz parts (< ~45 MB)."""
    if path.stat().st_size <= limit:
        return [path]
    import math
    n = math.ceil(path.stat().st_size / limit) + 1
    lines = gzip.open(path, "rt").readlines()
    per = math.ceil(len(lines) / n)
    out = []
    for i in range(n):
        q = path.with_name(path.name.replace(".jsonl.gz", f".p{i + 1}.jsonl.gz"))
        with gzip.open(q, "wt") as f:
            f.writelines(lines[i * per:(i + 1) * per])
        out.append(q)
    path.unlink()
    return out


def slug_variants(domain: str, name: str | None) -> list[str]:
    label = domain.split(".")[0]
    _, full, short = rd.brand_cores(name or label)
    v = [label, full, short, re.sub(r"^(get|try|use|join|go|with|hey)", "", label)]
    if name:
        v.append(re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-"))
    return [x for x in dict.fromkeys(v) if x and len(x) >= 2]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--providers", default="ashby,greenhouse,lever,recruitee,breezy,teamtailor,workable")
    ap.add_argument("--max-postings", type=int, default=60)
    a = ap.parse_args()
    provs = a.providers.split(",")
    LIVE.mkdir(parents=True, exist_ok=True)
    want: dict[str, dict[str, set]] = {p: {} for p in URL}  # provider -> slug -> origins

    def add(p, s, origin):
        if p in want and s:
            want[p].setdefault(s.lower(), set()).add(origin)

    rows = list(csv.DictReader(io.TextIOWrapper(gzip.open(RAW / "open_ats_feed" / "all.csv.gz"), encoding="utf-8")))
    for r in rows:
        n = int(r.get("open_postings") or 0)
        if 1 <= n <= a.max_postings:
            add(r["provider"], r["token"], "open_ats_feed")
    toks = json.load(gzip.open(RAW / "open_ats_feed" / "tokens.json.gz"))
    for s in toks.get("workable", []):
        add("workable", s, "open_ats_feed")
    hn = pdb.ROOT / ".cache" / "hn_hiring_posts.jsonl"
    if hn.exists():
        for line in open(hn):
            p = json.loads(line)
            for prov, ss in p["ats_slugs"].items():
                for s in ss:
                    add(prov, s, "hn_whoishiring")
    sc = pdb.ROOT / ".cache" / "site_scan.jsonl"
    if sc.exists():  # ATS boards linked from company homepages (careers links / embeds)
        for line in open(sc):
            r = json.loads(line)
            ats = (json.loads(r.get("social") or "{}") or {}).get("ats") or {}
            for prov, ss in ats.items():
                for s_ in ss:
                    add(prov.replace("_embed", ""), s_, f"site:{r['company_domain']}")
    for cc in sorted((RAW / "commoncrawl").glob("*_ats_urls.jsonl.gz")):  # E1: every board URL in Common Crawl
        for line in gzip.open(cc, "rt"):
            x = json.loads(line)
            add(x["provider"], x["slug"], "commoncrawl")
    conn = pdb.connect()
    for c in conn.execute("SELECT company_domain, company_name FROM companies WHERE status!='disqualified'"):
        for s in slug_variants(c["company_domain"], c["company_name"]):
            for prov in ("ashby", "greenhouse", "lever"):
                add(prov, s, f"infer:{c['company_domain']}")
    index = {}
    for prov in provs:
        slugs = sorted(want[prov])
        urls = {s: URL[prov].format(s=s) for s in slugs}
        print(f"{prov}: {len(slugs)} boards", file=sys.stderr)
        res = hf.fetch_many(urls.values(), workers=6 if prov == "workable" else 24, timeout=25, retries=2,
                            max_bytes=8_000_000)
        n_ok = 0
        for old_part in LIVE.glob(f"{prov}.p*.jsonl.gz"):
            old_part.unlink()
        with gzip.open(LIVE / f"{prov}.jsonl.gz", "wt") as f:
            for s in slugs:
                r = res[urls[s]]
                b = parse(prov, s, r)
                index[f"{prov}:{s}"] = {"status": r.status, "jobs": len(b["jobs"]) if b else 0,
                                        "origins": sorted(want[prov][s])[:5]}
                if b and b["jobs"]:
                    n_ok += 1
                    b["origins"] = sorted(want[prov][s])
                    f.write(json.dumps(b) + "\n")
        split_large(LIVE / f"{prov}.jsonl.gz")
        print(f"  {prov}: {n_ok} boards with jobs", file=sys.stderr)
    (LIVE / f"index_{'_'.join(provs)}.json").write_text(json.dumps(index) + "\n")
    st = {}
    for k, v in index.items():
        p = k.split(":")[0]
        st.setdefault(p, {"probed": 0, "with_jobs": 0, "jobs": 0})
        st[p]["probed"] += 1
        st[p]["with_jobs"] += v["jobs"] > 0
        st[p]["jobs"] += v["jobs"]
    print(json.dumps(st, indent=1))


if __name__ == "__main__":
    main()
