"""H2 fresh capital: every pre-seed / seed / Series A raise, no dollar cap.

Usage: python3 scripts/h2_ingest.py

Reads only raw_sources/H2_fresh_capital/ (see h2_snapshot.py). Builds one funding-event
ledger from every source, then decides per company:

  Event sources (confidence, strongest first)
    domain_keyed        Fliar Radar rounds (company_domain column), Form D issuer via YC CIK match
    company_attached    YC directory news[] attached to the company, headline names the company
    form_d              SEC Form D filing matched by CIK->domain or by legal-name match
    headline_name_match Daily RSS headlines (TechCrunch, Techmeme, Sifted, ...) / recent-raise lists
    program_terms       Accelerator check implied by the program (YC standard deal = $500K pre-seed)

  Stage scope: pre_seed | seed | series_a (incl. pre-Series B / extensions) | unknown-stage capital
  events. No dollar cap: amounts are recorded and banded (<$1M, $1-15M, >$15M) instead of filtered.
  Series B or later (on domain-keyed / company-attached evidence) -> disqualified as past Series A.

  H2 tag rules
    fresh_60d   any capital event dated within the last 60 days (incl. a YC check for a batch that
                started within 60 days)
    in_window   an external raise (not just the accelerator check) dated 2025-05-01 .. today
  Funding columns (funding_stage / funding_amount / funding_date) are filled for every company with
  evidence, tagged or not; funding_window_status in sublist_H2_funding_status_all.csv says why.

New companies are added only when they have a domain and pass ICP (US/UK/EU, B2B sector rules,
headcount 2-35 or unknown); everything else that looks relevant goes to the watchlist.
"""
from __future__ import annotations

import csv
import gzip
import io
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pipeline_db as pdb  # noqa: E402
import h3_ingest as h3  # noqa: E402

RAW = pdb.RAW_DIR / "H2_fresh_capital"
OUT = pdb.SUBLIST_DIR
RUN_DATE = datetime.now(timezone.utc).date()
WINDOW_START = date(2025, 5, 1)
FRESH_START = RUN_DATE - timedelta(days=60)
FX = {"$": 1.0, "€": 1.10, "£": 1.30}  # approximate USD conversion, documented in README
EARLY = ("pre_seed", "seed", "series_a")
HEADCOUNT_MIN, HEADCOUNT_MAX = 2, 35
YC_SEASON_START = {"Winter": (1, 1), "Spring": (4, 1), "Summer": (7, 1), "Fall": (10, 1)}
YC_STANDARD_DEAL = 500_000
CONF_RANK = {"domain_keyed": 0, "company_attached": 1, "form_d": 2, "company_attached_unnamed": 3,
             "headline_name_match": 4, "program_terms": 5}
RELIABLE = {"domain_keyed", "company_attached"}

# --------------------------------------------------------------------------- text parsing
RAISE = re.compile(r"\b(raises?|raised|raising|lands?|landed|secures?|secured|closes?|closed|nabs?|bags?|banks?|"
                   r"snags?|gets?|scores?|collects?|pulls? in|completes?|announces?|emerges? from stealth|"
                   r"funding|financing|investment|backs?|backed|leads?|led)\b", re.I)
ROUND = re.compile(r"\b(pre[- ]?seed|seed(?:[- ]stage)?|angel|pre[- ]series [a-k]|series [a-k]\b|growth (round|equity)|"
                   r"ipo|spac)", re.I)
AMOUNT = re.compile(r"([$€£])\s?(\d+(?:[.,]\d+)?)\s?(k|m|mn|million|b|bn|billion)?\b(?!\s*valuation)", re.I)
FUND_ISSUER = re.compile(r"\b(fund|funds|l\.?p\.?|lp|partners|capital|holdings|trust|reit|properties|realty|investors|"
                         r"ventures|spv|master|feeder|offshore|opportunit\w*|portfolio|series \d+|a series of|"
                         r"therapeutics|pharma\w*|bio\w*|medical|clinic|restaurant|real estate|apartments|"
                         r"energy|oil|gas|mining|church|homes|hotel|brewing)\b", re.I)
LEGAL_SUFFIX = re.compile(r"\b(inc|incorporated|corp|corporation|llc|ltd|limited|co|company|plc|gmbh|sas|bv|ab|"
                          r"pbc|the|technologies|technology|labs|lab|ai|hq|software|systems|holdings)\b", re.I)


def stage_of(text: str) -> tuple[str, str]:
    m = ROUND.search(text or "")
    if not m:
        return "unknown", ""
    r = m.group(0).lower()
    if "pre" in r and "seed" in r:
        return "pre_seed", m.group(0)
    if r.startswith("seed") or r == "angel":
        return ("pre_seed" if r == "angel" else "seed"), m.group(0)
    pm = re.match(r"pre[- ]series ([a-k])", r)
    if pm:
        return ("seed" if pm.group(1) == "a" else "series_a" if pm.group(1) == "b" else "series_b_plus"), m.group(0)
    sm = re.match(r"series ([a-k])", r)
    if sm:
        return ("series_a" if sm.group(1) == "a" else "series_b_plus"), m.group(0)
    return "series_b_plus", m.group(0)


def amount_of(text: str) -> tuple[int | None, str]:
    t = text or ""
    v = RAISE.search(t)
    cands = list(AMOUNT.finditer(t))
    if not cands:
        return None, ""
    after = [c for c in cands if v and c.start() > v.start()] or cands
    c = after[0]
    num = float(c.group(2).replace(",", ""))
    unit = (c.group(3) or "").lower()
    mult = 1e3 if unit == "k" else 1e6 if unit in ("m", "mn", "million") else 1e9 if unit in ("b", "bn", "billion") else 1
    usd = num * mult * FX[c.group(1)]
    return (int(usd) if usd >= 10_000 else None), c.group(0)


