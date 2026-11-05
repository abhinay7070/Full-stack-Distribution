"""Form D -> ICP filter -> issuer grouping -> domain resolution -> DB (H2 capital events + founders).

Usage: python3 scripts/formd_ingest.py [--no-resolve]
Input  .cache/formd_filings.jsonl.gz (formd_collect.py)
Output sublists/sublist_formd_issuers.csv        every ICP-passing issuer (resolved or not) = US TAM backbone
       sublists/sublist_formd_rejected.csv       one row per rejected filing with reason
       raw_sources/H2_fresh_capital/formd_run_report.json

ICP rules (per filing; reason codes in the rejected file)
  pooled fund / business combination / pooled-name pattern            -> reject
  industry in real estate, biotech/pharma, oil & gas, restaurants, retail, banking, lodging, travel, ... -> reject
  entity: Corporation; LLC only for tech industries; 'Other' only for non-US issuers (Ltd/GmbH/...) -> else reject
  year of incorporation: yet-to-be-formed -> reject; over five years -> keep only tech industry & <= $20M (flag)
  revenue range > $25M -> reject
  amount (sold, else offered): > $40M -> reject (past Series A); < $100K -> reject (friends & family noise)
  geo: US state, or UK / EU / EEA / CH country -> keep; else reject
"""
from __future__ import annotations

import csv
import gzip
import json
import re
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pipeline_db as pdb  # noqa: E402
import h3_ingest as h3  # noqa: E402
import resolve_domains as rd  # noqa: E402

IN = pdb.ROOT / ".cache" / "formd_filings.jsonl.gz"
OUT = pdb.SUBLIST_DIR / "sublist_formd_issuers.csv"
REJ = pdb.SUBLIST_DIR / "sublist_formd_rejected.csv"
REPORT = pdb.RAW_DIR / "H2_fresh_capital" / "formd_run_report.json"
TODAY = date.today()
FRESH = (TODAY - timedelta(days=60)).isoformat()
US_STATES = set("AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY "
                "NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY PR".split())
EXCL_IND = {"Pooled Investment Fund", "Other Real Estate", "Commercial", "Residential", "REITS and Finance",
            "Investing", "Biotechnology", "Pharmaceuticals", "Oil and Gas", "Coal Mining", "Restaurants", "Retailing",
            "Agriculture", "Construction", "Lodging and Conventions", "Tourism and Travel Services",
            "Airlines and Airports", "Other Travel", "Hospitals and Physicians", "Electric Utilities",
            "Investment Banking", "Commercial Banking", "Other Utilities"}
TECH_IND = {"Other Technology", "Computers", "Telecommunications"}
BIG_REV = {"$25,000,001 - $100,000,000", "Over $100,000,000"}
COUNTRY = {"UNITED KINGDOM": "GB", "ENGLAND": "GB", "GERMANY": "DE", "FRANCE": "FR", "NETHERLANDS": "NL",
           "IRELAND": "IE", "SPAIN": "ES", "ITALY": "IT", "SWEDEN": "SE", "DENMARK": "DK", "FINLAND": "FI",
           "NORWAY": "NO", "SWITZERLAND": "CH", "AUSTRIA": "AT", "BELGIUM": "BE", "PORTUGAL": "PT", "POLAND": "PL",
           "ESTONIA": "EE", "LITHUANIA": "LT", "LATVIA": "LV", "CZECH REPUBLIC": "CZ", "CZECHIA": "CZ",
           "LUXEMBOURG": "LU", "GREECE": "GR", "ROMANIA": "RO", "HUNGARY": "HU", "ICELAND": "IS", "CROATIA": "HR",
           "SLOVENIA": "SI", "SLOVAKIA": "SK", "BULGARIA": "BG", "CYPRUS": "CY", "MALTA": "MT"}
POOLED = re.compile(r"\b(fund|funds|l\.?\s?p\.?|spv|feeder|co-?invest\w*|a series of|series \w+ of|reit|realty|"
                    r"apartments?|properties|real estate|opportunity zone|syndicate|capital partners|"
                    r"investors,? (llc|lp)|bancorp|bank|church|ministr(y|ies)|hospital)\b", re.I)


def country_of(r: dict) -> str | None:
    st = (r.get("state") or "").upper()
    if st in US_STATES:
        return "US"
    desc = (r.get("state_desc") or "").upper().strip()
    return COUNTRY.get(desc)


def amount(r: dict) -> float | None:
    s, o = r.get("sold_amt"), r.get("offering_amt")
    return s if s and s > 0 else o


