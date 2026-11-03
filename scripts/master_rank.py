"""Master ranking across every hypothesis run so far.

Usage: python3 scripts/master_rank.py   (run after any h*_ingest.py)

Reads db/gtm_pipeline.db plus the per-hypothesis sublists and writes
sublists/master_priority.csv: every non-disqualified company, ranked. rank_breakdown lists
every point so the order is auditable.

Points
  hypotheses matched (priority_score)    x25
  H1 best role                           S1 +20 | S2 +12 | S3 +6 ; exact/hidden GTM role +8
  H2 capital event (external raise)      <=60d +15 | 61-180d +10 | 181-365d +6 | older in window +3
                  (accelerator check)    fresh YC batch check +8
                                         weak evidence (name match) -4 ; possible past Series A -15
  H3 founders                            A_verified +10 | B_likely +5
  headcount                              3-20 +5 | 21-35 +2
  funding unverified (no H2 evidence, YC 2022-24)   -3 ; geo unknown -3
"""
from __future__ import annotations

import csv
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pipeline_db as pdb  # noqa: E402

OUT = pdb.SUBLIST_DIR / "master_priority.csv"


def load(name: str) -> dict[str, dict]:
    p = pdb.SUBLIST_DIR / name
    if not p.exists():
        return {}
    with open(p, newline="") as f:
        return {r["company_domain"]: r for r in csv.DictReader(f)}


COLS = ["rank", "rank_score", "rank_breakdown", "priority_score", "matched_hypotheses", "company_domain", "company_name",
        "one_liner", "sector", "hq_country", "batch_or_investor", "headcount", "why_now", "h1_best_role", "h1_strength",
        "h1_role_url", "h1_comp", "h2_tier", "h2_event", "h2_confidence", "funding_stage", "funding_amount",
        "funding_date", "funding_window_status", "h3_tier", "gtm_engineer_hook", "ceo_name", "ceo_linkedin", "ceo_x",
        "flags"]


def main() -> None:
    conn = pdb.connect()
    h1, h2, h3 = load("sublist_H1_job_arbitrage.csv"), load("sublist_H2_fresh_capital.csv"), \
        load("sublist_H3_technical_founders.csv")
    fs = load("sublist_H2_funding_status_all.csv")
    founders: dict[str, list] = {}
    for f in conn.execute("SELECT * FROM founders ORDER BY founder_id"):
        founders.setdefault(f["company_domain"], []).append(dict(f))
    rows = []
    for c in conn.execute("SELECT * FROM companies WHERE status != 'disqualified'"):
        d = c["company_domain"]
        tags = json.loads(c["matched_hypotheses"])
        a, b, t = h1.get(d), h2.get(d), h3.get(d)
        pts, flags = {"hypotheses": 25 * c["priority_score"]}, []
        why = []
        if a:
            pts["h1_role"] = {"S1": 20, "S2": 12, "S3": 6}[a["match_strength"]]
            if re.search(r"exact_role|hidden_gtm", a["match_families"]):
                pts["h1_exact_or_hidden"] = 8
            why.append(f"hiring '{a['best_role_title']}'")
        if b:
            days = int(b["days_since_capital_event"] or 9999)
            if b["source_type"] == "program_terms":
                pts["h2_capital"] = 8
            else:
                pts["h2_capital"] = 15 if days <= 60 else 10 if days <= 180 else 6 if days <= 365 else 3
            if b["confidence"] in ("headline_name_match", "company_attached_unnamed"):
                pts["h2_weak_evidence"] = -4
            amt = f"${int(b['amount_usd']) / 1e6:.1f}M " if b["amount_usd"] else ""
            why.append(f"{amt}{b['round_stage'].replace('_', '-')} capital {b['days_since_capital_event']}d ago "
                       f"({b['source_type']})")
        status = (fs.get(d) or {}).get("funding_window_status", "")
        if "possible_past_series_a" in ((fs.get(d) or {}).get("flags") or ""):
            pts["possible_past_series_a"] = -15
            flags.append("possible_past_series_a_verify")
        tier = (t or {}).get("h3_tier") or (a or {}).get("h3_tier") or ""
        if "H3" in tags and tier in ("A_verified", "B_likely"):
            pts["h3_tier"] = 10 if tier == "A_verified" else 5
            why.append("all-technical founders" if tier == "A_verified" else "technical founder(s)")
        hc = c["headcount"]
        if hc is not None:
            pts["headcount"] = 5 if 3 <= hc <= 20 else 2 if hc <= 35 else 0
        h1_flags = (a or {}).get("icp_flags", "") + "|" + (t or {}).get("icp_flags", "")
        if "funding_window_unverified" in h1_flags and status != "verified_in_window":
            pts["funding_unverified"] = -3
            flags.append("funding_window_unverified")
        if "geo_unknown" in h1_flags or not c["hq_country"]:
            pts["geo_unknown"] = -3
            flags.append("geo_unknown")
        ceo = next((f for f in founders.get(d, []) if re.search(r"\bceo\b|chief executive", f["role"] or "", re.I)),
                   (founders.get(d) or [None])[0])
        rows.append({
            "rank_score": sum(pts.values()), "rank_breakdown": " ".join(f"{k}{v:+d}" for k, v in pts.items()),
            "priority_score": c["priority_score"], "matched_hypotheses": "|".join(tags), "company_domain": d,
            "company_name": c["company_name"], "one_liner": c["one_liner"], "sector": c["sector"],
            "hq_country": c["hq_country"], "batch_or_investor": c["batch_or_investor"], "headcount": hc,
            "why_now": "; ".join(why), "h1_best_role": (a or {}).get("best_role_title", ""),
            "h1_strength": (a or {}).get("match_strength", ""), "h1_role_url": (a or {}).get("best_role_url", ""),
            "h1_comp": (a or {}).get("best_role_comp", ""), "h2_tier": (b or {}).get("h2_tier", ""),
            "h2_event": f"{b['capital_event_date']} {b['round_label'] or b['round_stage']} {b['amount_band']}" if b else "",
            "h2_confidence": (b or {}).get("confidence", ""), "funding_stage": c["funding_stage"],
            "funding_amount": c["funding_amount"], "funding_date": c["funding_date"], "funding_window_status": status,
            "h3_tier": tier, "gtm_engineer_hook": (a or {}).get("gtm_engineer_hook", ""),
            "ceo_name": ceo["full_name"] if ceo else "", "ceo_linkedin": (ceo or {}).get("linkedin_url") or "",
            "ceo_x": f"@{ceo['x_handle']}" if ceo and ceo.get("x_handle") else "", "flags": "|".join(flags)})
    rows.sort(key=lambda r: (-r["rank_score"], r["headcount"] or 99, r["company_domain"]))
    for i, r in enumerate(rows, 1):
        r["rank"] = i
    with open(OUT, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLS)
        w.writeheader()
        w.writerows(rows)
    combo = {}
    for r in rows:
        combo[r["matched_hypotheses"]] = combo.get(r["matched_hypotheses"], 0) + 1
    print(json.dumps({"ranked_companies": len(rows), "by_hypothesis_combo": dict(sorted(combo.items(),
                                                                                         key=lambda x: -x[1]))}, indent=1))


if __name__ == "__main__":
    main()
