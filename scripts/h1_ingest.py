"""H1 job arbitrage: companies actively trying to hire GTM / growth / sales / marketing.

Usage: python3 scripts/h1_ingest.py

Reads only the raw snapshots under raw_sources/H1_job_arbitrage/ (see h1_snapshot.py and
h1_ats_probe.py). Steps:

1. Universe = every YC company flagged hiring (all batches, refreshed daily) + every company
   already in db/gtm_pipeline.db (H3) + any board found by the live ATS probe.
2. Every open posting is classified by a role taxonomy that reads past the literal title:
     S1 direct_gtm     sales / marketing / growth / revops / partnerships titles
     S1 exact_role     GTM Engineer, Growth Engineer, Growth Hacker, RevOps/Marketing Engineer
     S1 hidden_gtm     "Founding Hustler", "First Business Hire", "Head of Everything",
                       "Founding Partner (Sales)", "Business Lead", DevRel at DevTools/Infra
     S2 adjacent_gtm   presales/solutions, customer success/AM, chief of staff/bizops/generalist,
                       DevRel outside DevTools/Infra
     S3 paradox        any other non-engineering role at a 3-20 person, 100%-technical
                       (H3 A_verified), 0-GTM-founder team with no GTM role posted
   Internships are downgraded one level (an intern budget is not a GTM-hire budget).
3. ICP: Active, not Growth-stage, US/UK/EU (unknown kept + flagged), headcount 2-35 (unknown
   kept + flagged), B2B sector rules shared with H3, YC batch >= 2022 (2022-2024 flagged
   funding_window_unverified: no funding data yet, see H2).
4. Merge into the DB as H1 (idempotent, one proof per posting). Companies first seen here that
   pass the H3 founder rules on evidence (tier A/B) are also tagged H3.
5. Export sublists (company rows, posting rows, rejects, ATS watchlist) and the master ranking
   sheet sublists/h1_x_h3_priority.csv.
"""
from __future__ import annotations

import csv
import gzip
import io
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pipeline_db as pdb  # noqa: E402
import h3_ingest as h3  # noqa: E402  (shared geo / sector / founder rules)

RAW = pdb.RAW_DIR / "H1_job_arbitrage"
OUT = pdb.SUBLIST_DIR
H3_CSV = OUT / "sublist_H3_technical_founders.csv"

HEADCOUNT_MIN, HEADCOUNT_MAX = 2, 35
PARADOX_MIN, PARADOX_MAX = 3, 20
MIN_BATCH_YEAR = 2022
RETAINER_LOW, RETAINER_HIGH = 3_000 * 12, 5_000 * 12

# --------------------------------------------------------------------------- role taxonomy
R = lambda p: re.compile(p, re.I)  # noqa: E731
INTERN = R(r"\b(intern(ship)?s?|co-?op|summer (analyst|associate)|working student|werkstudent)\b")
RECRUITING = R(r"\b(recruit\w*|sourcer|talent (partner|acquisition|lead)|head of people|people (ops|partner)|hr\b|hrbp)")
EXACT_ROLE = R(r"\b((gtm|go[- ]to[- ]market|growth|revops|rev ops|revenue operations|marketing|outbound|"
               r"lifecycle|demand gen\w*)\s+(engineer\w*|automation|architect|systems)|growth hack\w*|"
               r"gtm (builder|automation|systems|technologist)|clay (expert|specialist|engineer|operator))\b")
HIDDEN_GTM = R(r"\b(hustler|first (business|commercial|non[- ]?technical|gtm|sales|growth|go[- ]to[- ]market) hire|"
               r"head of everything|founding (business|commercial|partner|biz)\b|(business|commercial) lead|"
               r"founding team[^,]*(business|commercial)|founding member[^,]*(business|commercial)|"
               r"member of non-?technical staff|nontechnical staff|non-technical (founding|generalist|hire))")
GTM_GENERAL = R(r"\b(gtm|go[- ]to[- ]market)\b")
NOT_A_ROLE = R(r"general application|open application|talent (pool|network|community)|former founders|"
               r"don.?t see (a|the|your) role|spontaneous application")
PAUSED = R(r"[\[(](paused|closed|filled|on hold)[\])]")
ENGINEER_NOUN = R(r"\b(engineer\w*|developer|scientist|researcher|technician|firmware|hardware|backend|frontend|"
                  r"full[- ]?stack|mathematical staff|mathematician|technical staff|robotics operator)\b")
LEGAL_FIN = R(r"\b(attorney|counsel|lawyer|paralegal|legal|accountant|controller|bookkeeper|actuary|"
              r"compliance|tax|payroll|underwriter|claims)\b")
TECH_CUSTOMER = R(r"\b(customer success engineer|technical account manager|support engineer)\b")
DEVREL = R(r"\b(developer (advocate|advocacy|relations|evangelist|community|marketing|experience advocate|"
           r"success)|devrel|dev rel|technical evangelist|evangelist|community (engineer|advocate))")
PRESALES = R(r"\b((sales|solutions?|pre-?sales|customer|value|partner) (engineer|architect|consultant)\w*|"
             r"solutions? (lead|manager|specialist|strategist)|demo engineer)")
SALES = R(r"\b(account executive|\bae\b|sdr|bdr|sales|business development|biz ?dev|bdm|closer|deal desk|"
          r"territory (manager|rep\w*)|account (director|lead)|head of revenue|chief revenue|\bcro\b|"
          r"revenue (lead|manager|director)|commercial (manager|director|lead|executive)|enterprise account|"
          r"seller|new business|inside sales|channel sales)")
REVOPS = R(r"\b(rev(enue)? ?ops|revenue operations|sales op(s|erations)|gtm (ops|operations|strategy|"
           r"analyst)|marketing op(s|erations)|crm (manager|admin\w*|"
           r"specialist|lead)|hubspot|salesforce admin\w*)")
MARKETING = R(r"\b(market(ing|er)|demand gen\w*|content (marketer|marketing|strategist|lead|writer|creator|"
              r"manager|producer)|brand (marketer|manager|lead|strategist)|seo\b|aeo\b|geo specialist|lifecycle|"
              r"performance (marketer|marketing)|paid (media|social|acquisition|search)|social media|"
              r"communications|\bpr\b|public relations|events? (manager|lead|marketer|coordinator)|copywriter|"
              r"\bugc\b|influencer|creator (partnerships|program)|user acquisition|community (manager|lead|"
              r"builder|growth)|ambassador|storyteller|head of (comms|brand|content|community)|brand (&|and) content|"
              r"creative strateg\w*)")