def reject_reason(r: dict) -> tuple[str | None, list[str]]:
    flags = []
    if r.get("status") != "parsed":
        return r.get("status"), flags
    ind = r.get("industry") or ""
    if r.get("is_pooled") or ind == "Pooled Investment Fund":
        return "pooled_fund", flags
    if r.get("is_bus_comb"):
        return "business_combination", flags
    if POOLED.search(r.get("name") or ""):
        return "pooled_or_non_startup_name", flags
    if ind in EXCL_IND:
        return f"industry:{ind}", flags
    ctry = country_of(r)
    if ctry not in h3.ALLOWED:
        return f"geo:{r.get('state_desc') or r.get('state')}", flags
    et = r.get("entity_type") or ""
    if et == "Limited Liability Company":
        if ind not in TECH_IND:
            return "entity:llc_non_tech", flags
        flags.append("llc")
    elif et == "Other":
        if ctry == "US":
            return "entity:other_us", flags
    elif et != "Corporation":
        return f"entity:{et}", flags
    amt = amount(r)
    choice = r.get("yoi_choice") or ""
    if choice == "yetToBeFormed":
        return "not_yet_formed", flags
    if choice == "overFiveYears":
        if ind not in TECH_IND or (amt or 0) > 20e6:
            return "incorporated_over_5y_non_tech_or_large", flags
        flags.append("incorporated_over_5y")
    if (r.get("revenue_range") or "") in BIG_REV:
        return "revenue_over_25m", flags
    if amt is not None and amt > 40e6:
        return "amount_over_40m_past_series_a", flags
    if amt is not None and 0 < amt < 100e3:
        return "amount_under_100k", flags
    if amt is None or amt == 0:
        flags.append("amount_undisclosed")
    if ind not in TECH_IND:
        flags.append(f"industry_{ind.lower().replace(' ', '_') or 'blank'}")
    return None, flags


def stage_guess(amt: float | None) -> str:
    if not amt:
        return "unknown"
    return "pre_seed" if amt < 1.5e6 else "seed" if amt < 8e6 else "series_a"


