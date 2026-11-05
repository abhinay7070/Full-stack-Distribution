"""H0 - ICP universe: every company a firmographic source places inside the ICP, signal or not.

Usage: python3 scripts/h0_universe.py
Sources (raw snapshots under raw_sources/H0_icp_universe/)
  yc_all.json.gz            YC directory, all batches (devasheeshG/yc-api, refreshed)          batch year >= 2021
  getro/<network>.json.gz   37 VC talent networks on Getro (live public API the boards call)    stage <= A, <= 50 ppl
  accelerators.json.gz      Techstars / 500 Global / Alchemist / EF (yigitmeteozcan/startups)  cohort year >= 2023
  startuppy.json.gz         a16z Speedrun SR001-SR007, Antler, EF, Techstars (JohnMarcBautista/startuppy)
Every record goes through icp_rules.check(); rejects -> sublists/sublist_H0_rejected.csv (source, name, reason).
Tag H0 has weight 0 (membership, not a signal). Existing non-null company fields are never overwritten.
"""
from __future__ import annotations

import csv
import gzip
import json
import re
import shutil
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
import icp_rules as icp  # noqa: E402
import pipeline_db as pdb  # noqa: E402

RAW = pdb.RAW_DIR / "H0_icp_universe"
EXT = Path(sys.argv[1]) if len(sys.argv) > 1 else None
OUT = pdb.SUBLIST_DIR / "sublist_H0_icp_universe.csv"
REJ = pdb.SUBLIST_DIR / "sublist_H0_rejected.csv"
BOARDS = pdb.ROOT / ".cache" / "vc_boards.json"
YC_SEASON = {"winter": "W", "spring": "X", "summer": "S", "fall": "F"}


def gz_json(p: Path):
    with gzip.open(p, "rt") as f:
        return json.load(f)


def snapshot(src: Path, name: str) -> Path:
    dst = RAW / name
    if src and src.exists():
        RAW.mkdir(parents=True, exist_ok=True)
        with open(src, "rb") as fi, gzip.open(dst, "wb", compresslevel=9) as fo:
            shutil.copyfileobj(fi, fo)
    return dst


# ------------------------------------------------------------------ adapters -> common record
def yc_records() -> list[dict]:
    out = []
    for r in gz_json(RAW / "yc_all.json.gz"):
        b = (r.get("batch") or "").lower().split()
        yr = int(b[-1]) if b and b[-1].isdigit() else None
        out.append({"source": "yc", "program": f"YC {YC_SEASON.get(b[0], '?')}{str(yr)[2:]}" if yr else "YC",
                    "name": r.get("name"), "website": r.get("website"), "year": yr,
                    "country": r.get("country") or icp.country_of(r.get("all_locations")),
                    "headcount": int(r["team_size"]) if str(r.get("team_size") or "").isdigit() else None,
                    "stage": "seed" if (r.get("stage") or "") == "Early" else ("growth" if r.get("stage") == "Growth"
                                                                              else None),
                    "status": r.get("status"), "industry": r.get("industry") or "", "sub": r.get("subindustry") or "",
                    "tags": r.get("tags") or [], "text": f"{r.get('one_liner') or ''} {r.get('long_description') or ''}",
                    "one_liner": r.get("one_liner"), "linkedin": r.get("linkedin_url"), "url": r.get("url"),
                    "founders": [{"name": f["full_name"], "role": f.get("title") or "", "linkedin": f.get("linkedin_url"),
                                  "x": f.get("x_url")} for f in r.get("founders") or []
                                 if str(f.get("is_active", True)) != "False"],
                    "min_year": 2021})
    return out


