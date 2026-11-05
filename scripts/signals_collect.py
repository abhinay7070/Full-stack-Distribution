"""Collect raw evidence for H4 (launches), H7 (verified revenue), H8 (open-source traction).

Usage: python3 scripts/signals_collect.py [h4|h7|h8|all] [<ext dir with cloned mirrors>]
Outputs (.cache/*.jsonl) consumed by tag_signals.py; raw payloads under raw_sources/H4_launch_decay,
raw_sources/H7_revenue_no_gtm, raw_sources/H8_oss_traction.

H4  Show HN + Launch HN stories (hn.algolia.com, weekly windows, last 180 days, every hit, url -> domain);
    YC Launch posts (launches[] in the YC directory snapshot); Product Hunt daily top products
    (yiGmMk/producthunt mirror + startupdashboard launches; names only -> matched to the universe by brand).
H7  TrustMRR: startupdashboard mrrLeaderboard (all daily files), FlyingCarlos/trustmrr-dashboard snapshots
    (1,300+ startups, daily MRR), and the live trustmrr.com/startup/<slug>.md page per startup (website, country,
    category, founded) - Stripe/LemonSqueezy-verified MRR.
H8  GitHub org pages (github.com/<org>, server-rendered repo list with stars) for every org linked from a
    company homepage or YC profile; trending repos (startupdashboard + linmoxi33 dataset).
"""
from __future__ import annotations

import glob
import gzip
import html
import json
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import http_fetch as hf  # noqa: E402
import pipeline_db as pdb  # noqa: E402

CACHE = pdb.ROOT / ".cache"
TODAY = date.today()
EXT = Path(sys.argv[2]) if len(sys.argv) > 2 else None


def dump(path: Path, rows: list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    op = gzip.open if path.suffix == ".gz" else open
    with op(path, "wt") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


# ------------------------------------------------------------------ H4
def h4() -> None:
    raw = pdb.RAW_DIR / "H4_launch_decay"
    out = []
    start = TODAY - timedelta(days=180)
    d = start
    while d <= TODAY:
        a, b = int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp()), \
            int(datetime((d + timedelta(days=7)).year, (d + timedelta(days=7)).month, (d + timedelta(days=7)).day,
                         tzinfo=timezone.utc).timestamp())
        for tag in ("show_hn", "launch_hn"):
            page = 0
            while True:
                u = (f"https://hn.algolia.com/api/v1/search_by_date?tags={tag}&numericFilters=created_at_i>={a},"
                     f"created_at_i<{b}&hitsPerPage=1000&page={page}")
                if tag == "launch_hn":
                    u = (f"https://hn.algolia.com/api/v1/search_by_date?query=%22Launch%20HN%22&tags=story&numericFilters="
                         f"created_at_i>={a},created_at_i<{b}&hitsPerPage=1000&page={page}")
                r = hf.fetch(u, max_age=None if d < TODAY - timedelta(days=10) else 3600 * 6)
                if r.status != 200:
                    break
                j = r.json()
                for h in j["hits"]:
                    t = h.get("title") or ""
                    if tag == "launch_hn" and not t.lower().startswith("launch hn"):
                        continue
                    out.append({"source": tag, "id": h["objectID"], "title": t, "url": h.get("url"),
                                "domain": pdb.normalize_domain(h.get("url")), "date": h["created_at"][:10],
                                "points": h.get("points"), "comments": h.get("num_comments"),
                                "hn_url": f"https://news.ycombinator.com/item?id={h['objectID']}"})
                page += 1
                if page >= j.get("nbPages", 0):
                    break
        d += timedelta(days=7)
    # YC launches
    for r in json.load(gzip.open(pdb.RAW_DIR / "H0_icp_universe" / "yc_all.json.gz", "rt")):
        dom = pdb.normalize_domain(r.get("website"))
        for lch in r.get("launches") or []:
            dt = (lch.get("created_at") or "")[:10]
            if dom and dt >= start.isoformat():
                out.append({"source": "yc_launch", "id": lch.get("id"), "title": lch.get("title"), "url": lch.get("url"),
                            "domain": dom, "date": dt, "points": int(lch.get("votes") or 0), "comments": None,
                            "hn_url": None})
    # Product Hunt (names only)
    ph = []
    if EXT:
        for f in sorted(glob.glob(str(EXT / "yiGmMk_producthunt" / "static" / "data" / "en" / "*.json"))):
            dd = json.load(open(f))
            for p in dd.get("products") or []:
                x = p.get("producthunt") or {}
                ph.append({"source": "producthunt_top10", "id": x.get("url"), "title": f"{x.get('name')} - {x.get('tagline')}",
                           "name": x.get("name"), "url": (x.get("url") or "").split("?")[0], "domain": None,
                           "date": (x.get("featuredAt") or dd["date"])[:10], "points": x.get("votesCount"),
                           "desc": (x.get("description") or "")[:400]})
        for f in sorted(glob.glob(str(EXT / "brianboler_startupdashboard" / "data" / "history" / "*.json"))):
            dd = json.load(open(f))
            for x in (dd.get("sections") or {}).get("launches") or []:
                if x.get("source") == "producthunt":
                    ph.append({"source": "producthunt_daily", "id": x.get("id"), "title": f"{x.get('title')} - {x.get('meta')}",
                               "name": x.get("title"), "url": (x.get("url") or "").split("?")[0], "domain": None,
                               "date": (x.get("createdAt") or "")[:10], "points": x.get("points"), "desc": x.get("meta")})
    seen, uniq = set(), []
    for r in out + ph:
        k = (r["source"], str(r["id"]))
        if k not in seen:
            seen.add(k)
            uniq.append(r)
    dump(raw / "launches.jsonl.gz", uniq)
    dump(CACHE / "launches.jsonl", uniq)
    print(json.dumps({"h4_launch_items": len(uniq), "by_source": {s: sum(1 for r in uniq if r["source"] == s)
                                                                  for s in {r["source"] for r in uniq}}}))


