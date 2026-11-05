"""Build the unified master: one DB-backed workbook + CSVs for daily tracking.

Usage: python3 scripts/build_master.py

First principles
  * One entity per tab, one row per thing, one key: company_domain.
      Master        1 row per company      (who, why them, why now, how to reach, what next)
      Contacts      1 row per founder      (decision makers)
      Signals       1 row per evidence     (every H1/H2/H3 proof with source URL)
      Open_Roles    1 row per GTM posting  (H1)
      Outreach_Log  1 row per touch        (you fill this in)
  * The DB (db/gtm_pipeline.db) is the source of truth. The workbook is a view plus input cells.
    Before rebuilding, the yellow tracking cells of the existing workbook (Master stage / next action /
    date / notes, and every Outreach_Log row) are synced into the DB tables `tracking` and
    `outreach_log`, so a rebuild never loses your edits.
  * Score = rank_score from master_rank.py (unchanged), split into components. Fit (icp_tier) and
    timing (wave) are separate columns so you can filter on either.

Outputs: master/gtm_master.xlsx and master/*.csv
"""
from __future__ import annotations

import csv
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.comments import Comment
from openpyxl.formatting.rule import CellIsRule, FormulaRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pipeline_db as pdb  # noqa: E402
import master_rank  # noqa: E402

OUT_DIR = pdb.ROOT / "master"
XLSX = OUT_DIR / "gtm_master.xlsx"
SUB = pdb.SUBLIST_DIR
EXAMPLE_DOMAIN = "example.com"

STAGES = ["Not started", "Researching", "Contacted", "Follow-up 1", "Follow-up 2", "Replied", "Call booked",
          "Trial / proposal", "Offer", "Won", "Lost", "Not a fit"]
CLOSED = ["Won", "Lost", "Not a fit"]
EMAIL_CONF = ["published_ceo", "published_founder", "clay_valid", "derived_from_pattern", "published_founders_inbox",
              "published_generic", "clay_unverified", "guess_first_at_domain", "clay_invalid", "manual"]
CHANNELS = ["LinkedIn", "Email", "X", "Job application", "Warm intro", "Call", "Other"]
TOUCH_TYPES = ["First touch", "Follow-up", "Reply", "Call", "Proposal", "Other"]
OUTCOMES = ["Sent", "No reply", "Replied - positive", "Replied - negative", "Call booked", "Not a fit"]
WAVES = ["W1 Hot (days 1-7)", "W2 Warm (days 8-14)", "W3 Cold-fit (days 15-30)", "Nurture (day 30+)",
         "Enrich first", "Review fit"]
TIERS = ["A_high_confidence", "B_likely_fit", "C_needs_enrichment", "D_review"]
HYP_FILES = {"H0_Universe": "sublist_H0_icp_universe.csv", "H1_Hiring": "sublist_H1_job_arbitrage.csv",
             "H2_Capital": "sublist_H2_fresh_capital.csv", "H3_Founders": "sublist_H3_technical_founders.csv",
             "H3x_Founders": "sublist_H3_expanded.csv", "H4_Launch": "sublist_H4_launch_decay.csv",
             "H5_Outbound": "sublist_H5_outbound_stack.csv", "H6_FounderLed": "sublist_H6_founder_led_sales.csv",
             "H7_Revenue": "sublist_H7_revenue_no_gtm.csv", "H8_OSS": "sublist_H8_oss_traction.csv",
             "H9_WarmPath": "sublist_H9_warm_paths.csv", "H10_StaleRole": "sublist_H10_stale_gtm_roles.csv"}
HYP_COLS = ["H1", "H2", "H3", "H4", "H5", "H6", "H7", "H8", "H9", "H10"]
HYP_NAMES = {"H0": "ICP universe", "H1": "Hiring GTM", "H2": "Fresh capital", "H3": "Technical founders",
             "H4": "Launch decay", "H5": "Outbound stack", "H6": "Founder-led sales", "H7": "Verified revenue",
             "H8": "OSS traction", "H9": "Warm path", "H10": "Stale GTM role"}

CONSUMER = re.compile(r"\b(consumer|for (kids|parents|families|couples|teens|gamers|fans|singles|creators|students|you)|"
                      r"dating|game|gaming|social app|fitness|personal|wellness|travel|shopping|fashion|beauty|pet|"
                      r"music|friends)\b", re.I)
BIO = re.compile(r"\b(therapeut|drug|biotech|molecule|protein|antibod|clinical trial|wet lab)\w*", re.I)
SERVICES = re.compile(r"\b(agency|consult(ing|ancy)|staffing agency|done-for-you)\b", re.I)


# --------------------------------------------------------------------------- helpers
def read_csv(name: str) -> list[dict]:
    p = SUB / name
    if not p.exists():
        return []
    with open(p, newline="") as f:
        return list(csv.DictReader(f))


def to_date(s) -> date | None:
    if not s:
        return None
    if isinstance(s, datetime):
        return s.date()
    if isinstance(s, date):
        return s
    try:
        return date.fromisoformat(str(s)[:10])
    except ValueError:
        return None


def to_int(s):
    try:
        return int(float(s)) if s not in (None, "") else None
    except ValueError:
        return None


# --------------------------------------------------------------------------- 0. aliases
def fold_aliases(conn) -> list[str]:
    done = []
    for alias, canon in pdb.DOMAIN_ALIASES.items():
        a = conn.execute("SELECT * FROM companies WHERE company_domain = ?", (alias,)).fetchone()
        if not a:
            continue
        for p in json.loads(a["live_signal_proof"]):
            h = p.pop("hypothesis")
            p.pop("captured_at", None)
            pdb.upsert_company(conn, canon, h, proof=p)
        for k in ("funding_stage", "funding_amount", "funding_date"):
            if a[k] is not None:
                conn.execute(f"UPDATE companies SET {k} = COALESCE({k}, ?) WHERE company_domain = ?", (a[k], canon))
        conn.execute("DELETE FROM companies WHERE company_domain = ?", (alias,))
        done.append(f"{alias} -> {canon}")
    conn.commit()
    return done


# --------------------------------------------------------------------------- 1. sync user edits back
def sync_from_workbook(conn) -> dict:
    if not XLSX.exists():
        return {"synced": False}
    wb = load_workbook(XLSX, data_only=True)
    n_track = n_log = n_email = 0
    if "Master" in wb.sheetnames:
        ws = wb["Master"]
        hdr = {c.value: i for i, c in enumerate(ws[HEADER_ROW]) if c.value}
        for row in ws.iter_rows(min_row=HEADER_ROW + 1, values_only=True):
            d = row[hdr["company_domain"]]
            if not d:
                continue
            stage, act, when, notes = (row[hdr["stage"]], row[hdr["next_action"]], to_date(row[hdr["next_action_date"]]),
                                       row[hdr["notes"]])
            em = row[hdr["email"]] if "email" in hdr else None
            if isinstance(em, str) and "@" in em:
                em = em.strip().lower()
                cur = conn.execute("SELECT email FROM company_emails WHERE company_domain=?", (d,)).fetchone()
                if not cur or cur["email"] != em:
                    conn.execute(
                        "INSERT INTO company_emails (company_domain, email, contact_name, email_confidence, source_type, "
                        "evidence, found_at) VALUES (?,?,NULL,'manual','manual','typed in workbook',?) "
                        "ON CONFLICT(company_domain) DO UPDATE SET email=excluded.email, contact_name=NULL, "
                        "email_confidence='manual', source_type='manual', evidence=excluded.evidence, "
                        "found_at=excluded.found_at", (d, em, pdb.utcnow()))
                    n_email += 1
            if (stage and stage != "Not started") or act or when or notes:
                conn.execute(
                    "INSERT INTO tracking (company_domain, stage, next_action, next_action_date, notes, updated_at) "
                    "VALUES (?,?,?,?,?,?) ON CONFLICT(company_domain) DO UPDATE SET stage=excluded.stage, "
                    "next_action=excluded.next_action, next_action_date=excluded.next_action_date, "
                    "notes=excluded.notes, updated_at=excluded.updated_at",
                    (d, stage or "Not started", act, when.isoformat() if when else None, notes, pdb.utcnow()))
                n_track += 1
    if "Outreach_Log" in wb.sheetnames:
        ws = wb["Outreach_Log"]
        hdr = {c.value: i for i, c in enumerate(ws[1]) if c.value}
        for row in ws.iter_rows(min_row=2, values_only=True):
            d = row[hdr["company_domain"]]
            t = to_date(row[hdr["touch_date"]])
            if not d or not t or d == EXAMPLE_DOMAIN:
                continue
            vals = [t.isoformat(), pdb.normalize_domain(d) or d] + [row[hdr[k]] for k in (
                "contact_name", "channel", "touch_type", "angle", "outcome", "next_step", "notes")]
            cur = conn.execute("INSERT OR IGNORE INTO outreach_log (touch_date, company_domain, contact_name, channel, "
                               "touch_type, angle, outcome, next_step, notes) VALUES (?,?,?,?,?,?,?,?,?)", vals)
            n_log += cur.rowcount
    conn.commit()
    return {"synced": True, "tracking_rows": n_track, "new_log_rows": n_log, "manual_emails": n_email}


