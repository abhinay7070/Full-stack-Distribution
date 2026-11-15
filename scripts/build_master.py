"""Build the 8-o Outbound OS workbook (master/gtm_master.xlsx) from the DB. Layout lives in scripts/workbook.py.

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

from openpyxl import load_workbook

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pipeline_db as pdb  # noqa: E402
import master_rank  # noqa: E402
import workbook  # noqa: E402
import workspace_seed  # noqa: E402

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
TIERS = ["A · Core ICP", "B · Strong fit", "C · Probable - verify", "D · Long-shot"]
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
    tier = m["icp_class"]  # from icp_rules.fit(): 100-point ICP fit score -> A/B/C/D (or Outside SAM)
    if tier in ("A · Core ICP", "B · Strong fit") and (m["site_flags"] or "domain_match_medium_verify" in risks):
        tier = "C · Probable - verify"  # dead/parked site or a name-only domain match: verify before outreach
    fams = m["h1_family"] or ""
    days = None
    if m.get("funding_date") and st == "verified_in_window":
        try:
            days = (date.today() - date.fromisoformat(m["funding_date"][:10])).days
        except ValueError:
            days = None
    if tier in ("D · Long-shot", "Outside SAM"):
        wave = "Review fit"
    elif "exact_role" in fams or "H9" in tags:
        wave = "W1 Hot (days 1-7)"  # exact job or a warm path: never bury it, even if data is thin
    elif tier == "C · Probable - verify":
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


def cluster_ring(m: dict, tier: str, email: dict | None) -> tuple[str, str]:
    """Primary pain cluster (first match wins) + effort ring (R1 sniper / R2 rifle / R3 net / Enrich / Review)."""
    fam, s1 = m["h1_family"] or "", m["h1_strength"] or ""
    try:
        fresh = m["funding_window_status"] == "verified_in_window" and \
            (date.today() - date.fromisoformat((m["funding_date"] or "")[:10])).days <= 90
    except ValueError:
        fresh = False
    if m["h10_days_open"]:
        cl = "C2 Stale GTM role"
    elif s1 and re.search(r"exact_role|hidden_gtm", fam):
        cl = "C1a Hiring my exact role"
    elif s1 in ("S1", "S2"):
        cl = "C1 Hiring GTM"
    elif m["h4_tier"] in ("decay_window", "pre_decay"):
        cl = "C6 Just launched"
    elif fresh:
        cl = "C3 Fresh capital"
    elif m["h6_strength"] and m["h3_tier"] in ("A_verified", "B_likely"):
        cl = "C4 Founder-led, technical"
    elif m["h5_strength"] == "S1":
        cl = "C5 Outbound tooling live"
    elif float(m["h7_mrr"] or 0) >= 10000:
        cl = "C7 Revenue, no GTM team"
    else:
        cl = "C8 Fit only (nurture)"
    reach = (email or {}).get("email_confidence") in ("published_ceo", "published_founder", "published_founders_inbox",
                                                       "clay_valid")
    if tier.startswith("D") or tier == "Outside SAM":
        ring = "Review"
    elif tier.startswith("C"):
        ring = "Enrich"
    elif cl in ("C1a Hiring my exact role", "C2 Stale GTM role") or (cl == "C1 Hiring GTM" and reach) \
            or int(m["signal_count"] or 0) >= 3:
        ring = "R1 Sniper"
    elif not cl.startswith("C8"):
        ring = "R2 Rifle"
    else:
        ring = "R3 Net"
    return cl, ring


def gtm_play(m: dict) -> str:
    tags = set((m["matched_hypotheses"] or "").split("|"))
    if "H9" in tags:
        return "Warm intro"
    if m["h1_strength"] in ("S1", "S2") or "H10" in tags:
        return "Job: apply + CEO DM"
    if m["h2_tier"] == "fresh_60d" or ("H2" in tags and m["h2_confidence"] not in ("program_terms",)):
        return "Retainer: capital-led"
    if "H7" in tags:
        return "Retainer: revenue-led"
    if "H4" in tags or "H5" in tags:
        return "Retainer: launch/outbound sprint"
    if "H6" in tags or "H3" in tags:
        return "Founder DM: teardown"
    return "Nurture"


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
            "icp_tier": tier, "icp_fit_score": to_int(m["icp_fit_score"]), "sector_category": m["sector_category"],
            "gtm_play": gtm_play(m), **dict(zip(("cluster", "ring"), cluster_ring(m, tier, emails.get(d)))), "fit_geo": to_int(m["fit_geo"]), "fit_stage": to_int(m["fit_stage"]),
            "fit_headcount": to_int(m["fit_headcount"]), "fit_sector": to_int(m["fit_sector"]),
            "stage_evidence": m["stage_evidence"], "outside_sam_reason": m["outside_sam_reason"],
            "rank_score": int(m["rank_score"]),
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
    outside = sorted([r for r in rows if r["icp_tier"] == "Outside SAM"], key=lambda r: -r["icp_fit_score"])
    rows = [r for r in rows if r["icp_tier"] != "Outside SAM"]
    RINGS = ["R1 Sniper", "R2 Rifle", "R3 Net", "Enrich", "Review"]
    rows.sort(key=lambda r: (RINGS.index(r["ring"]), WAVES.index(r["wave"]), -r["icp_fit_score"], -r["rank_score"]))

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
    return {"rows": rows, "outside": outside, "contacts": contacts, "signals": signals, "roles": roles, "log": log, "hyp": hyp,
            "events": events, "unresolved": unresolved, "sources": sources}


# --------------------------------------------------------------------------- 4. workbook (v5: scripts/workbook.py)
HEADER_ROW = 1  # legacy (v4) workbook header row, used by sync_from_workbook


def main() -> None:
    conn = pdb.init_db()
    folded = fold_aliases(conn)
    v5 = workbook.sync_workspace(conn, XLSX)          # v5 file (Google Sheets download): Log, Angles, ... -> DB
    legacy = sync_from_workbook(conn) if not v5.get("synced") else {"skipped": "v5 workbook"}
    seeded = workspace_seed.seed(conn)                # INSERT OR IGNORE: never overwrites synced rows
    data = assemble(conn)
    counts = workbook.build(data, conn, XLSX, OUT_DIR)
    for old in ("companies.csv", "signals.csv", "open_roles.csv", "outreach_log.csv"):
        (OUT_DIR / old).unlink(missing_ok=True)
    rows = data["rows"]
    summary = {"aliases_folded": folded, "sync_v5": v5, "sync_legacy": legacy, "seeded": seeded, "tabs": counts,
               "by_tier": dict(Counter(r["icp_tier"] for r in rows)),
               "workbook": str(XLSX.relative_to(pdb.ROOT))}
    print(json.dumps(summary, indent=1, default=str))
    conn.close()


if __name__ == "__main__":
    main()