def main() -> None:
    resolve = "--no-resolve" not in sys.argv
    filings = [json.loads(x) for x in gzip.open(IN, "rt")]
    rej, keep = [], defaultdict(list)
    reasons = defaultdict(int)
    for r in filings:
        why, flags = reject_reason(r)
        if why:
            reasons[why.split(":")[0] if why.startswith(("industry", "geo", "entity")) else why] += 1
            rej.append({"adsh": r["adsh"], "cik": r.get("cik"), "name": r.get("name"), "file_date": r.get("file_date"),
                        "industry": r.get("industry"), "state": r.get("state_desc") or r.get("state"),
                        "amount": amount(r), "reason": why})
            continue
        r["flags"] = flags
        keep[r["cik"]].append(r)
    issuers = []
    for cik, fl in keep.items():
        fl.sort(key=lambda r: r["file_date"])
        last = fl[-1]
        amts = [amount(r) for r in fl if amount(r)]
        persons, seen = [], set()
        for r in reversed(fl):
            for p in r.get("persons") or []:
                nm = f"{p['first']} {p['last']}".strip()
                if nm.lower() in seen or p["first"].lower() in ("n/a", "na", ""):
                    continue
                seen.add(nm.lower())
                persons.append({**p, "full": nm})
        sale = min((r["first_sale"] for r in fl if r.get("first_sale")), default=None)
        issuers.append({
            "cik": cik, "name": last["name"], "city": last.get("city", "").title(), "state": last.get("state"),
            "country": country_of(last), "industry": last.get("industry"), "entity_type": last.get("entity_type"),
            "yoi": last.get("yoi"), "revenue_range": last.get("revenue_range"),
            "amount_usd": int(max(amts)) if amts else None, "first_sale": sale,
            "first_filing": fl[0]["file_date"], "last_filing": last["file_date"], "filings": len(fl),
            "event_date": last["file_date"], "flags": sorted({f for r in fl for f in r.get("flags", [])}),
            "persons": persons[:8],
            "filing_url": f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}&type=D",
            "adsh": last["adsh"]})
    print(f"filings {len(filings)}  rejected {len(rej)}  issuers kept {len(issuers)}", file=sys.stderr)

    conn = pdb.init_db()
    # 1) existing DB companies by exact brand match (+ Form D tracker CIK->domain hints from H2 snapshot)
    by_name = defaultdict(set)
    for c in conn.execute("SELECT company_domain, company_name FROM companies WHERE company_name IS NOT NULL"):
        _, full, short = rd.brand_cores(c["company_name"])
        by_name[full].add(c["company_domain"])
    hint = {}
    try:
        import pandas as pd
        cd = pd.read_parquet(pdb.RAW_DIR / "H2_fresh_capital" / "formd_tracker" / "company_domains.parquet")
        cikc = next(c for c in cd.columns if "cik" in c.lower())
        domc = next(c for c in cd.columns if "domain" in c.lower())
        for _, row in cd.iterrows():
            d = pdb.normalize_domain(str(row[domc]))
            if d:
                hint[str(row[cikc]).lstrip("0")] = d
    except Exception as e:  # noqa: BLE001
        print("formd_tracker hints unavailable:", e, file=sys.stderr)
    ents = []
    for i in issuers:
        _, full, short = rd.brand_cores(i["name"])
        hit = by_name.get(full, set())
        hd = [hint[i["cik"]]] if i["cik"] in hint else []
        if len(hit) == 1:
            hd = list(hit) + hd
        i["hint_domains"] = hd
        ents.append({"key": f"formd:{i['cik']}", "name": i["name"],
                     "hints": {"city": i["city"], "persons": [p["last"] for p in i["persons"]],
                               "domains": hd, "tech": True}})
    res = rd.resolve(ents) if resolve else {}
    stats = defaultdict(int)
    run = pdb.start_run(conn, "H2", "sec_form_d", "Form D/D-A 2025-05-01..today; ICP filter; resolve_domains",
                        "raw_sources/H2_fresh_capital/sec_formd_bulk|sec_formd_efts")
    rows = []
    for i in issuers:
        rv = res.get(f"formd:{i['cik']}") or {}
        dom = rv.get("domain")
        i.update({"domain": dom, "resolution": rv.get("status", "not_run"), "resolution_score": rv.get("score"),
                  "resolution_confidence": rv.get("confidence"), "resolution_evidence": rv.get("evidence")})
        stats[i["resolution"]] += 1
        rows.append(i)
        if not dom:
            continue
        brand = rd.brand_cores(i["name"])[0].title()
        existing = conn.execute("SELECT funding_date FROM companies WHERE company_domain=?", (dom,)).fetchone()
        newer = not existing or not existing["funding_date"] or existing["funding_date"] < i["event_date"]
        stg = stage_guess(i["amount_usd"])
        tier = "fresh_60d" if i["event_date"] >= FRESH else "in_window"
        pdb.upsert_company(
            conn, dom, "H2",
            proof={"source": "sec_form_d", "url": i["filing_url"],
                   "snippet": f"[{tier}|form_d|{i['event_date']}] {i['name']} Form D {i['filings']} filing(s), "
                              f"${(i['amount_usd'] or 0) / 1e6:.2f}M, {i['industry']}, {i['city']} {i['state']}"},
            company_name=None if existing else brand, hq_country=i["country"],
            funding_stage=stg if newer else None, funding_amount=i["amount_usd"] if newer else None,
            funding_date=i["event_date"] if newer else None)
        conn.execute("INSERT OR REPLACE INTO funding_events VALUES (?,?,?,?,?,?,?,?,?,?)",
                     (f"form_d:{i['adsh']}", dom, "sec_form_d", i["event_date"], stg, i["amount_usd"], "form_d",
                      i["filing_url"], json.dumps({"cik": i["cik"], "name": i["name"], "industry": i["industry"],
                                                   "first_sale": i["first_sale"], "flags": i["flags"]}), pdb.utcnow()))
        execs = [p for p in i["persons"] if "Executive Officer" in p["rels"]] or i["persons"][:3]
        for p in execs[:4]:
            role = (p.get("clar") or "").strip() or "/".join(p["rels"])
            pdb.upsert_founder(conn, dom, p["full"].title() if p["full"].isupper() else p["full"],
                               role=role[:80], source_url=i["filing_url"])
    conn.commit()
    pdb.finish_run(conn, run, records_raw=len(filings), records_unique=len(issuers),
                   records_icp_pass=stats["resolved"], stop_reason="exhausted")
    cols = ["cik", "name", "domain", "resolution", "resolution_confidence", "resolution_score", "city", "state",
            "country", "industry", "entity_type", "yoi", "revenue_range", "amount_usd", "first_sale", "first_filing",
            "last_filing", "filings", "flags", "persons", "filing_url", "resolution_evidence"]
    with open(OUT, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for i in sorted(rows, key=lambda x: x["last_filing"], reverse=True):
            w.writerow({**i, "flags": "|".join(i["flags"]),
                        "persons": "; ".join(f"{p['full']} ({'/'.join(p['rels'])})" for p in i["persons"])})
    with open(REJ, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rej[0].keys()))
        w.writeheader()
        w.writerows(rej)
    rep = {"run_at": pdb.utcnow(), "filings": len(filings), "rejected": len(rej),
           "reject_reasons": dict(sorted(reasons.items(), key=lambda x: -x[1])), "issuers_icp": len(issuers),
           "resolution": dict(stats)}
    REPORT.write_text(json.dumps(rep, indent=1) + "\n")
    print(json.dumps(rep, indent=1))


if __name__ == "__main__":
    main()