# --------------------------------------------------------------------------- 2. tier + wave
def classify(m: dict, founders: list[dict], email: dict | None) -> tuple[str, str, list[str], str]:
    """-> (icp_tier, wave, risks, segment). Fit and timing are independent; every rule is in Start_Here."""
    st, conf = m["funding_window_status"], m["h2_confidence"]
    flags = m["flags"] or ""
    hc, band = to_int(m["headcount"]), m["headcount_band"] or ""
    geo = bool(m["hq_country"])
    size_ok = (hc is not None and 2 <= hc <= 35) or band == "1-10"
    contact = any(f["linkedin_url"] for f in founders) or bool((email or {}).get("email")) or bool(founders)
    bi = m["batch_or_investor"] or ""
    yc = re.search(r"YC [WXSF](\d\d)", bi)
    program_in_window = bool((yc and int(yc.group(1)) >= 25) or re.search(r"SR00[4-7]|2025|2026", bi))
    listed_early = bool(bi) or (m["funding_stage"] or "") in ("pre_seed", "seed", "series_a")
    tags = set((m["matched_hypotheses"] or "").split("|"))
    mrr = float(m["h7_mrr"] or 0)
    segment = "bootstrapped_revenue" if m["h7_segment"] == "bootstrapped_revenue" and "H2" not in tags and not bi \
        else "venture_track"
    risks = []
    text = m["one_liner"] or ""
    if CONSUMER.search(text):
        risks.append("consumer_keyword")
    if BIO.search(text):
        risks.append("bio_keyword")
    if SERVICES.search(text):
        risks.append("services_keyword")
    if "possible_past_series_a" in flags:
        risks.append("possible_past_series_a")
    if conf in ("headline_name_match", "company_attached_unnamed", "news_resolved"):
        risks.append("funding_name_match_verify")
    if "no_funding_or_stage_evidence" in flags:
        risks.append("no_funding_or_stage_evidence")
    if not geo:
        risks.append("geo_unknown")
    if not size_ok:
        risks.append("headcount_unknown" if hc is None and not band else f"headcount_{hc if hc is not None else band}")
    if not contact:
        risks.append("no_founder_contact")
    if m["site_flags"]:
        risks.append("site_" + m["site_flags"].replace("|", "_"))
    if "domain_match_medium_verify" in flags:
        risks.append("domain_match_medium_verify")
    hard = [r for r in risks if r in ("consumer_keyword", "bio_keyword", "services_keyword") or r.startswith("site_")]
    strong_funding = st == "verified_in_window" and "funding_name_match_verify" not in risks \
        and "possible_past_series_a" not in risks
    evidence = strong_funding or program_in_window or listed_early or (segment == "bootstrapped_revenue" and mrr >= 10000)
    if strong_funding and geo and size_ok and contact and not [r for r in risks if r != "no_founder_contact" or not contact]:
        tier = "A_high_confidence"
    elif evidence and geo and contact and not hard and "possible_past_series_a" not in risks \
            and "domain_match_medium_verify" not in risks and (size_ok or band == "11-50" or hc is None):
        tier = "B_likely_fit"
    elif geo and not hard:
        tier = "C_needs_enrichment"
    else:
        tier = "D_review"
    fams = m["h1_family"] or ""
    days = None
    if m.get("funding_date") and st == "verified_in_window":
        try:
            days = (date.today() - date.fromisoformat(m["funding_date"][:10])).days
        except ValueError:
            days = None
    if tier == "D_review":
        wave = "Review fit"
    elif "exact_role" in fams or "H9" in tags:
        wave = "W1 Hot (days 1-7)"  # exact job or a warm path: never bury it, even if data is thin
    elif tier == "C_needs_enrichment":
        wave = "Enrich first"
    elif m["h1_strength"] == "S1" or "H10" in tags:
        wave = "W1 Hot (days 1-7)"
    elif m["h1_strength"] in ("S2", "S3") or (days is not None and days <= 180) or m["h2_tier"] == "fresh_60d" \
            or m["h4_tier"] in ("decay_window", "pre_decay"):
        wave = "W2 Warm (days 8-14)"
    elif (m["h3_tier"] == "A_verified" and hc is not None and 3 <= hc <= 20) or m["h5_strength"] == "S1" \
            or m["h6_strength"] == "S1" or mrr >= 10000 or m["h8_strength"] == "S1":
        wave = "W3 Cold-fit (days 15-30)"
    else:
        wave = "Nurture (day 30+)"
    return tier, wave, risks, segment


def suggested_action(wave: str, m: dict, risks: list[str]) -> str:
    ceo = (m["ceo_name"] or "the CEO").split(" ")[0] if m["ceo_name"] else "the CEO"
    raise_txt = ""
    if m["funding_amount"] and m["funding_window_status"] == "verified_in_window":
        raise_txt = f"${int(float(m['funding_amount'])) / 1e6:.1f}M raise"
    tags = set((m["matched_hypotheses"] or "").split("|"))
    if wave.startswith("W1"):
        if "H9" in tags:
            return f"Ask {m['h9_connection']} for a warm intro to {ceo}; send a 1-page 30/60/90 GTM plan with it"
        if "H10" in tags and m["h10_role"]:
            return (f"'{m['h10_role']}' has been open {m['h10_days_open']} days: DM {ceo} offering to cover it now as a "
                    f"30-day GTM-engineer retainer while they keep searching; apply in parallel")
        return (f"Apply to '{m['h1_best_role']}' AND DM {ceo} a 1-page 30/60/90 GTM plan referencing the posting; "
                f"offer a paid 30-day trial/retainer as the bridge")
    if wave.startswith("W2"):
        if m["h4_tier"] in ("decay_window", "pre_decay") and not raise_txt:
            return f"DM {ceo}: launch {m['h4_date']} ({m['h4_channel']}) traffic is decaying; offer a 30-day post-launch pipeline sprint"
        hook = f"congrats on the {raise_txt}" if raise_txt else "reference their open non-eng role"
        return f"DM {ceo}: {hook}; offer a 60-day GTM-engineer retainer to turn capital into pipeline"
    if wave.startswith("W3"):
        if m["h5_strength"] == "S1":
            return f"DM {ceo}: they run {m['h5_evidence'][:60]}; offer to wire it into a working outbound engine (retainer)"
        if float(m["h7_mrr"] or 0) >= 10000:
            return f"DM {ceo}: ${float(m['h7_mrr']):,.0f} verified MRR with no GTM team; offer a $3-5K/mo growth retainer"
        return f"Cold DM {ceo}: 3-point teardown of their outbound/website funnel + retainer offer"
    if wave.startswith("Nurture"):
        return "Follow founders on LinkedIn/X, engage with posts; revisit when a new signal fires"
    if wave == "Enrich first":
        missing = [r for r in risks if r in ("headcount_unknown", "no_founder_contact", "geo_unknown",
                                             "no_funding_or_stage_evidence")]
        return f"Enrich: {', '.join(missing) or 'funding evidence'} (Clay); then re-run build_master.py"
    return f"Check ICP fit ({', '.join(risks[:3])}); set stage 'Not a fit' or keep"


def components(breakdown: str) -> dict:
    pts = {k: int(v) for k, v in re.findall(r"([a-z0-9_]+)([+-]\d+)", breakdown or "")}
    return {
        "score_signals": pts.get("signals", 0) + pts.get("hypotheses", 0),
        "score_hiring": pts.get("h1_role", 0) + pts.get("h1_exact_or_hidden", 0) + pts.get("h10_stale", 0),
        "score_capital": pts.get("h2_capital", 0) + pts.get("h2_weak_evidence", 0) + pts.get("h7_revenue", 0),
        "score_founders": pts.get("h3_tier", 0) + pts.get("h6_founder_led", 0) + pts.get("h9_warm", 0),
        "score_size": pts.get("headcount", 0),
        "score_motion": pts.get("h4_launch", 0) + pts.get("h5_outbound", 0) + pts.get("h8_oss", 0),
        "score_penalties": pts.get("no_stage_evidence", 0) + pts.get("funding_unverified", 0) + pts.get("geo_unknown", 0)
        + pts.get("possible_past_series_a", 0) + pts.get("site_dead", 0),
    }


