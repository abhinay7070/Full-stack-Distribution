"""HN 'Ask HN: Who is hiring?' May 2025 -> today: every top-level post -> company, domain, roles, stage, size.

Usage: python3 scripts/hn_hiring.py
Raw: raw_sources/H1_job_arbitrage/hn_whoishiring/<thread_id>.json.gz (full Algolia item tree, unfiltered)
Out: .cache/hn_hiring_posts.jsonl (parsed posts, consumed by universe_ingest.py)
     sublists/sublist_hn_hiring_posts.csv
Also harvests ATS board slugs (Ashby / Greenhouse / Lever / Workable / YC) for ats_sweep.py.
"""
from __future__ import annotations

import csv
import gzip
import html
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import http_fetch as hf  # noqa: E402
import icp_rules as icp  # noqa: E402
import pipeline_db as pdb  # noqa: E402

RAW = pdb.RAW_DIR / "H1_job_arbitrage" / "hn_whoishiring"
OUT = pdb.ROOT / ".cache" / "hn_hiring_posts.jsonl"
CSV_OUT = pdb.SUBLIST_DIR / "sublist_hn_hiring_posts.csv"
START = "2025-05-01"
ATS = {
    "ashby": re.compile(r"jobs\.ashbyhq\.com/([A-Za-z0-9._-]+)", re.I),
    "greenhouse": re.compile(r"(?:boards|job-boards)\.(?:eu\.)?greenhouse\.io/([A-Za-z0-9_-]+)", re.I),
    "lever": re.compile(r"jobs\.(?:eu\.)?lever\.co/([A-Za-z0-9._-]+)", re.I),
    "workable": re.compile(r"apply\.workable\.com/([A-Za-z0-9_-]+)", re.I),
    "yc": re.compile(r"ycombinator\.com/companies/([A-Za-z0-9_-]+)", re.I),
}
SKIP_HOSTS = {"ashbyhq.com", "greenhouse.io", "lever.co", "workable.com", "ycombinator.com", "linkedin.com",
              "github.com", "google.com", "notion.site", "notion.so", "docs.google.com", "forms.gle", "calendly.com",
              "wellfound.com", "angel.co", "workatastartup.com", "bamboohr.com", "rippling.com", "breezy.hr",
              "jobvite.com", "smartrecruiters.com", "recruitee.com", "teamtailor.com", "personio.de", "personio.com",
              "gem.com", "dover.com", "dover.io", "typeform.com", "airtable.com", "youtube.com", "x.com",
              "twitter.com", "medium.com", "substack.com", "techcrunch.com", "bloomberg.com", "forbes.com",
              "news.ycombinator.com", "hnhiring.com", "loom.com", "tally.so", "jobs.gem.com", "paylocity.com",
              "icims.com", "myworkdayjobs.com", "ziprecruiter.com", "indeed.com", "glassdoor.com", "welcometothejungle.com",
              "otta.com", "builtin.com", "remoteok.com", "weworkremotely.com", "keybase.io", "pinpointhq.com",
              "applytojob.com", "jazzhr.com", "zohorecruit.com", "join.com", "homerun.co", "trakstar.com"}
ROLEISH = re.compile(r"\b(engineer\w*|developer|manager|sales|marketing|growth|designer|lead|head of|founding|"
                     r"account (executive|manager)|sdr|bdr|gtm|success|operations|ops|scientist|analyst|"
                     r"architect|product|recruiter|director|vp|chief|writer|advocate|specialist|associate|"
                     r"partnerships?|representative|intern)\b", re.I)
SIZE = re.compile(r"\b(?:team of|we are|we're|currently|a|small)\s*(?:a\s*)?(\d{1,3})\s*(?:-|to)?\s*(?:\d{1,3})?\s*"
                  r"(?:people|person|employees|engineers|folks|of us|ftes?)\b", re.I)
SIZE2 = re.compile(r"\b(\d{1,3})\s*(?:people|person|employees|ftes?)\b", re.I)


def clean(t: str) -> str:
    t = re.sub(r"<p>", "\n", t or "")
    t = re.sub(r"<[^>]+>", " ", t)
    return html.unescape(re.sub(r"[ \t]+", " ", t)).strip()


def threads() -> list[dict]:
    r = hf.fetch("https://hn.algolia.com/api/v1/search_by_date?tags=story,author_whoishiring&hitsPerPage=100",
                 max_age=3600 * 6)
    return [{"id": h["objectID"], "title": h["title"], "date": h["created_at"][:10]} for h in r.json()["hits"]
            if "Who is hiring" in h["title"] and h["created_at"][:10] >= START]