# ------------------------------------------------------------------ H7
def h7() -> None:
    raw = pdb.RAW_DIR / "H7_revenue_no_gtm"
    lb = {}
    if EXT:
        for f in sorted(glob.glob(str(EXT / "brianboler_startupdashboard" / "data" / "history" / "*.json"))):
            dd = json.load(open(f))
            for x in (dd.get("sections") or {}).get("mrrLeaderboard") or []:
                lb[x.get("name")] = {**x, "_date": dd.get("date")}
    snaps = {}
    if EXT and (EXT / "FlyingCarlos_trustmrr-dashboard" / "data" / "snapshots.jsonl").exists():
        for line in open(EXT / "FlyingCarlos_trustmrr-dashboard" / "data" / "snapshots.jsonl"):
            r = json.loads(line)
            s = snaps.setdefault(r["slug"], {"slug": r["slug"], "name": r["name"], "series": []})
            s["series"].append([r["date"], r.get("mrr"), r.get("rev30")])
            s.update({k: r.get(k) for k in ("category", "onSale", "price", "growthMRR30d")})
    # live per-startup markdown page: website / country / category / founded
    urls = {slug: f"https://trustmrr.com/startup/{slug}.md" for slug in snaps}
    res = hf.fetch_many(urls.values(), workers=4, timeout=20, retries=1)
    recs = []
    for slug, s in snaps.items():
        r = res[urls[slug]]
        md = r.text if r.status == 200 else ""
        def fld(label):
            m = re.search(rf"(?im)^\s*-\s*{label}:\s*\[?([^\]\n]+?)\]?(?:\(|$)", md)
            return m.group(1).strip() if m else None
        web_v = fld("Website")
        web = re.match(r"https?://\S+", web_v or "")
        ctry_v, cat_v, fnd_v = fld("Country"), fld("Category"), fld("Founded(?: date)?")
        ctry = re.match(r".+", ctry_v or "")
        cat = re.match(r".+", cat_v or "")
        fnd = re.match(r"\d{4}(?:-\d\d(?:-\d\d)?)?", fnd_v or "")
        ser = sorted(s["series"])
        last = ser[-1] if ser else [None, None, None]
        m90 = next((p[1] for p in ser if p[0] >= (TODAY - timedelta(days=90)).isoformat()), None)
        recs.append({"slug": slug, "name": s["name"], "website": web.group(0) if web else None,
                     "domain": pdb.normalize_domain(web.group(0)) if web else None,
                     "country": ctry.group(0).strip() if ctry else None, "category": (cat.group(0).strip() if cat else
                                                                                      s.get("category")),
                     "founded": fnd.group(0) if fnd else None, "mrr": last[1], "mrr_date": last[0],
                     "mrr_90d_ago": m90, "for_sale": s.get("onSale"), "md_status": r.status,
                     "url": f"https://trustmrr.com/startup/{slug}"})
    for name, x in lb.items():
        d = pdb.normalize_domain(x.get("url"))
        if d and d != "trustmrr.com" and not any(r["domain"] == d for r in recs):
            recs.append({"slug": None, "name": name, "website": x.get("url"), "domain": d, "country": x.get("country"),
                         "category": x.get("category"), "founded": None, "mrr": x.get("mrr"), "mrr_date": x["_date"],
                         "mrr_90d_ago": (x.get("history") or [None])[0], "for_sale": None, "md_status": None,
                         "url": x.get("url"), "desc": x.get("description")})
    dump(raw / "trustmrr.jsonl.gz", recs)
    dump(CACHE / "trustmrr.jsonl", recs)
    print(json.dumps({"h7_records": len(recs), "with_domain": sum(1 for r in recs if r["domain"])}))