# --------------------------------------------------------------------------- 3. assemble rows
def assemble(conn) -> dict:
    master_rank.main()
    master = read_csv("master_priority.csv")
    founders = defaultdict(list)
    for f in conn.execute("SELECT * FROM founders ORDER BY founder_id"):
        founders[f["company_domain"]].append(dict(f))
    track = {r["company_domain"]: dict(r) for r in conn.execute("SELECT * FROM tracking")}
    emails = {r["company_domain"]: dict(r) for r in conn.execute("SELECT * FROM company_emails")}
    now = pdb.utcnow()
    rows = []
    for m in master:
        d = m["company_domain"]
        tier, wave, risks, segment = classify(m, founders[d], emails.get(d))
        comp = components(m["rank_breakdown"])
        ceo = next((f for f in founders[d] if re.search(r"\bceo\b|chief executive", f["role"] or "", re.I)),
                   (founders[d] or [None])[0])
        t = track.get(d, {})
        alltags = m["matched_hypotheses"].split("|") if m["matched_hypotheses"] else []
        tags = [t for t in alltags if t != "H0"]
        rows.append({
            "rank": int(m["rank"]), "company_domain": d, "company_name": m["company_name"], "wave": wave,
            "icp_tier": tier, "rank_score": int(m["rank_score"]),
            "stage": t.get("stage") or "Not started", "next_action": t.get("next_action"),
            "next_action_date": to_date(t.get("next_action_date")), "notes": t.get("notes"),
            "suggested_action": suggested_action(wave, m, risks),
            "signals": "+".join(tags), "signal_count": len(tags),
            **{h: ("Y" if h in tags else "") for h in HYP_COLS}, "segment": segment,
            "launch": f"{m['h4_tier']} {m['h4_date']} {m['h4_channel']}".strip(), "outbound_stack": m["h5_evidence"],
            "founder_led": m["h6_evidence"], "verified_mrr": to_int(m["h7_mrr"]), "oss_stars": to_int(m["h8_stars"]),
            "stale_role_days": to_int(m["h10_days_open"]), "warm_path": m["h9_connection"],
            "gtm_roles_open": to_int(m["gtm_roles_open"]), "open_roles_total": to_int(m["open_roles_total"]),
            "sources": m["sources"], "headcount_band": m["headcount_band"], "site_status": to_int(m["site_status"]),
            "tech_stack": m["tech_stack"], "linkedin_company": (f"https://www.linkedin.com/company/{m['linkedin_company']}"
                                                                if m["linkedin_company"] and "/" not in m["linkedin_company"]
                                                                else m["linkedin_company"] or None),
            "domain_confidence": m["resolution_confidence"],
            "why_now": m["why_now"], "pitch_hook": m["gtm_engineer_hook"],
            "hiring_role": m["h1_best_role"], "hiring_strength": m["h1_strength"], "role_url": m["h1_role_url"],
            "role_comp": m["h1_comp"],
            "last_raise_date": to_date(m["funding_date"]),
            "raise_stage": m["funding_stage"], "raise_amount_usd": to_int(m["funding_amount"]),
            "funding_status": m["funding_window_status"], "capital_evidence": m["h2_confidence"],
            "founder_tier": m["h3_tier"], "headcount": to_int(m["headcount"]), "sector": m["sector"],
            "h2_event": m["h2_event"],
            "hq_country": m["hq_country"], "batch_or_investor": m["batch_or_investor"], "one_liner": m["one_liner"],
            "ceo_name": (ceo or {}).get("full_name"), "ceo_role": (ceo or {}).get("role"),
            "ceo_linkedin": (ceo or {}).get("linkedin_url"),
            "ceo_x": f"@{ceo['x_handle']}" if ceo and ceo.get("x_handle") else None,
            "email": (emails.get(d) or {}).get("email"), "email_confidence": (emails.get(d) or {}).get("email_confidence"),
            "email_contact": (emails.get(d) or {}).get("contact_name"), "email_alt": (emails.get(d) or {}).get("email_alt"),
            "email_source": (emails.get(d) or {}).get("source_url") if str((emails.get(d) or {}).get("source_url") or "")
            .startswith("http") else None,
            "founders_count": len(founders[d]), "risk_flags": "|".join(risks),
            "score_breakdown": m["rank_breakdown"], **comp, "website": f"https://{d}",
        })
        conn.execute(
            "INSERT INTO scores (company_domain, rank, rank_score, score_signals, score_hiring, score_capital, "
            "score_founders, score_size, score_motion, score_penalties, icp_tier, wave, computed_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(company_domain) DO UPDATE SET rank=excluded.rank, rank_score=excluded.rank_score, "
            "score_signals=excluded.score_signals, score_hiring=excluded.score_hiring, score_capital=excluded.score_capital, "
            "score_founders=excluded.score_founders, score_size=excluded.score_size, "
            "score_motion=excluded.score_motion, score_penalties=excluded.score_penalties, icp_tier=excluded.icp_tier, "
            "wave=excluded.wave, "
            "computed_at=excluded.computed_at",
            (d, int(m["rank"]), int(m["rank_score"]), *comp.values(), tier, wave, now))
    conn.commit()
    # wave-first ordering inside the sheet keeps today's work on top; rank stays the global score order
    rows.sort(key=lambda r: (WAVES.index(r["wave"]), -r["rank_score"], r["headcount"] or 99))

    contacts = []
    by_domain = {r["company_domain"]: r for r in rows}
    for d, fl in founders.items():
        if d not in by_domain:
            continue
        for f in fl:
            contacts.append({
                "company_domain": d, "company_name": by_domain[d]["company_name"], "wave": by_domain[d]["wave"],
                "full_name": f["full_name"], "role": f["role"],
                "is_ceo": "Y" if re.search(r"\bceo\b|chief executive", f["role"] or "", re.I) else "",
                "is_technical": {1: "Y", 0: "N"}.get(f["is_technical"], "?"), "linkedin_url": f["linkedin_url"],
                "x_handle": f"@{f['x_handle']}" if f["x_handle"] else None,
                "email": f["verified_email"] or (emails.get(d) or {}).get("email")
                if (emails.get(d) or {}).get("contact_name") == f["full_name"] else f["verified_email"],
                "email_status": (emails.get(d) or {}).get("email_confidence")
                if (emails.get(d) or {}).get("contact_name") == f["full_name"] else f["verification_status"]})
    contacts.sort(key=lambda c: (WAVES.index(c["wave"]), c["company_domain"], c["is_ceo"] != "Y"))

    signals = []
    names = HYP_NAMES
    for c in conn.execute("SELECT company_domain, live_signal_proof FROM companies"):
        if c["company_domain"] not in by_domain:
            continue
        for p in json.loads(c["live_signal_proof"]):
            m = re.match(r"\[(\w+)\|(\w+)\|(\d{4}-\d\d-\d\d)\]\s*(.*)", p.get("snippet") or "")
            signals.append({
                "company_domain": c["company_domain"], "company_name": by_domain[c["company_domain"]]["company_name"],
                "hypothesis": p["hypothesis"], "signal": names.get(p["hypothesis"], p["hypothesis"]),
                "event_date": to_date(m.group(3)) if m else None,
                "detail": (m.group(4) if m else p.get("snippet") or "")[:250],
                "evidence_strength": m.group(2) if m else "", "source": p.get("source"), "url": p.get("url"),
                "captured_at": to_date(p.get("captured_at"))})
    signals.sort(key=lambda s: (s["company_domain"], s["hypothesis"]))

    roles = [r for r in read_csv("sublist_H1_job_postings.csv") if r["company_domain"] in by_domain]
    seen_urls = {r.get("url") for r in roles}
    for j in conn.execute("SELECT * FROM jobs WHERE strength IN ('S1','S2','S3')"):
        if j["company_domain"] in by_domain and j["url"] not in seen_urls:
            roles.append({"company_domain": j["company_domain"], "company_name": by_domain[j["company_domain"]]["company_name"],
                          "title": j["title"], "family": j["family"], "strength": j["strength"], "reasoning": "",
                          "location": j["location"], "salary_range": j["comp"], "equity_range": None, "experience": None,
                          "source": j["source"], "url": j["url"], "posted_at": to_date(j["posted_at"])})
    for r in roles:
        r["wave"] = by_domain[r["company_domain"]]["wave"]
    roles.sort(key=lambda r: (WAVES.index(r["wave"]), r["strength"], r["company_domain"]))
    log = [dict(r) for r in conn.execute("SELECT * FROM outreach_log ORDER BY touch_date, touch_id")]
    for r in log:
        r["touch_date"] = to_date(r["touch_date"])
    hyp = {}
    for h, f in HYP_FILES.items():
        rs = [r for r in read_csv(f) if r.get("company_domain") in by_domain]
        for r in rs:
            r["wave"] = by_domain[r["company_domain"]]["wave"]
            r["icp_tier"] = by_domain[r["company_domain"]]["icp_tier"]
            r["rank"] = by_domain[r["company_domain"]]["rank"]
        hyp[h] = sorted(rs, key=lambda r: r["rank"])
    events = []
    for e in conn.execute("SELECT * FROM funding_events ORDER BY event_date DESC"):
        if e["company_domain"] in by_domain:
            events.append({**dict(e), "company_name": by_domain[e["company_domain"]]["company_name"],
                           "event_date": to_date(e["event_date"])})
    unresolved = []
    for r in read_csv("sublist_formd_issuers.csv"):
        if r.get("resolution") != "resolved":
            unresolved.append({"source": "sec_form_d", "name": r["name"], "domain": "", "status": r["resolution"],
                               "date": to_date(r["last_filing"]), "location": f"{r['city']}, {r['state']}",
                               "amount_usd": to_int(r["amount_usd"]), "people": r["persons"][:200], "url": r["filing_url"],
                               "evidence": r["industry"]})
    for r in read_csv("sublist_unverified_stage_pool.csv"):
        unresolved.append({"source": r["source"], "name": r["name"], "domain": r["domain"], "status": r["reason"],
                           "date": to_date(r.get("date")), "location": "", "amount_usd": None, "people": "",
                           "url": r.get("url"), "evidence": r.get("evidence")})
    sources = [dict(r) for r in conn.execute(
        "SELECT hypothesis, source, query, raw_path, records_raw, records_unique, records_icp_pass, stop_reason, "
        "MAX(completed_at) AS completed_at FROM ingestion_runs GROUP BY hypothesis, source ORDER BY hypothesis, source")]
    return {"rows": rows, "contacts": contacts, "signals": signals, "roles": roles, "log": log, "hyp": hyp,
            "events": events, "unresolved": unresolved, "sources": sources}


