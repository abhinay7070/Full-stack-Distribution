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
def classify(m: dict, fs: dict, founders: list[dict], h1: dict | None) -> tuple[str, str, list[str]]:
    d = m["company_domain"]
    st = (fs.get(d) or {}).get("funding_window_status", "")
    flags = f"{m['flags']}|{(fs.get(d) or {}).get('flags', '')}"
    hc = to_int(m["headcount"])
    geo = bool(m["hq_country"])
    hc_ok = hc is not None and 2 <= hc <= 35
    li = any(f["linkedin_url"] for f in founders)
    yc = re.search(r"YC [WXSF](\d\d)", m["batch_or_investor"] or "")
    # accelerator money received inside the window (YC 2025+ batches, Speedrun SR004-7, 2025/26 programs)
    program_in_window = bool((yc and int(yc.group(1)) >= 25) or re.search(r"SR00[4-7]|2025|2026",
                                                                          m["batch_or_investor"] or ""))
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
    if (m.get("h2_confidence") or "") in ("headline_name_match", "company_attached_unnamed"):
        risks.append("funding_name_match_verify")
    if "funding_window_unverified" in flags and st != "verified_in_window":
        risks.append("older_batch_funding_unverified")
    if not geo:
        risks.append("geo_unknown")
    if not hc_ok:
        risks.append("headcount_unknown")
    if not li:
        risks.append("no_founder_contact")
    hard = [r for r in risks if r in ("consumer_keyword", "bio_keyword", "services_keyword")]
    strong_funding = (st == "verified_in_window" and "funding_name_match_verify" not in risks
                      and "possible_past_series_a" not in risks)
    if strong_funding and geo and hc_ok and li and not risks:
        tier = "A_high_confidence"
    elif (strong_funding or program_in_window) and geo and hc_ok and li and not hard \
            and "possible_past_series_a" not in risks:
        tier = "B_likely_fit"  # a weak external-raise match falls back to the accelerator check
    elif geo and not hard:
        tier = "C_needs_enrichment"
    else:
        tier = "D_review"
    fams = (h1 or {}).get("match_families", "")
    days = None
    if m.get("funding_date") and st == "verified_in_window":
        days = (date.today() - date.fromisoformat(m["funding_date"])).days
    if tier == "D_review":
        wave = "Review fit"
    elif "exact_role" in fams:
        wave = "W1 Hot (days 1-7)"  # they are hiring the exact job: never bury it, even if data is thin
    elif tier == "C_needs_enrichment":
        wave = "Enrich first"
    elif m["h1_strength"] == "S1":
        wave = "W1 Hot (days 1-7)"
    elif m["h1_strength"] in ("S2", "S3") or (days is not None and days <= 180) or m["h2_tier"] == "fresh_60d":
        wave = "W2 Warm (days 8-14)"
    elif m["h3_tier"] == "A_verified" and hc is not None and 3 <= hc <= 20:
        wave = "W3 Cold-fit (days 15-30)"
    else:
        wave = "Nurture (day 30+)"
    return tier, wave, risks


def suggested_action(wave: str, m: dict, risks: list[str]) -> str:
    ceo = (m["ceo_name"] or "the CEO").split(" ")[0] if m["ceo_name"] else "the CEO"
    raise_txt = ""
    if m["funding_amount"] and m["funding_window_status"] == "verified_in_window":
        raise_txt = f"${int(float(m['funding_amount'])) / 1e6:.1f}M raise"
    if wave.startswith("W1"):
        return (f"Apply to '{m['h1_best_role']}' AND DM {ceo} a 1-page 30/60/90 GTM plan referencing the posting; "
                f"offer a paid 30-day trial/retainer as the bridge")
    if wave.startswith("W2"):
        hook = f"congrats on the {raise_txt}" if raise_txt else "reference their open non-eng role"
        return f"DM {ceo}: {hook}; offer a 60-day GTM-engineer retainer to turn capital into pipeline"
    if wave.startswith("W3"):
        return f"Cold DM {ceo}: 3-point teardown of their outbound/website funnel + retainer offer"
    if wave.startswith("Nurture"):
        return "Follow founders on LinkedIn/X, engage with posts; revisit when a new signal fires"
    if wave == "Enrich first":
        missing = [r for r in risks if r in ("headcount_unknown", "no_founder_contact", "geo_unknown",
                                             "older_batch_funding_unverified")]
        return f"Enrich: {', '.join(missing) or 'funding evidence'}; then re-run build_master.py"
    return f"Check ICP fit ({', '.join(risks[:3])}); set stage 'Not a fit' or keep"


