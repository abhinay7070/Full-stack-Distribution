"""Merge live evidence into the DB: HN 'Who is hiring' posts, funding news rounds, ATS boards.

Usage: python3 scripts/universe_ingest.py hn|news|ats|all
Each source: map to a company domain (published domain -> brand-matched link -> resolve_domains), ICP gate,
then write evidence rows (jobs / funding_events) and upsert the company.

New-company admission rule (maker/checker): a company that is in no other ICP source enters the DB only with
explicit early-stage evidence in its own text (pre-seed / seed / Series A, accelerator, 'founding' role, team <= 35,
'first GTM/sales hire', backed-by-tier-1-seed language). Otherwise it goes to
sublists/sublist_unverified_stage_pool.csv (counted in the TAM report, not in the ICP universe).
Hard rejects: Series B+ / public / >50 people / non-ICP geo / non-ICP sector / .edu .gov .ac.* / institutions.
"""
from __future__ import annotations

import csv
import gzip
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import h1_ingest as h1  # noqa: E402
import icp_rules as icp  # noqa: E402
import pipeline_db as pdb  # noqa: E402
import resolve_domains as rd  # noqa: E402

CACHE = pdb.ROOT / ".cache"
POOL = pdb.SUBLIST_DIR / "sublist_unverified_stage_pool.csv"
REJ = pdb.SUBLIST_DIR / "sublist_universe_ingest_rejected.csv"
TODAY = date.today()
FRESH = (TODAY - timedelta(days=60)).isoformat()
EARLY = re.compile(
    r"\b(pre-?seed|seed[- ]stage|seed round|seed funding|series a\b|early[- ]stage|stealth|"
    r"founding (engineer|team|member|ae|account|gtm|designer|sales|marketing|growth|product|hire|software|ml|ai|"
    r"full[- ]stack|backend|frontend|operations|ops|business|bd|solutions|customer|recruiter|data)|"
    r"first (sales|gtm|marketing|growth|business|commercial|revenue|customer success|ops|operations|non-technical) hire|"
    r"y ?combinator|\(yc [wsfx]\d{2}\)|\byc [wsfx]\d{2}\b|a16z speedrun|techstars|entrepreneur first|antler|"
    r"backed by .{0,60}(a16z|andreessen|sequoia|accel|index ventures|lightspeed|greylock|benchmark|founders fund|"
    r"general catalyst|khosla|kleiner|nfx|first round|felicis|y ?combinator|seedcamp|localglobe|point nine|"
    r"balderton|atomico|northzone|creandum|cherry|speedinvest|kindred|notion capital|conviction|south park)|"
    r"team of (\d|[12]\d|3[0-5])\b|\b([2-9]|[12]\d|3[0-5])[- ]person team|(\d|[12]\d|3[0-5]) (people|employees) "
    r"(strong|today|currently)|small team of)", re.I)
LATE = re.compile(r"\b(series [b-h]\b|publicly traded|nasdaq|nyse|fortune 500 company|ipo\b|\d{3,}\+? employees|"
                  r"thousands of (employees|people)|unicorn valued|global leader with offices)", re.I)
INSTITUTION = re.compile(r"\b(university|universit[äé]t|college|institute|school of|hospital|government|ministry|"
                         r"council|foundation|federal|national lab|department of|agency|museum|nonprofit|non-profit)\b", re.I)
BAD_TLD = re.compile(r"\.(edu|gov|mil|ac\.[a-z]{2}|gov\.[a-z]{2}|edu\.[a-z]{2}|nhs\.uk)$")


def existing_icp(conn) -> set[str]:
    return {r[0] for r in conn.execute("SELECT company_domain FROM companies WHERE status!='disqualified'")}