# --------------------------------------------------------------------------- 4. workbook
FONT = "Arial"
HEADER_ROW = 1
F_BASE = Font(name=FONT, size=10)
F_HEAD = Font(name=FONT, size=10, bold=True, color="FFFFFF")
F_TITLE = Font(name=FONT, size=16, bold=True)
F_H2 = Font(name=FONT, size=12, bold=True)
F_LINK = Font(name=FONT, size=10, color="0563C1", underline="single")
F_INPUT = Font(name=FONT, size=10, color="0000FF")
F_EX = Font(name=FONT, size=10, italic=True, color="808080")
FILL_HEAD = PatternFill("solid", fgColor="1F3864")
FILL_INPUT_HEAD = PatternFill("solid", fgColor="BF8F00")
FILL_INPUT = PatternFill("solid", fgColor="FFF2CC")
FILL_FORMULA_HEAD = PatternFill("solid", fgColor="375623")
FILL_SECTION = PatternFill("solid", fgColor="D9E1F2")
THIN = Side(style="thin", color="BFBFBF")
WRAP = Alignment(wrap_text=True, vertical="top")

# (header, width, kind) ; kind: data | input | formula | link | date | money
MASTER_COLS = [
    ("rank", 6, "data"), ("company_domain", 22, "data"), ("company_name", 22, "data"), ("wave", 20, "data"),
    ("icp_tier", 18, "data"), ("rank_score", 9, "data"),
    ("stage", 14, "input"), ("next_action", 30, "input"), ("next_action_date", 13, "input_date"),
    ("notes", 30, "input"), ("due", 7, "formula"), ("touches", 8, "formula"), ("last_touch", 12, "formula"),
    ("suggested_action", 50, "data"), ("signals", 10, "data"), ("signal_count", 7, "data"),
    *[(h, 5, "data") for h in ["H1", "H2", "H3", "H4", "H5", "H6", "H7", "H8", "H9", "H10"]], ("segment", 16, "data"),
    ("why_now", 45, "data"), ("pitch_hook", 60, "data"),
    ("hiring_role", 30, "data"), ("hiring_strength", 7, "data"), ("role_url", 14, "link"), ("role_comp", 22, "data"),
    ("last_raise_date", 12, "date"), ("days_since_raise", 9, "formula"), ("raise_stage", 10, "data"),
    ("raise_amount_usd", 14, "money"), ("funding_status", 22, "data"), ("capital_evidence", 18, "data"),
    ("h2_event", 30, "data"), ("founder_tier", 14, "data"), ("launch", 26, "data"), ("outbound_stack", 34, "data"),
    ("founder_led", 30, "data"), ("verified_mrr", 11, "money"), ("oss_stars", 9, "data"), ("stale_role_days", 8, "data"),
    ("warm_path", 18, "data"), ("gtm_roles_open", 7, "data"), ("open_roles_total", 7, "data"),
    ("headcount", 9, "data"), ("headcount_band", 9, "data"), ("sector", 18, "data"), ("hq_country", 7, "data"),
    ("batch_or_investor", 16, "data"), ("sources", 22, "data"), ("one_liner", 45, "data"),
    ("site_status", 7, "data"), ("tech_stack", 30, "data"), ("linkedin_company", 12, "link"),
    ("domain_confidence", 9, "data"),
    ("ceo_name", 20, "data"), ("ceo_role", 14, "data"), ("ceo_linkedin", 14, "link"), ("ceo_x", 14, "data"),
    ("email", 28, "input"), ("email_confidence", 22, "data"), ("email_contact", 18, "data"), ("email_alt", 24, "data"),
    ("email_source", 10, "link"),
    ("founders_count", 7, "data"), ("risk_flags", 30, "data"), ("score_breakdown", 45, "data"),
    ("score_signals", 7, "data"), ("score_hiring", 7, "data"), ("score_capital", 7, "data"),
    ("score_founders", 7, "data"), ("score_size", 7, "data"), ("score_motion", 7, "data"), ("score_penalties", 7, "data"),
    ("website", 14, "link"),
]


def style_header(ws, cols, row=1):
    for i, (name, width, kind) in enumerate(cols, 1):
        c = ws.cell(row=row, column=i, value=name)
        c.font = F_HEAD
        c.fill = FILL_INPUT_HEAD if kind.startswith("input") else FILL_FORMULA_HEAD if kind == "formula" else FILL_HEAD
        c.alignment = Alignment(wrap_text=True, vertical="center")
        ws.column_dimensions[get_column_letter(i)].width = width
    ws.row_dimensions[row].height = 30
    ws.freeze_panes = ws.cell(row=row + 1, column=4)


def write_table(ws, cols, rows, start=2):
    for r_i, r in enumerate(rows, start):
        for c_i, (name, _, kind) in enumerate(cols, 1):
            v = clean(r.get(name))
            c = ws.cell(row=r_i, column=c_i, value=v)
            c.font = F_BASE
            if kind == "link" and v:
                c.hyperlink = v
                c.value = "open"
                c.font = F_LINK
            elif kind in ("date", "input_date"):
                c.number_format = "yyyy-mm-dd"
            elif kind == "money":
                c.number_format = '$#,##0;($#,##0);"-"'
            if kind.startswith("input"):
                c.fill = FILL_INPUT
                c.font = F_INPUT


def clean(v):
    """Drop control characters and neutralise strings Excel would read as formulas."""
    if v == "" or v is None:
        return None
    if isinstance(v, str):
        v = ILLEGAL_CHARACTERS_RE.sub("", v)
        if v[:1] in ("=", "+", "@") or (v[:1] == "-" and not v[1:2].isdigit()):
            v = " " + v
    return v


def col(cols, name):
    return get_column_letter([c[0] for c in cols].index(name) + 1)


