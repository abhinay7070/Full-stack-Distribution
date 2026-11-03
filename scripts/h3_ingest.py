"""H3 - Technical-founder startups: harvest -> filter -> classify -> merge -> DB + CSV.

Reads ONLY the frozen raw snapshots in raw_sources/H3_technical_founders/ (written by
h3_snapshot.py), so every run is reproducible and nothing is fetched here.

Outputs
  sublists/sublist_H3_technical_founders.csv   ICP + H3 passes (tiers A/B/C)
  sublists/sublist_H3_rejected.csv             every harvested record not exported, with reason
  db/gtm_pipeline.db                           companies/founders upserted (H3 tag), ingestion_runs audit
"""
from __future__ import annotations

import csv
import gzip
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pipeline_db as pdb  # noqa: E402

RAW = pdb.RAW_DIR / "H3_technical_founders"
OUT_CSV = pdb.SUBLIST_DIR / "sublist_H3_technical_founders.csv"
REJ_CSV = pdb.SUBLIST_DIR / "sublist_H3_rejected.csv"
HEADCOUNT_MIN, HEADCOUNT_MAX = 2, 35

YC_BATCHES = ["winter-2025", "spring-2025", "summer-2025", "fall-2025",
              "winter-2026", "spring-2026", "summer-2026", "fall-2026"]
SPEEDRUN_COHORTS = {"SR004": 2025, "SR005": 2025, "SR006": 2026, "SR007": 2026}

# --------------------------------------------------------------------------- geography
EUROPE = {
    "AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "GR", "HU", "IE", "IT", "LV",
    "LT", "LU", "MT", "NL", "PL", "PT", "RO", "SK", "SI", "ES", "SE",       # EU-27
    "GB", "CH", "NO", "IS", "LI",                                           # UK + EEA/EFTA
}
ALLOWED = EUROPE | {"US"}
COUNTRY_NAMES = {
    "united states": "US", "united states of america": "US", "usa": "US", "us": "US",
    "united kingdom": "GB", "uk": "GB", "england": "GB", "scotland": "GB", "wales": "GB",
    "northern ireland": "GB", "austria": "AT", "belgium": "BE", "bulgaria": "BG", "croatia": "HR",
    "cyprus": "CY", "czech republic": "CZ", "czechia": "CZ", "denmark": "DK", "estonia": "EE",
    "finland": "FI", "france": "FR", "germany": "DE", "greece": "GR", "hungary": "HU",
    "ireland": "IE", "italy": "IT", "latvia": "LV", "lithuania": "LT", "luxembourg": "LU",
    "malta": "MT", "netherlands": "NL", "the netherlands": "NL", "poland": "PL", "portugal": "PT",
    "romania": "RO", "slovakia": "SK", "slovenia": "SI", "spain": "ES", "sweden": "SE",
    "switzerland": "CH", "norway": "NO", "iceland": "IS", "liechtenstein": "LI",
    "canada": "CA", "india": "IN", "israel": "IL", "singapore": "SG", "australia": "AU",
    "brazil": "BR", "mexico": "MX", "japan": "JP", "south korea": "KR", "turkey": "TR",
    "united arab emirates": "AE", "nigeria": "NG", "colombia": "CO", "argentina": "AR",
    "new zealand": "NZ", "montenegro": "ME", "hong kong": "HK", "china": "CN", "chile": "CL",
    "pakistan": "PK", "indonesia": "ID", "philippines": "PH", "kenya": "KE", "egypt": "EG",
}
US_STATE = re.compile(r",\s*(A[LKZR]|C[AOT]|D[EC]|FL|GA|HI|I[DLNA]|K[SY]|LA|M[EDAINSOT]|N[EVHJMYCD]|"
                      r"O[HKR]|PA|RI|S[CD]|T[NX]|UT|V[TA]|W[AVIY])\b")
EF_CITY = {"london": "GB", "paris": "FR", "berlin": "DE", "new york": "US", "san francisco": "US",
           "singapore": "SG", "bangalore": "IN", "hong kong": "HK", "toronto": "CA"}


def country_from_text(text: str | None) -> str | None:
    """First recognizable country in a 'City, Region, Country; City2, ...' string."""
    if not text:
        return None
    for loc in re.split(r";", text):
        parts = [p.strip().lower() for p in loc.split(",") if p.strip()]
        for p in reversed(parts):
            if p in COUNTRY_NAMES:
                return COUNTRY_NAMES[p]
        if US_STATE.search(loc):
            return "US"
    return None