def gate(text: str, country: str | None, team: int | None, stage: str | None, domain: str | None, name: str,
         in_universe: bool) -> tuple[str, str, list[str], str]:
    """-> (decision admit|pool|reject, reason, flags, sector)."""
    if domain and BAD_TLD.search(domain):
        return "reject", "institution_tld", [], ""
    if INSTITUTION.search(name or ""):
        return "reject", "institution_name", [], ""
    if stage == "series_b_plus" or LATE.search(text or ""):
        return "reject", "late_stage", [], ""
    keep, why, flags, label = icp.check(country=country, headcount=team, stage=stage, text=text or "",
                                        tags=[], year=None)
    if not keep:
        return "reject", why, flags, ""
    if in_universe:
        return "admit", "", flags, label
    if stage in ("pre_seed", "seed", "series_a") or EARLY.search(text or ""):
        return "admit", "", flags + ["admitted_on_text_evidence"], label
    return "pool", "no_early_stage_evidence", flags, label


def brand_link(name: str, hosts: list[str]) -> str | None:
    _, full, short = rd.brand_cores(name)
    for h in hosts:
        lab = re.sub(r"[^a-z0-9]", "", h.split(".")[0])
        lab2 = re.sub(r"^(get|try|use|join|go|with|hey|meet)", "", lab)
        if len(short) >= 3 and (short in lab or lab2 == short or lab == full or (len(lab2) >= 4 and lab2 in full)):
            return h
    return None


def resolve_all(items: list[dict], prefix: str) -> dict[str, str | None]:
    ents = [{"key": f"{prefix}:{rd.alnum(i['name'])}", "name": i["name"],
             "hints": {"domains": i.get("hint_domains") or [], "city": i.get("city"), "tech": True}}
            for i in items if i.get("name") and len(rd.alnum(i["name"])) >= 2]
    ents = list({e["key"]: e for e in ents}.values())
    res = rd.resolve(ents, workers=32)
    return {k: v.get("domain") for k, v in res.items()}


def write_rows(path: Path, rows: list[dict], append: bool) -> None:
    if not rows:
        return
    mode = "a" if append and path.exists() else "w"
    with open(path, mode, newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()), extrasaction="ignore")
        if mode == "w":
            w.writeheader()
        w.writerows(rows)