GROWTH = R(r"\bgrowth\b(?! stage)")
PARTNERSHIPS = R(r"\b(partnerships?|alliances?|channel (manager|partner\w*|lead)|partner (manager|success|"
                 r"marketing|development))")
CUSTOMER = R(r"\b(customer (success|experience|onboarding|growth|outcomes)|client (success|partner|relations)|"
             r"account manag\w*|implementation (manager|specialist|lead|consultant|strategist)|onboarding "
             r"(manager|specialist|lead)|forward[- ]deployed (strategist|operator|deployment|gtm|account|"
             r"business|product)|deployment strategist|engagement manager|success (manager|lead))")
BIZOPS = R(r"\b(chief of staff|business operations|bizops|biz ops|strateg(y|ic) (&|and) operations|strategic "
           r"operations|founding operator|founder.?s? associate|founders associate|generalist|"
           r"special projects|general manager|\bgm\b|(entrepreneur|founder)[- ]in[- ]residence|"
           r"business associate|strategy (lead|associate|manager))")
ENGINEERING = R(r"\b(engineer\w*|developer|scientist|researcher|research (associate|lead)|member of (the )?"
                r"technical staff|\bmts\b|architect|devops|\bsre\b|programmer|technician|machinist|firmware|"
                r"\bqa\b|tester|data analyst|analyst, data|ml\b|machine learning|\bai (trainer|tutor)|"
                r"annotator|cto|tech lead|technical lead|hacker|mathematical staff)")
SENIOR = R(r"\b(vp|vice president|chief|head of|director|cro|cmo)\b")
NON_ENG_ROLES = {"Operations", "Product", "Design", "Support", "Recruiting & HR", "Finance", "Legal"}

STRENGTH_ORDER = {"S1": 0, "S2": 1, "S3": 2}
DOWNGRADE = {"S1": "S2", "S2": "S3", "S3": None}


def classify_job(title: str, role_field: str | None, sector: str) -> dict:
    """Return {family, strength, why}. strength None = not a GTM signal on its own."""
    t = re.sub(r"\s+", " ", title or "").strip()
    rf = role_field or ""
    fam = strength = None
    why = ""
    if NOT_A_ROLE.search(t):
        fam, why = "not_a_role", "catch-all application, not a role"
    elif RECRUITING.search(t) or rf == "Recruiting & HR":
        fam, why = "recruiting", "people/recruiting role" + (" (GTM recruiter = GTM team scaling)"
                                                           if re.search(r"gtm|go to market|sales", t, re.I) else "")
    elif EXACT_ROLE.search(t):
        fam, strength, why = "exact_role", "S1", f"exact role match ('{EXACT_ROLE.search(t).group(0)}')"
    elif DEVREL.search(t):
        dev_facing = sector in ("DevTools", "Infra")
        fam, strength = "devrel", "S1" if dev_facing else "S2"
        why = f"DevRel {'is the GTM motion at a ' + sector + ' company' if dev_facing else 'outside dev-tools'}"
    elif PRESALES.search(t):
        fam, strength, why = "presales", "S2", "technical sales capacity (deals in motion)"
    elif TECH_CUSTOMER.search(t):
        fam, strength, why = "customer", "S2", "technical post-sale role (expansion / retention)"
    elif LEGAL_FIN.search(t) or rf in ("Legal", "Finance"):
        fam, why = "other_non_eng", f"legal/finance role ({rf or 'title'})"
    elif ENGINEER_NOUN.search(t):
        fam, why = "engineering", f"engineering/research role ('{ENGINEER_NOUN.search(t).group(0)}')"
    elif HIDDEN_GTM.search(t):
        fam, strength, why = "hidden_gtm", "S1", f"unlabeled first-GTM hire ('{HIDDEN_GTM.search(t).group(0)}')"
    elif REVOPS.search(t):
        fam, strength, why = "revops", "S1", "GTM systems / RevOps"
    elif SALES.search(t) and not re.search(r"revenue cycle|sales tax", t, re.I):
        fam, strength, why = "sales", "S1", "sales hire"
    elif GTM_GENERAL.search(t):
        fam, strength, why = "gtm_generalist", "S1", "generalist GTM owner (GTM lead / founding GTM)"
    elif PARTNERSHIPS.search(t):
        fam, strength, why = "partnerships", "S1", "partnerships / BD"
    elif MARKETING.search(t):
        fam, strength, why = "marketing", "S1", "marketing hire"
    elif GROWTH.search(t):
        fam, strength, why = "growth", "S1", "growth hire"
    elif CUSTOMER.search(t):
        fam, strength, why = "customer", "S2", "post-sale GTM (success / account mgmt / implementation)"
    elif BIZOPS.search(t):
        fam, strength, why = "bizops_generalist", "S2", "generalist / chief-of-staff (usually owns early GTM)"
    elif ENGINEERING.search(t) or rf in ("Engineering", "Science"):
        fam, why = "engineering", "engineering/research role"
    elif rf == "Sales":
        fam, strength, why = "sales", "S2", "ATS role field = Sales, title non-standard (lower confidence)"
    elif rf == "Marketing":
        fam, strength, why = "marketing", "S2", "ATS role field = Marketing, title non-standard (lower confidence)"
    else:
        fam, why = "other_non_eng", f"non-engineering role ({rf or 'unlabeled'})"
    intern = bool(INTERN.search(t))
    if intern and strength:
        strength = DOWNGRADE[strength]
        why += "; intern -> downgraded"
    return {"family": fam, "strength": strength, "why": why, "intern": intern,
            "senior": bool(SENIOR.search(t)) and fam not in ("engineering",)}


# --------------------------------------------------------------------------- helpers
def gz_json(path: Path):
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return json.load(f)


def gz_csv(path: Path) -> list[dict]:
    with gzip.open(path, "rb") as f:
        return list(csv.DictReader(io.TextIOWrapper(f, encoding="utf-8", newline="")))


