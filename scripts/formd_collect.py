"""SEC Form D collector: every Reg D filing from 2025-05-01 to today -> one normalized record per filing.

Usage: python3 scripts/formd_collect.py            (bulk quarters on disk + live EFTS for later dates)

Sources
  bulk   raw_sources/H2_fresh_capital/sec_formd_bulk/<q>_d.zip  (SEC DERA quarterly data sets, 2025Q2-2026Q1)
  live   efts.sec.gov full-text search (forms D, D/A), one query per day, paginated to exhaustion, for dates
         after the last bulk quarter; each surviving filing's primary_doc.xml is fetched and parsed.
         Raw: raw_sources/H2_fresh_capital/sec_formd_efts/{hits,docs}.jsonl.gz

Output  .cache/formd_filings.jsonl.gz  (every filing, unfiltered, normalized)  -> formd_ingest.py applies ICP.
Name pre-filter for live docs only skips obvious pooled vehicles (fund / L.P. / SPV / "a series of") to save
requests; skipped filings are still written (status 'skipped_name_prefilter') so counts reconcile.
"""
from __future__ import annotations

import csv
import gzip
import io
import json
import re
import sys
import xml.etree.ElementTree as ET
import zipfile
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import http_fetch as hf  # noqa: E402
import pipeline_db as pdb  # noqa: E402

BULK = pdb.RAW_DIR / "H2_fresh_capital" / "sec_formd_bulk"
LIVE = pdb.RAW_DIR / "H2_fresh_capital" / "sec_formd_efts"
OUT = pdb.ROOT / ".cache" / "formd_filings.jsonl.gz"
START = date(2025, 5, 1)
POOLED_NAME = re.compile(
    r"\b(fund|funds|l\.?\s?p\.?|spv|feeder|co-?invest\w*|a series of|series \w+ of|master|reit|realty|"
    r"apartments?|properties|real estate|opportunity zone|portfolio \w+|syndicate|investors,? (llc|lp)|"
    r"capital partners|partners (i|ii|iii|iv|v|vi|vii|viii|ix|x)\b|\b(i|ii|iii|iv|v|vi|vii|viii|ix|x),? (llc|lp|l\.p\.))\b",
    re.I)


def d_iso(s: str) -> str | None:
    s = (s or "").strip()
    if not s:
        return None
    for fmt in ("%d-%b-%Y", "%Y-%m-%d"):
        try:
            from datetime import datetime
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            pass
    return None