# ------------------------------------------------------------------ HN Who is hiring
def ingest_hn(conn) -> dict:
    posts = [json.loads(x) for x in open(CACHE / "hn_hiring_posts.jsonl")]
    uni = existing_icp(conn)
    need = []
    for p in posts:
        p["domain"] = brand_link(p["company"], p["domains"]) or (p["domains"][0] if len(p["domains"]) == 1 and
                                                                 brand_link(p["company"], p["domains"][:1]) else None)
        if not p["domain"] and p["company"]:
            need.append({"name": p["company"], "hint_domains": p["domains"][:3]})
    resolved = resolve_all(need, "hn")
    st, pool, rej = Counter(), [], []
    run = pdb.start_run(conn, "H1", "hn_whoishiring", "Ask HN: Who is hiring? 2025-05..today (Algolia items)",
                        "raw_sources/H1_job_arbitrage/hn_whoishiring")
    by_dom = defaultdict(list)
    for p in posts:
        d = p["domain"] or resolved.get(f"hn:{rd.alnum(p['company'])}")
        if not d:
            st["no_domain"] += 1
            pool.append({"source": "hn_whoishiring", "name": p["company"], "domain": "", "reason": "unresolved_domain",
                         "date": p["posted_at"], "url": p["url"], "evidence": p["first_line"][:200]})
            continue
        by_dom[d].append(p)
    for d, ps in by_dom.items():
        text = " ".join(p["text"] for p in ps)[:20000]
        team = next((p["team_size"] for p in ps if p["team_size"]), None)
        stage = next((p["stage"] for p in ps if p["stage"]), None)
        country = next((p["country"] for p in ps if p["country"]), None)
        dec, why, flags, label = gate(text, country, team, stage, d, ps[0]["company"], d in uni)
        st[dec] += 1
        if dec == "reject":
            rej.append({"source": "hn_whoishiring", "name": ps[0]["company"], "domain": d, "reason": why})
            continue
        if dec == "pool":
            pool.append({"source": "hn_whoishiring", "name": ps[0]["company"], "domain": d, "reason": why,
                         "date": ps[-1]["posted_at"], "url": ps[-1]["url"], "evidence": ps[-1]["first_line"][:200]})
            continue
        pdb.upsert_company(conn, d, "H0", proof={"source": "hn_whoishiring", "url": ps[-1]["url"],
                                                 "snippet": f"[universe|hn_whoishiring] {ps[-1]['first_line'][:160]}"},
                           company_name=None)
        best = None
        for p in ps:
            for role in (p["roles"] or [p["first_line"][:120]]):
                c = h1.classify_job(role, None, label or "")
                key = f"hn:{p['id']}:{rd.alnum(role)[:40]}"
                conn.execute(
                    "INSERT INTO jobs (job_key, company_domain, source, title, url, location, posted_at, first_seen_at, "
                    "last_seen_at, family, strength, text_signals, description_snip) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(job_key) DO UPDATE SET last_seen_at=excluded.last_seen_at",
                    (key, d, "hn_whoishiring", role[:200], p["url"], None, p["posted_at"], p["posted_at"],
                     p["posted_at"], c["family"], c["strength"], None, p["text"][:1500]))
                if c["strength"] and (not best or h1.STRENGTH_ORDER[c["strength"]] < h1.STRENGTH_ORDER[best[0]["strength"]]):
                    best = (c, role, p)
        row = conn.execute("SELECT company_name, hq_country, headcount, sector FROM companies WHERE company_domain=?",
                           (d,)).fetchone()
        sets = {"company_name": ps[0]["company"][:100] if not row["company_name"] else None,
                "hq_country": country if not row["hq_country"] else None,
                "headcount": team if row["headcount"] is None and team and team <= 50 else None,
                "sector": label if not row["sector"] and label else None}
        sets = {k: v for k, v in sets.items() if v}
        if sets:
            conn.execute(f"UPDATE companies SET {', '.join(k + '=?' for k in sets)} WHERE company_domain=?",
                         (*sets.values(), d))
        if best and ps[-1]["posted_at"] >= (TODAY - timedelta(days=120)).isoformat():
            c, role, p = best
            pdb.upsert_company(conn, d, "H1", proof={"source": "hn_whoishiring", "url": p["url"],
                                                     "snippet": f"[H1|{c['strength']}|{p['posted_at']}] {role[:120]} "
                                                                f"({c['family']})"})
    conn.commit()
    pdb.finish_run(conn, run, records_raw=len(posts), records_unique=len(by_dom), records_icp_pass=st["admit"],
                   stop_reason="exhausted")
    write_rows(POOL, pool, append=False)
    write_rows(REJ, rej, append=False)
    return {"posts": len(posts), "domains": len(by_dom), **st}