# --------------------------------------------------------------------------- sector
BIOTECH = re.compile(r"\b(therapeutic|drug (discovery|development)|biotech|antibod|gene (therapy|editing)|"
                     r"cell therapy|clinical[- ]stage|small molecule|protein design|pharmaceutical|"
                     r"oncology|vaccine|biolog(ics|y lab)|wet[- ]lab|peptide)", re.I)
AGENCY = re.compile(r"\b(marketing agency|creative agency|dev(elopment)? shop|staffing agency|"
                    r"recruiting agency|consultancy|we are an agency)\b", re.I)
CONSUMER = re.compile(r"\b(for (kids|parents|families|couples|teens|gamers|fans|singles)|dating app|"
                      r"social app|mobile game|consumer app|personal finance app|fitness app)\b", re.I)
AI_SOFT = re.compile(r"\b(ai|a\.i\.|artificial intelligence|machine learning|ml|autonom\w*|software|"
                     r"platform|saas|agent\w*|llm|data|simulation|computer vision|perception|"
                     r"analytics|automat\w*|api|cloud|model\w*|copilot)\b", re.I)
HW_AI = re.compile(r"\b(ai|a\.i\.|artificial intelligence|machine learning|ml|autonom\w*|robot\w*|humanoid\w*|"
                   r"llm|computer vision|perception|world models?|software|simulation|agent\w*|"
                   r"inference|ai chips?|gpu|compute|data cent(er|re)s?|copilot)\b", re.I)
B2B_HINT = re.compile(r"\b(for (businesses|companies|enterprises|teams|schools|universities|teachers|"
                      r"districts|employers|clinics|hospitals|providers|banks|insurers|governments?)|"
                      r"b2b|enterprise|saas)\b", re.I)


def sector_decision(industry: str, sub: str, tags: list[str], text: str) -> tuple[bool, str, str]:
    """Return (keep, sector_label, reason)."""
    ind = (industry or "").strip()
    sub_l = (sub or "").lower()
    tag_l = " | ".join(tags).lower()
    blob = f"{text} {tag_l} {sub_l}"
    if BIOTECH.search(blob) or "biotech" in tag_l or "drug discovery" in sub_l or "therapeutics" in sub_l:
        return False, "", "sector:biotech/pharma"
    if AGENCY.search(text):
        return False, "", "sector:agency/services"
    if ind in ("Consumer",) or re.search(r"\b(gaming|consumer|b2c|media / entertainment|creator economy)\b", ind.lower()):
        if not B2B_HINT.search(blob):
            return False, "", f"sector:consumer ({ind})"
    if CONSUMER.search(text) and not B2B_HINT.search(blob):
        return False, "", "sector:consumer (description)"
    if ind == "Healthcare" or "healthcare ->" in sub_l:
        if any(k in sub_l for k in ("medical devices", "diagnostics", "consumer health", "healthcare services")) \
                and not (AI_SOFT.search(text) and B2B_HINT.search(blob)):
            return False, "", f"sector:healthcare non-software ({sub})"
    if ind == "Education" or "edtech" in tag_l:
        if not B2B_HINT.search(blob):
            return False, "", "sector:consumer edtech"
    hardware = ind in ("Industrials",) or re.search(
        r"hardware|manufacturing|robotics|drones|space|aviation|energy|climate|cleantech|oil and gas|"
        r"automotive|3d printing|semiconductor|wearables|agtech|mobility", f"{sub_l} {tag_l}")
    if hardware:
        if not HW_AI.search(text):
            return False, "", "sector:raw hardware (no AI/autonomy/software signal)"
        return True, "Industrial / Physical AI", ""
    # label
    if ind == "Fintech" or re.search(r"\b(fintech|insurtech|stablecoin|payments?|banking|lending|accounting)\b", blob):
        label = "Fintech"
    elif re.search(r"developer tools|dev tools|devops|engineering, product and design|open source|"
                   r"\bfor (developers|engineers|engineering teams)\b|\bsdk\b|\bide\b|code (review|generation|gen)|"
                   r"coding agent|\bcodebase", blob):
        label = "DevTools"
    elif re.search(r"infrastructure|ai infra|\binfra\b|gpu|inference|compute|database|security|cybersecurity", blob):
        label = "Infra"
    elif re.search(r"\bagent(s|ic)?\b", blob):
        label = "AI Agents"
    elif AI_SOFT.search(text) and re.search(r"\b(ai|ml|llm|machine learning|artificial intelligence)\b", blob):
        label = "Vertical AI"
    else:
        label = "B2B SaaS"
    return True, label, ""