def build_workbook(data: dict) -> dict:
    rows = data["rows"]
    wb = Workbook()
    ws0 = wb.active
    ws0.title = "Start_Here"
    dash = wb.create_sheet("Dashboard")
    ws = wb.create_sheet("Master")
    wc = wb.create_sheet("Contacts")
    wsig = wb.create_sheet("Signals")
    wr = wb.create_sheet("Open_Roles")
    wl = wb.create_sheet("Outreach_Log")
    wdd = wb.create_sheet("Data_Dictionary")
    n = len(rows)
    last = n + 1

    # ---------------- Outreach_Log (first, Master formulas reference it)
    LOG_COLS = [("touch_date", 12, "input_date"), ("company_domain", 22, "input"), ("contact_name", 20, "input"),
                ("channel", 14, "input"), ("touch_type", 13, "input"), ("angle", 30, "input"),
                ("outcome", 18, "input"), ("next_step", 30, "input"), ("notes", 40, "input"),
                ("company_name", 22, "formula"), ("wave", 20, "formula")]
    style_header(wl, LOG_COLS)
    wl.freeze_panes = "C2"
    example = {"touch_date": date.today(), "company_domain": EXAMPLE_DOMAIN, "contact_name": "Jane Doe (CEO)",
               "channel": "LinkedIn", "touch_type": "First touch", "angle": "Hiring 'Founding GTM Engineer' + 30/60/90 plan",
               "outcome": "Sent", "next_step": "Follow-up in 3 days", "notes": "EXAMPLE ROW - keep or delete; ignored by all counts"}
    log_rows = [example] + data["log"]
    LOG_ROWS = 1000
    for i in range(2, LOG_ROWS + 2):
        r = log_rows[i - 2] if i - 2 < len(log_rows) else {}
        for c_i, (name, _, kind) in enumerate(LOG_COLS, 1):
            c = wl.cell(row=i, column=c_i)
            if kind.startswith("input"):
                c.value = clean(r.get(name))
                c.fill = FILL_INPUT
                c.font = F_EX if i == 2 else F_INPUT
                if kind == "input_date":
                    c.number_format = "yyyy-mm-dd"
        wl.cell(row=i, column=10, value=f'=IF(B{i}="","",IFERROR(INDEX(Master!$C:$C,MATCH(B{i},Master!$B:$B,0)),"not in Master"))').font = F_BASE
        wl.cell(row=i, column=11, value=f'=IF(B{i}="","",IFERROR(INDEX(Master!$D:$D,MATCH(B{i},Master!$B:$B,0)),""))').font = F_BASE
    for rng, opts in (("D", CHANNELS), ("E", TOUCH_TYPES), ("G", OUTCOMES)):
        dv = DataValidation(type="list", formula1='"' + ",".join(opts) + '"', allow_blank=True)
        wl.add_data_validation(dv)
        dv.add(f"{rng}2:{rng}{LOG_ROWS + 1}")
    wl.auto_filter.ref = f"A1:K{LOG_ROWS + 1}"

    # ---------------- Master
    style_header(ws, MASTER_COLS)
    write_table(ws, MASTER_COLS, rows)
    C = {name: col(MASTER_COLS, name) for name, _, _ in MASTER_COLS}
    closed = ",".join(f'{C["stage"]}{{r}}="{s}"' for s in CLOSED)
    for r in range(2, last + 1):
        ws[f"{C['touches']}{r}"] = f"=COUNTIF(Outreach_Log!$B:$B,{C['company_domain']}{r})"
        ws[f"{C['last_touch']}{r}"] = (f'=IF({C["touches"]}{r}=0,"",_xlfn.MAXIFS(Outreach_Log!$A:$A,'
                                       f'Outreach_Log!$B:$B,{C["company_domain"]}{r}))')
        ws[f"{C['last_touch']}{r}"].number_format = "yyyy-mm-dd"
        ws[f"{C['due']}{r}"] = (f'=IF(AND({C["next_action_date"]}{r}<>"",{C["next_action_date"]}{r}<=TODAY(),'
                                f'NOT(OR({closed.format(r=r)}))),"DUE","")')
        ws[f"{C['days_since_raise']}{r}"] = f'=IF({C["last_raise_date"]}{r}="","",TODAY()-{C["last_raise_date"]}{r})'
        for k in ("touches", "last_touch", "due", "days_since_raise"):
            ws[f"{C[k]}{r}"].font = F_BASE
    dv = DataValidation(type="list", formula1='"' + ",".join(STAGES) + '"', allow_blank=True)
    ws.add_data_validation(dv)
    dv.add(f"{C['stage']}2:{C['stage']}{last}")
    dvd = DataValidation(type="date", operator="greaterThan", formula1="DATE(2026,1,1)", allow_blank=True)
    ws.add_data_validation(dvd)
    dvd.add(f"{C['next_action_date']}2:{C['next_action_date']}{last}")
    ws.auto_filter.ref = f"A1:{get_column_letter(len(MASTER_COLS))}{last}"
    red = PatternFill("solid", fgColor="F8CBAD")
    ws.conditional_formatting.add(f"{C['due']}2:{C['due']}{last}",
                                  CellIsRule(operator="equal", formula=['"DUE"'], fill=red, font=Font(name=FONT, bold=True)))
    for conf, color in (("guess_first_at_domain", "808080"), ("clay_invalid", "C00000")):
        ws.conditional_formatting.add(f"{C['email']}2:{C['email']}{last}", FormulaRule(
            formula=[f'{C["email_confidence"]}2="{conf}"'], font=Font(name=FONT, italic=True, color=color)))
    tier_fill = {"A_high_confidence": "C6EFCE", "B_likely_fit": "DDEBF7", "C_needs_enrichment": "FFF2CC",
                 "D_review": "F2F2F2"}
    for t, color in tier_fill.items():
        ws.conditional_formatting.add(f"{C['icp_tier']}2:{C['icp_tier']}{last}",
                                      FormulaRule(formula=[f'{C["icp_tier"]}2="{t}"'], fill=PatternFill("solid", fgColor=color)))
    ws[f"{C['rank_score']}1"].comment = Comment(
        "Score from scripts/master_rank.py: 25 per hypothesis matched + hiring role (S1 20 / S2 12 / S3 6, exact or "
        "hidden GTM role +8) + capital recency (<=60d 15, <=180d 10, <=365d 6, older 3, YC batch check 8; "
        "name-match evidence -4) + founders (A 10 / B 5) + headcount (3-20: 5, 21-35: 2) - penalties. "
        "Components in score_* columns.", "build_master")
    ws[f"{C['stage']}1"].comment = Comment("YOU EDIT (yellow). Dropdown. Synced to DB table `tracking` on rebuild.",
                                           "build_master")

    # ---------------- Contacts
    CONTACT_COLS = [("company_domain", 22, "data"), ("company_name", 22, "data"), ("wave", 20, "data"),
                    ("full_name", 22, "data"), ("role", 16, "data"), ("is_ceo", 6, "data"), ("is_technical", 8, "data"),
                    ("linkedin_url", 12, "link"), ("x_handle", 16, "data"), ("email", 26, "input"),
                    ("email_status", 12, "data")]
    style_header(wc, CONTACT_COLS)
    write_table(wc, CONTACT_COLS, data["contacts"])
    wc.auto_filter.ref = f"A1:{get_column_letter(len(CONTACT_COLS))}{len(data['contacts']) + 1}"

    # ---------------- Signals
    SIG_COLS = [("company_domain", 22, "data"), ("company_name", 22, "data"), ("hypothesis", 6, "data"),
                ("signal", 16, "data"), ("event_date", 12, "date"), ("detail", 70, "data"),
                ("evidence_strength", 18, "data"), ("source", 22, "data"), ("url", 10, "link"),
                ("captured_at", 12, "date")]
    style_header(wsig, SIG_COLS)
    write_table(wsig, SIG_COLS, data["signals"])
    wsig.auto_filter.ref = f"A1:{get_column_letter(len(SIG_COLS))}{len(data['signals']) + 1}"

    # ---------------- Open_Roles
    ROLE_COLS = [("company_domain", 22, "data"), ("company_name", 22, "data"), ("wave", 20, "data"),
                 ("title", 34, "data"), ("family", 16, "data"), ("strength", 7, "data"), ("reasoning", 40, "data"),
                 ("location", 22, "data"), ("salary_range", 18, "data"), ("equity_range", 14, "data"),
                 ("experience", 14, "data"), ("source", 12, "data"), ("url", 10, "link")]
    style_header(wr, ROLE_COLS)
    write_table(wr, ROLE_COLS, data["roles"])
    wr.auto_filter.ref = f"A1:{get_column_letter(len(ROLE_COLS))}{len(data['roles']) + 1}"

    # ---------------- per-hypothesis tabs, funding events, unresolved TAM, sources
    extra = {}
    for tab, rs in data["hyp"].items():
        wsx = wb.create_sheet(tab)
        lead = ["rank", "company_domain", "company_name", "wave", "icp_tier"]
        keys = lead + [k for k in (list(rs[0].keys()) if rs else []) if k not in lead][:28]
        cols = [(k, 12 if k in ("rank", "wave", "icp_tier") else 22 if k in ("company_domain", "company_name") else
                 (10 if "url" in k else 24), "link" if k.endswith("url") else "data") for k in keys]
        style_header(wsx, cols)
        write_table(wsx, cols, rs)
        wsx.freeze_panes = "D2"
        if rs:
            wsx.auto_filter.ref = f"A1:{get_column_letter(len(cols))}{len(rs) + 1}"
        extra[tab] = len(rs)
    EV_COLS = [("event_date", 12, "date"), ("company_domain", 22, "data"), ("company_name", 22, "data"),
               ("stage", 10, "data"), ("amount_usd", 14, "money"), ("confidence", 14, "data"), ("source", 22, "data"),
               ("detail", 60, "data"), ("url", 10, "link")]
    wev = wb.create_sheet("Funding_Events")
    style_header(wev, EV_COLS)
    write_table(wev, EV_COLS, data["events"])
    wev.auto_filter.ref = f"A1:{get_column_letter(len(EV_COLS))}{len(data['events']) + 1}"
    UN_COLS = [("source", 18, "data"), ("name", 28, "data"), ("domain", 20, "input"), ("status", 22, "data"),
               ("date", 12, "date"), ("location", 20, "data"), ("amount_usd", 13, "money"), ("people", 40, "data"),
               ("evidence", 40, "data"), ("url", 10, "link")]
    wun = wb.create_sheet("Unresolved_TAM")
    style_header(wun, UN_COLS)
    write_table(wun, UN_COLS, data["unresolved"])
    wun.auto_filter.ref = f"A1:{get_column_letter(len(UN_COLS))}{len(data['unresolved']) + 1}"
    SRC_COLS = [("hypothesis", 6, "data"), ("source", 24, "data"), ("query", 60, "data"), ("raw_path", 40, "data"),
                ("records_raw", 10, "data"), ("records_unique", 10, "data"), ("records_icp_pass", 10, "data"),
                ("stop_reason", 12, "data"), ("completed_at", 20, "data")]
    wsrc = wb.create_sheet("Sources")
    style_header(wsrc, SRC_COLS)
    write_table(wsrc, SRC_COLS, data["sources"])
    # ---------------- Dashboard
    build_dashboard(dash, C, last)
    # ---------------- Start_Here + dictionary
    build_start(ws0, data)
    build_dictionary(wdd, [("Master", MASTER_COLS), ("Contacts", CONTACT_COLS), ("Signals", SIG_COLS),
                           ("Open_Roles", ROLE_COLS), ("Outreach_Log", LOG_COLS), ("Funding_Events", EV_COLS),
                           ("Unresolved_TAM", UN_COLS), ("Sources", SRC_COLS)])
    for sheet in wb.worksheets:
        sheet.sheet_view.zoomScale = 90
    OUT_DIR.mkdir(exist_ok=True)
    wb.save(XLSX)
    return {"Master": n, "Contacts": len(data["contacts"]), "Signals": len(data["signals"]),
            "Open_Roles": len(data["roles"]), "Outreach_Log": len(data["log"]), **extra,
            "Funding_Events": len(data["events"]), "Unresolved_TAM": len(data["unresolved"])}