# ------------------------------------------------------------------ funding news
def ingest_news(conn) -> dict:
    rounds = [json.loads(x) for x in open(CACHE / "news_rounds.jsonl")]
    uni = existing_icp(conn)
    need = [{"name": r["company"], "hint_domains": r["hint_hosts"][:5]} for r in rounds if not r["domain"]]
    resolved = resolve_all(need, "news")
    st, pool, rej = Counter(), [], []
    run = pdb.start_run(conn, "H2", "funding_news_rss", "15 outlets, paged RSS back to 2025-05-01",
                        "raw_sources/H2_fresh_capital/news_rss")
    for r in rounds:
        d = r["domain"] or resolved.get(f"news:{rd.alnum(r['company'])}")
        conf = "news_linked" if r["domain"] else "news_resolved"
        if not d:
            st["no_domain"] += 1
            pool.append({"source": f"news:{r['feed']}", "name": r["company"], "domain": "", "reason": "unresolved_domain",
                         "date": r["date"], "url": r["url"], "evidence": r["title"][:200]})
            continue
        amt = r["amount_usd"]
        stage = r["stage"] or (None if amt is None else "seed" if amt < 8e6 else "series_a" if amt <= 40e6 else
                               "series_b_plus")
        country = r["country"] or (r["country_hint"] if not r["country"] else None)
        dec, why, flags, label = gate(f"{r['title']} {r['lead']}", country, None, stage, d, r["company"], True)
        if dec == "reject" or (amt and amt > 40e6 and stage != "series_a"):
            st["reject"] += 1
            rej.append({"source": f"news:{r['feed']}", "name": r["company"], "domain": d,
                        "reason": why or "amount_over_40m"})
            if why == "late_stage" and d in uni:
                conn.execute("INSERT OR REPLACE INTO funding_events VALUES (?,?,?,?,?,?,?,?,?,?)",
                             (f"news:{r['url']}", d, f"news:{r['feed']}", r["date"], "series_b_plus", amt, conf,
                              r["url"], r["title"][:300], pdb.utcnow()))
            continue
        st["admit"] += 1
        tier = "fresh_60d" if r["date"] >= FRESH else "in_window"
        pdb.upsert_company(conn, d, "H2", proof={"source": f"news:{r['feed']}", "url": r["url"],
                                                 "snippet": f"[{tier}|{conf}|{r['date']}] {r['title'][:180]}"})
        row = conn.execute("SELECT company_name, hq_country, sector, funding_date FROM companies WHERE company_domain=?",
                           (d,)).fetchone()
        sets = {"company_name": r["company"][:100] if not row["company_name"] else None,
                "hq_country": country if not row["hq_country"] and country else None,
                "sector": label if not row["sector"] and label else None}
        if not row["funding_date"] or row["funding_date"] < r["date"]:
            sets.update({"funding_date": r["date"], "funding_stage": stage if stage in ("pre_seed", "seed", "series_a")
                         else "unknown", "funding_amount": amt})
        sets = {k: v for k, v in sets.items() if v is not None}
        if sets:
            conn.execute(f"UPDATE companies SET {', '.join(k + '=?' for k in sets)} WHERE company_domain=?",
                         (*sets.values(), d))
        conn.execute("INSERT OR REPLACE INTO funding_events VALUES (?,?,?,?,?,?,?,?,?,?)",
                     (f"news:{r['url']}", d, f"news:{r['feed']}", r["date"], stage or "unknown", amt, conf, r["url"],
                      r["title"][:300], pdb.utcnow()))
    conn.commit()
    pdb.finish_run(conn, run, records_raw=len(rounds), records_unique=len(rounds), records_icp_pass=st["admit"],
                   stop_reason="exhausted")
    write_rows(POOL, pool, append=True)
    write_rows(REJ, rej, append=True)
    return {"rounds": len(rounds), **st}