# ------------------------------------------------------------------ H8
def h8() -> None:
    raw = pdb.RAW_DIR / "H8_oss_traction"
    conn = pdb.connect()
    orgs = {}
    for r in conn.execute("SELECT company_domain, github_orgs FROM site_scan WHERE github_orgs != '[]'"):
        for o in json.loads(r["github_orgs"])[:2]:
            orgs.setdefault(o, set()).add(r["company_domain"])
    for r in json.load(gzip.open(pdb.RAW_DIR / "H0_icp_universe" / "yc_all.json.gz", "rt")):
        m = re.search(r"github\.com/([A-Za-z0-9-]+)", r.get("github_url") or "")
        d = pdb.normalize_domain(r.get("website"))
        if m and d:
            orgs.setdefault(m.group(1).lower(), set()).add(d)
    res = hf.fetch_many([f"https://github.com/{o}" for o in orgs], workers=6, timeout=20, retries=1)
    recs = []
    for o, doms in orgs.items():
        r = res[f"https://github.com/{o}"]
        t = r.text if r.status == 200 else ""
        stars = []
        for m in re.finditer(r'href="/' + re.escape(o) + r'/([A-Za-z0-9_.-]+)/stargazers"[^>]*>\s*(?:<svg.*?</svg>)?\s*'
                             r'([0-9.,]+k?)', t, re.I | re.S):
            v = m.group(2).lower().replace(",", "")
            n = float(v[:-1]) * 1000 if v.endswith("k") else float(v or 0)
            stars.append((m.group(1), int(n)))
        if not stars:  # user profile pages / pinned repos
            for m in re.finditer(r'/' + re.escape(o) + r'/([A-Za-z0-9_.-]+)/stargazers.{0,400}?([0-9.,]+k?)\s*<', t,
                                 re.I | re.S):
                v = m.group(2).lower().replace(",", "")
                try:
                    stars.append((m.group(1), int(float(v[:-1]) * 1000 if v.endswith("k") else float(v))))
                except ValueError:
                    pass
        stars = sorted(dict(stars).items(), key=lambda x: -x[1])
        recs.append({"org": o, "domains": sorted(doms), "status": r.status, "repos_seen": len(stars),
                     "top_repo": stars[0][0] if stars else None, "top_stars": stars[0][1] if stars else 0,
                     "stars_total_seen": sum(s for _, s in stars), "repos": stars[:5]})
    trending = []
    if EXT:
        for f in sorted(glob.glob(str(EXT / "brianboler_startupdashboard" / "data" / "history" / "*.json"))):
            dd = json.load(open(f))
            for x in (dd.get("sections") or {}).get("trendingRepos") or []:
                trending.append({"repo": x.get("title"), "date": dd.get("date"), "stars": x.get("points")})
        for f in sorted(glob.glob(str(EXT / "linmoxi33_github-trending-dataset" / "data" / "20*.json"))):
            try:
                dd = json.load(open(f))
            except ValueError:
                continue
            items = dd if isinstance(dd, list) else dd.get("repos") or dd.get("items") or []
            for x in items:
                if isinstance(x, dict):
                    nm = x.get("full_name") or x.get("repo") or x.get("name")
                    trending.append({"repo": nm, "date": Path(f).stem, "stars": x.get("stars")})
    dump(raw / "github_orgs.jsonl.gz", recs)
    dump(raw / "trending.jsonl.gz", trending)
    dump(CACHE / "github_orgs.jsonl", recs)
    dump(CACHE / "trending.jsonl", trending)
    print(json.dumps({"h8_orgs": len(recs), "orgs_with_stars": sum(1 for r in recs if r["top_stars"]),
                      "trending_items": len(trending)}))


if __name__ == "__main__":
    w = sys.argv[1] if len(sys.argv) > 1 else "all"
    if w in ("h4", "all"):
        h4()
    if w in ("h7", "all"):
        h7()
    if w in ("h8", "all"):
        h8()