def band(amount: int | None) -> str:
    if amount is None:
        return "undisclosed"
    return "<$1M" if amount < 1e6 else "$1-15M" if amount <= 15e6 else ">$15M"


ACQUIRED = re.compile(r"\b(acquir(es|ed|ing)|acquisition|buys|bought|merges? with|to be acquired)\b", re.I)
LATE_AMOUNT = 40_000_000  # an undisclosed-stage round this large is past Series A in practice


def is_funding_headline(t: str) -> bool:
    return (bool(RAISE.search(t or "")) and bool(AMOUNT.search(t or "") or ROUND.search(t or ""))
            and not ACQUIRED.search(t or ""))


def infer_late(stage: str, amount: int | None, label: str) -> tuple[str, str]:
    if stage == "unknown" and amount and amount >= LATE_AMOUNT:
        return "series_b_plus", f"{label or 'undisclosed stage'} (>=${LATE_AMOUNT // 1_000_000}M, inferred past Series A)"
    return stage, label


def parse_date(s: str | None) -> date | None:
    if not s:
        return None
    s = s.strip()
    for fmt in ("%b %d, %Y", "%Y-%m-%d", "%Y%m%d", "%B %d, %Y"):
        try:
            return datetime.strptime(s[:len(datetime.now().strftime(fmt))] if fmt != "%b %d, %Y" else s, fmt).date()
        except ValueError:
            pass
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def norm(s: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def core_name(s: str | None) -> str:
    return norm(LEGAL_SUFFIX.sub(" ", re.sub(r"[.,]", " ", s or "")))


LABEL_AFFIX = re.compile(r"^(get|try|use|go|join|with|hey|meet|hello|the)|(hq|app|ai|labs|lab|inc|io|tech|co|hub)$")


def label_core(domain: str) -> str:
    lab = norm(domain.split(".")[0])
    for _ in range(2):
        stripped = LABEL_AFFIX.sub("", lab)
        lab = stripped if len(stripped) >= 3 else lab
    return lab


def gz_json(p: Path):
    with gzip.open(p, "rt", encoding="utf-8") as f:
        return json.load(f)


def gz_csv(p: Path) -> list[dict]:
    with gzip.open(p, "rb") as f:
        return list(csv.DictReader(io.TextIOWrapper(f, encoding="utf-8", newline="")))


def ev(stage, amount, d, precision, source, stype, conf, url, snippet, name=None, round_label=""):
    return {"stage": stage, "amount_usd": amount, "date": d.isoformat() if d else None, "date_precision": precision,
            "source": source, "source_type": stype, "confidence": conf, "url": url, "snippet": (snippet or "")[:300],
            "company_name": name, "round_label": round_label}


# --------------------------------------------------------------------------- universe
def load_db(conn) -> dict[str, dict]:
    return {r["company_domain"]: dict(r) for r in conn.execute("SELECT * FROM companies")}


def yc_icp(c: dict) -> tuple[bool, str, dict]:
    """ICP check on a YC directory record (for companies not yet in the DB)."""
    if c.get("status") != "Active":
        return False, f"status:{c.get('status')}", {}
    if c.get("stage") == "Growth":
        return False, "yc_stage:Growth", {}
    flags = []
    country = c.get("country") or h3.country_from_text(c.get("all_locations"))
    if country is None:
        flags.append("geo_unknown")
    elif country not in h3.ALLOWED:
        return False, f"geo:{country}", {}
    hc = c.get("team_size")
    if hc is None:
        flags.append("headcount_unknown")
    elif not HEADCOUNT_MIN <= hc <= HEADCOUNT_MAX:
        return False, f"headcount:{hc}", {}
    keep, sector, why = h3.sector_decision(c.get("industry") or "", c.get("subindustry") or "", c.get("tags") or [],
                                           f"{c.get('one_liner') or ''} {c.get('long_description') or ''}")
    if not keep:
        return False, why, {}
    m = re.match(r"(Winter|Spring|Summer|Fall) (20\d\d)", c.get("batch") or "")
    short = f"YC {dict(Winter='W', Spring='X', Summer='S', Fall='F')[m.group(1)]}{m.group(2)[2:]}" if m else "YC"
    return True, "", {"company_name": c.get("name"), "one_liner": c.get("one_liner"), "hq_country": country,
                      "sector": sector, "batch_or_investor": short, "headcount": hc, "flags": flags,
                      "source_url": c.get("url")}


FLIAR_EXCLUDE = {"Life Sciences", "Consumer", "Retail", "Consumer Goods", "Media & Entertainment", "Gaming",
                 "Food and Agriculture", "Real Estate and Construction", "Healthcare", "Energy", "Environment",
                 "Auto", "Financial Services", "Business Services", "Telecom"}


NON_ICP_TEXT = re.compile(r"\b(medical|medtech|patient\w*|clinic\w*|surg\w*|patholog\w*|hospital\w*|drug|pharma\w*|"
                          r"game|games|gaming|fashion|beauty|cosmetic\w*|food|restaurant\w*|brew\w*|real estate|"
                          r"property|properties|hotel\w*|travel|consumer\w*|retail\w*|apparel|pet|pets|wellness|"
                          r"fitness|dating|drone\w*|battery|batteries|solar|biotech\w*|therapeut\w*|diagnos\w*|"
                          r"care home|nursing|dental|veterinar\w*|wine|spirits|coffee|mental health|women.?s health|telehealth|"
                          r"therapy|fertility|skincare|furniture|grocery)\b", re.I)


_NON_ICP_PLACES = (r"Tel Aviv|Israel|India|Bengaluru|Bangalore|Mumbai|Delhi|Gurugram|Hyderabad|Singapore|Dubai|UAE|"
                   r"Abu Dhabi|Riyadh|Saudi|Canada|Toronto|Vancouver|Montreal|Australia|Sydney|Melbourne|Brazil|São Paulo|"
                   r"Nigeria|Lagos|Kenya|Nairobi|South Africa|Cape Town|China|Beijing|Shanghai|Shenzhen|Japan|Tokyo|"
                   r"Korea|Seoul|Mexico|Indonesia|Jakarta|Vietnam|Pakistan|Egypt|Cairo|Turkey|Istanbul|Hong Kong")
GEO_CONFLICT = re.compile(rf"\b({_NON_ICP_PLACES})(,[\w ]+)?[- ]based|\bbased in ({_NON_ICP_PLACES})|"
                          rf"\bheadquartered in ({_NON_ICP_PLACES})")


def fliar_icp(r: dict) -> tuple[bool, str, dict]:
    country = h3.COUNTRY_NAMES.get((r.get("country") or "").lower())
    if country not in h3.ALLOWED:
        return False, f"geo:{r.get('country') or 'unknown'}", {}
    if r.get("sector") in FLIAR_EXCLUDE:
        return False, f"sector:{r.get('sector')}", {}
    fy = int(r["founded_year"]) if (r.get("founded_year") or "").isdigit() else None
    if fy and fy < 2016:
        return False, f"founded:{fy} (pre-2016, not an early-stage startup)", {}
    text = f"{r.get('company_name')} {r.get('sector')} {r.get('news_excerpt')}"
    g = GEO_CONFLICT.search(r.get("news_excerpt") or "")
    if g:
        return False, f"geo:excerpt says '{g.group(0)[:40]}' (dataset country {r.get('country')})", {}
    dom = pdb.normalize_domain(r.get("company_domain"))
    for tok in re.findall(r"\b([a-z0-9-]+\.(?:ai|io|so|com|co|app|dev|xyz|tech|eu|de|fr|uk))\b", (r.get("news_excerpt") or "").lower()):
        if norm(tok.split(".")[0]) == core_name(r.get("company_name")) and pdb.normalize_domain(tok) != dom:
            return False, f"domain conflict: excerpt names {tok}, dataset links {dom}", {}
    if NON_ICP_TEXT.search(text):
        return False, f"sector:non-ICP keyword '{NON_ICP_TEXT.search(text).group(0)}'", {}
    if not (h3.AI_SOFT.search(text) or h3.B2B_HINT.search(text)
            or r.get("sector") in ("Enterprise Applications", "Enterprise Infrastructure", "Fintech", "AI-native")):
        return False, "sector:no software/B2B signal in round text", {}
    keep, sector, why = h3.sector_decision(r.get("sector") or "", "", [], text)
    if not keep:
        return False, why, {}
    return True, "", {"company_name": r.get("company_name"), "one_liner": None, "hq_country": country,
                      "sector": sector, "batch_or_investor": None, "headcount": None,
                      "flags": ["headcount_unknown", "founders_unknown", "source_25pct_sample"], "source_url": r.get("fliar_profile_url")}


# --------------------------------------------------------------------------- source adapters
YC_EVENTS: dict[str, list] = {}   # YC news + standard deal per YC domain, attached to any universe member
YC_REJECT: dict[str, str] = {}    # YC directory says this company fails ICP -> no other source may add it


def from_yc(db: dict, events: dict, new_cands: dict, stats: dict, rejects: list) -> dict[str, dict]:
    data = gz_json(RAW / "yc_directory" / "all.json.gz")
    yc_by_domain = {}
    n_head = n_ev = 0
    for c in data:
        d = pdb.normalize_domain(c.get("website"))
        if not d:
            continue
        yc_by_domain[d] = c
        YC_RECORDS[d] = c
        evs = []
        for n in c.get("news") or []:
            t = n.get("title") or ""
            if not is_funding_headline(t):
                continue
            n_head += 1
            named = core_name(c.get("name")) and core_name(c.get("name")) in norm(t + " " + (n.get("url") or ""))
            st, label = stage_of(t)
            amt, _ = amount_of(t)
            st, label = infer_late(st, amt, label)
            evs.append(ev(st, amt, parse_date(n.get("date")), "day", "yc_news", "announcement",
                          "company_attached" if named else "company_attached_unnamed", n.get("url"), t,
                          c.get("name"), label))
        m = re.match(r"(Winter|Spring|Summer|Fall) (20\d\d)", c.get("batch") or "")
        if m:  # YC standard deal: every company in the batch receives it (same on first and later runs)
            mo, dy = YC_SEASON_START[m.group(1)]
            evs.append(ev("pre_seed", YC_STANDARD_DEAL, date(int(m.group(2)), mo, dy), "quarter_start_approx",
                          "yc_standard_deal", "program_terms", "program_terms", c.get("url"),
                          f"YC standard deal at batch start ({c.get('batch')})", c.get("name"), "YC"))
        YC_EVENTS[d] = evs
        if d in db:
            n_ev += len(evs)
        elif any(e["confidence"] == "company_attached" and e["stage"] in EARLY + ("unknown",) and e["date"]
                 and e["date"] >= WINDOW_START.isoformat() for e in evs):
            ok, why, fields = yc_icp(c)
            if ok:
                new_cands[d] = {**fields, "discovered_by": "yc_news"}
                n_ev += len(evs)
            else:
                YC_REJECT[d] = why
                rejects.append({"stage": "new_company_icp", "source": "yc_news", "company_name": c.get("name"),
                                "company_domain": d, "reason": why, "evidence": evs[0]["snippet"],
                                "url": evs[0]["url"]})
    stats["yc_directory"] = {"companies": len(data), "funding_headlines": n_head, "events_used": n_ev}
    return yc_by_domain


_YC_FAIL: dict[str, str] = {}


def yc_icp_fail_any() -> dict[str, str]:
    """ICP verdict from the YC directory for every YC company (not only those with news)."""
    if not _YC_FAIL:
        for d, c in YC_RECORDS.items():
            ok, why, _ = yc_icp(c)
            if not ok:
                _YC_FAIL[d] = why
        _YC_FAIL.setdefault("", "")
    return _YC_FAIL


YC_RECORDS: dict[str, dict] = {}


def from_fliar(db: dict, events: dict, new_cands: dict, stats: dict, rejects: list, watch: list) -> None:
    seen, rows = set(), []
    for f in sorted((RAW / "fliar_funding_sample").glob("*.csv.gz")):
        for r in gz_csv(f):
            if r["round_id"] not in seen:
                seen.add(r["round_id"])
                rows.append(r)
    STAGE = {"Pre-seed": "pre_seed", "Angel": "pre_seed", "Seed": "seed", "Pre-Series A": "seed",
             "Series A": "series_a", "Pre-Series B": "series_a"}
    by_dom = defaultdict(list)
    for r in rows:
        d = pdb.normalize_domain(r.get("company_domain"))
        if not d:
            continue
        s = r["stage"]
        st = STAGE.get(s) or ("series_b_plus" if re.match(r"(Series [B-H]|Pre-Series [C-D]|Growth)", s) else "unknown")
        amt = int(float(r["amount_usd"])) if r.get("amount_usd") and r.get("amount_status") != "not_disclosed" else None
        st, _lbl = infer_late(st, amt, s)
        e = ev(st, amt, parse_date(r["announced_date"]), "day", "fliar_radar", "dataset", "domain_keyed",
               r.get("news_url") or r.get("fliar_profile_url"), r.get("news_excerpt"), r.get("company_name"), s)
        by_dom[d].append((r, e))
    used = new = 0
    for d, items in by_dom.items():  # decide per company with ALL of its rounds (order-independent)
        if d in db or d in new_cands:
            events[d] += [e for _, e in items]
            used += len(items)
            continue
        inwin = [(r, e) for r, e in items if e["date"] and e["date"] >= WINDOW_START.isoformat()
                 and r["stage"] not in ("Debt", "Grant", "Venture debt", "Strategic", "Crowdfunding")]
        late = [e for _, e in items if e["stage"] == "series_b_plus"]
        early = [(r, e) for r, e in inwin if e["stage"] in EARLY]
        if not inwin:
            continue  # no early-stage capital event inside the window
        r0, e0 = max(early or inwin, key=lambda x: x[1]["date"])
        if late:
            rejects.append({"stage": "new_company_icp", "source": "fliar_radar", "company_name": r0.get("company_name"),
                            "company_domain": d, "reason": f"past Series A: {late[-1]['round_label']} {late[-1]['date']}",
                            "evidence": late[-1]["snippet"][:150], "url": late[-1]["url"]})
            continue
        ok, why, fields = fliar_icp(r0)
        if d in YC_REJECT:
            ok, why = False, f"{YC_REJECT[d]} (YC directory)"
        elif d in yc_icp_fail_any():
            ok, why = False, f"{yc_icp_fail_any()[d]} (YC directory)"
        if not early:
            if ok:
                watch.append({"kind": "new_round_stage_undisclosed", "company_name": r0.get("company_name"),
                              "company_domain": d, "stage": "unknown", "amount_usd": e0["amount_usd"],
                              "date": e0["date"], "source": "fliar_radar", "url": e0["url"],
                              "detail": (r0.get("news_excerpt") or "")[:200],
                              "next_step": "confirm stage <= Series A + ICP, then add"})
            continue
        if ok:
            new_cands[d] = {**fields, "discovered_by": "fliar_radar"}
            events[d] += [e for _, e in items]
            new += 1
        else:
            rejects.append({"stage": "new_company_icp", "source": "fliar_radar", "company_name": r0.get("company_name"),
                            "company_domain": d, "reason": why,
                            "evidence": f"{r0['stage']} {r0.get('amount_usd')} {r0['announced_date']}", "url": e0["url"]})
    stats["fliar_radar"] = {"rounds_unique": len(rows), "max_date": max(r["announced_date"] for r in rows),
                            "events_on_universe": used, "new_candidates": new,
                            "note": "25% company-hash sample of Fliar Radar; recall per company is all-or-nothing"}


def name_index(db: dict, new_cands: dict) -> dict[str, list[str]]:
    idx = defaultdict(list)
    for d, r in list(db.items()) + list(new_cands.items()):
        n = (r.get("company_name") or "").strip()
        if len(norm(n)) >= 3:
            idx[n].append(d)
    return idx


SUBJECT_OK = {"Exclusive", "YC", "AI", "US", "UK", "EU", "NYC", "SF", "London", "Berlin", "Paris", "Startup", "Fintech",
              "Seattle", "Boston", "Austin", "Stockholm", "Munich", "Amsterdam", "Report", "Sources", "Source"}


def match_headline(t: str, idx: dict[str, list[str]], max_words: int = 4) -> list[tuple[str, str]]:
    """Exact (case-sensitive) company name used as the subject of a raise headline."""
    toks = re.findall(r"[\w$€£.&'’\-/]+", t)
    hits = []
    for i in range(len(toks)):
        for k in range(1, max_words + 1):
            n = " ".join(toks[i:i + k]).strip(".,'’")
            if n in idx:
                pat = rf"(^|(?<![A-Z][\w’'.-])[\s‘'\"(:]){re.escape(n)}(’s|'s)?(,| \(| (raises|raised|lands|landed|secures|secured|closes|" \
                      rf"closed|nabs|bags|banks|gets|has raised|announces|emerges|snags|scores|pulls))"
                m = re.search(pat, t)
                if m:
                    prev = t[:m.start() + len(m.group(1))].rstrip().split(" ")[-1] if m.start() > 0 else ""
                    if prev and re.fullmatch(r"[A-Z][\w’'.&]*", prev) and prev not in SUBJECT_OK:
                        continue  # e.g. "Photon Bridge raises" is not "Bridge"
                    hits += [(n, d) for d in idx[n]]
    return hits


def from_daily(events: dict, idx: dict, stats: dict, watch: list) -> list[dict]:
    files = sorted((RAW / "startup_pulse_daily").glob("*.json.gz"))
    headlines, forms = {}, {}
    for f in files:
        x = gz_json(f)["sections"]
        for sec in ("headlines", "news"):
            for it in x.get(sec) or []:
                t = it.get("title") or ""
                if is_funding_headline(t) and t not in headlines:
                    headlines[t] = it
        for it in x.get("formDFilings") or []:
            forms[(it.get("cik"), it.get("dateFiled"))] = it
    matched = 0
    for t, it in headlines.items():
        st, label = stage_of(t)
        amt, _ = amount_of(t)
        st, label = infer_late(st, amt, label)
        e = ev(st, amt, parse_date(it.get("createdAt")), "day", f"daily_rss:{it.get('source')}", "announcement",
               "headline_name_match", it.get("url"), t, None, label)
        hits = match_headline(t, idx)
        for n, d in hits:
            events[d].append({**e, "company_name": n})
        if hits:
            matched += 1
        elif st in EARLY:
            watch.append({"kind": "unmatched_funding_headline", "company_name": "", "company_domain": "",
                          "stage": st, "amount_usd": amt, "date": e["date"], "source": e["source"], "url": e["url"],
                          "detail": t, "next_step": "no domain in feed - resolve company + domain, then re-run"})
    stats["startup_pulse_daily"] = {"days": len(files), "first_day": files[0].name[:10], "last_day": files[-1].name[:10],
                                    "funding_headlines_unique": len(headlines), "headlines_matched_to_universe": matched,
                                    "form_d_filings_unique": len(forms),
                                    "note": "feed keeps top ~40 Form D filings and ~60 headlines per day (not exhaustive)"}
    return list(forms.values())


def cik_maps() -> tuple[dict, dict]:
    cd = pd.read_parquet(RAW / "formd_tracker" / "company_domains.parquet")
    yc = pd.read_parquet(RAW / "formd_tracker" / "yc_matches.parquet")
    by_cik = {}
    for r in cd.itertuples():
        d = pdb.normalize_domain(r.domain)
        if d:
            by_cik[str(r.cik).zfill(10)] = (d, "form_d_cik_domain_" + r.source)
    for r in yc.itertuples():
        d = pdb.normalize_domain(r.website)
        if d:
            by_cik[str(r.cik).zfill(10)] = (d, "form_d_cik_yc_match")
    return by_cik, {}


def from_form_d(filings: list[dict], db: dict, new_cands: dict, events: dict, stats: dict, watch: list) -> None:
    by_cik, _ = cik_maps()
    core_idx = defaultdict(set)
    for d, r in list(db.items()) + list(new_cands.items()):
        cn = core_name(r.get("company_name"))
        if len(cn) >= 5:
            core_idx[cn].add(d)
    st = Counter()
    bay = gz_json(RAW / "formd_bay_area" / "companies.json.gz")
    rows = [{"cik": str(f.get("cik")).zfill(10), "company": f.get("company"), "date": f.get("dateFiled"),
             "url": f.get("url"), "amount": None, "src": "sec_form_d_daily"} for f in filings]
    rows += [{"cik": str(b.get("cik")).zfill(10), "company": b.get("name"), "date": b.get("latest_raise"),
              "url": None, "amount": b.get("total_sold") if b.get("raises") == 1 else None,
              "src": "sec_form_d_bay_area", "industry": b.get("industry")} for b in bay]
    for f in rows:
        name = f["company"] or ""
        if FUND_ISSUER.search(name):
            st["fund_or_non_startup_issuer"] += 1
            continue
        d, how = by_cik.get(f["cik"], (None, None))
        if d and (d in db or d in new_cands):
            cname = (db.get(d) or new_cands.get(d) or {}).get("company_name")
            if core_name(name) not in (label_core(d), core_name(cname)):
                d, how = None, None  # CIK->domain link not corroborated by the issuer's legal name
        if not (d and (d in db or d in new_cands)):
            ds = {x for x in core_idx.get(core_name(name), set()) if label_core(x) == core_name(name)}
            d, how = (next(iter(ds)), "form_d_legal_name+domain_match") if len(ds) == 1 else (None, None)
        e = ev("unknown", f["amount"], parse_date(f["date"]), "day", f["src"], "form_d", "form_d",
               f["url"] or f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={f['cik']}&type=D",
               f"Form D filed by {name} ({how})" if d else f"Form D filed by {name}", name)
        if d and (d in db or d in new_cands):
            events[d].append(e)
            st["matched_to_universe"] += 1
        elif e["date"] and e["date"] >= FRESH_START.isoformat() and f["cik"] in by_cik:
            dom = by_cik[f["cik"]][0]
            watch.append({"kind": "new_form_d_issuer", "company_name": name, "company_domain": dom, "stage": "unknown",
                          "amount_usd": f["amount"], "date": e["date"], "source": f["src"], "url": e["url"],
                          "detail": f"domain via {by_cik[f['cik']][1]}",
                          "next_step": "verify startup/stage on homepage, then add"})
            st["new_issuer_watchlist"] += 1
        else:
            st["unmatched"] += 1
    stats["sec_form_d"] = {"filings_considered": len(rows), **st}


def from_landedjobs(events: dict, idx: dict, stats: dict, watch: list) -> None:
    rows = gz_json(RAW / "landedjobs_recent_raises" / "startups.json.gz")["startups"]
    m = 0
    for r in rows:
        st, label = stage_of(r.get("round") or "")
        st, label = infer_late(st, r.get("amount_usd"), label)
        e = ev(st, r.get("amount_usd"), parse_date(r.get("funded_at")), "day", "landedjobs", "announcement",
               "headline_name_match", r.get("source_url"), f"{r.get('company')} {r.get('round')} {r.get('amount')} "
               f"({r.get('location')})", r.get("company"), r.get("round") or "")
        ds = idx.get((r.get("company") or "").strip(), [])
        for d in ds:
            events[d].append(e)
        if ds:
            m += 1
        elif st in EARLY:
            dom = pdb.normalize_domain(r.get("source_url"))
            watch.append({"kind": "recent_raise_unmatched", "company_name": r.get("company"),
                          "company_domain": dom if dom and norm(r.get("company"))[:5] in dom.replace(".", "") else "",
                          "stage": st, "amount_usd": r.get("amount_usd"), "date": e["date"], "source": "landedjobs",
                          "url": r.get("source_url"), "detail": f"{r.get('sector')} | {r.get('location')}",
                          "next_step": "check HQ country + headcount, then add"})
    stats["landedjobs"] = {"rows": len(rows), "matched_to_universe": m}


# --------------------------------------------------------------------------- decision
def decide(d: str, evs: list[dict]) -> dict:
    # dedupe identical url+stage
    uniq, seen = [], set()
    for e in sorted(evs, key=lambda e: (CONF_RANK[e["confidence"]], e["date"] or "")):
        k = (e["url"], e["stage"], e["date"])
        if k not in seen:
            seen.add(k)
            uniq.append(e)
    evs = uniq
    in_window = [e for e in evs if e["date"] and WINDOW_START.isoformat() <= e["date"] <= RUN_DATE.isoformat()]
    reliable_late = [e for e in evs if e["stage"] == "series_b_plus" and e["confidence"] in RELIABLE]
    weak_late = [e for e in evs if e["stage"] == "series_b_plus" and e["confidence"] not in RELIABLE]
    external = [e for e in in_window if e["source_type"] != "program_terms" and e["stage"] != "series_b_plus"]
    fresh = [e for e in in_window if e["date"] >= FRESH_START.isoformat() and e["stage"] != "series_b_plus"]
    out = {"events": evs, "past_series_a": None, "flags": []}
    if reliable_late:
        e = max(reliable_late, key=lambda e: e["date"] or "")
        out["past_series_a"] = f"{e['round_label'] or 'Series B+'} {('$%.1fM' % (e['amount_usd'] / 1e6)) if e['amount_usd'] else ''} " \
                               f"({e['date']}, {e['source']})".replace("  ", " ")
    if weak_late:
        out["flags"].append("possible_past_series_a_name_match_verify")
    pool = fresh or external
    if pool and not out["past_series_a"]:
        best = sorted(pool, key=lambda e: (e["date"], -CONF_RANK[e["confidence"]],
                                           e["stage"] != "unknown"), reverse=True)[0]
        out["h2"] = True
        out["tier"] = "fresh_60d" if fresh else "in_window"
        out["best"] = best
    else:
        out["h2"] = False
    # funding columns: latest dated external raise (<= Series A); accelerator check only as fallback
    nonlate = [e for e in evs if e["date"] and e["stage"] != "series_b_plus"]
    ext_nonlate = [e for e in nonlate if e["source_type"] != "program_terms"]
    pool2 = ext_nonlate or nonlate
    pick = max(pool2, key=lambda e: (e["date"], -CONF_RANK[e["confidence"]], e["stage"] != "unknown")) if pool2 else None
    out["funding"] = pick
    if out["past_series_a"]:
        status = "past_series_a"
    elif external:
        status = "verified_in_window"
    elif any(e["source_type"] != "program_terms" for e in evs if e["stage"] != "series_b_plus"):
        status = "external_raise_before_window_only"
    elif any(e["source_type"] == "program_terms" for e in evs):
        status = "accelerator_check_only"
    else:
        status = "no_evidence"
    out["status"] = status
    return out


def days_since(iso: str | None) -> int | None:
    return (RUN_DATE - date.fromisoformat(iso)).days if iso else None


# --------------------------------------------------------------------------- main
H2_COLS = ["company_domain", "company_name", "one_liner", "sector", "hq_country", "batch_or_investor", "headcount",
           "h2_tier", "days_since_capital_event", "capital_event_date", "date_precision", "round_stage", "round_label",
           "amount_usd", "amount_band", "source_type", "confidence", "evidence_url", "evidence_snippet",
           "external_raises_in_window", "all_events", "funding_window_status", "flags", "other_hypotheses",
           "ceo_name", "ceo_linkedin", "ceo_x", "discovered_by"]
STATUS_COLS = ["company_domain", "company_name", "batch_or_investor", "matched_hypotheses", "funding_window_status",
               "h2_tagged", "h2_tier", "funding_stage", "funding_amount", "funding_date", "date_precision",
               "funding_source", "confidence", "past_series_a", "flags", "events_count"]
REJ_COLS = ["stage", "source", "company_name", "company_domain", "reason", "evidence", "url"]
WATCH_COLS = ["kind", "company_name", "company_domain", "stage", "amount_usd", "date", "source", "url", "detail",
              "next_step"]


def write_csv(path, cols, rows):
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def main() -> None:
    conn = pdb.init_db()
    db = load_db(conn)
    stats = {"run_date": RUN_DATE.isoformat(), "window_start": WINDOW_START.isoformat(),
             "fresh_start": FRESH_START.isoformat()}
    events: dict[str, list] = defaultdict(list)
    new_cands: dict[str, dict] = {}
    rejects, watch = [], []
    runs = {}
    for src, q in [("yc_directory", "news[] funding headlines + YC standard deal"),
                   ("fliar_radar", "domain-keyed rounds, 25% sample"),
                   ("startup_pulse_daily", "RSS funding headlines + Form D filings, daily since 2026-07-08"),
                   ("sec_form_d", "Form D: CIK->domain (formd-tracker) or legal-name match"),
                   ("landedjobs", "recent raises list")]:
        runs[src] = pdb.start_run(conn, "H2", src, q, raw_path=f"raw_sources/H2_fresh_capital")

    from_yc(db, events, new_cands, stats, rejects)
    from_fliar(db, events, new_cands, stats, rejects, watch)
    for d in list(db) + list(new_cands):
        events[d] += YC_EVENTS.get(d, [])
    idx = name_index(db, new_cands)
    filings = from_daily(events, idx, stats, watch)
    from_form_d(filings, db, new_cands, events, stats, watch)
    from_landedjobs(events, idx, stats, watch)

    founders = defaultdict(list)
    for f in conn.execute("SELECT * FROM founders"):
        founders[f["company_domain"]].append(dict(f))

    universe = list(db) + [d for d in new_cands if d not in db]
    decisions = {d: decide(d, events.get(d, [])) for d in universe}

    # new candidates that end up without an H2 tag (e.g. only a pre-window raise) are not added
    added = 0
    h2_rows, status_rows = [], []
    for d in universe:
        dec = decisions[d]
        is_new = d not in db
        if is_new and not dec["h2"]:
            rejects.append({"stage": "new_company_scope", "source": new_cands[d]["discovered_by"],
                            "company_name": new_cands[d]["company_name"], "company_domain": d,
                            "reason": dec["past_series_a"] and f"past Series A: {dec['past_series_a']}"
                            or "no raise inside window", "evidence": "", "url": ""})
            continue
        f = dec["funding"]
        fields = {}
        if f:
            fields = {"funding_stage": f["stage"] if f["stage"] in EARLY else "unknown",
                      "funding_amount": f["amount_usd"], "funding_date": f["date"]}
        if is_new:
            nc = new_cands[d]
            fields.update(company_name=nc["company_name"], one_liner=nc["one_liner"], hq_country=nc["hq_country"],
                          sector=nc["sector"], batch_or_investor=nc["batch_or_investor"], headcount=nc["headcount"])
            added += 1
        if dec["h2"]:
            b = dec["best"]
            for e in dec["events"]:
                if e["date"] and e["date"] >= WINDOW_START.isoformat() and e["stage"] != "series_b_plus":
                    pdb.upsert_company(conn, d, "H2", proof={
                        "source": e["source"], "url": e["url"],
                        "snippet": f"[{e['stage']}|{e['confidence']}|{e['date']}] {e['snippet'][:200]}"}, **fields)
        elif fields:
            sets = ", ".join(f"{k} = COALESCE(?, {k})" for k in fields)
            conn.execute(f"UPDATE companies SET {sets}, last_updated_at = ? WHERE company_domain = ?",
                         [*fields.values(), pdb.utcnow(), d])
        if dec["past_series_a"]:
            conn.execute("UPDATE companies SET disqualification_reason = ?, "
                         "status = CASE WHEN status = 'new' THEN 'disqualified' ELSE status END, last_updated_at = ? "
                         "WHERE company_domain = ?", (f"past Series A: {dec['past_series_a']}", pdb.utcnow(), d))

        row = conn.execute("SELECT * FROM companies WHERE company_domain = ?", (d,)).fetchone()
        tags = json.loads(row["matched_hypotheses"])
        status_rows.append({
            "company_domain": d, "company_name": row["company_name"], "batch_or_investor": row["batch_or_investor"],
            "matched_hypotheses": "|".join(tags), "funding_window_status": dec["status"], "h2_tagged": dec["h2"],
            "h2_tier": dec.get("tier", ""), "funding_stage": row["funding_stage"], "funding_amount": row["funding_amount"],
            "funding_date": row["funding_date"], "date_precision": f["date_precision"] if f else "",
            "funding_source": f["source"] if f else "", "confidence": f["confidence"] if f else "",
            "past_series_a": dec["past_series_a"] or "", "flags": "|".join(dec["flags"]),
            "events_count": len(dec["events"])})
        if dec["h2"]:
            b = dec["best"]
            ceo = next((x for x in founders.get(d, []) if re.search(r"\bceo\b|chief executive", x["role"] or "", re.I)),
                       (founders.get(d) or [None])[0])
            ext = [e for e in dec["events"] if e["source_type"] != "program_terms" and e["date"]
                   and e["date"] >= WINDOW_START.isoformat() and e["stage"] != "series_b_plus"]
            flags = list(dec["flags"]) + (new_cands[d]["flags"] if is_new else [])
            if b["confidence"] in ("headline_name_match", "company_attached_unnamed"):
                flags.append("name_match_verify")
            if b["source_type"] == "form_d":
                flags.append("form_d_stage_unknown")
            h2_rows.append({
                "company_domain": d, "company_name": row["company_name"], "one_liner": row["one_liner"],
                "sector": row["sector"], "hq_country": row["hq_country"], "batch_or_investor": row["batch_or_investor"],
                "headcount": row["headcount"], "h2_tier": dec["tier"], "days_since_capital_event": days_since(b["date"]),
                "capital_event_date": b["date"], "date_precision": b["date_precision"], "round_stage": b["stage"],
                "round_label": b["round_label"], "amount_usd": b["amount_usd"], "amount_band": band(b["amount_usd"]),
                "source_type": b["source_type"], "confidence": b["confidence"], "evidence_url": b["url"],
                "evidence_snippet": b["snippet"], "external_raises_in_window": len(ext),
                "all_events": " || ".join(f"{e['date']} {e['stage']} {band(e['amount_usd'])} [{e['source']}/{e['confidence']}]"
                                          for e in sorted(dec["events"], key=lambda e: e["date"] or "", reverse=True)[:6]),
                "funding_window_status": dec["status"], "flags": "|".join(dict.fromkeys(flags)),
                "other_hypotheses": "|".join(t for t in tags if t != "H2"),
                "ceo_name": ceo["full_name"] if ceo else "", "ceo_linkedin": (ceo or {}).get("linkedin_url") or "",
                "ceo_x": f"@{ceo['x_handle']}" if ceo and ceo.get("x_handle") else "",
                "discovered_by": new_cands[d]["discovered_by"] if is_new else (
                    "h2_new:" + ("yc_news" if (row["batch_or_investor"] or "").startswith("YC") else "fliar_radar")
                    if tags == ["H2"] else "existing_db")})
    conn.commit()

    tier_rank = {"fresh_60d": 0, "in_window": 1}
    conf_ok = {"domain_keyed": 0, "company_attached": 0, "form_d": 1, "program_terms": 1,
               "company_attached_unnamed": 2, "headline_name_match": 2}
    h2_rows.sort(key=lambda r: (tier_rank[r["h2_tier"]], conf_ok[r["confidence"]], r["days_since_capital_event"] or 9999))
    write_csv(OUT / "sublist_H2_fresh_capital.csv", H2_COLS, h2_rows)
    write_csv(OUT / "sublist_H2_funding_status_all.csv", STATUS_COLS,
              sorted(status_rows, key=lambda r: (r["funding_window_status"], r["company_domain"])))
    write_csv(OUT / "sublist_H2_rejected.csv", REJ_COLS, rejects)
    watch.sort(key=lambda r: (r["kind"], r["date"] or ""), reverse=False)
    write_csv(OUT / "sublist_H2_watchlist.csv", WATCH_COLS, watch)

    st = Counter(r["funding_window_status"] for r in status_rows)
    report = {
        "generated_at": pdb.utcnow(), "scope": stats, "universe_db_companies": len(db),
        "new_companies_added": added, "h2_tagged": len(h2_rows),
        "h2_by_tier": dict(Counter(r["h2_tier"] for r in h2_rows)),
        "h2_by_stage": dict(Counter(r["round_stage"] for r in h2_rows)),
        "h2_by_confidence": dict(Counter(r["confidence"] for r in h2_rows)),
        "h2_by_amount_band": dict(Counter(r["amount_band"] for r in h2_rows)),
        "funding_window_status": dict(st), "rejected_rows": len(rejects),
        "rejected_by_reason": dict(Counter(r["reason"].split(":")[0] for r in rejects).most_common(15)),
        "watchlist_rows": len(watch), "watchlist_by_kind": dict(Counter(w["kind"] for w in watch)),
        "reconciliation": {"status_rows": len(status_rows), "db_after": conn.execute(
            "SELECT COUNT(*) FROM companies").fetchone()[0], "balanced": len(status_rows) == conn.execute(
            "SELECT COUNT(*) FROM companies").fetchone()[0]},
    }
    for src, rid in runs.items():
        s = stats.get(src, {})
        pdb.finish_run(conn, rid, pages_fetched=1,
                       records_raw=s.get("companies") or s.get("rounds_unique") or s.get("funding_headlines_unique")
                       or s.get("filings_considered") or s.get("rows") or 0,
                       records_unique=s.get("events_used") or s.get("events_on_universe")
                       or s.get("headlines_matched_to_universe") or s.get("matched_to_universe") or 0,
                       records_icp_pass=sum(1 for r in h2_rows if src.split("_")[0] in (r["all_events"] or "")),
                       stop_reason="exhausted")
    (RAW / "h2_run_report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    conn.close()


if __name__ == "__main__":
    main()