def norm(s: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def money_k(s: str) -> list[float]:
    out = []
    for num, unit in re.findall(r"(\d+(?:\.\d+)?)\s*([kKmM]?)", s.replace(",", "")):
        v = float(num) * (1_000 if unit.lower() == "k" else 1_000_000 if unit.lower() == "m" else 1)
        if v >= 10_000:
            out.append(v)
    return out


def salary_mid(s: str | None) -> float | None:
    if not s or "$" not in s:
        return None
    vals = money_k(s)
    return sum(vals[:2]) / len(vals[:2]) if vals else None


def batch_year(batch: str | None) -> int | None:
    m = re.search(r"(20\d\d)", batch or "")
    return int(m.group(1)) if m else None


def batch_short(batch: str | None) -> str:
    m = re.match(r"(Winter|Spring|Summer|Fall) (20\d\d)", batch or "")
    if not m:
        return f"YC {batch or 'unspecified'}"
    return f"YC {dict(Winter='W', Spring='X', Summer='S', Fall='F')[m.group(1)]}{m.group(2)[2:]}"


# --------------------------------------------------------------------------- hooks
FAMILY_HOOK = {
    "exact_role": "They are hiring the exact job you do. Pitch a 60-90 day retainer as the bridge while they search, "
                  "or yourself as the hire - you ship the stack on day 1 instead of after a 3-month search.",
    "hidden_gtm": "'{title}' is an unlabeled first GTM hire. A GTM Engineer is the systematized version: "
                  "founder-led-sales support + automated outbound + CRM/attribution plumbing, without "
                  "needing a second hire to build the tools.",
    "sales": "An {title} without pipeline infrastructure spends the ramp prospecting by hand. A GTM Engineer builds "
             "the engine first (ICP list -> enrichment -> signal-based sequencing -> CRM) so the founders/AE "
             "only close.",
    "sdr": "SDR/BDR seats are manual pipeline generation. A GTM Engineer automates list-building, enrichment and "
           "personalized sequencing - the output of several SDR seats for one retainer.",
    "marketing": "A '{title}' hire on an all-technical team usually lands without data plumbing. A GTM Engineer "
                 "ships programmatic SEO/content, intent signals and lifecycle automation wired into the CRM.",
    "growth": "'{title}' = they want measurable acquisition. A technical GTM Engineer runs growth as code: "
              "experiments, enrichment, outbound and attribution in one owner.",
    "gtm_generalist": "'{title}' = they want one person to own GTM end to end. A GTM Engineer is that owner with "
                      "the tooling built in: ICP data, outbound automation, CRM and attribution from week one.",
    "revops": "A RevOps posting means CRM/tooling pain is already felt. A GTM Engineer covers RevOps and builds "
              "the pipeline the CRM is supposed to track.",
    "partnerships": "A partnerships/BD hire implies a channel motion. A GTM Engineer instruments partner sourcing, "
                    "enrichment and co-selling workflows so one person can run it.",
    "devrel": "DevRel is the GTM motion for {sector}. A technical GTM Engineer pairs developer content with "
              "signal-based outbound to devs who star/fork/install, and converts usage into pipeline.",
    "presales": "A technical-sales hire signals deals in motion with no pre-sales capacity. A GTM Engineer "
                "covers demo/POC tooling and builds the top-of-funnel those deals need.",
    "customer": "A post-sale hire means paying customers exist but expansion is manual. A GTM Engineer wires "
                "product-usage signals into the CRM, expansion plays and referral loops.",
    "bizops_generalist": "'{title}' at this stage usually owns GTM by default. Pitch a GTM Engineer as the "
                         "focused version: revenue systems first, ops second.",
    "paradox": "{hc}-person, 100% technical team hiring a non-engineering '{title}' with no GTM role posted: "
               "the GTM function is unowned. Pitch a GTM Engineer as the higher-leverage first non-eng hire.",
}


def make_hook(rec: dict, best: dict) -> str:
    fam = best["family"] if best["strength"] != "S3" else "paradox"
    if fam == "sales" and re.search(r"\b(sdr|bdr|sales development|business development rep)", best["title"], re.I):
        fam = "sdr"
    base = FAMILY_HOOK.get(fam, FAMILY_HOOK["bizops_generalist"]).format(
        title=best["title"].strip(), sector=rec.get("sector") or "this company", hc=rec.get("headcount") or "?")
    ctx = []
    if rec.get("headcount"):
        ctx.append(f"team of {rec['headcount']}")
    if rec.get("h3_tier") == "A_verified":
        ctx.append("all founders technical")
    if rec.get("gtm_founders") == 0:
        ctx.append("no GTM founder")
    n = len(rec["gtm_jobs"])
    if n > 1:
        ctx.append(f"{n} GTM-type roles open at once")
    if best.get("senior"):
        ctx.append("leader hire with no team under them yet")
    mid = salary_mid(best.get("salary_range"))
    if mid:
        lo, hi = RETAINER_LOW / mid * 100, RETAINER_HIGH / mid * 100
        ctx.append(f"posted cash {best['salary_range']} -> a $3-5K/mo retainer is ~{lo:.0f}-{hi:.0f}% of it")
    ceo = rec.get("ceo_name")
    tail = f" Context: {'; '.join(ctx)}." if ctx else ""
    who = f" Pitch to {ceo}." if ceo else ""
    return f"{base}{tail}{who}"


# --------------------------------------------------------------------------- sources
def load_h3_rows() -> dict[str, dict]:
    if not H3_CSV.exists():
        return {}
    with open(H3_CSV, newline="") as f:
        return {r["company_domain"]: r for r in csv.DictReader(f)}


def founders_from_yc(c: dict) -> list[dict]:
    fs = [{"name": f["full_name"], "title": f.get("title") or "", "bio": f.get("founder_bio") or "",
           "linkedin": f.get("linkedin_url"), "x": f.get("x_url")}
          for f in c.get("founders") or [] if f.get("is_active", True) and f.get("full_name")]
    for f in fs:
        f["tech_class"], f["is_gtm"], f["evidence"] = h3.classify_founder(f["title"], f["bio"])
    return fs


def pick_ceo(founders: list[dict]) -> dict | None:
    for f in founders:
        if re.search(r"\bceo\b|chief executive", f.get("title") or f.get("role") or "", re.I):
            return f
    return founders[0] if founders else None


def harvest_yc(stats: dict, rejects: list) -> dict[str, dict]:
    data = gz_json(RAW / "yc_waas" / "hiring.json.gz")
    meta = gz_json(RAW / "yc_waas" / "meta.json.gz")
    stats["yc"] = {"feed_companies": len(data), "meta_hiring_count": meta["companies"]["hiring"]["count"],
                   "feed_last_updated": meta.get("last_updated"),
                   "companies_with_jobs": sum(1 for c in data if c.get("jobs")),
                   "jobs_total": sum(len(c.get("jobs") or []) for c in data)}
    out: dict[str, dict] = {}
    for c in data:
        name, jobs = c.get("name"), c.get("jobs") or []

        def rej(reason, domain=None):
            rejects.append({"source": "yc_waas", "company_name": name, "company_domain": domain or "",
                            "batch": c.get("batch"), "headcount": c.get("team_size"), "open_jobs": len(jobs),
                            "gtm_like_titles": " | ".join(j["title"] for j in jobs
                                                          if classify_job(j["title"], j.get("role"), "")["strength"]),
                            "reason": reason, "source_url": c.get("url"), "stage": "1_icp_filter"})
        domain = pdb.normalize_domain(c.get("website"))
        if not jobs:
            rej("no open postings in feed", domain)
            continue
        if not domain:
            rej(f"no usable company domain ({c.get('website')!r})")
            continue
        if c.get("status") != "Active":
            rej(f"status:{c.get('status')}", domain)
            continue
        if c.get("stage") == "Growth":
            rej("yc_stage:Growth (past Series A)", domain)
            continue
        yr = batch_year(c.get("batch"))
        flags = []
        if yr is None:
            flags.append("batch_unspecified")
        elif yr < MIN_BATCH_YEAR:
            rej(f"batch:{c.get('batch')} (<{MIN_BATCH_YEAR}; Seed-Series A in May25-Nov26 window implausible)", domain)
            continue
        elif yr < 2025:
            flags.append("funding_window_unverified")
        country = c.get("country") or h3.country_from_text(c.get("all_locations"))
        if country is None:
            flags.append("geo_unknown")
        elif country not in h3.ALLOWED:
            rej(f"geo:{country}", domain)
            continue
        hc = c.get("team_size")
        if hc is None:
            flags.append("headcount_unknown")
        elif not HEADCOUNT_MIN <= hc <= HEADCOUNT_MAX:
            rej(f"headcount:{hc}", domain)
            continue
        text = f"{c.get('one_liner') or ''} {c.get('long_description') or ''}"
        keep, sector, why = h3.sector_decision(c.get("industry") or "", c.get("subindustry") or "",
                                               c.get("tags") or [], text)
        if not keep:
            rej(why, domain)
            continue
        founders = founders_from_yc(c)
        tier, note, gtm = h3.h3_verdict(founders)
        rec = {
            "company_domain": domain, "company_name": name, "website": c.get("website"),
            "one_liner": c.get("one_liner") or "", "sector": sector, "hq_city": c.get("city") or "",
            "hq_country": country, "batch_or_investor": batch_short(c.get("batch")), "cohort_year": yr,
            "headcount": hc, "founders": founders, "h3_tier": tier or "X_has_non_technical_founder",
            "h3_note": note, "gtm_founders": gtm, "icp_flags": flags, "source_url": c.get("url"),
            "sources": {"yc_waas"},
            "jobs": [{"title": j.get("title") or "", "role_field": j.get("role"), "url": j.get("url"),
                      "location": j.get("location") or "", "salary_range": j.get("salary_range") or "",
                      "equity_range": j.get("equity_range") or "", "experience": j.get("experience") or "",
                      "type": j.get("type") or "", "source": "yc_waas"} for j in jobs],
        }
        if domain in out:  # two YC listings on one domain: union postings
            out[domain]["jobs"] += rec["jobs"]
            stats["yc"]["domain_collisions"] = stats["yc"].get("domain_collisions", 0) + 1
        else:
            out[domain] = rec
    return out


def db_universe(conn, h3_rows: dict[str, dict]) -> dict[str, dict]:
    """Companies already in the DB (H3) that are not covered by the YC hiring feed."""
    out = {}
    for r in conn.execute("SELECT * FROM companies"):
        d = r["company_domain"]
        h = h3_rows.get(d, {})
        founders = [{"name": f["full_name"], "title": f["role"] or "", "linkedin": f["linkedin_url"],
                     "x": f["x_handle"], "tech_class": {1: "technical", 0: "non_technical"}.get(f["is_technical"],
                                                                                              "unknown")}
                    for f in conn.execute("SELECT * FROM founders WHERE company_domain=?", (d,))]
        out[d] = {
            "company_domain": d, "company_name": r["company_name"], "website": h.get("website") or f"https://{d}",
            "one_liner": r["one_liner"] or "", "sector": r["sector"] or "", "hq_city": h.get("hq_city", ""),
            "hq_country": r["hq_country"], "batch_or_investor": r["batch_or_investor"],
            "cohort_year": int(h["cohort_year"]) if h.get("cohort_year") else None, "headcount": r["headcount"],
            "founders": founders, "h3_tier": h.get("h3_tier") or "", "h3_note": h.get("h3_fit_note") or "",
            "gtm_founders": int(h["gtm_headcount_founders"]) if h.get("gtm_headcount_founders") else 0,
            "icp_flags": [f for f in (h.get("icp_flags") or "").split("|") if f], "source_url": h.get("source_url"),
            "sources": set(), "jobs": [],
        }
    return out


def attach_open_jobs_data(universe: dict, stats: dict) -> None:
    comps = gz_json(RAW / "open_jobs_data" / "companies.json.gz")
    jobs = gz_json(RAW / "open_jobs_data" / "jobs.json.gz")
    by_name = defaultdict(list)
    for j in jobs:
        by_name[norm(j["company"])].append(j)
    matched, collisions = 0, []
    for c in comps:
        key = norm(c["name"])
        for d, rec in universe.items():
            # strict: same display name AND the company's own domain label is the board slug
            if norm(rec["company_name"]) == key and norm(d.split(".")[0]) == norm(c["slug"]):
                n_post, hc = len(by_name[key]), rec.get("headcount")
                if n_post > HEADCOUNT_MAX or (hc and n_post > 2 * hc + 5) or hc is None:
                    collisions.append(f"{c['name']} ({n_post} postings) vs {d} (headcount {hc})")
                    continue
                matched += 1
                rec["sources"].add("open_jobs_data")
                rec["jobs"] += [{"title": j["title"], "role_field": j.get("department"), "url": j["applyUrl"],
                                 "location": "; ".join(j.get("locations") or []), "salary_range": "",
                                 "equity_range": "", "experience": "", "type": j.get("employmentType") or "",
                                 "posted_at": j.get("postedAt"), "source": f"open_jobs_data:{j['platform']}"}
                                for j in by_name[key]]
    stats["open_jobs_data"] = {"companies_tracked": len(comps), "postings": len(jobs),
                               "matched_to_universe": matched,
                               "rejected_name_collisions": collisions}


def attach_startuppy(universe: dict, stats: dict, rejects: list) -> None:
    jobs = gz_json(RAW / "startuppy_jobs" / "jobs.json.gz")
    st = Counter()
    for j in jobs:
        d = pdb.normalize_domain(j.get("website") or "")
        if (j.get("status") or "").lower() == "closed":
            st["closed"] += 1
            continue
        if d in universe:
            st["matched"] += 1
            universe[d]["sources"].add("startuppy_jobs")
            universe[d]["jobs"].append({"title": j["role"], "role_field": None, "url": j.get("url"),
                                        "location": j.get("location") or "", "salary_range": j.get("compensation") or "",
                                        "equity_range": "", "experience": "", "type": "",
                                        "posted_at": j.get("dateFound"), "source": f"startuppy:{j.get('source')}"})
        else:
            st["outside_universe"] += 1
            rejects.append({"source": "startuppy_jobs", "company_name": j.get("company"), "company_domain": d or "",
                            "batch": "", "headcount": "", "open_jobs": 1, "gtm_like_titles": j.get("role"),
                            "reason": f"not in ICP universe (headcount unknown; funding: "
                                      f"{(j.get('funding') or {}).get('summary') or 'n/a'})",
                            "source_url": j.get("url"), "stage": "1_icp_filter"})
    stats["startuppy_jobs"] = {"postings": len(jobs), **st}


def attach_ats_live(universe: dict, stats: dict) -> None:
    idx_path = RAW / "ats_live" / "index.json"
    status_path = RAW / "ats_live" / "probe_status.json"
    stats["ats_live"] = json.loads(status_path.read_text()) if status_path.exists() else {"status": "not_run"}
    if not idx_path.exists():
        return
    index = json.loads(idx_path.read_text())
    used = 0
    for d, boards in index.items():
        if d not in universe:
            continue
        for b in boards:
            if b["confidence"] == "slug_only":
                continue
            payload = gz_json(pdb.ROOT / b["raw_path"])
            items = payload if isinstance(payload, list) else payload.get("jobs") or []
            for j in items:
                if b["provider"] == "ashby" and j.get("isListed") is False:
                    continue
                title = j.get("title") or j.get("text") or ""
                url = j.get("jobUrl") or j.get("absolute_url") or j.get("hostedUrl") or j.get("url") or j.get("shortlink")
                loc = j.get("location") or (j.get("categories") or {}).get("location") or ""
                if isinstance(loc, dict):
                    loc = loc.get("name", "")
                comp = (j.get("compensation") or {}).get("compensationTierSummary") if b["provider"] == "ashby" else ""
                universe[d]["jobs"].append({"title": title, "role_field": j.get("department"), "url": url,
                                            "location": loc, "salary_range": comp or "", "equity_range": "",
                                            "experience": "", "type": j.get("employmentType") or "",
                                            "posted_at": j.get("publishedAt") or j.get("updated_at"),
                                            "source": f"ats_live:{b['provider']}"})
                used += 1
            universe[d]["sources"].add(f"ats_live:{b['provider']}")
    stats["ats_live"]["postings_used"] = used


def ats_discovery(universe: dict, stats: dict) -> dict[str, dict]:
    """Map universe domains to known live ATS boards (open-ats-feed index, as of its snapshot)."""
    boards = gz_csv(RAW / "open_ats_feed" / "all.csv.gz")
    census = {(b["p"], b["t"]): b["titles"] for b in gz_json(RAW / "open_ats_feed" / "role-census-titles.json.gz")}
    by_token = defaultdict(list)
    for b in boards:
        by_token[b["token"].lower()].append(b)
    found = {}
    for d, rec in universe.items():
        label = d.split(".")[0]
        keys = {norm(rec["company_name"]): "name_eq_token"}
        if len(label) >= 5:
            keys.setdefault(label, "domain_label_eq_token")
            keys.setdefault(label.replace("-", ""), "domain_label_eq_token")
        for k, how in keys.items():
            if k and k in by_token:
                b = by_token[k][0]
                found[d] = {"ats_provider": b["provider"], "ats_slug": b["token"],
                            "ats_open_postings": int(b["open_postings"] or 0), "ats_match": how,
                            "ats_census_titles": census.get((b["provider"], b["token"]), [])}
                break
    stats["open_ats_feed"] = {"live_boards_indexed": len(boards), "universe_domains_mapped": len(found)}
    return found


def getro_watchlist(universe: dict, stats: dict) -> tuple[dict, list]:
    rows = gz_csv(RAW / "getro_portfolios" / "getro_portfolios.csv.gz")
    ctx, new = {}, []
    for r in rows:
        d = pdb.normalize_domain(r.get("Domain"))
        n = int(r["Active Jobs"] or 0) if (r.get("Active Jobs") or "").isdigit() else 0
        if not d:
            continue
        if d in universe:
            ctx[d] = {"getro_fund": r["Fund"], "getro_active_jobs": n}
            continue
        hc = int(r["Head Count"]) if (r.get("Head Count") or "").isdigit() else None
        country = h3.country_from_text(r.get("Locations"))
        if (n > 0 and r["Stage"] in ("pre_seed", "seed", "series_a") and hc is not None
                and HEADCOUNT_MIN <= hc <= HEADCOUNT_MAX and (country in h3.ALLOWED)):
            keep, sector, _ = h3.sector_decision(r.get("Industries") or "", "", [], r.get("Description") or "")
            if keep:
                new.append({"company_domain": d, "company_name": r["Company"], "source": f"getro:{r['Fund']}",
                            "stage": r["Stage"], "headcount": hc, "hq_country": country, "sector": sector,
                            "open_postings": n, "as_of": "2026-08-22 (getro snapshot)",
                            "next_step": "titles not in feed - resolve with h1_ats_probe.py once ATS hosts allowed"})
    stats["getro"] = {"rows": len(rows), "universe_context": len(ctx), "new_icp_candidates_titles_unknown": len(new)}
    return ctx, new


# --------------------------------------------------------------------------- evaluation
def evaluate(rec: dict) -> tuple[bool, str]:
    seen, classified = set(), []
    for j in rec["jobs"]:
        if PAUSED.search(j["title"] or ""):
            rec.setdefault("paused_postings", []).append(j["title"])
            continue
        key = (norm(j["title"]), j.get("url"))
        if key in seen:
            continue
        seen.add(key)
        classified.append({**j, **classify_job(j["title"], j.get("role_field"), rec.get("sector") or "")})
    rec["jobs"] = classified
    gtm = [j for j in classified if j["strength"] in ("S1", "S2")]
    other = [j for j in classified if j["family"] in ("other_non_eng", "recruiting", "bizops_generalist")
             and not j["intern"]]
    hc = rec.get("headcount")
    paradox = (not gtm and other and rec.get("h3_tier") == "A_verified" and rec.get("gtm_founders", 0) == 0
               and hc is not None and PARADOX_MIN <= hc <= PARADOX_MAX)
    if paradox:
        for j in other:
            j["strength"], j["why"] = "S3", f"paradox: {j['why']} at {hc}-person all-technical team, no GTM role"
        gtm = other
    rec["gtm_jobs"] = sorted(gtm, key=lambda j: (STRENGTH_ORDER[j["strength"]], j["family"] != "exact_role",
                                                 j["intern"]))
    if gtm:
        return True, ""
    fams = Counter(j["family"] for j in classified)
    if not classified:
        return False, "no postings found in any source"
    if set(fams) <= {"engineering"}:
        return False, f"engineering-only postings ({fams['engineering']})"
    why = []
    if rec.get("h3_tier") != "A_verified":
        why.append(f"h3_tier={rec.get('h3_tier') or 'n/a'}")
    if hc is None or not PARADOX_MIN <= hc <= PARADOX_MAX:
        why.append(f"headcount={hc}")
    if rec.get("gtm_founders"):
        why.append("has GTM founder")
    interns = sum(j["intern"] for j in classified)
    return False, (f"no GTM role; non-eng postings {dict(fams)} fail paradox rule ({', '.join(why) or 'intern-only'})"
                   + (f"; {interns} intern posting(s)" if interns else ""))


def rank(rec: dict) -> tuple[int, str]:
    best = rec["gtm_jobs"][0]
    parts = {"strength": {"S1": 30, "S2": 20, "S3": 10}[best["strength"]]}
    if best["family"] in ("exact_role", "hidden_gtm"):
        parts["exact_or_hidden_role"] = 10
    parts["h3_tier"] = {"A_verified": 10, "B_likely": 5}.get(rec.get("h3_tier"), 0)
    if rec.get("gtm_founders") == 0:
        parts["no_gtm_founder"] = 5
    hc = rec.get("headcount")
    if hc is not None:
        parts["headcount_fit"] = 5 if PARADOX_MIN <= hc <= PARADOX_MAX else 2
    if (rec.get("cohort_year") or 0) >= 2025:
        parts["recent_cohort"] = 5
    if len(rec["gtm_jobs"]) >= 2:
        parts["multi_gtm_roles"] = 3
    if "geo_unknown" in rec["icp_flags"]:
        parts["geo_unknown"] = -3
    if "funding_window_unverified" in rec["icp_flags"]:
        parts["funding_window_unverified"] = -3
    return sum(parts.values()), " ".join(f"{k}{'+' if v >= 0 else ''}{v}" for k, v in parts.items())


# --------------------------------------------------------------------------- main
CO_COLS = ["company_domain", "company_name", "website", "one_liner", "sector", "hq_city", "hq_country",
           "batch_or_investor", "cohort_year", "headcount", "h3_tier", "h3_fit_note", "gtm_founders",
           "match_strength", "match_families", "best_role_title", "best_role_url", "best_role_location",
           "best_role_comp", "best_role_reasoning", "gtm_roles_count", "gtm_role_titles", "total_open_postings",
           "engineering_postings", "gtm_engineer_hook", "ceo_name", "ceo_linkedin", "ceo_x", "founders",
           "icp_flags", "job_sources", "ats_board", "ats_open_postings_2026_09", "getro_active_jobs", "source_url"]
JOB_COLS = ["company_domain", "company_name", "title", "family", "strength", "reasoning", "intern", "senior",
            "location", "salary_range", "equity_range", "experience", "role_field", "source", "url"]
REJ_COLS = ["stage", "source", "company_name", "company_domain", "batch", "headcount", "open_jobs", "gtm_like_titles",
            "reason", "source_url"]
WATCH_COLS = ["company_domain", "company_name", "source", "stage", "headcount", "hq_country", "sector",
              "ats_provider", "ats_slug", "open_postings", "as_of", "next_step"]


def founder_str(fs: list[dict]) -> str:
    return "; ".join(f"{f['name']} ({f.get('title') or '?'}) [{f.get('tech_class', 'unknown')}]" for f in fs)


def x_of(f: dict | None) -> str:
    if not f:
        return ""
    x = f.get("x") or ""
    h = h3.x_handle(x) if "/" in x else x
    return f"@{h.lstrip('@')}" if h else ""


def write_csv(path: Path, cols: list[str], rows: list[dict]) -> None:
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def main() -> None:
    conn = pdb.init_db()
    stats: dict = {}
    rejects: list[dict] = []
    h3_rows = load_h3_rows()

    run_yc = pdb.start_run(conn, "H1", "yc_waas", "companies/hiring.json: all batches, jobs[] per company",
                           raw_path="raw_sources/H1_job_arbitrage/yc_waas")
    yc = harvest_yc(stats, rejects)
    universe = db_universe(conn, h3_rows)
    in_db_before = set(universe)
    for d, rec in yc.items():  # YC feed wins on fields (fresher); keep DB-only fields if YC lacks them
        if d in universe:
            base = universe[d]
            rec["sources"] |= base["sources"]
            if not rec["founders"]:
                rec["founders"] = base["founders"]
            if d in h3_rows:  # H3 tier already decided on the H3 run's evidence
                rec["h3_tier"], rec["h3_note"] = base["h3_tier"], base["h3_note"]
        universe[d] = rec

    run_oj = pdb.start_run(conn, "H1", "open_jobs_data", "jobs.json matched to universe by company name + slug",
                           raw_path="raw_sources/H1_job_arbitrage/open_jobs_data")
    attach_open_jobs_data(universe, stats)
    run_sp = pdb.start_run(conn, "H1", "startuppy_jobs", "jobs.json matched to universe by website domain",
                           raw_path="raw_sources/H1_job_arbitrage/startuppy_jobs")
    attach_startuppy(universe, stats, rejects)
    attach_ats_live(universe, stats)
    run_ats = pdb.start_run(conn, "H1", "open_ats_feed", "board-token index lookup for every universe domain",
                            raw_path="raw_sources/H1_job_arbitrage/open_ats_feed")
    ats = ats_discovery(universe, stats)
    run_gt = pdb.start_run(conn, "H1", "getro_portfolios", "active-job counts; ICP-filtered new candidates",
                           raw_path="raw_sources/H1_job_arbitrage/getro_portfolios")
    getro_ctx, getro_new = getro_watchlist(universe, stats)

    matched, unmatched_db = [], 0
    for d, rec in universe.items():
        ok, why = evaluate(rec)
        if ok:
            matched.append(rec)
        elif rec["jobs"] or d not in in_db_before:
            rejects.append({"source": "+".join(sorted(rec["sources"])) or "db", "company_name": rec["company_name"],
                            "company_domain": d, "batch": rec.get("batch_or_investor"), "headcount": rec.get("headcount"),
                            "open_jobs": len(rec["jobs"]), "gtm_like_titles": "", "reason": why,
                            "source_url": rec.get("source_url"), "stage": "2_role_filter"})
        else:
            unmatched_db += 1

    # ---- enrich + rank
    for rec in matched:
        ceo = pick_ceo(rec["founders"])
        rec["ceo"] = ceo
        rec["ceo_name"] = ceo["name"] if ceo else ""
        best = rec["gtm_jobs"][0]
        rec["hook"] = make_hook(rec, best)
        rec["rank_score"], rec["rank_breakdown"] = rank(rec)

    # ---- DB merge
    new_h3 = 0
    for rec in matched:
        d = rec["company_domain"]
        fields = dict(company_name=rec["company_name"], one_liner=rec["one_liner"] or None,
                      hq_country=rec["hq_country"], sector=rec["sector"] or None,
                      batch_or_investor=rec["batch_or_investor"], headcount=rec["headcount"])
        for j in rec["gtm_jobs"]:
            pdb.upsert_company(conn, d, "H1", proof={
                "source": j["source"], "url": j.get("url"),
                "snippet": f"{j['title']} [{j['family']}/{j['strength']}] {j.get('salary_range') or ''}".strip()},
                **fields)
        if d not in in_db_before:
            for f in rec["founders"]:
                pdb.upsert_founder(conn, d, f["name"], role=f.get("title") or None,
                                   is_technical={"technical": 1, "non_technical": 0}.get(f.get("tech_class")),
                                   linkedin_url=f.get("linkedin"), x_handle=(x_of(f) or None) and x_of(f)[1:],
                                   source_url=rec.get("source_url"))
            if rec["h3_tier"] in ("A_verified", "B_likely"):
                pdb.upsert_company(conn, d, "H3", proof={"source": "h1_ingest:h3_rules", "url": rec.get("source_url"),
                                                         "snippet": f"{rec['h3_tier']}: {rec['h3_note']}"})
                new_h3 += 1
    conn.commit()
    scores = {r["company_domain"]: (r["priority_score"], json.loads(r["matched_hypotheses"]))
              for r in conn.execute("SELECT company_domain, priority_score, matched_hypotheses FROM companies")}

    # ---- exports
    def co_row(rec):
        best = rec["gtm_jobs"][0]
        a = ats.get(rec["company_domain"], {})
        ceo = rec.get("ceo")
        return {
            **{k: rec.get(k) for k in ("company_domain", "company_name", "website", "one_liner", "sector", "hq_city",
                                       "hq_country", "batch_or_investor", "cohort_year", "headcount", "h3_tier",
                                       "gtm_founders", "source_url", "ceo_name")},
            "h3_fit_note": rec.get("h3_note"), "match_strength": best["strength"],
            "match_families": "|".join(sorted({j["family"] for j in rec["gtm_jobs"]})),
            "best_role_title": best["title"].strip(), "best_role_url": best.get("url"),
            "best_role_location": best.get("location"),
            "best_role_comp": " / ".join(x for x in (best.get("salary_range"), best.get("equity_range")) if x),
            "best_role_reasoning": best["why"], "gtm_roles_count": len(rec["gtm_jobs"]),
            "gtm_role_titles": " | ".join(f"{j['title'].strip()} [{j['family']}/{j['strength']}]" for j in rec["gtm_jobs"]),
            "total_open_postings": len(rec["jobs"]),
            "engineering_postings": sum(j["family"] == "engineering" for j in rec["jobs"]),
            "gtm_engineer_hook": rec["hook"], "ceo_linkedin": (ceo or {}).get("linkedin") or "", "ceo_x": x_of(ceo),
            "founders": founder_str(rec["founders"]), "icp_flags": "|".join(rec["icp_flags"]),
            "job_sources": "|".join(sorted({j["source"] for j in rec["gtm_jobs"]})),
            "ats_board": f"{a['ats_provider']}:{a['ats_slug']}" if a else "",
            "ats_open_postings_2026_09": a.get("ats_open_postings", ""),
            "getro_active_jobs": getro_ctx.get(rec["company_domain"], {}).get("getro_active_jobs", ""),
            "priority_score": scores[rec["company_domain"]][0],
            "matched_hypotheses": "|".join(scores[rec["company_domain"]][1]),
            "rank_score": rec["rank_score"], "rank_breakdown": rec["rank_breakdown"],
        }

    matched.sort(key=lambda r: (-r["rank_score"], r.get("headcount") or 99, r["company_domain"]))
    rows = [co_row(r) for r in matched]
    write_csv(OUT / "sublist_H1_job_arbitrage.csv", CO_COLS + ["priority_score", "matched_hypotheses"], rows)
    jobs_rows = [{"company_domain": r["company_domain"], "company_name": r["company_name"], **j,
                  "reasoning": j["why"], "title": j["title"].strip()} for r in matched for j in r["gtm_jobs"]]
    write_csv(OUT / "sublist_H1_job_postings.csv", JOB_COLS, jobs_rows)
    rejects.sort(key=lambda r: (r["stage"], r["source"], r["reason"], r["company_name"] or ""))
    write_csv(OUT / "sublist_H1_rejected.csv", REJ_COLS, rejects)

    master = [r for r in rows if "H3" in r["matched_hypotheses"].split("|")]
    for i, r in enumerate(master, 1):
        r["rank"] = i
    master_cols = (["rank", "rank_score", "rank_breakdown", "priority_score", "matched_hypotheses"] +
                   [c for c in CO_COLS if c not in ("website",)])
    write_csv(OUT / "h1_x_h3_priority.csv", master_cols, master)

    watch = []
    for d, a in ats.items():
        if d not in {r["company_domain"] for r in matched} and a["ats_open_postings"] > 0:
            u = universe[d]
            watch.append({"company_domain": d, "company_name": u["company_name"], "source": "db_universe",
                          "stage": "", "headcount": u.get("headcount"), "hq_country": u.get("hq_country"),
                          "sector": u.get("sector"), "ats_provider": a["ats_provider"], "ats_slug": a["ats_slug"],
                          "open_postings": a["ats_open_postings"], "as_of": "2026-09-03 (open-ats-feed)",
                          "next_step": "board known, titles not in snapshot - h1_ats_probe.py"})
    for g in getro_new:
        a = ats_discovery({g["company_domain"]: {"company_name": g["company_name"]}}, {}).get(g["company_domain"], {})
        watch.append({**g, "ats_provider": a.get("ats_provider", ""), "ats_slug": a.get("ats_slug", "")})
    write_csv(OUT / "sublist_H1_ats_watchlist.csv", WATCH_COLS, watch)

    # ---- audit
    yc_pass = sum(1 for r in matched if "yc_waas" in r["sources"])
    pdb.finish_run(conn, run_yc, expected_total=stats["yc"]["meta_hiring_count"], pages_fetched=1,
                   records_raw=stats["yc"]["feed_companies"], records_unique=len(yc), records_icp_pass=yc_pass,
                   stop_reason="exhausted")
    pdb.finish_run(conn, run_oj, expected_total=stats["open_jobs_data"]["postings"], pages_fetched=1,
                   records_raw=stats["open_jobs_data"]["postings"],
                   records_unique=stats["open_jobs_data"]["matched_to_universe"],
                   records_icp_pass=sum(1 for r in matched if "open_jobs_data" in r["sources"]), stop_reason="exhausted")
    pdb.finish_run(conn, run_sp, expected_total=stats["startuppy_jobs"]["postings"], pages_fetched=1,
                   records_raw=stats["startuppy_jobs"]["postings"],
                   records_unique=stats["startuppy_jobs"].get("matched", 0),
                   records_icp_pass=sum(1 for r in matched if "startuppy_jobs" in r["sources"]), stop_reason="exhausted")
    pdb.finish_run(conn, run_ats, expected_total=stats["open_ats_feed"]["live_boards_indexed"], pages_fetched=1,
                   records_raw=stats["open_ats_feed"]["live_boards_indexed"],
                   records_unique=stats["open_ats_feed"]["universe_domains_mapped"], records_icp_pass=0,
                   stop_reason="exhausted (discovery only; no titles)")
    pdb.finish_run(conn, run_gt, expected_total=stats["getro"]["rows"], pages_fetched=1, records_raw=stats["getro"]["rows"],
                   records_unique=stats["getro"]["new_icp_candidates_titles_unknown"], records_icp_pass=0,
                   stop_reason="exhausted (watchlist only; no titles)")

    strength = Counter(r["match_strength"] for r in rows)
    fam = Counter(f for r in rows for f in r["match_families"].split("|"))
    report = {
        "generated_at": pdb.utcnow(), "sources": stats,
        "universe": {"companies": len(universe), "already_in_db": len(in_db_before),
                     "yc_hiring_icp_candidates": len(yc), "db_companies_no_postings_visible": unmatched_db},
        "matched_companies": len(rows), "matched_postings": len(jobs_rows), "by_strength": dict(strength),
        "by_family": dict(fam), "h1_x_h3": len(master), "new_companies_added": sum(
            1 for r in matched if r["company_domain"] not in in_db_before),
        "new_companies_tagged_h3_by_rule": new_h3, "rejected_rows": len(rejects),
        "reject_reasons": dict(Counter(r["reason"].split(" (")[0].split(":")[0] for r in rejects).most_common()),
        "watchlist_rows": len(watch),
        "reconciliation": {
            "yc_feed_companies": stats["yc"]["feed_companies"],
            "yc_icp_candidates": len(yc),
            "yc_icp_rejects": sum(1 for r in rejects if r["stage"] == "1_icp_filter" and r["source"] == "yc_waas"),
            "yc_balanced": stats["yc"]["feed_companies"] == len(yc) + stats["yc"].get("domain_collisions", 0) + sum(
                1 for r in rejects if r["stage"] == "1_icp_filter" and r["source"] == "yc_waas"),
            "universe": len(universe), "matched": len(rows),
            "role_rejects": sum(1 for r in rejects if r["stage"] == "2_role_filter"),
            "db_no_postings_visible": unmatched_db,
            "universe_balanced": len(universe) == len(rows) + unmatched_db + sum(
                1 for r in rejects if r["stage"] == "2_role_filter"),
            "paused_postings_skipped": sum(len(r.get("paused_postings", [])) for r in universe.values()),
        },
    }
    (RAW / "h1_run_report.json").write_text(json.dumps(report, indent=2, default=list) + "\n")
    print(json.dumps(report, indent=2, default=list))
    conn.close()


if __name__ == "__main__":
    main()
