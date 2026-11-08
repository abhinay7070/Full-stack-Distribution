"""Collect the full text of every GTM-family job posting at an ICP company (role-scope research, step 1).

Input : db/gtm_pipeline.db `jobs` (GTM families) + master/open_roles.csv (YC WaaS rows, not in `jobs`).
Fetch : full descriptions with list structure kept (bullets / section headers), from
        Ashby  posting-api (descriptionHtml) · Greenhouse boards-api (content=true) · Lever postings (lists[]) ·
        YC WaaS public job page (data-page JSON). Teamtailor / Recruitee / Breezy / closed postings fall back to the
        6,000-char raw payload in raw_sources/H1_job_arbitrage/ats_live; HN posts use the full raw comment.
Output: research/gtm_role_scope/corpus/postings.jsonl.gz  (one record per posting, full text + provenance)
        research/gtm_role_scope/corpus/postings_index.csv (same rows, no text)
No posting is dropped silently: `text_source` says where the text came from, `text_status` flags empty text.
"""
from __future__ import annotations

import csv
import gzip
import html
import json
import re
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import http_fetch as hf  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "raw_sources" / "H1_job_arbitrage"
OUT = ROOT / "research" / "gtm_role_scope" / "corpus"
GTM_FAMILIES = ("exact_role", "hidden_gtm", "growth", "gtm_generalist", "sales", "marketing", "revops", "partnerships",
                "presales", "devrel", "customer", "bizops_generalist")
hf.RATE.setdefault("www.ycombinator.com", 1.0)

BLOCK = re.compile(r"</?(p|div|br|h[1-6]|ul|ol|tr|section|article|header|footer)[^>]*>", re.I)
LI = re.compile(r"<li[^>]*>", re.I)
HEAD = re.compile(r"<h[1-6][^>]*>(.*?)</h[1-6]>|<(?:strong|b)>([^<]{3,80})</(?:strong|b)>", re.I | re.S)
TAG = re.compile(r"<[^>]+>")


def html_to_text(h: str | None) -> str:
    """HTML -> plain text that keeps structure: headings as '## ', list items as '- ', blocks as new lines."""
    if not h:
        return ""
    h = html.unescape(h)
    h = HEAD.sub(lambda m: f"\n## {(m.group(1) or m.group(2) or '').strip()}\n", h)
    h = LI.sub("\n- ", h)
    h = BLOCK.sub("\n", h)
    t = html.unescape(TAG.sub(" ", h))
    lines = [re.sub(r"[ \t ]+", " ", ln).strip() for ln in t.splitlines()]
    out, blank = [], False
    for ln in lines:
        if ln in ("", "-", "## "):
            blank = True
            continue
        if blank and out:
            out.append("")
        out.append(ln)
        blank = False
    return "\n".join(out).strip()


def load_targets(conn) -> list[dict]:
    q = f"""select j.job_key, j.company_domain, c.company_name, j.source, j.title, j.url, j.location, j.department,
                   j.comp, j.posted_at, j.family, j.strength, s.icp_tier, c.headcount, c.headcount_band, c.funding_stage,
                   c.hq_country, c.sector, j.description_snip
            from jobs j join companies c using(company_domain) left join scores s using(company_domain)
            where j.family in ({",".join("?" * len(GTM_FAMILIES))})"""
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute(q, GTM_FAMILIES)]
    seen = {r["url"] for r in rows}
    meta = {r["company_domain"]: dict(r) for r in conn.execute(
        "select c.company_domain, c.company_name, s.icp_tier, c.headcount, c.headcount_band, c.funding_stage, "
        "c.hq_country, c.sector from companies c left join scores s using(company_domain)")}
    with open(ROOT / "master" / "open_roles.csv", newline="") as fh:
        for r in csv.DictReader(fh):
            if r["source"] != "yc_waas" or r["url"] in seen or r["family"] not in GTM_FAMILIES:
                continue
            m = meta.get(r["company_domain"], {})
            rows.append({"job_key": "yc_waas:" + r["url"].rsplit("/", 1)[-1], "company_domain": r["company_domain"],
                         "company_name": r["company_name"], "source": "yc_waas", "title": r["title"], "url": r["url"],
                         "location": r["location"], "department": None, "comp": r["salary_range"],
                         "equity": r["equity_range"], "experience": r["experience"], "posted_at": r["posted_at"] or None,
                         "family": r["family"], "strength": r["strength"], "icp_tier": m.get("icp_tier"),
                         "headcount": m.get("headcount"), "headcount_band": m.get("headcount_band"),
                         "funding_stage": m.get("funding_stage"), "hq_country": m.get("hq_country"),
                         "sector": m.get("sector"), "description_snip": ""})
            seen.add(r["url"])
    return rows


def raw_fallback(providers: set[str], ids: set[str]) -> dict[str, str]:
    """job id -> 6,000-char text from the raw sweep payloads."""
    out = {}
    for p in providers:
        f = RAW / "ats_live" / f"{p}.jsonl.gz"
        if not f.exists():
            continue
        with gzip.open(f, "rt") as fh:
            for line in fh:
                d = json.loads(line)
                for j in d.get("jobs") or []:
                    k = f"{p}:{d['slug']}:{j.get('id')}"
                    if k in ids and j.get("description"):
                        out[k] = j["description"]
    return out