def parse_post(c: dict, th: dict) -> dict | None:
    raw = c.get("text") or ""
    if not raw or c.get("author") in (None, "") or len(raw) < 40:
        return None
    text = clean(raw)
    first = text.split("\n", 1)[0][:400]
    segs = [s.strip() for s in re.split(r"\s+[|•·–—]\s+|\s\|\s|\|", first) if s.strip()]
    company = re.sub(r"\(.*?\)", "", segs[0]).strip(" :-") if segs else ""
    company = re.sub(r"\s+is hiring.*$|\s+-\s+.*$", "", company, flags=re.I).strip()
    hrefs = [html.unescape(h) for h in re.findall(r'href="([^"]+)"', raw)]
    urls = hrefs + re.findall(r"https?://[^\s<>\"')]+", text)
    slugs = {k: sorted({m.group(1).lower() for u in urls for m in [p.search(u)] if m}) for k, p in ATS.items()}
    doms = []
    for u in urls:
        d = pdb.normalize_domain(u)
        if d and d not in SKIP_HOSTS and not any(d.endswith("." + s) for s in SKIP_HOSTS):
            doms.append(d)
    m = re.search(r"\b([a-z0-9-]+\.(?:com|ai|io|co|dev|app|so|tech|xyz|health|inc|us|eu|uk|de|fr))\b",
                  first.lower())
    if m:
        d = pdb.normalize_domain(m.group(1))
        if d and d not in SKIP_HOSTS:
            doms.insert(0, d)
    doms = list(dict.fromkeys(doms))
    roles = [s for s in segs[1:] if ROLEISH.search(s) and len(s) < 160]
    loc_txt = " ; ".join(s for s in segs[1:] if not ROLEISH.search(s))
    country = icp.country_of(loc_txt) or icp.country_of(first)
    if not country and re.search(r"\b(remote \(us|us[- ]only|usa|united states|sf\b|nyc\b|bay area|"
                                 r"new york|san francisco|boston|seattle|austin|los angeles)", first, re.I):
        country = "US"
    if not country and re.search(r"\b(london|uk\b|united kingdom)", first, re.I):
        country = "GB"
    if not country and re.search(r"\b(berlin|munich|germany)\b", first, re.I):
        country = "DE"
    if not country and re.search(r"\b(paris|france)\b", first, re.I):
        country = "FR"
    if not country and re.search(r"\b(amsterdam|netherlands)\b", first, re.I):
        country = "NL"
    size = None
    ms = SIZE.search(text) or SIZE2.search(text)
    if ms:
        size = int(ms.group(1))
    return {"id": c["id"], "thread": th["id"], "thread_month": th["title"].split("(")[-1].rstrip(")"),
            "posted_at": (c.get("created_at") or "")[:10], "author": c.get("author"), "company": company[:120],
            "first_line": first, "roles": roles[:8], "domains": doms[:5], "ats_slugs": slugs, "country": country,
            "stage": icp.stage_from_text(text), "yc": bool(re.search(r"\(YC [WSFX]\d{2}\)|\bYC [WSFX]\d{2}\b|"
                                                                     r"y combinator", text, re.I)),
            "team_size": size, "text": text[:4000], "url": f"https://news.ycombinator.com/item?id={c['id']}"}


def main() -> None:
    RAW.mkdir(parents=True, exist_ok=True)
    posts = []
    ths = threads()
    for th in ths:
        r = hf.fetch(f"https://hn.algolia.com/api/v1/items/{th['id']}", max_age=None if th["date"] < "2026-10-15"
                     else 3600 * 12, max_bytes=40_000_000, timeout=60)
        if r.status != 200:
            print("thread error", th, r.status, file=sys.stderr)
            continue
        with gzip.open(RAW / f"{th['id']}.json.gz", "wt") as f:
            f.write(r.text)
        item = r.json()
        n = 0
        for c in item.get("children") or []:
            p = parse_post(c, th)
            if p:
                posts.append(p)
                n += 1
        print(f"{th['title']}: {n} posts", file=sys.stderr)
    with open(OUT, "w") as f:
        for p in posts:
            f.write(json.dumps(p) + "\n")
    cols = ["posted_at", "company", "domains", "country", "stage", "yc", "team_size", "roles", "ats_slugs", "url",
            "first_line"]
    with open(CSV_OUT, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for p in posts:
            w.writerow({**p, "domains": " ".join(p["domains"]), "roles": " | ".join(p["roles"]),
                        "ats_slugs": json.dumps({k: v for k, v in p["ats_slugs"].items() if v})})
    print(json.dumps({"threads": len(ths), "posts": len(posts),
                      "with_domain": sum(1 for p in posts if p["domains"]),
                      "with_ats_slug": sum(1 for p in posts if any(p["ats_slugs"].values())),
                      "unique_companies": len({p['company'].lower() for p in posts})}))


if __name__ == "__main__":
    main()