def components(breakdown: str) -> dict:
    pts = {k: int(v) for k, v in re.findall(r"([a-z0-9_]+)([+-]\d+)", breakdown or "")}
    return {
        "score_signals": pts.get("hypotheses", 0),
        "score_hiring": pts.get("h1_role", 0) + pts.get("h1_exact_or_hidden", 0),
        "score_capital": pts.get("h2_capital", 0) + pts.get("h2_weak_evidence", 0),
        "score_founders": pts.get("h3_tier", 0),
        "score_size": pts.get("headcount", 0),
        "score_penalties": pts.get("funding_unverified", 0) + pts.get("geo_unknown", 0)
        + pts.get("possible_past_series_a", 0),
    }


# --------------------------------------------------------------------------- 3. assemble rows
def assemble(conn) -> dict:
    master_rank.main()
    master = read_csv("master_priority.csv")
    fs = {r["company_domain"]: r for r in read_csv("sublist_H2_funding_status_all.csv")}
    h1 = {r["company_domain"]: r for r in read_csv("sublist_H1_job_arbitrage.csv")}
    founders = defaultdict(list)
    for f in conn.execute("SELECT * FROM founders ORDER BY founder_id"):
        founders[f["company_domain"]].append(dict(f))
    track = {r["company_domain"]: dict(r) for r in conn.execute("SELECT * FROM tracking")}
    emails = {r["company_domain"]: dict(r) for r in conn.execute("SELECT * FROM company_emails")}
    now = pdb.utcnow()
    rows = []
    for m in master:
        d = m["company_domain"]
        tier, wave, risks = classify(m, fs, founders[d], h1.get(d))
        comp = components(m["rank_breakdown"])
        ceo = next((f for f in founders[d] if re.search(r"\bceo\b|chief executive", f["role"] or "", re.I)),
                   (founders[d] or [None])[0])
        t = track.get(d, {})
        tags = m["matched_hypotheses"].split("|") if m["matched_hypotheses"] else []
        rows.append({
            "rank": int(m["rank"]), "company_domain": d, "company_name": m["company_name"], "wave": wave,
            "icp_tier": tier, "rank_score": int(m["rank_score"]),
            "stage": t.get("stage") or "Not started", "next_action": t.get("next_action"),
            "next_action_date": to_date(t.get("next_action_date")), "notes": t.get("notes"),
            "suggested_action": suggested_action(wave, m, risks),
            "signals": "+".join(tags), "signal_count": len(tags),
            "has_H1_hiring": "Y" if "H1" in tags else "", "has_H2_capital": "Y" if "H2" in tags else "",
            "has_H3_tech_founders": "Y" if "H3" in tags else "",
            "why_now": m["why_now"], "pitch_hook": m["gtm_engineer_hook"],
            "hiring_role": m["h1_best_role"], "hiring_strength": m["h1_strength"], "role_url": m["h1_role_url"],
            "role_comp": m["h1_comp"],
            "last_raise_date": to_date(m["funding_date"]),
            "raise_stage": m["funding_stage"], "raise_amount_usd": to_int(m["funding_amount"]),
            "funding_status": m["funding_window_status"], "capital_evidence": m["h2_confidence"],
            "founder_tier": m["h3_tier"], "headcount": to_int(m["headcount"]), "sector": m["sector"],
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
            "score_founders, score_size, score_penalties, icp_tier, wave, computed_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(company_domain) DO UPDATE SET rank=excluded.rank, rank_score=excluded.rank_score, "
            "score_signals=excluded.score_signals, score_hiring=excluded.score_hiring, score_capital=excluded.score_capital, "
            "score_founders=excluded.score_founders, score_size=excluded.score_size, "
            "score_penalties=excluded.score_penalties, icp_tier=excluded.icp_tier, wave=excluded.wave, "
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
    names = {"H1": "Hiring GTM", "H2": "Fresh capital", "H3": "Technical founders"}
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
    for r in roles:
        r["wave"] = by_domain[r["company_domain"]]["wave"]
    roles.sort(key=lambda r: (WAVES.index(r["wave"]), r["strength"], r["company_domain"]))
    log = [dict(r) for r in conn.execute("SELECT * FROM outreach_log ORDER BY touch_date, touch_id")]
    for r in log:
        r["touch_date"] = to_date(r["touch_date"])
    return {"rows": rows, "contacts": contacts, "signals": signals, "roles": roles, "log": log}


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
    ("has_H1_hiring", 6, "data"), ("has_H2_capital", 6, "data"), ("has_H3_tech_founders", 6, "data"),
    ("why_now", 45, "data"), ("pitch_hook", 60, "data"),
    ("hiring_role", 30, "data"), ("hiring_strength", 7, "data"), ("role_url", 14, "link"), ("role_comp", 22, "data"),
    ("last_raise_date", 12, "date"), ("days_since_raise", 9, "formula"), ("raise_stage", 10, "data"),
    ("raise_amount_usd", 14, "money"), ("funding_status", 22, "data"), ("capital_evidence", 18, "data"),
    ("founder_tier", 14, "data"), ("headcount", 9, "data"), ("sector", 18, "data"), ("hq_country", 7, "data"),
    ("batch_or_investor", 16, "data"), ("one_liner", 45, "data"),
    ("ceo_name", 20, "data"), ("ceo_role", 14, "data"), ("ceo_linkedin", 14, "link"), ("ceo_x", 14, "data"),
    ("email", 28, "input"), ("email_confidence", 22, "data"), ("email_contact", 18, "data"), ("email_alt", 24, "data"),
    ("email_source", 10, "link"),
    ("founders_count", 7, "data"), ("risk_flags", 30, "data"), ("score_breakdown", 45, "data"),
    ("score_signals", 7, "data"), ("score_hiring", 7, "data"), ("score_capital", 7, "data"),
    ("score_founders", 7, "data"), ("score_size", 7, "data"), ("score_penalties", 7, "data"),
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

    # ---------------- Dashboard
    build_dashboard(dash, C, last)
    # ---------------- Start_Here + dictionary
    build_start(ws0, data)
    build_dictionary(wdd, [("Master", MASTER_COLS), ("Contacts", CONTACT_COLS), ("Signals", SIG_COLS),
                           ("Open_Roles", ROLE_COLS), ("Outreach_Log", LOG_COLS)])
    for sheet in wb.worksheets:
        sheet.sheet_view.zoomScale = 90
    OUT_DIR.mkdir(exist_ok=True)
    wb.save(XLSX)
    return {"Master": n, "Contacts": len(data["contacts"]), "Signals": len(data["signals"]),
            "Open_Roles": len(data["roles"]), "Outreach_Log": len(data["log"])}


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
    for i, (label, k) in enumerate([("Hiring GTM (H1)", "has_H1_hiring"), ("Fresh capital (H2)", "has_H2_capital"),
                                    ("Technical founders (H3)", "has_H3_tech_founders")], r0):
        ws.cell(row=i, column=1, value=label).font = F_BASE
        ws.cell(row=i, column=2, value=f'=COUNTIF({M(k)},"Y")').font = F_BASE
    for i, k in enumerate((3, 2, 1), r0 + 3):
        ws.cell(row=i, column=1, value=f"Companies with {k} signal(s)").font = F_BASE
        ws.cell(row=i, column=2, value=f'=COUNTIF({M("signal_count")},{k})').font = F_BASE

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
        ("What this is", "One list of every US/UK/EU pre-seed→Series A B2B/AI startup found so far, scored on three "
         "signals: H1 hiring GTM now, H2 fresh capital, H3 all-technical founders. Goal: land one Founding GTM / "
         "Growth Engineer role or a $3-5K/mo retainer.", None),
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
        ("W1 Hot (days 1-7)", "Hiring a GTM role now (S1), or hiring the exact GTM/Growth Engineer role in any tier. "
         "Apply AND DM the CEO.", None),
        ("W2 Warm (days 8-14)", "Adjacent GTM role (S2/S3) or verified raise in the last 180 days / fresh YC batch.", None),
        ("W3 Cold-fit (days 15-30)", "All-technical founders, 3-20 people, no live signal: teardown-led cold DM.", None),
        ("Nurture (day 30+)", "Fits, no timing signal. Follow and engage; re-scored when a signal fires.", None),
        ("Enrich first", "Tier C: missing headcount / founder contact / funding evidence.", None),
        ("Review fit", "Tier D: possible consumer / bio / services or unknown geo. Decide keep or 'Not a fit'.", None),
        ("", None, None),
        ("ICP TIERS (fit)", None, F_H2),
        ("A_high_confidence", "Verified: US/UK/EU HQ, 2-35 people, external raise (pre-seed→A) since 2025-05-01, "
         "founder LinkedIn, no risk flags.", None),
        ("B_likely_fit", "As A, but funding may be only the accelerator check (YC 2025-26 $500K, Speedrun) and minor "
         "flags allowed.", None),
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
    "signals": "Hypotheses matched (H1 hiring, H2 capital, H3 technical founders).",
    "signal_count": "Number of hypotheses matched.", "has_H1_hiring": "Y if H1.", "has_H2_capital": "Y if H2.",
    "has_H3_tech_founders": "Y if H3.", "why_now": "One-line reason combining the live signals.",
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