# --------------------------------------------------------------------------- founder classification
TECH_TITLE = re.compile(r"\b(cto|chief (technology|technical|scientist|science|engineering|ai|architect)\w*|"
                        r"engineer\w*|head of (ml|ai|engineering|research)|founding engineer|"
                        r"hardware architect|vp of vulnerability|technology|scientist)\b", re.I)
GTM_TITLE = re.compile(r"\b(cro|cmo|cgo|chief (revenue|marketing|growth|commercial|business)|"
                       r"head of (sales|growth|marketing|gtm)|vp (of )?(sales|marketing|growth))\b", re.I)
NONTECH_TITLE = re.compile(r"\b(coo|cfo|president|chief operating|chief financial)\b", re.I)
TECH_BIO = re.compile(
    r"(computer science|\bcs\b|\beecs\b|\bcse\b|software|\bswe\b|engineer|developer|programm|coding|"
    r"\bcoder\b|machine learning|\bml\b|deep learning|\bai (research|researcher|engineer|lab)|"
    r"research (scientist|engineer)|data scien|\bphd\b|ph\.d|robotic|mechatronic|electrical|"
    r"physics|mathemat|\bmath\b|statistic|full[- ]?stack|backend|front[- ]?end|infra|distributed systems|"
    r"open[- ]source|compiler|kernel|\bgpu\b|\bllm|neural|computer vision|\bnlp\b|hacker|hackathon|"
    r"olympiad|\bioi\b|\bimo\b|\bipho\b|\bicpc\b|github|\bcto\b|technical|founding engineer|"
    r"built (the|a|an|our)|\bdarpa\b|quant(itative)? (dev|research)|algorithm|cryptograph|"
    r"security research|reverse engineer|\bsre\b|devops|firmware|embedded|\bfpga\b|silicon|"
    r"aerospace|\bbs[ce]?\b in|\bms\b in|\bmeng\b|\bbeng\b|stanford cs|waterloo|\bmit\b|iit\b|"
    r"\beng\b|caltech|cyber|red team|security engineer|\bquant\b|computational|control(s)? system|"
    r"\bi (built|wrote|coded|shipped)\b|wrote (most of )?(the|our) (mvp|code)|mechanical|eVTOL)", re.I)
NONTECH_BIO = re.compile(
    r"(\bsales\b|marketing|marketer|\bgrowth\b|go[- ]to[- ]market|\bgtm\b|business development|"
    r"account executive|\bmba\b|mckinsey|\bbcg\b|\bbain\b|investment bank|banker|private equity|"
    r"\bconsultant\b|consulting|lawyer|attorney|\bj\.?d\.?\b|product manager|\bpm\b|"
    r"(head of|led|scaled|vp of|director of|strategy (and|&)) \w{0,12} ?operations|accountant|\bcpa\b|\bcfa\b|"
    r"hedge fund analyst|journalist|designer|venture capital|\bpe investor\b)", re.I)
GTM_BIO = re.compile(
    r"(head of (sales|growth|marketing|gtm)|led (sales|growth|marketing|gtm)|growth marketer|"
    r"growth lead|sales lead|account executive|\bvp,? (of )?(sales|marketing|growth)|"
    r"business development|go[- ]to[- ]market lead|marketing lead|demand gen|\bsdr\b|\bbdr\b)", re.I)


def classify_founder(title: str, bio: str) -> tuple[str, bool, str]:
    """Return (technical|non_technical|unknown, is_gtm, evidence)."""
    title = title or ""
    bio = (bio or "").strip()
    is_gtm = bool(GTM_TITLE.search(title) or GTM_BIO.search(bio))
    if GTM_TITLE.search(title):
        return "non_technical", True, f"title '{title}'"
    if TECH_TITLE.search(title):
        return "technical", is_gtm, f"title '{title}'"
    m = TECH_BIO.search(bio)
    if m:
        return "technical", is_gtm, f"bio '{m.group(0)}'"
    if NONTECH_TITLE.search(title):
        return "non_technical", is_gtm, f"title '{title}'"
    n = NONTECH_BIO.search(bio)
    if n:
        return "non_technical", is_gtm, f"bio '{n.group(0)}'"
    return "unknown", is_gtm, "no background data" if len(bio) < 3 else "bio has no background signal"