# ------------------------------------------------------------------ ATS boards
def ingest_ats(conn) -> dict:
    live = pdb.RAW_DIR / "H1_job_arbitrage" / "ats_live"
    boards = []
    for p in sorted(live.glob("*.jsonl.gz")):
        boards += [json.loads(x) for x in gzip.open(p, "rt")]
    uni = existing_icp(conn)
    names = {r["company_domain"]: r["company_name"] for r in conn.execute("SELECT company_domain, company_name FROM companies")}
    # site-scan discovered slugs -> domain
    site_slugs = {}
    for r in conn.execute("SELECT company_domain, social FROM site_scan"):
        try:
            ats = json.loads(r["social"] or "{}").get("ats") or {}
        except ValueError:
            continue
        for prov, ss in ats.items():
            for s in ss:
                site_slugs[(prov.replace("_embed", ""), s)] = r["company_domain"]
    need, mapped = [], {}
    for b in boards:
        key = (b["provider"], b["slug"])
        dom = site_slugs.get(key)
        infer = [o.split(":", 1)[1] for o in b.get("origins", []) if o.startswith("infer:")]
        txt = " ".join(f"{j.get('title')} {j.get('description') or ''}" for j in b["jobs"][:15])[:30000]
        if not dom and infer:
            for d in infer:
                nm = names.get(d) or d.split(".")[0]
                _, full, short = rd.brand_cores(nm)
                # corroborate: brand appears in the postings, or the board's own company name matches
                if (short and len(short) >= 3 and short in rd.alnum(txt)) or \
                        (b.get("company_name") and rd.alnum(b["company_name"]) in (full, short)):
                    dom = d
                    break
        if dom:
            mapped[key] = dom
        else:
            nm = b.get("company_name") or b["slug"].replace("-", " ").replace("_", " ")
            hosts = [pdb.normalize_domain(u) for u in re.findall(r"https?://[^\s\"'<>)]+", txt)]
            hosts = [h for h in dict.fromkeys(hosts) if h and "ashbyhq" not in h and "greenhouse" not in h
                     and "lever.co" not in h and "linkedin" not in h]
            b["_name"] = nm
            need.append({"name": nm, "hint_domains": [h for h in hosts if brand_link(nm, [h])][:2]})
    resolved = resolve_all(need, "ats")
    st, pool, rej = Counter(), [], []
    run = pdb.start_run(conn, "H1", "ats_live", "Ashby/Greenhouse/Lever/Workable/Recruitee/Breezy/Teamtailor",
                        "raw_sources/H1_job_arbitrage/ats_live")
    by_dom = defaultdict(list)
    for b in boards:
        d = mapped.get((b["provider"], b["slug"])) or resolved.get(f"ats:{rd.alnum(b.get('_name') or '')}")
        if not d:
            st["no_domain"] += 1
            continue
        by_dom[d].append(b)
    for d, bs in by_dom.items():
        jobs = [j for b in bs for j in b["jobs"]]
        n_open = len({(j.get("title") or "").lower() for j in jobs})
        text = " ".join(f"{j.get('title')} {j.get('description') or ''}" for j in jobs[:20])[:40000]
        locs = " ; ".join(str(j.get("location") or "") for j in jobs)
        country = icp.country_of(*[str(j.get("location") or "") for j in jobs[:10]])
        if n_open > 40:
            st["reject"] += 1
            rej.append({"source": "ats_live", "name": bs[0].get("company_name") or bs[0]["slug"], "domain": d,
                        "reason": f"open_roles_{n_open}_gt_40"})
            continue
        dec, why, flags, label = gate(text, country, None, None, d, bs[0].get("company_name") or "", d in uni)
        if dec == "reject":
            st["reject"] += 1
            rej.append({"source": "ats_live", "name": bs[0].get("company_name") or bs[0]["slug"], "domain": d, "reason": why})
            continue
        if dec == "pool":
            st["pool"] += 1
            pool.append({"source": f"ats:{bs[0]['provider']}", "name": bs[0].get("company_name") or bs[0]["slug"],
                         "domain": d, "reason": why, "date": max((j.get("posted_at") or "") for j in jobs),
                         "url": jobs[0].get("url"), "evidence": f"{n_open} open roles; {locs[:120]}"})
            continue
        st["admit"] += 1
        sector = label or (conn.execute("SELECT sector FROM companies WHERE company_domain=?", (d,)).fetchone() or
                           {"sector": ""})["sector"] or ""
        pdb.upsert_company(conn, d, "H0", proof={"source": f"ats:{bs[0]['provider']}", "url": bs[0]["jobs"][0].get("url"),
                                                 "snippet": f"[universe|ats] {n_open} open roles"})
        best = None
        for b in bs:
            for j in b["jobs"]:
                c = h1.classify_job(j.get("title") or "", j.get("department"), sector)
                sig = job_text_signals(j.get("description") or "")
                key = f"{b['provider']}:{b['slug']}:{j.get('id')}"
                conn.execute(
                    "INSERT INTO jobs (job_key, company_domain, source, title, url, location, department, comp, posted_at, "
                    "first_seen_at, last_seen_at, family, strength, text_signals, description_snip) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(job_key) DO UPDATE SET "
                    "last_seen_at=excluded.last_seen_at, family=excluded.family, strength=excluded.strength, "
                    "text_signals=excluded.text_signals, company_domain=excluded.company_domain",
                    (key, d, f"ats:{b['provider']}", (j.get("title") or "")[:200], j.get("url"),
                     str(j.get("location") or "")[:120], (j.get("department") or "")[:80], j.get("comp"),
                     j.get("posted_at"), pdb.utcnow()[:10], pdb.utcnow()[:10], c["family"], c["strength"],
                     json.dumps(sig) if sig else None, (j.get("description") or "")[:1500]))
                if c["strength"] and (not best or h1.STRENGTH_ORDER[c["strength"]] < h1.STRENGTH_ORDER[best[0]["strength"]]):
                    best = (c, j, b)
        row = conn.execute("SELECT company_name, hq_country, sector FROM companies WHERE company_domain=?", (d,)).fetchone()
        sets = {"company_name": (bs[0].get("company_name") or bs[0]["slug"].replace("-", " ").title())[:100]
                if not row["company_name"] else None, "hq_country": country if not row["hq_country"] else None,
                "sector": label if not row["sector"] and label else None}
        sets = {k: v for k, v in sets.items() if v}
        if sets:
            conn.execute(f"UPDATE companies SET {', '.join(k + '=?' for k in sets)} WHERE company_domain=?",
                         (*sets.values(), d))
        if best:
            c, j, b = best
            pdb.upsert_company(conn, d, "H1", proof={"source": f"ats:{b['provider']}", "url": j.get("url"),
                                                     "snippet": f"[H1|{c['strength']}|{j.get('posted_at') or ''}] "
                                                                f"{(j.get('title') or '')[:120]} ({c['family']})"})
    conn.commit()
    pdb.finish_run(conn, run, records_raw=len(boards), records_unique=len(by_dom), records_icp_pass=st["admit"],
                   stop_reason="exhausted")
    write_rows(POOL, pool, append=True)
    write_rows(REJ, rej, append=True)
    return {"boards": len(boards), "domains": len(by_dom), **st}