def section(ws, r, text):
    ws.cell(row=r, column=1, value=text).font = F_H2
    for c in range(1, 9):
        ws.cell(row=r, column=c).fill = FILL_SECTION


def build_dashboard(ws, C, last):
    M = lambda k: f"Master!${C[k]}$2:${C[k]}${last}"  # noqa: E731
    ws.column_dimensions["A"].width = 34
    for c in "BCDEFGH":
        ws.column_dimensions[c].width = 15
    ws["A1"] = "GTM Master — Dashboard"
    ws["A1"].font = F_TITLE
    ws["A2"] = "All numbers are live formulas over Master and Outreach_Log. Yellow cells are inputs you set."
    ws["A2"].font = F_EX

    section(ws, 4, "Inputs")
    inputs = [("Campaign start date", date.today(), "yyyy-mm-dd", "Day 1 of the 30-day sprint (default: build date)"),
              ("Target touches per working day", 15, "0", "Assumption: 15 personalised touches/day is sustainable solo"),
              ("Working days per week", 5, "0", "Assumption"),
              ("Target reply rate", 0.15, "0.0%", "Assumption: 10-20% for signal-based founder DMs"),
              ("Target call rate (calls / replies)", 0.4, "0.0%", "Assumption")]
    for i, (label, v, fmt, note) in enumerate(inputs, 5):
        ws.cell(row=i, column=1, value=label).font = F_BASE
        c = ws.cell(row=i, column=2, value=v)
        c.font, c.fill, c.number_format = F_INPUT, FILL_INPUT, fmt
        ws.cell(row=i, column=3, value=note).font = F_EX

    section(ws, 11, "The list (fit x timing)")
    ws.cell(row=12, column=1, value="Wave \\ ICP tier").font = Font(name=FONT, bold=True)
    for j, t in enumerate(TIERS, 2):
        ws.cell(row=12, column=j, value=t).font = Font(name=FONT, bold=True, size=9)
    ws.cell(row=12, column=6, value="Total").font = Font(name=FONT, bold=True)
    for i, w in enumerate(WAVES, 13):
        ws.cell(row=i, column=1, value=w).font = F_BASE
        for j, t in enumerate(TIERS, 2):
            ws.cell(row=i, column=j, value=f'=COUNTIFS({M("wave")},$A{i},{M("icp_tier")},{get_column_letter(j)}$12)').font = F_BASE
        ws.cell(row=i, column=6, value=f"=SUM(B{i}:E{i})").font = Font(name=FONT, bold=True)
    tr = 13 + len(WAVES)
    ws.cell(row=tr, column=1, value="Total").font = Font(name=FONT, bold=True)
    for j in range(2, 7):
        L = get_column_letter(j)
        ws.cell(row=tr, column=j, value=f"=SUM({L}13:{L}{tr - 1})").font = Font(name=FONT, bold=True)

    section(ws, tr + 2, "Signals coverage")
    r0 = tr + 3
    for i, h in enumerate(HYP_COLS, r0):
        ws.cell(row=i, column=1, value=f"{HYP_NAMES[h]} ({h})").font = F_BASE
        ws.cell(row=i, column=2, value=f'=COUNTIF({M(h)},"Y")').font = F_BASE
    r0 += len(HYP_COLS) - 3
    for i, k in enumerate((3, 2, 1), r0 + 3):
        ws.cell(row=i, column=1, value=f"Companies with {k}{'+' if k == 3 else ''} signal(s)").font = F_BASE
        ws.cell(row=i, column=2, value=(f'=COUNTIF({M("signal_count")},">=3")' if k == 3 else
                                        f'=COUNTIF({M("signal_count")},{k})')).font = F_BASE

    section(ws, r0 + 7, "Pipeline (Master.stage)")
    p0 = r0 + 8
    for i, s in enumerate(STAGES, p0):
        ws.cell(row=i, column=1, value=s).font = F_BASE
        ws.cell(row=i, column=2, value=f'=COUNTIF({M("stage")},$A{i})').font = F_BASE
    pe = p0 + len(STAGES)
    ws.cell(row=pe, column=1, value="Actions due today or overdue").font = Font(name=FONT, bold=True)
    ws.cell(row=pe, column=2, value=f'=COUNTIF({M("due")},"DUE")').font = Font(name=FONT, bold=True, color="C00000")

    section(ws, pe + 2, "Activity (Outreach_Log, example row excluded)")
    a0 = pe + 3
    L = "Outreach_Log!"
    ex = f'{L}$B:$B,"<>{EXAMPLE_DOMAIN}"'
    act = [("Touches logged (all time)", f'=COUNTIFS({L}$A:$A,">0",{ex})', "0"),
           ("Touches in last 7 days", f'=COUNTIFS({L}$A:$A,">="&(TODAY()-6),{L}$A:$A,"<="&TODAY(),{ex})', "0"),
           ("Companies touched", f'=COUNTIF({M("touches")},">0")', "0"),
           ("Replies (positive + negative)", f'=COUNTIFS({L}$G:$G,"Replied*",{ex})', "0"),
           ("Calls booked", f'=COUNTIFS({L}$G:$G,"Call booked",{ex})', "0"),
           ("Reply rate (replies / companies touched)", f"=IFERROR(B{a0 + 3}/B{a0 + 2},0)", "0.0%")]
    for i, (label, f, fmt) in enumerate(act, a0):
        ws.cell(row=i, column=1, value=label).font = F_BASE
        c = ws.cell(row=i, column=2, value=f)
        c.font, c.number_format = F_BASE, fmt

    section(ws, a0 + 7, "30-day plan vs actual")
    h = a0 + 8
    for j, t in enumerate(["Week", "From", "To", "Focus", "Target touches", "Actual touches", "Target replies",
                           "Actual replies"], 1):
        ws.cell(row=h, column=j, value=t).font = Font(name=FONT, bold=True)
    focus = ["W1 Hot: apply + founder DM (hiring GTM now)", "W2 Warm: fresh capital + adjacent roles; W1 follow-ups",
             "W3 Cold-fit: all-technical 3-20 teams; enrich C-tier", "Follow-ups, calls, proposals; Nurture"]
    for k in range(4):
        r = h + 1 + k
        ws.cell(row=r, column=1, value=f"Week {k + 1}").font = F_BASE
        ws.cell(row=r, column=2, value=f"=$B$5+{7 * k}").number_format = "yyyy-mm-dd"
        ws.cell(row=r, column=3, value=f"=$B$5+{7 * k + 6}").number_format = "yyyy-mm-dd"
        ws.cell(row=r, column=4, value=focus[k]).font = F_BASE
        ws.cell(row=r, column=5, value="=$B$6*$B$7").font = F_BASE
        ws.cell(row=r, column=6, value=f'=COUNTIFS({L}$A:$A,">="&B{r},{L}$A:$A,"<="&C{r},{ex})').font = F_BASE
        ws.cell(row=r, column=7, value=f"=ROUND(E{r}*$B$8,0)").font = F_BASE
        ws.cell(row=r, column=8, value=f'=COUNTIFS({L}$A:$A,">="&B{r},{L}$A:$A,"<="&C{r},{L}$G:$G,"Replied*",{ex})').font = F_BASE
        for cc in (2, 3):
            ws.cell(row=r, column=cc).font = F_BASE
    tot = h + 5
    ws.cell(row=tot, column=1, value="30-day total").font = Font(name=FONT, bold=True)
    for cc in "EFGH":
        ws[f"{cc}{tot}"] = f"=SUM({cc}{h + 1}:{cc}{h + 4})"
        ws[f"{cc}{tot}"].font = Font(name=FONT, bold=True)
    ws.cell(row=tot + 1, column=1, value="Calls implied by targets").font = F_BASE
    ws.cell(row=tot + 1, column=2, value=f"=ROUND(G{tot}*$B$9,0)").font = F_BASE

    section(ws, tot + 3, "Email coverage (Master.email_confidence)")
    e0 = tot + 4
    for i, k in enumerate(EMAIL_CONF, e0):
        ws.cell(row=i, column=1, value=k).font = F_BASE
        ws.cell(row=i, column=2, value=f'=COUNTIF({M("email_confidence")},$A{i})').font = F_BASE
    ws.cell(row=e0 + len(EMAIL_CONF), column=1, value="No email").font = Font(name=FONT, bold=True)
    ws.cell(row=e0 + len(EMAIL_CONF), column=2, value=f'=COUNTBLANK({M("email")})').font = Font(name=FONT, bold=True)
    for row in ws.iter_rows():
        for c in row:
            if c.font is None or c.font.name != FONT:
                c.font = Font(name=FONT, size=10, bold=c.font.bold if c.font else False)