def hn_texts() -> dict[str, str]:
    out = {}
    for f in (RAW / "hn_whoishiring").glob("*.json.gz"):
        d = json.load(gzip.open(f, "rt"))
        for ch in d.get("children") or []:
            if ch.get("text"):
                out[str(ch["id"])] = html_to_text(ch["text"].replace("<p>", "\n\n"))
    return out


def board_of(r: dict) -> tuple[str, str] | None:
    k = r["job_key"].split(":")
    if r["source"] in ("ats:ashby", "ats:greenhouse", "ats:lever") and len(k) >= 3:
        return k[0], k[1]
    return None


def fetch_boards(boards: set[tuple[str, str]]) -> dict[str, tuple[str, str]]:
    """Fetch each board once; return job_key -> (text, posted_at_live)."""
    url = {"ashby": "https://api.ashbyhq.com/posting-api/job-board/{s}?includeCompensation=true",
           "greenhouse": "https://boards-api.greenhouse.io/v1/boards/{s}/jobs?content=true",
           "lever": "https://api.lever.co/v0/postings/{s}?mode=json"}
    urls = {url[p].format(s=s): (p, s) for p, s in boards}
    res = hf.fetch_many(list(urls), workers=8, max_age=7 * 86400)
    out = {}
    for u, r in res.items():
        p, s = urls[u]
        if r.status != 200:
            continue
        try:
            d = r.json()
        except ValueError:
            continue
        if p == "ashby":
            for j in d.get("jobs") or []:
                out[f"ashby:{s}:{j.get('id')}"] = html_to_text(j.get("descriptionHtml")) or (j.get("descriptionPlain") or "")
        elif p == "greenhouse":
            for j in d.get("jobs") or []:
                out[f"greenhouse:{s}:{j.get('id')}"] = html_to_text(j.get("content"))
        elif p == "lever" and isinstance(d, list):
            for j in d:
                parts = [html_to_text(j.get("description")) or (j.get("descriptionPlain") or "")]
                for L in j.get("lists") or []:
                    parts.append(f"## {html_to_text(L.get('text'))}\n" + html_to_text(f"<ul>{L.get('content') or ''}</ul>"))
                parts.append(html_to_text(j.get("additional")) or (j.get("additionalPlain") or ""))
                out[f"lever:{s}:{j.get('id')}"] = "\n\n".join(x for x in parts if x)
    return out


YC_DESC = re.compile(r'data-page="([^"]+)"')


def fetch_yc(urls: list[str]) -> dict[str, str]:
    res = hf.fetch_many(urls, workers=2, max_age=7 * 86400)
    out = {}
    for u, r in res.items():
        m = YC_DESC.search(r.text or "") if r.status == 200 else None
        if not m:
            continue
        try:
            d = json.loads(html.unescape(m.group(1)))
        except ValueError:
            continue
        job = (d.get("props") or {}).get("job") or {}
        txt = job.get("description") or job.get("descriptionHtml") or ""
        out[u] = html_to_text(txt) if "<" in txt else txt
    return out


def main() -> None:
    conn = sqlite3.connect(ROOT / "db" / "gtm_pipeline.db", timeout=900)
    rows = load_targets(conn)
    print(f"targets: {len(rows)} postings", flush=True)
    boards = {b for r in rows if (b := board_of(r))}
    print(f"live ATS boards to fetch: {len(boards)}", flush=True)
    live = fetch_boards(boards)
    yc_urls = [r["url"] for r in rows if r["source"] == "yc_waas"]
    yc = fetch_yc(yc_urls)
    keys = {r["job_key"] for r in rows}
    fb = raw_fallback({"ashby", "greenhouse", "lever", "teamtailor", "recruitee", "breezy"}, keys)
    hn = hn_texts()
    OUT.mkdir(parents=True, exist_ok=True)
    stats = defaultdict(int)
    idx_cols = ["job_key", "company_domain", "company_name", "source", "title", "family", "strength", "icp_tier",
                "headcount", "headcount_band", "funding_stage", "hq_country", "sector", "location", "comp", "posted_at",
                "url", "text_source", "text_status", "text_chars"]
    with gzip.open(OUT / "postings.jsonl.gz", "wt") as gz, open(OUT / "postings_index.csv", "w", newline="") as ix:
        w = csv.DictWriter(ix, fieldnames=idx_cols, extrasaction="ignore")
        w.writeheader()
        for r in sorted(rows, key=lambda x: (x["company_domain"], x["job_key"])):
            k = r["job_key"]
            if k in live and live[k]:
                text, src = live[k], "live_ats_full"
            elif r["source"] == "yc_waas" and yc.get(r["url"]):
                text, src = yc[r["url"]], "live_yc_waas"
            elif r["source"] == "hn_whoishiring" and hn.get(k.split(":")[1]):
                text, src = hn[k.split(":")[1]], "raw_hn_full"
            elif k in fb:
                text, src = fb[k], "raw_ats_6000cap"
            else:
                text, src = r.get("description_snip") or "", "db_snip_1500cap"
            r.pop("description_snip", None)
            r["text"], r["text_source"], r["text_chars"] = text, src, len(text)
            r["text_status"] = "ok" if len(text) >= 200 else ("short" if text else "empty")
            stats[src] += 1
            stats["status_" + r["text_status"]] += 1
            gz.write(json.dumps(r, ensure_ascii=False) + "\n")
            w.writerow(r)
    rep = {"postings": len(rows), "boards_fetched": len(boards), "by_text_source": dict(stats)}
    (OUT / "collect_report.json").write_text(json.dumps(rep, indent=2))
    print(json.dumps(rep, indent=2))


if __name__ == "__main__":
    main()