def getro_records() -> list[dict]:
    boards = json.loads(BOARDS.read_text()) if BOARDS.exists() else {}
    (RAW / "getro").mkdir(parents=True, exist_ok=True)
    s = requests.Session()
    out = []
    for url, (nid, name) in sorted(boards.items()):
        if nid == "consider":
            continue
        p = RAW / "getro" / f"{nid}.json.gz"
        if not p.exists() or time.time() - p.stat().st_mtime > 86400 * 3:
            comps, page = [], 0
            while True:
                try:
                    r = s.post(f"https://api.getro.com/api/v2/collections/{nid}/search/companies",
                               json={"hitsPerPage": 100, "page": page, "filters": {}}, timeout=40,
                               headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json",
                                        "Origin": url, "Referer": url + "/"})
                    res = r.json()["results"]
                except Exception as e:  # noqa: BLE001
                    print("getro error", name, page, e, file=sys.stderr)
                    break
                batch = res.get("companies") or []
                comps += batch
                page += 1
                if not batch or len(comps) >= res.get("count", 0) or page > 200:
                    break
                time.sleep(0.25)
            with gzip.open(p, "wt") as f:
                json.dump({"network_id": nid, "name": name, "board": url, "count": len(comps), "companies": comps}, f)
        d = gz_json(p)
        for c in d["companies"]:
            hc = c.get("head_count")
            out.append({"source": f"getro:{d['name']}", "program": d["name"], "name": c.get("name"),
                        "website": c.get("domain"), "year": None,
                        "country": icp.country_of(*(c.get("locations") or [])) if c.get("locations") else None,
                        "band": icp.GETRO_BAND.get(hc) if isinstance(hc, int) else None,
                        "stage": c.get("stage"), "industry": "", "sub": "", "tags": c.get("industry_tags") or [],
                        "text": c.get("description") or "", "one_liner": (c.get("description") or "")[:200],
                        "url": d["board"], "active_jobs": c.get("active_jobs_count") or 0, "founders": [],
                        "min_year": 0})
    return out


def accel_records() -> list[dict]:
    out = []
    for r in gz_json(RAW / "accelerators.json.gz"):
        if r["source"] not in ("techstars", "500", "alchemist", "ef"):
            continue
        yr = r.get("year") if isinstance(r.get("year"), int) else None
        if r.get("isExit") or r.get("isUnicorn"):
            st = "acquired"
        else:
            st = None
        out.append({"source": r["source"], "program": r.get("program") or r["source"], "name": r.get("name"),
                    "website": r.get("website"), "year": yr,
                    "country": icp.country_of(r.get("location"), (r.get("extra") or {}).get("country")),
                    "stage": "acquired" if st else "seed", "industry": "", "sub": "", "tags": r.get("tags") or [],
                    "text": r.get("description") or "", "one_liner": (r.get("description") or "")[:200],
                    "linkedin": (r.get("social") or {}).get("linkedin"), "url": None,
                    "founders": [{"name": f["name"], "role": f.get("role") or "", "linkedin": f.get("linkedin") or None}
                                 for f in r.get("founders") or [] if f.get("name")],
                    "min_year": 2023})
    return out


def startuppy_records() -> list[dict]:
    out = []
    for r in gz_json(RAW / "startuppy.json.gz")["companies"]:
        if r.get("source") not in ("a16z-speedrun", "antler"):
            continue
        yr = int(r["year"]) if str(r.get("year") or "").isdigit() else None
        tags = r.get("tags")
        if isinstance(tags, str):
            tags = re.findall(r"'([^']+)'", tags)
        out.append({"source": r["source"], "program": f"{r.get('source_label') or r['source']} {r.get('batch') or ''}".strip(),
                    "name": r.get("name"), "website": r.get("website"), "year": yr,
                    "country": icp.country_of(r.get("locations")),
                    "headcount": int(r["team_size"]) if str(r.get("team_size") or "").isdigit() else None,
                    "stage": "seed", "status": r.get("status"), "industry": r.get("industry") or "",
                    "sub": r.get("subindustry") or "", "tags": tags or [],
                    "text": f"{r.get('one_liner') or ''} {r.get('description') or ''}", "one_liner": r.get("one_liner"),
                    "url": r.get("yc_url"), "founders": [], "min_year": 2023 if r["source"] == "antler" else 2022})
    return out


# ------------------------------------------------------------------ main
def fill_missing(conn, d: str, **fields) -> None:
    row = conn.execute("SELECT * FROM companies WHERE company_domain=?", (d,)).fetchone()
    sets = {k: v for k, v in fields.items() if v not in (None, "") and row[k] in (None, "")}
    if sets:
        conn.execute(f"UPDATE companies SET {', '.join(k + '=?' for k in sets)} WHERE company_domain=?",
                     (*sets.values(), d))


def add_source(conn, d: str, src: str) -> None:
    row = conn.execute("SELECT sources FROM companies WHERE company_domain=?", (d,)).fetchone()
    cur = json.loads(row["sources"]) if row and row["sources"] else []
    if src not in cur:
        cur.append(src)
        conn.execute("UPDATE companies SET sources=? WHERE company_domain=?", (json.dumps(sorted(cur)), d))