def num(s) -> float | None:
    try:
        return float(str(s).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


# ------------------------------------------------------------------ bulk
def from_bulk() -> tuple[list[dict], str | None]:
    out, last_q_end = [], None
    for z in sorted(BULK.glob("*_d.zip")):
        q = z.stem.split("_")[0].upper()
        zf = zipfile.ZipFile(z)

        def tsv(n):
            name = next(x for x in zf.namelist() if x.endswith(f"/{n}.tsv"))
            return list(csv.DictReader(io.TextIOWrapper(zf.open(name), encoding="utf-8", errors="replace"),
                                       delimiter="\t"))
        subs = {r["ACCESSIONNUMBER"]: r for r in tsv("FORMDSUBMISSION")}
        iss = {}
        for r in tsv("ISSUERS"):
            if r["IS_PRIMARYISSUER_FLAG"] == "YES":
                iss[r["ACCESSIONNUMBER"]] = r
        off = {r["ACCESSIONNUMBER"]: r for r in tsv("OFFERING")}
        rel: dict[str, list] = {}
        for r in tsv("RELATEDPERSONS"):
            rel.setdefault(r["ACCESSIONNUMBER"], []).append({
                "first": r["FIRSTNAME"], "last": r["LASTNAME"], "city": r["CITY"],
                "rels": [x for x in (r["RELATIONSHIP_1"], r["RELATIONSHIP_2"], r["RELATIONSHIP_3"]) if x],
                "clar": r["RELATIONSHIPCLARIFICATION"]})
        for acc, s in subs.items():
            i, o = iss.get(acc), off.get(acc)
            if not i or not o:
                continue
            fd = d_iso(s["FILING_DATE"])
            out.append({
                "adsh": acc, "source": f"bulk:{q}", "form": s["SUBMISSIONTYPE"], "file_date": fd,
                "cik": i["CIK"].lstrip("0"), "name": i["ENTITYNAME"].strip(), "city": i["CITY"].strip(),
                "state": i["STATEORCOUNTRY"].strip(), "state_desc": i["STATEORCOUNTRYDESCRIPTION"].strip(),
                "street": i["STREET1"].strip(), "phone": i["ISSUERPHONENUMBER"].strip(),
                "jurisdiction": i["JURISDICTIONOFINC"].strip(), "entity_type": i["ENTITYTYPE"].strip(),
                "yoi_choice": i["YEAROFINC_TIMESPAN_CHOICE"], "yoi": i["YEAROFINC_VALUE_ENTERED"],
                "prev_names": [x for x in (i["ISSUER_PREVIOUSNAME_1"], i["EDGAR_PREVIOUSNAME_1"]) if x],
                "industry": o["INDUSTRYGROUPTYPE"], "revenue_range": o["REVENUERANGE"],
                "is_amendment": o["ISAMENDMENT"] == "true", "first_sale": d_iso(o["SALE_DATE"]),
                "yet_to_occur": o["YETTOOCCUR"] == "true", "is_equity": o["ISEQUITYTYPE"] == "true",
                "is_pooled": o["ISPOOLEDINVESTMENTFUNDTYPE"] == "true",
                "is_bus_comb": o["ISBUSINESSCOMBINATIONTRANS"] == "true",
                "offering_amt": num(o["TOTALOFFERINGAMOUNT"]), "sold_amt": num(o["TOTALAMOUNTSOLD"]),
                "investors": num(o["TOTALNUMBERALREADYINVESTED"]), "exemptions": o["FEDERALEXEMPTIONS_ITEMS_LIST"],
                "persons": rel.get(acc, []), "status": "parsed"})
        y, qn = int(q[:4]), int(q[-1])
        end = date(y, qn * 3, 1) + timedelta(days=32)
        last_q_end = max(last_q_end or "", (end.replace(day=1) - timedelta(days=1)).isoformat())
    return [r for r in out if r["file_date"] and r["file_date"] >= START.isoformat()], last_q_end


# ------------------------------------------------------------------ live
def _t(el, path):
    for p in path.split("/"):
        if el is None:
            return None
        el = next((c for c in el if c.tag.split("}")[-1] == p), None)
    return el.text.strip() if el is not None and el.text else None


def _find(el, name):
    return next((c for c in el.iter() if c.tag.split("}")[-1] == name), None)


def parse_doc(xml: str, hit: dict) -> dict | None:
    try:
        root = ET.fromstring(xml.encode() if isinstance(xml, str) else xml)
    except ET.ParseError:
        return None
    pi, od = _find(root, "primaryIssuer"), _find(root, "offeringData")
    if pi is None or od is None:
        return None
    yoi = _find(pi, "yearOfInc")
    choice = None
    if yoi is not None:
        for c in yoi:
            t = c.tag.split("}")[-1]
            if t in ("withinFiveYears", "overFiveYears", "yetToBeFormed") and (c.text or "").strip() == "true":
                choice = t
    persons = []
    rl = _find(root, "relatedPersonsList")
    for rp in (rl if rl is not None else []):
        rels = [c.text for c in rp.iter() if c.tag.split("}")[-1] == "relationship" and c.text]
        persons.append({"first": _t(rp, "relatedPersonName/firstName") or "",
                        "last": _t(rp, "relatedPersonName/lastName") or "",
                        "city": _t(rp, "relatedPersonAddress/city") or "", "rels": rels,
                        "clar": _t(rp, "relationshipClarification") or ""})
    ex = [c.text for c in od.iter() if c.tag.split("}")[-1] == "item" and c.text]
    return {
        "adsh": hit["adsh"], "source": "efts", "form": hit.get("form"), "file_date": hit.get("file_date"),
        "cik": (_t(pi, "cik") or hit["ciks"][0]).lstrip("0"), "name": (_t(pi, "entityName") or "").strip(),
        "city": _t(pi, "issuerAddress/city") or "", "state": _t(pi, "issuerAddress/stateOrCountry") or "",
        "state_desc": _t(pi, "issuerAddress/stateOrCountryDescription") or "",
        "street": _t(pi, "issuerAddress/street1") or "", "phone": _t(pi, "issuerPhoneNumber") or "",
        "jurisdiction": _t(pi, "jurisdictionOfInc") or "", "entity_type": _t(pi, "entityType") or "",
        "yoi_choice": choice, "yoi": _t(pi, "yearOfInc/value") or "",
        "prev_names": [c.text for c in pi.iter() if c.tag.split("}")[-1] == "value" and c.text
                       and c is not None and False],
        "industry": _t(od, "industryGroup/industryGroupType") or "",
        "revenue_range": _t(od, "issuerSize/revenueRange") or _t(od, "issuerSize/aggregateNetAssetValueRange") or "",
        "is_amendment": (_t(od, "typeOfFiling/newOrAmendment/isAmendment") or "") == "true",
        "first_sale": _t(od, "typeOfFiling/dateOfFirstSale/value"),
        "yet_to_occur": (_t(od, "typeOfFiling/dateOfFirstSale/yetToOccur") or "") == "true",
        "is_equity": (_t(od, "typesOfSecuritiesOffered/isEquityType") or "") == "true",
        "is_pooled": (_t(od, "typesOfSecuritiesOffered/isPooledInvestmentFundType") or "") == "true"
                     or (_t(od, "industryGroup/industryGroupType") or "") == "Pooled Investment Fund",
        "is_bus_comb": (_t(od, "businessCombinationTransaction/isBusinessCombinationTransaction") or "") == "true",
        "offering_amt": num(_t(od, "offeringSalesAmounts/totalOfferingAmount")),
        "sold_amt": num(_t(od, "offeringSalesAmounts/totalAmountSold")),
        "investors": num(_t(od, "investors/totalNumberAlreadyInvested")), "exemptions": ", ".join(ex),
        "persons": persons, "status": "parsed"}


def from_live(after: str) -> list[dict]:
    LIVE.mkdir(parents=True, exist_ok=True)
    day, today = date.fromisoformat(after) + timedelta(days=1), date.today()
    hits, pages = [], 0
    while day <= today:
        for form in ("D", "D/A"):
            frm = 0
            while True:
                u = (f"https://efts.sec.gov/LATEST/search-index?forms={form}&dateRange=custom&startdt={day}"
                     f"&enddt={day}&from={frm}")
                r = hf.fetch(u, max_age=None if day < today - timedelta(days=3) else 3600)
                pages += 1
                if r.status != 200:
                    print("EFTS error", r.status, u, file=sys.stderr)
                    break
                d = r.json()
                hh = d["hits"]["hits"]
                for h in hh:
                    s = h["_source"]
                    hits.append({"adsh": s["adsh"], "form": s["form"], "file_date": s["file_date"],
                                 "ciks": s["ciks"], "display_names": s["display_names"],
                                 "biz_locations": s.get("biz_locations"), "biz_states": s.get("biz_states"),
                                 "inc_states": s.get("inc_states"), "doc": h["_id"].split(":", 1)[1]})
                frm += len(hh)
                if not hh or frm >= d["hits"]["total"]["value"] or frm >= 9900:
                    break
        day += timedelta(days=1)
    seen, uniq = set(), []
    for h in hits:
        if h["adsh"] not in seen:
            seen.add(h["adsh"])
            uniq.append(h)
    with gzip.open(LIVE / "hits.jsonl.gz", "wt") as f:
        for h in uniq:
            f.write(json.dumps(h) + "\n")
    print(f"EFTS: {pages} pages, {len(uniq)} filings", file=sys.stderr)
    todo, out = [], []
    for h in uniq:
        nm = re.sub(r"\s*\(CIK \d+\)\s*$", "", h["display_names"][0]).strip()
        if POOLED_NAME.search(nm):
            out.append({"adsh": h["adsh"], "source": "efts", "form": h["form"], "file_date": h["file_date"],
                        "cik": h["ciks"][0].lstrip("0"), "name": nm, "status": "skipped_name_prefilter"})
        else:
            todo.append(h)
    urls = {h["adsh"]: f"https://www.sec.gov/Archives/edgar/data/{h['ciks'][0].lstrip('0')}/"
                       f"{h['adsh'].replace('-', '')}/{h['doc']}" for h in todo}
    print(f"fetching {len(urls)} primary docs", file=sys.stderr)
    res = hf.fetch_many(urls.values(), workers=8)
    with gzip.open(LIVE / "docs.jsonl.gz", "wt") as f:
        for h in todo:
            r = res[urls[h["adsh"]]]
            f.write(json.dumps({"adsh": h["adsh"], "url": urls[h["adsh"]], "status": r.status, "xml": r.text}) + "\n")
            rec = parse_doc(r.text, h) if r.status == 200 else None
            out.append(rec or {"adsh": h["adsh"], "source": "efts", "form": h["form"], "file_date": h["file_date"],
                               "cik": h["ciks"][0].lstrip("0"), "name": h["display_names"][0],
                               "status": f"doc_error_{r.status}"})
    return out


def main() -> None:
    bulk, last = from_bulk()
    print(f"bulk filings in window: {len(bulk)} (through {last})", file=sys.stderr)
    live = from_live(last)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(OUT, "wt") as f:
        for r in bulk + live:
            f.write(json.dumps(r) + "\n")
    st = {}
    for r in bulk + live:
        st[r["status"]] = st.get(r["status"], 0) + 1
    print(json.dumps({"bulk": len(bulk), "live": len(live), "by_status": st}))


if __name__ == "__main__":
    main()