def build_start(ws, data):
    rows = data["rows"]
    ws.column_dimensions["A"].width = 28
    ws.column_dimensions["B"].width = 110
    lines = [
        ("GTM Master — start here", None, F_TITLE),
        (f"Built {date.today().isoformat()} from db/gtm_pipeline.db by scripts/build_master.py. "
         f"{len(rows)} companies, 1 row each, keyed by company_domain.", None, F_EX),
        ("", None, None),
        ("What this is", "The ICP universe (H0): every US/UK/EU pre-seed→Series A B2B/AI startup found across SEC Form D, "
         "37 VC portfolio networks, YC/Techstars/500/Alchemist/EF/Speedrun/Antler, every public ATS board, HN 'Who is "
         "hiring', 15 funding-news outlets, TrustMRR and launch channels - scored on ten live signals. Goal: land one "
         "Founding GTM / Growth Engineer role or a $3-5K/mo retainer.", None),
        ("The 10 signals", "H1 hiring GTM now | H2 fresh capital | H3 all-technical founders | H4 launch 14-45d ago "
         "(decay) | H5 building outbound stack | H6 founder-led sales | H7 verified MRR, no GTM team | H8 open-source "
         "traction | H9 warm path (your LinkedIn) | H10 GTM role open 60+ days. Each has its own tab with evidence.", None),
        ("Segments", "venture_track = funded/accelerator/VC-listed (job + retainer targets). bootstrapped_revenue = "
         "TrustMRR-verified revenue, no funding evidence (retainer targets).", None),
        ("Unresolved_TAM", "In-ICP companies whose website could not be verified (Form D issuers, HN/ATS/news names) "
         "or without early-stage proof. Fill the yellow domain cell, or run them through Clay.", None),
        ("Fit vs timing", "icp_tier = how sure we are they match the ICP (A best). wave = when to contact them "
         "(W1 this week). rank_score = overall priority (see header comment).", None),
        ("", None, None),
        ("DAILY ROUTINE (45-90 min)", None, F_H2),
        ("1. Dashboard", "Check 'Actions due today' and last-7-day touches vs target.", None),
        ("2. Master", "Filter due = DUE first. Then filter wave = current week's wave; work top-down (sheet is "
         "sorted wave → rank_score).", None),
        ("3. Act", "Use suggested_action + pitch_hook + why_now. Founder links in ceo_linkedin / Contacts.", None),
        ("4. Log", "Every touch = one row in Outreach_Log (date, domain, contact, channel, type, outcome). "
         "touches / last_touch in Master update automatically.", None),
        ("5. Update", "Set Master.stage and next_action + next_action_date (the due flag fires on that date).", None),
        ("6. Rebuild (optional)", "python3 scripts/build_master.py — syncs your yellow cells + log into the DB, "
         "re-scores with any new data, rewrites this file. Your edits are kept.", None),
        ("", None, None),
        ("COLOUR LEGEND", None, F_H2),
        ("Yellow cell / gold header", "Input: you edit these (stage, next_action, next_action_date, notes, "
         "Outreach_Log, Dashboard inputs).", None),
        ("Green header", "Formula: do not type over (due, touches, last_touch, days_since_raise).", None),
        ("Navy header", "Data from the pipeline: overwritten on rebuild (edit upstream, not here).", None),
        ("", None, None),
        ("WAVES (timing)", None, F_H2),
        ("W1 Hot (days 1-7)", "Exact GTM/Growth Engineer role, any direct GTM role (S1), a GTM role open 60+ days (H10) "
         "or a warm path (H9). Apply AND DM the CEO.", None),
        ("W2 Warm (days 8-14)", "Adjacent GTM role (S2/S3), raise in the last 180 days / fresh YC batch, or a launch in "
         "the last 45 days (H4).", None),
        ("W3 Cold-fit (days 15-30)", "All-technical 3-20 person team, outbound tooling on site (H5 S1), founder-led "
         "sales (H6 S1), >= $10K verified MRR (H7) or strong OSS (H8 S1): teardown-led cold DM.", None),
        ("Nurture (day 30+)", "Fits, no timing signal. Follow and engage; re-scored when a signal fires.", None),
        ("Enrich first", "Tier C: missing headcount / founder contact / funding evidence.", None),
        ("Review fit", "Tier D: possible consumer / bio / services or unknown geo. Decide keep or 'Not a fit'.", None),
        ("", None, None),
        ("ICP TIERS (fit)", None, F_H2),
        ("A_high_confidence", "Verified: US/UK/EU HQ, 2-35 people, external raise (pre-seed→A) since 2025-05-01 "
         "(Form D, linked news, domain-keyed round), founder on file, no risk flags.", None),
        ("B_likely_fit", "Early-stage evidence (raise, accelerator batch, VC portfolio stage <= A, or >= $10K MRR), "
         "location and founder known, live site, no hard flags; size 1-35 or 11-50 band.", None),
        ("C_needs_enrichment", "Location known, but headcount, founder contact or funding evidence missing.", None),
        ("D_review", "Keyword risk (consumer/bio/services), unknown geo or conflicting funding evidence.", None),
        ("", None, None),
        ("OUTREACH_LOG EXAMPLE", "Row 2 of Outreach_Log is an example (example.com) and is excluded from every count. "
         "Format: 2026-11-04 | acme.ai | Jane Doe (CEO) | LinkedIn | First touch | angle | Sent | Follow-up in 3 days.",
         None),
        ("Sources & method", "README.md (H1/H2/H3 sections), raw payloads in raw_sources/, per-hypothesis CSVs in "
         "sublists/. Every Master row links to its evidence in Signals.", None),
    ]
    for i, (a, b, f) in enumerate(lines, 1):
        ca = ws.cell(row=i, column=1, value=a)
        ca.font = f or Font(name=FONT, size=10, bold=bool(b))
        if b:
            cb = ws.cell(row=i, column=2, value=b)
            cb.font, cb.alignment = F_BASE, WRAP
        if a.startswith("Yellow"):
            ca.fill = FILL_INPUT
        elif a.startswith("Green"):
            ca.fill, ca.font = FILL_FORMULA_HEAD, F_HEAD
        elif a.startswith("Navy"):
            ca.fill, ca.font = FILL_HEAD, F_HEAD