def main() -> None:
    if EXT:
        snapshot(EXT / "ycapi" / "companies" / "all.json", "yc_all.json.gz")
        snapshot(EXT / "yigitmeteozcan_startups" / "data" / "all.json", "accelerators.json.gz")
        snapshot(EXT / "JohnMarcBautista_startuppy" / "data.json", "startuppy.json.gz")
    recs = yc_records() + getro_records() + accel_records() + startuppy_records()
    conn = pdb.init_db()
    run = pdb.start_run(conn, "H0", "yc+getro+accelerators+startuppy", "icp_rules.check per record",
                        "raw_sources/H0_icp_universe")
    rej, kept = [], defaultdict(list)
    stats = Counter()
    for r in recs:
        d = pdb.normalize_domain(r.get("website"))
        if not d:
            stats["no_domain"] += 1
            rej.append({"source": r["source"], "name": r["name"], "domain": "", "reason": "no_domain"})
            continue
        keep, why, flags, label = icp.check(country=r.get("country"), headcount=r.get("headcount"), band=r.get("band"),
                                            stage=r.get("stage"), industry=r.get("industry", ""), sub=r.get("sub", ""),
                                            tags=r.get("tags"), text=r.get("text", ""), year=r.get("year"),
                                            min_year=r.get("min_year", 2021), status=r.get("status"))
        if not keep:
            stats[f"reject:{why.split(':')[0]}"] += 1
            rej.append({"source": r["source"], "name": r["name"], "domain": d, "reason": why})
            continue
        stats["kept_records"] += 1
        kept[d].append((r, flags, label))
    for d, items in kept.items():
        r0, flags, label = items[0]
        progs = sorted({x[0]["program"] for x in items})
        pdb.upsert_company(conn, d, "H0", proof={"source": r0["source"], "url": r0.get("url") or f"https://{d}",
                                                  "snippet": f"[universe|{'/'.join(progs)[:120]}] "
                                                             f"{'; '.join(sorted({f for x in items for f in x[1]}))}"})
        fill_missing(conn, d, company_name=r0["name"], one_liner=(r0.get("one_liner") or "")[:300] or None,
                     hq_country=next((x[0]["country"] for x in items if x[0].get("country")), None),
                     sector=label or None, batch_or_investor=", ".join(progs)[:200],
                     headcount=next((x[0]["headcount"] for x in items if x[0].get("headcount") is not None), None),
                     headcount_band=next((x[0]["band"] for x in items if x[0].get("band")), None),
                     founded_year=str(next((x[0]["year"] for x in items if x[0].get("year")), "") or "") or None,
                     linkedin_url=next((x[0]["linkedin"] for x in items if x[0].get("linkedin")), None))
        for x in items:
            add_source(conn, d, x[0]["source"].split(":")[0] if x[0]["source"].startswith("getro") else x[0]["source"])
            if x[0]["source"].startswith("getro"):
                add_source(conn, d, x[0]["source"])
            for f in x[0].get("founders") or []:
                pdb.upsert_founder(conn, d, f["name"], role=(f.get("role") or None),
                                   linkedin_url=f.get("linkedin") or None,
                                   x_handle=(f.get("x") or "").rstrip("/").split("/")[-1] or None,
                                   source_url=x[0].get("url"))
    conn.commit()
    pdb.finish_run(conn, run, records_raw=len(recs), records_unique=len(kept), records_icp_pass=len(kept),
                   stop_reason="exhausted")
    with open(REJ, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["source", "name", "domain", "reason"])
        w.writeheader()
        w.writerows(rej)
    cols = ["company_domain", "company_name", "sources", "batch_or_investor", "hq_country", "sector", "headcount",
            "headcount_band", "founded_year", "matched_hypotheses", "one_liner"]
    with open(OUT, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for c in conn.execute("SELECT * FROM companies WHERE matched_hypotheses LIKE '%\"H0\"%' ORDER BY company_domain"):
            w.writerow(dict(c))
    rep = {"run_at": pdb.utcnow(), "records": len(recs), "unique_domains_kept": len(kept),
           "by_source_records": dict(Counter(r["source"].split(":")[0] for r in recs)), "stats": dict(stats)}
    (RAW / "h0_run_report.json").write_text(json.dumps(rep, indent=1) + "\n")
    print(json.dumps(rep, indent=1))


if __name__ == "__main__":
    main()