def h3_verdict(founders: list[dict]) -> tuple[str | None, str, int]:
    """Return (tier or None if H3 fails, note, gtm_founder_count)."""
    if not founders:
        return "C_unverified", "founder list not published by source", 0
    cls = Counter(f["tech_class"] for f in founders)
    gtm = sum(f["is_gtm"] for f in founders)
    n = len(founders)
    if cls["non_technical"]:
        who = "; ".join(f"{f['name']} ({f['evidence']})" for f in founders if f["tech_class"] == "non_technical")
        return None, f"non-technical founder: {who}", gtm
    if gtm > 1:
        return None, f"{gtm} GTM-function founders", gtm
    base = f"{cls['technical']}/{n} founders technical"
    gtm_note = "0 GTM founders" if gtm == 0 else "1 founder with GTM background"
    if cls["technical"] == n:
        return "A_verified", f"{base}, {gtm_note}", gtm
    if cls["technical"]:
        return "B_likely", f"{base}, {cls['unknown']} unknown (no bio signal), {gtm_note}", gtm
    return "C_unverified", f"0/{n} founders with background data, {gtm_note}", gtm


GTM_JOB = re.compile(r"\b(sales|account executive|\bae\b|sdr|bdr|growth|marketing|demand gen|gtm|"
                     r"go[- ]to[- ]market|business development|partnerships|revenue|customer success)\b", re.I)


def x_handle(url: str | None) -> str | None:
    if not url:
        return None
    m = re.search(r"(?:twitter|x)\.com/@?([A-Za-z0-9_]{1,15})", url)
    return m.group(1) if m else None


def load_gz_json(path: Path):
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return json.load(f)