DEFS = {
    "rank": "Global order by rank_score (1 = highest).", "company_domain": "Primary key: normalized root domain.",
    "wave": "When to contact (see Start_Here).", "icp_tier": "How sure we are of ICP fit (see Start_Here).",
    "rank_score": "Priority score; components in score_* columns.", "stage": "INPUT pipeline stage (dropdown).",
    "next_action": "INPUT your next step.", "next_action_date": "INPUT date of next step (drives due).",
    "notes": "INPUT free text.", "due": "FORMULA: DUE when next_action_date <= today and stage not closed.",
    "touches": "FORMULA: rows in Outreach_Log for this domain.", "last_touch": "FORMULA: latest touch_date.",
    "suggested_action": "Default next step for the wave, personalised with CEO / role / raise.",
    "signals": "Signal hypotheses matched (H1-H10; H0 = universe membership, not a signal).",
    "signal_count": "Number of signal hypotheses matched.", "H1": "Y: hiring GTM now.", "H2": "Y: capital event in window.",
    "H3": "Y: technical founding team.", "H4": "Y: launch in last 120 days.", "H5": "Y: outbound stack being built.",
    "H6": "Y: founder-led sales.", "H7": "Y: verified MRR >= $5K.", "H8": "Y: open-source traction.",
    "H9": "Y: warm path in your LinkedIn.", "H10": "Y: GTM role open 60+ days.",
    "segment": "venture_track | bootstrapped_revenue.", "h2_event": "Best capital event (date, stage, amount, source).",
    "launch": "H4 tier, date, channel.", "outbound_stack": "H5 evidence.", "founder_led": "H6 evidence.",
    "verified_mrr": "H7 TrustMRR MRR (USD).", "oss_stars": "H8 GitHub stars across listed repos.",
    "stale_role_days": "H10 days the GTM role has been open.", "warm_path": "H9 connection name.",
    "gtm_roles_open": "Live S1/S2 GTM postings.", "open_roles_total": "All postings seen.",
    "headcount_band": "Source band when no exact count.", "sources": "Every source that lists the company.",
    "site_status": "Homepage HTTP status.", "tech_stack": "Tools detected on site / DNS.",
    "linkedin_company": "Company LinkedIn page.", "domain_confidence": "Name->domain resolution confidence (blank = "
    "domain published by the source).", "score_motion": "H4 + H5 + H8 points.",
    "stage": "INPUT pipeline stage (dropdown) / funding stage in event tabs.", "amount_usd": "USD.",
    "confidence": "Evidence strength.", "status": "Why unresolved.", "people": "Form D executives/directors.",
    "query": "Exact query / filter used.", "raw_path": "Raw payload location.", "records_raw": "Before filters.",
    "records_unique": "Unique entities.", "records_icp_pass": "Passed ICP.", "stop_reason": "Pagination end reason.",
    "completed_at": "Run end (UTC).", "name": "Entity name.", "date": "Event / filing / post date.",
    "why_now": "One-line reason combining the live signals.",
    "pitch_hook": "H1 role-specific reason a GTM Engineer beats the hire they posted.",
    "hiring_role": "Best GTM-type open role (H1).", "hiring_strength": "S1 direct/exact, S2 adjacent, S3 paradox.",
    "role_url": "Posting URL.", "role_comp": "Posted salary / equity.",
    "last_raise_date": "Latest dated capital event <= Series A.", "days_since_raise": "FORMULA: today - last_raise_date.",
    "raise_stage": "pre_seed | seed | series_a | unknown.", "raise_amount_usd": "USD; EUR x1.10, GBP x1.30 approx.",
    "funding_status": "verified_in_window | accelerator_check_only | external_raise_before_window_only | no_evidence.",
    "capital_evidence": "Confidence of the H2 evidence (domain_keyed strongest).",
    "founder_tier": "H3: A_verified all technical, B_likely, C_unverified.", "headcount": "Team size from source listing.",
    "sector": "Rule-based label.", "hq_country": "ISO-2.", "batch_or_investor": "Accelerator batch or source.",
    "one_liner": "Company description.", "ceo_name": "CEO (or first founder).", "ceo_role": "Title.",
    "ceo_linkedin": "LinkedIn URL.", "ceo_x": "X handle.", "founders_count": "Founders on file.",
    "risk_flags": "Why a row is not tier A.", "score_breakdown": "Every point of rank_score.",
    "score_signals": "25 x hypotheses.", "score_hiring": "H1 points.", "score_capital": "H2 points.",
    "score_founders": "H3 points.", "score_size": "Headcount fit points.", "score_penalties": "Negative points.",
    "website": "https://domain.", "full_name": "Founder name.", "role": "Founder title.", "is_ceo": "Y if CEO.",
    "is_technical": "Y/N/? from H3 classifier.", "linkedin_url": "LinkedIn.", "x_handle": "X handle.",
    "email": "Master: one outreach email per company (yellow: overwrite to correct it; synced to DB as manual). "
             "Contacts: the founder's address when it is that person's.",
    "email_confidence": "published_ceo / published_founder (self-published on YC Launch or bio) > clay_valid > "
                        "derived_from_pattern > published_founders_inbox > published_generic > clay_unverified > "
                        "guess_first_at_domain (UNVERIFIED pattern guess) > manual.",
    "email_contact": "Whose address it is (founder name, 'founders inbox', 'company inbox').",
    "email_alt": "Next-best different address (e.g. founders@ when the CEO's own address is the pick).",
    "email_source": "Public page where the address was published.", "email_status": "unverified | valid | invalid | catch_all | risky | unknown.",
    "hypothesis": "H1/H2/H3.", "signal": "Signal name.", "event_date": "Date of the underlying event (H2).",
    "detail": "Evidence text.", "evidence_strength": "Confidence label.", "source": "Source dataset / feed.",
    "url": "Evidence or posting URL.", "captured_at": "When the pipeline captured it.", "title": "Posting title.",
    "family": "Role family (taxonomy).", "strength": "S1/S2/S3.", "reasoning": "Why the title maps to GTM.",
    "location": "Posting location.", "salary_range": "Posted salary.", "equity_range": "Posted equity.",
    "experience": "Posted experience.", "touch_date": "INPUT date of touch.", "contact_name": "INPUT who.",
    "channel": "INPUT dropdown.", "touch_type": "INPUT dropdown.", "angle": "INPUT message angle used.",
    "outcome": "INPUT dropdown.", "next_step": "INPUT.", "company_name": "Company name.",
}


def build_dictionary(ws, tabs):
    hdr = [("tab", 14, "data"), ("column", 22, "data"), ("type", 10, "data"), ("definition", 90, "data")]
    style_header(ws, hdr)
    ws.freeze_panes = "A2"
    r = 2
    for tab, cols in tabs:
        for name, _, kind in cols:
            for j, v in enumerate([tab, name, "input" if kind.startswith("input") else
                                   "formula" if kind == "formula" else "data", DEFS.get(name, "")], 1):
                c = ws.cell(row=r, column=j, value=v)
                c.font = F_BASE
                c.alignment = WRAP
            r += 1


# --------------------------------------------------------------------------- 5. CSVs
def write_csvs(data):
    def dump(name, rows, cols):
        with open(OUT_DIR / name, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
            w.writeheader()
            for r in rows:
                w.writerow({k: (v.isoformat() if isinstance(v, date) else v) for k, v in r.items()})
    dump("companies.csv", data["rows"], [c[0] for c in MASTER_COLS if c[2] != "formula"])
    dump("contacts.csv", data["contacts"], list(data["contacts"][0]) if data["contacts"] else [])
    dump("signals.csv", data["signals"], list(data["signals"][0]) if data["signals"] else [])
    dump("open_roles.csv", data["roles"], list(data["roles"][0]) if data["roles"] else [])
    dump("outreach_log.csv", data["log"], ["touch_id", "touch_date", "company_domain", "contact_name", "channel",
                                           "touch_type", "angle", "outcome", "next_step", "notes"])


def main() -> None:
    conn = pdb.init_db()
    folded = fold_aliases(conn)
    synced = sync_from_workbook(conn)
    data = assemble(conn)
    counts = build_workbook(data)
    write_csvs(data)
    rows = data["rows"]
    summary = {"aliases_folded": folded, "sync": synced, "tabs": counts,
               "by_tier": dict(Counter(r["icp_tier"] for r in rows)),
               "by_wave": {w: sum(r["wave"] == w for r in rows) for w in WAVES},
               "workbook": str(XLSX.relative_to(pdb.ROOT))}
    print(json.dumps(summary, indent=1))
    conn.close()


if __name__ == "__main__":
    main()
