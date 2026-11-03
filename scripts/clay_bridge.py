"""Round-trip the company list through Clay for email finding + validation.

  python3 scripts/clay_bridge.py export
      -> master/clay_import.csv : 1 row per company (all 1,7xx), sorted by outreach priority.
         Import it into a Clay table, add a work-email waterfall + email validation, export CSV.
  python3 scripts/clay_bridge.py import <clay_export.csv> [--email-col "Work Email"] [--status-col "Validation"]
      -> upserts company_emails (source_type 'clay'), then run python3 scripts/build_master.py

Import keeps the better address per company. Order (best first):
  manual > published_ceo > published_founder > clay_valid > derived_from_pattern > published_founders_inbox
  > published_generic > clay_unverified > guess_first_at_domain
A Clay result that loses still lands in `email_alt`. If Clay validation marks the current pick invalid,
the pick is replaced by the best other address, or kept with email_confidence 'clay_invalid'.
Column names are auto-detected (any header containing "email"; status from "valid"/"status"/"verif");
override with --email-col / --status-col when the export has several email columns.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pipeline_db as pdb  # noqa: E402

OUT = pdb.ROOT / "master" / "clay_import.csv"
REPORT = pdb.ROOT / "raw_sources" / "clay_import_report.json"
RANK = ["manual", "published_ceo", "published_founder", "clay_valid", "derived_from_pattern",
        "published_founders_inbox", "published_generic", "clay_unverified", "guess_first_at_domain", "clay_invalid"]
WAVES = ["W1 Hot (days 1-7)", "W2 Warm (days 8-14)", "W3 Cold-fit (days 15-30)", "Nurture (day 30+)",
         "Enrich first", "Review fit"]
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[a-z]{2,}$", re.I)
VALID = re.compile(r"^(valid|deliverable|safe|ok|verified|true|yes)$", re.I)
INVALID = re.compile(r"invalid|undeliverable|bounce|false|^no$|disposable|spamtrap", re.I)


def export() -> None:
    conn = pdb.connect()
    founders = {}
    for f in conn.execute("SELECT * FROM founders ORDER BY founder_id"):
        founders.setdefault(f["company_domain"], []).append(dict(f))
    em = {r["company_domain"]: dict(r) for r in conn.execute("SELECT * FROM company_emails")}
    rows = []
    for c in conn.execute("SELECT c.*, s.rank, s.wave, s.icp_tier FROM companies c LEFT JOIN scores s USING (company_domain) "
                          "WHERE c.status != 'disqualified'"):
        d = c["company_domain"]
        fl = founders.get(d, [])
        ceo = next((f for f in fl if re.search(r"\bceo\b|chief executive", f["role"] or "", re.I)), fl[0] if fl else None)
        name = (ceo or {}).get("full_name") or ""
        parts = re.sub(r"\(.*?\)", " ", name).split()
        e = em.get(d, {})
        conf = e.get("email_confidence") or ""
        rows.append({
            "Company Domain": d, "Company Name": c["company_name"],
            "First Name": parts[0] if parts else "", "Last Name": parts[-1] if len(parts) > 1 else "",
            "Full Name": name, "Title": (ceo or {}).get("role") or "",
            "LinkedIn URL": (ceo or {}).get("linkedin_url") or "",
            "Needs Person Search": "" if ceo else "Y",
            "Already Has Self-Published Email": "Y" if conf in ("published_ceo", "published_founder") else "",
            "Current Email": e.get("email") or "", "Current Email Confidence": conf,
            "Wave": c["wave"] or "", "ICP Tier": c["icp_tier"] or "", "Rank": c["rank"] or ""})
    rows.sort(key=lambda r: (WAVES.index(r["Wave"]) if r["Wave"] in WAVES else 9, int(r["Rank"] or 99999)))
    for i, r in enumerate(rows, 1):
        r["Priority Order"] = i
    OUT.parent.mkdir(exist_ok=True)
    with open(OUT, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["Priority Order"] + [k for k in rows[0] if k != "Priority Order"])
        w.writeheader()
        w.writerows(rows)
    print(json.dumps({"file": str(OUT.relative_to(pdb.ROOT)), "rows": len(rows),
                      "with_ceo_name": sum(1 for r in rows if not r["Needs Person Search"]),
                      "with_linkedin": sum(1 for r in rows if r["LinkedIn URL"]),
                      "needs_person_search": sum(1 for r in rows if r["Needs Person Search"]),
                      "already_self_published": sum(1 for r in rows if r["Already Has Self-Published Email"])}, indent=1))


def pick_cols(headers: list[str], email_col: str | None, status_col: str | None) -> tuple[str, list[str], str | None]:
    low = {h.lower().strip(): h for h in headers}
    dom = next((low[k] for k in ("company domain", "domain", "company_domain", "website") if k in low), None)
    if not dom:
        sys.exit(f"No domain column found in {headers}")
    if email_col:
        ecols = [email_col]
    else:
        ecols = [h for h in headers if "email" in h.lower() and not re.search(r"current|valid|status|verif|confidence|self-published", h, re.I)]
        ecols.sort(key=lambda h: (0 if re.search(r"waterfall|final|work", h, re.I) else 1, headers.index(h)))
    if status_col is None:
        status_col = next((h for h in headers if re.search(r"valid|verif|status", h, re.I)
                           and not re.search(r"confidence", h, re.I)), None)
    return dom, ecols, status_col


def run_import(path: Path, email_col: str | None, status_col: str | None) -> None:
    conn = pdb.connect()
    conn.executescript((pdb.ROOT / "db" / "schema.sql").read_text())
    with open(path, newline="", encoding="utf-8-sig") as f:
        rd = csv.DictReader(f)
        rows = list(rd)
        dom_col, ecols, scol = pick_cols(rd.fieldnames or [], email_col, status_col)
    live = {r["company_domain"] for r in conn.execute("SELECT company_domain FROM companies WHERE status!='disqualified'")}
    stats = {"rows": len(rows), "email_columns_used": ecols, "status_column_used": scol}
    cnt: dict[str, int] = {}
    now = pdb.utcnow()
    for r in rows:
        d = pdb.normalize_domain(r.get(dom_col))
        if d not in live:
            cnt["unknown_domain"] = cnt.get("unknown_domain", 0) + 1
            continue
        found = next((r[c].strip().lower() for c in ecols if EMAIL_RE.match((r.get(c) or "").strip())), None)
        status = (r.get(scol) or "").strip() if scol else ""
        cur = conn.execute("SELECT * FROM company_emails WHERE company_domain=?", (d,)).fetchone()
        cur = dict(cur) if cur else None
        if cur and cur["source_type"] == "manual":
            cnt["manual_kept"] = cnt.get("manual_kept", 0) + 1
            continue
        if not found:
            cnt["clay_no_result"] = cnt.get("clay_no_result", 0) + 1
            continue
        conf = "clay_invalid" if INVALID.search(status) else "clay_valid" if VALID.match(status) else "clay_unverified"
        name = (r.get("Full Name") or "").strip() or None
        new = {"email": found, "contact_name": name, "contact_role": (r.get("Title") or "").strip() or None,
               "email_confidence": conf}
        if cur and cur["email"] == found:  # Clay validated our own pick
            if conf == "clay_invalid":
                alt = cur["email_alt"]
                if alt and alt != found:
                    upd = {"email": alt, "email_confidence": cur["email_alt_confidence"] or "published_generic",
                           "contact_name": None, "email_alt": found, "email_alt_confidence": "clay_invalid"}
                else:
                    upd = {"email_confidence": "clay_invalid"}
                key = "current_pick_invalid"
            elif conf == "clay_valid" and RANK.index(cur["email_confidence"]) > RANK.index("clay_valid"):
                upd, key = {"email_confidence": "clay_valid"}, "current_pick_validated"
            else:
                upd, key = {}, "current_pick_confirmed"
        elif conf == "clay_invalid":
            upd, key = {}, "clay_result_invalid_ignored"
        elif not cur or RANK.index(conf) < RANK.index(cur["email_confidence"]):
            upd = {**new, "email_alt": cur["email"] if cur else None,
                   "email_alt_confidence": cur["email_confidence"] if cur else None}
            key = "clay_replaced_pick"
        else:
            upd = {"email_alt": found, "email_alt_confidence": conf}
            key = "clay_kept_as_alt"
        cnt[key] = cnt.get(key, 0) + 1
        if not upd:
            continue
        upd.update({"source_type": "clay", "source_url": f"clay:{path.name}", "found_at": now,
                    "evidence": f"Clay export {path.name}; status={status or 'n/a'}"})
        if cur:
            conn.execute(f"UPDATE company_emails SET {', '.join(k + '=?' for k in upd)} WHERE company_domain=?",
                         (*upd.values(), d))
        else:
            conn.execute(f"INSERT INTO company_emails (company_domain, {', '.join(upd)}) VALUES (?{',?' * len(upd)})",
                         (d, *upd.values()))
    conn.commit()
    stats["outcomes"] = dict(sorted(cnt.items(), key=lambda x: -x[1]))
    stats["now_by_confidence"] = dict(conn.execute(
        "SELECT email_confidence, COUNT(*) FROM company_emails GROUP BY 1 ORDER BY 2 DESC").fetchall())
    REPORT.write_text(json.dumps({"run_at": now, "file": path.name, **stats}, indent=1) + "\n")
    print(json.dumps(stats, indent=1))


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("export")
    imp = sub.add_parser("import")
    imp.add_argument("file", type=Path)
    imp.add_argument("--email-col")
    imp.add_argument("--status-col")
    a = ap.parse_args()
    export() if a.cmd == "export" else run_import(a.file, a.email_col, a.status_col)


if __name__ == "__main__":
    main()