def load_gz_csv(path: Path) -> list[dict]:
    with gzip.open(path, "rt", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


# --------------------------------------------------------------------------- source adapters
# Each adapter yields normalized records:
# {source, source_program, cohort, cohort_year, name, website, one_liner, industry, sub, tags,
#  text, country, city, headcount, status, founders[{name,title,bio,linkedin,x}], jobs[], source_url}

def harvest_yc(stats: dict) -> list[dict]:
    out = []
    xcheck = {}
    for b in YC_BATCHES:
        rows = load_gz_json(RAW / "yc_devasheeshG" / f"{b}.json.gz")
        oss = load_gz_json(RAW / "yc_oss_crosscheck" / f"{b}.json.gz")
        a, o = {r["slug"] for r in rows}, {r["slug"] for r in oss}
        xcheck[b] = {"primary": len(a), "yc_oss": len(o), "missing_in_primary": sorted(o - a),
                     "extra_in_primary": sorted(a - o)}
        season, year = b.split("-")
        short = f"{ {'winter': 'W', 'spring': 'X', 'summer': 'S', 'fall': 'F'}[season]}{year[2:]}"
        for r in rows:
            out.append({
                "source": "yc", "source_program": "Y Combinator", "cohort": f"YC {short}",
                "cohort_year": int(year), "name": r["name"], "website": r.get("website"),
                "one_liner": r.get("one_liner") or "", "industry": r.get("industry") or "",
                "sub": r.get("subindustry") or "", "tags": r.get("tags") or [],
                "text": f"{r.get('one_liner') or ''} {r.get('long_description') or ''}",
                "country": r.get("country") or country_from_text(r.get("all_locations")),
                "city": r.get("city") or (r.get("all_locations") or "").split(";")[0],
                "headcount": r.get("team_size"), "status": r.get("status"),
                "founders": [{"name": f["full_name"], "title": f.get("title") or "",
                              "bio": f.get("founder_bio") or "", "linkedin": f.get("linkedin_url"),
                              "x": f.get("x_url")} for f in r.get("founders") or [] if f.get("is_active", True)],
                "jobs": [j.get("title", "") for j in r.get("jobs") or []],
                "is_hiring": r.get("isHiring"), "source_url": r.get("url"),
            })
    stats["yc_crosscheck"] = xcheck
    return out


def harvest_speedrun(stats: dict) -> list[dict]:
    data = load_gz_json(RAW / "speedrun_startuppy" / "data.json.gz")["companies"]
    founders_by_domain = defaultdict(list)
    for f in load_gz_csv(RAW / "ldws123_founders" / "a16z.csv.gz"):
        if f["Batch"].startswith("SR") and f["Full Name"].strip():
            founders_by_domain[pdb.normalize_domain(f["Domain"] or f["Website"])].append(f)
    out = []
    for r in data:
        if r.get("source") != "a16z-speedrun" or r.get("batch") not in SPEEDRUN_COHORTS:
            continue
        dom = pdb.normalize_domain(r.get("website"))
        fs = founders_by_domain.get(dom, []) if dom else []
        out.append({
            "source": "a16z_speedrun", "source_program": "a16z Speedrun", "cohort": f"a16z Speedrun {r['batch']}",
            "cohort_year": SPEEDRUN_COHORTS[r["batch"]], "name": r["name"], "website": r.get("website"),
            "one_liner": r.get("one_liner") or "", "industry": r.get("industry") or "", "sub": "",
            "tags": r.get("tags") or [], "text": f"{r.get('one_liner') or ''} {r.get('description') or ''}",
            "country": country_from_text(r.get("locations")), "city": (r.get("locations") or "").split(",")[0],
            "headcount": r.get("team_size"), "status": r.get("status"),
            "founders": [{"name": f["Full Name"].strip(), "title": f["Title"], "bio": "",
                          "linkedin": f["LinkedIn"] or None, "x": None} for f in fs],
            "jobs": [], "is_hiring": r.get("is_hiring"), "source_url": r.get("yc_url"),
        })
    return out


def harvest_ef(stats: dict) -> list[dict]:
    data = load_gz_json(RAW / "portfolios_yigitmeteozcan" / "ef.json.gz")
    data = data if isinstance(data, list) else data["companies"]
    ldw = defaultdict(list)
    for f in load_gz_csv(RAW / "ldws123_founders" / "entrepreneur_first.csv.gz"):
        ldw[f["Company"].strip().lower()].append(f)
    out = []
    for r in data:
        if str(r.get("year")) not in ("2025", "2026"):
            continue
        match = ldw.get(r["name"].strip().lower(), [])
        website = r.get("website") or (match[0]["Website"] or match[0]["Domain"] if match else "")
        city = match[0]["Location"] if match else ""
        founders = r.get("founders") or [{"name": m["Full Name"], "role": m["Title"], "linkedin": m["LinkedIn"]}
                                         for m in match]
        out.append({
            "source": "entrepreneur_first", "source_program": "Entrepreneur First",
            "cohort": f"Entrepreneur First {r['year']}", "cohort_year": int(r["year"]), "name": r["name"],
            "website": website, "one_liner": r.get("description") or "", "industry": "", "sub": "",
            "tags": r.get("tags") or [], "text": r.get("description") or "",
            "country": EF_CITY.get(city.strip().lower()), "city": city, "headcount": None, "status": "Active",
            "founders": [{"name": (f.get("name") or "").strip(), "title": f.get("role") or "", "bio": "",
                          "linkedin": f.get("linkedin") or None, "x": None} for f in founders if f.get("name")],
            "jobs": [], "is_hiring": None, "source_url": "https://www.joinef.com/portfolio/",
        })
    return out


def harvest_techstars(stats: dict) -> list[dict]:
    data = load_gz_json(RAW / "portfolios_yigitmeteozcan" / "techstars.json.gz")
    data = data if isinstance(data, list) else data["companies"]
    out = []
    for r in data:
        if str(r.get("year")) not in ("2025", "2026"):
            continue
        extra = r.get("extra") or {}
        out.append({
            "source": "techstars", "source_program": "Techstars", "cohort": f"{r.get('program')} {r['year']}",
            "cohort_year": int(r["year"]), "name": r["name"], "website": r.get("website"),
            "one_liner": r.get("description") or "", "industry": "", "sub": "", "tags": r.get("tags") or [],
            "text": r.get("description") or "",
            "country": COUNTRY_NAMES.get((extra.get("country") or "").lower()) or country_from_text(r.get("location")),
            "city": extra.get("city") or "", "headcount": None, "status": "Active", "founders": [],
            "jobs": [], "is_hiring": None, "source_url": "https://www.techstars.com/portfolio",
        })
    return out


ADAPTERS = [
    ("yc", "Y Combinator W25-F26 (devasheeshG/yc-api, cross-checked vs yc-oss/api)", harvest_yc),
    ("a16z_speedrun", "a16z Speedrun SR004-SR007 (startuppy + LdwS123 founders)", harvest_speedrun),
    ("entrepreneur_first", "Entrepreneur First 2025-2026 (yigitmeteozcan + LdwS123 domains)", harvest_ef),
    ("techstars", "Techstars 2025-2026 programs (yigitmeteozcan)", harvest_techstars),
]

SOURCE_PRIORITY = {"yc": 0, "a16z_speedrun": 1, "entrepreneur_first": 2, "techstars": 3}
TIER_RANK = {"A_verified": 0, "B_likely": 1, "C_unverified": 2}


# --------------------------------------------------------------------------- pipeline
def evaluate(rec: dict) -> tuple[dict | None, str]:
    """Apply ICP + H3 filters. Return (enriched record, '') or (None, reason)."""
    flags = []
    if rec["status"] and rec["status"] != "Active":
        return None, f"status:{rec['status']}"
    if rec["country"] is None:
        flags.append("geo_unknown")
    elif rec["country"] not in ALLOWED:
        return None, f"geo:{rec['country']}"
    hc = rec["headcount"]
    if hc is None:
        flags.append("headcount_unknown")
    elif not HEADCOUNT_MIN <= hc <= HEADCOUNT_MAX:
        return None, f"headcount:{hc}"
    keep, label, why = sector_decision(rec["industry"], rec["sub"], rec["tags"], rec["text"])
    if not keep:
        return None, why
    for f in rec["founders"]:
        f["tech_class"], f["is_gtm"], f["evidence"] = classify_founder(f["title"], f["bio"])
    tier, note, gtm = h3_verdict(rec["founders"])
    if tier is None:
        return None, f"h3:{note}"
    rec.update(sector=label, h3_tier=tier, h3_note=note, gtm_founders=gtm, icp_flags=flags,
               open_gtm_jobs=[j for j in rec["jobs"] if GTM_JOB.search(j)])
    return rec, ""


def merge(a: dict, b: dict) -> dict:
    """Merge two passing records for the same domain; higher-quality source wins on fields."""
    p, s = sorted([a, b], key=lambda r: (SOURCE_PRIORITY[r["source"]], TIER_RANK[r["h3_tier"]]))
    p = dict(p)
    p["cohorts"] = sorted(set(p.get("cohorts", [p["cohort"]]) + s.get("cohorts", [s["cohort"]])))
    p["sources"] = sorted(set(p.get("sources", [p["source"]]) + s.get("sources", [s["source"]])))
    p["programs"] = sorted(set(p.get("programs", [p["source_program"]]) + s.get("programs", [s["source_program"]])))
    for k in ("headcount", "country", "city", "one_liner"):
        if not p.get(k) and s.get(k):
            p[k] = s[k]
    if not p["founders"] and s["founders"]:
        p["founders"], p["h3_tier"], p["h3_note"] = s["founders"], s["h3_tier"], s["h3_note"]
    p["icp_flags"] = [f for f in p["icp_flags"] if not (f == "headcount_unknown" and p["headcount"] is not None)
                      and not (f == "geo_unknown" and p["country"])]
    return p


CSV_COLS = ["company_domain", "company_name", "website", "one_liner", "sector", "hq_city", "hq_country",
            "source_programs", "batch_or_investor", "cohort_year", "headcount", "gtm_headcount_founders",
            "open_gtm_job_titles", "is_hiring", "h3_tier", "h3_fit_note", "founders_count", "founders",
            "founder_linkedin_urls", "founder_x_handles", "icp_flags", "source_url"]


def main() -> None:
    stats: dict = {}
    conn = pdb.init_db()
    now = pdb.utcnow()
    passes: dict[str, dict] = {}
    rejected: list[dict] = []
    per_source = {}

    for key, label, fn in ADAPTERS:
        run_id = pdb.start_run(conn, "H3", key, label, str((RAW).relative_to(pdb.ROOT)))
        recs = fn(stats)
        raw_n, icp_n, domains = len(recs), 0, set()
        for rec in recs:
            dom = pdb.normalize_domain(rec["website"])
            if dom is None:
                rejected.append({"source": key, "cohort": rec["cohort"], "company_name": rec["name"],
                                 "website": rec["website"] or "", "reason": "no_usable_domain"})
                continue
            domains.add(dom)
            ev, why = evaluate(rec)
            if ev is None:
                rejected.append({"source": key, "cohort": rec["cohort"], "company_name": rec["name"],
                                 "website": rec["website"] or "", "reason": why, "company_domain": dom})
                continue
            icp_n += 1
            ev["company_domain"] = dom
            passes[dom] = merge(passes[dom], ev) if dom in passes else ev
        expected = None
        if key == "yc":
            expected = sum(v["yc_oss"] for v in stats["yc_crosscheck"].values())
        pdb.finish_run(conn, run_id, expected_total=expected, pages_fetched=1, records_raw=raw_n,
                       records_unique=len(domains), records_icp_pass=icp_n, stop_reason="exhausted")
        per_source[key] = {"raw": raw_n, "unique_domains": len(domains), "pass": icp_n}

    # ---------------------------------------------------------------- DB upsert
    for dom, r in passes.items():
        cohorts = r.get("cohorts", [r["cohort"]])
        pdb.upsert_company(
            conn, dom, "H3",
            proof={"source": ",".join(r.get("sources", [r["source"]])), "url": r["source_url"],
                   "snippet": f"{'; '.join(cohorts)} | {r['h3_tier']} | {r['h3_note']}"},
            company_name=r["name"], one_liner=r["one_liner"][:500], hq_country=r["country"],
            sector=r["sector"], batch_or_investor="; ".join(cohorts), headcount=r["headcount"],
            gtm_headcount=r["gtm_founders"],
        )
        for f in r["founders"]:
            if not f["name"]:
                continue
            pdb.upsert_founder(
                conn, dom, f["name"], role=f["title"] or None,
                is_technical={"technical": 1, "non_technical": 0}.get(f["tech_class"]),
                linkedin_url=f["linkedin"], x_handle=x_handle(f["x"]), source_url=r["source_url"],
            )
    conn.commit()

    # ---------------------------------------------------------------- CSV export
    rows = []
    for dom, r in passes.items():
        cohorts = r.get("cohorts", [r["cohort"]])
        rows.append({
            "company_domain": dom, "company_name": r["name"], "website": r["website"],
            "one_liner": " ".join(r["one_liner"].split())[:300], "sector": r["sector"],
            "hq_city": r["city"], "hq_country": r["country"] or "",
            "source_programs": "; ".join(r.get("programs", [r["source_program"]])),
            "batch_or_investor": "; ".join(cohorts), "cohort_year": r["cohort_year"],
            "headcount": "" if r["headcount"] is None else r["headcount"],
            "gtm_headcount_founders": r["gtm_founders"],
            "open_gtm_job_titles": " | ".join(r["open_gtm_jobs"]),
            "is_hiring": "" if r["is_hiring"] is None else str(bool(r["is_hiring"])).lower(),
            "h3_tier": r["h3_tier"], "h3_fit_note": r["h3_note"], "founders_count": len(r["founders"]),
            "founders": "; ".join(f"{f['name']} ({f['title'] or 'Founder'}) [{f['tech_class']}]" for f in r["founders"]),
            "founder_linkedin_urls": " | ".join(f["linkedin"] for f in r["founders"] if f["linkedin"]),
            "founder_x_handles": " | ".join("@" + h for f in r["founders"] if (h := x_handle(f["x"]))),
            "icp_flags": ",".join(r["icp_flags"]), "source_url": r["source_url"],
        })
    rows.sort(key=lambda x: (TIER_RANK[x["h3_tier"]], -x["cohort_year"], x["company_name"].lower()))
    with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CSV_COLS)
        w.writeheader()
        w.writerows(rows)
    with open(REJ_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["source", "cohort", "company_name", "company_domain", "website", "reason"])
        w.writeheader()
        w.writerows(sorted(rejected, key=lambda x: (x["source"], x["cohort"], x["company_name"].lower())))

    report = {"generated_at": now, "per_source": per_source, "exported": len(rows),
              "rejected": len(rejected), "yc_crosscheck": stats["yc_crosscheck"],
              "tiers": Counter(r["h3_tier"] for r in rows),
              "reject_reasons": Counter(r["reason"].split(":")[0] + ":" + r["reason"].split(":")[1].split("(")[0].strip()
                                        if ":" in r["reason"] else r["reason"] for r in rejected).most_common()}
    (RAW / "h3_run_report.json").write_text(json.dumps(report, indent=2, default=list) + "\n")
    print(json.dumps({k: v for k, v in report.items() if k != "yc_crosscheck"}, indent=2, default=list))


if __name__ == "__main__":
    main()