TOOLS = re.compile(r"\b(clay(?:\.com)?|apollo(?:\.io)?|instantly|smartlead|lemlist|outreach(?:\.io)?|salesloft|"
                   r"hubspot|salesforce|attio|pipedrive|close\.com|gong|zoominfo|sales navigator|common room|unify|"
                   r"rb2b|warmly|koala|clearbit|6sense|n8n|zapier|make\.com|heyreach|la growth machine|"
                   r"amplemarket|regie|lavender|chili ?piper|default\.com|hockeystack)\b", re.I)
OUTBOUND = re.compile(r"\b(build (out )?(our|the) outbound|outbound (motion|engine|playbook|program|system|infrastructure)|"
                      r"cold (email|outreach|calling)|sequenc(e|es|ing)|prospecting|pipeline generation|lead gen\w*|"
                      r"gtm (engineering|automation|systems|stack)|revops|enrichment)\b", re.I)
FOUNDER_LED = re.compile(r"\b(founder[- ]led (sales|gtm|growth)|currently (led|run|handled) by (our|the) (founders?|ceo)|"
                         r"take (over )?(sales|gtm) from (the|our) (founders?|ceo)|work(ing)? (directly|closely) with "
                         r"(our|the) (ceo|founders?) (on|to) (sales|close|gtm|revenue)|first (sales|gtm|commercial|"
                         r"revenue|business) hire|our founders (are|have been) (selling|closing))\b", re.I)


def job_text_signals(desc: str) -> dict:
    out = {}
    tools = sorted({m.group(1).lower().replace(".com", "").replace(".io", "") for m in TOOLS.finditer(desc or "")})
    if tools:
        out["tools"] = tools
    if OUTBOUND.search(desc or ""):
        out["outbound"] = OUTBOUND.search(desc).group(0)[:60]
    if FOUNDER_LED.search(desc or ""):
        out["founder_led"] = FOUNDER_LED.search(desc).group(0)[:80]
    return out


def main() -> None:
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    conn = pdb.init_db()
    out = {}
    if which in ("hn", "all"):
        out["hn"] = ingest_hn(conn)
    if which in ("news", "all"):
        out["news"] = ingest_news(conn)
    if which in ("ats", "all"):
        out["ats"] = ingest_ats(conn)
    out["companies"] = conn.execute("SELECT COUNT(*) FROM companies WHERE status!='disqualified'").fetchone()[0]
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
