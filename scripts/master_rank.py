"""Master ranking v2 across H0-H10 (reads the DB + evidence tables + per-hypothesis sublists).

Usage: python3 scripts/master_rank.py      -> sublists/master_priority.csv (every live company, ranked)

Points (rank_breakdown lists every point; v1 components unchanged, v2 adds H4-H10)
  signals        25 x number of signal hypotheses matched (H1-H10; H0 universe membership scores 0)
  H1 hiring      best GTM role S1 +20 | S2 +12 | S3 +6 ; exact / hidden GTM role +8
  H2 capital     external raise <=60d +15 | <=180d +10 | <=365d +6 | older in window +3 ; accelerator check only +8
                 weak evidence (name match / resolved by name) -4 ; possible past Series A -15
  H3 founders    A_verified +10 | B_likely +5
  size           3-20 people +5 | 21-35 +2 | band 1-10 +3 | band 11-50 +1
  H4 launch      decay_window +6 | pre_decay +3 | post_launch +2
  H5 outbound    S1 +6 | S2 +3
  H6 founder-led S1 +6 | S2 +3
  H7 revenue     MRR >= $50K +8 | >= $10K +6 | >= $5K +3
  H8 OSS         S1 +6 | S2 +3
  H9 warm path   +15
  H10 stale role S1 +6 | S2 +4
  penalties      no funding/stage evidence -3 ; geo unknown -3 ; homepage dead/parked -5
"""
from __future__ import annotations

import csv
import json
import re
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import icp_rules as icp  # noqa: E402
import pipeline_db as pdb  # noqa: E402

OUT = pdb.SUBLIST_DIR / "master_priority.csv"
TODAY = date.today()
STR = {"S1": 0, "S2": 1, "S3": 2}


def load(name: str) -> dict[str, dict]:
    p = pdb.SUBLIST_DIR / name
    if not p.exists():
        return {}
    with open(p, newline="") as f:
        return {r["company_domain"]: r for r in csv.DictReader(f) if r.get("company_domain")}


STRONG = {"form_d", "domain_keyed", "news_linked", "company_attached"}
WEAK = {"headline_name_match", "company_attached_unnamed", "news_resolved"}


def stage_evidence(c, cap_conf, cap_amt, cap_stage, tags) -> str:
    bi = c["batch_or_investor"] or ""
    srcs = c["sources"] or ""
    yc = re.search(r"YC [WXSF](\d\d)", bi)
    if cap_conf in STRONG:
        if cap_stage == "pre_seed" or (cap_amt and cap_amt < 1_000_000 and cap_stage not in ("seed", "series_a")):
            return "verified_pre_seed"
        return "verified_seed_a"
    if cap_conf == "program_terms" or (yc and int(yc.group(1)) >= 25) or re.search(r"SR00[4-7]|2025|2026", bi):
        return "accelerator_in_window"
    if "getro" in srcs:
        return "vc_listed_early"
    if bi:
        return "accelerator_older"
    if cap_conf in WEAK:
        return "weak_name_match"
    if "H1" in tags or "hn_whoishiring" in srcs or "ats" in srcs or "H0" in tags:
        return "text_only"
    return "none"


COLS = ["rank", "icp_fit_score", "icp_class", "sector_category", "outside_sam_reason", "fit_geo", "fit_stage",
        "fit_headcount", "fit_sector", "stage_evidence", "rank_score", "rank_breakdown", "priority_score", "signal_count", "matched_hypotheses", "company_domain",
        "company_name", "one_liner", "sector", "hq_country", "batch_or_investor", "sources", "headcount", "headcount_band",
        "why_now", "h1_best_role", "h1_strength", "h1_family", "h1_role_url", "h1_comp", "gtm_roles_open", "open_roles_total",
        "h2_tier", "h2_event", "h2_confidence", "funding_stage", "funding_amount", "funding_date", "funding_window_status",
        "h3_tier", "h4_tier", "h4_date", "h4_channel", "h5_strength", "h5_evidence", "h6_strength", "h6_evidence",
        "h7_mrr", "h7_segment", "h8_strength", "h8_stars", "h9_connection", "h10_days_open", "h10_role",
        "site_status", "site_flags", "tech_stack", "linkedin_company", "gtm_engineer_hook", "ceo_name", "ceo_role",
        "ceo_linkedin", "ceo_x", "founders_count", "resolution_confidence", "flags"]


def main() -> None:
    conn = pdb.init_db()
    h1, h2, h3s = load("sublist_H1_job_arbitrage.csv"), load("sublist_H2_fresh_capital.csv"), \
        load("sublist_H3_technical_founders.csv")
    h3x, h4, h5, h6 = load("sublist_H3_expanded.csv"), load("sublist_H4_launch_decay.csv"), \
        load("sublist_H5_outbound_stack.csv"), load("sublist_H6_founder_led_sales.csv")
    h7, h8, h9, h10 = load("sublist_H7_revenue_no_gtm.csv"), load("sublist_H8_oss_traction.csv"), \
        load("sublist_H9_warm_paths.csv"), load("sublist_H10_stale_gtm_roles.csv")
    fs = load("sublist_H2_funding_status_all.csv")
    founders = defaultdict(list)
    for f in conn.execute("SELECT * FROM founders ORDER BY founder_id"):
        founders[f["company_domain"]].append(dict(f))
    jobs = defaultdict(list)
    for j in conn.execute("SELECT company_domain, title, url, comp, family, strength, posted_at, source, last_seen_at "
                          "FROM jobs"):
        jobs[j["company_domain"]].append(dict(j))
    fev = defaultdict(list)
    for e in conn.execute("SELECT * FROM funding_events"):
        fev[e["company_domain"]].append(dict(e))
    scan = {r["company_domain"]: dict(r) for r in conn.execute("SELECT * FROM site_scan")}
    resconf = {}
    for r in conn.execute("SELECT domain, confidence FROM resolutions WHERE status='resolved'"):
        if r["domain"] and resconf.get(r["domain"]) != "high":
            resconf[r["domain"]] = r["confidence"]
    rows = []
    for c in conn.execute("SELECT * FROM companies WHERE status != 'disqualified'"):
        d = c["company_domain"]
        tags = json.loads(c["matched_hypotheses"])
        sig = [t for t in tags if t != "H0"]
        pts, flags, why = {"signals": 25 * len(sig)}, [], []
        # ---- H1: best role from v1 sublist or jobs table
        a = h1.get(d)
        best = None
        if a:
            best = {"title": a["best_role_title"], "strength": a["match_strength"], "family": a["match_families"],
                    "url": a["best_role_url"], "comp": a["best_role_comp"]}
        live_gtm = [j for j in jobs.get(d, []) if j["strength"] in ("S1", "S2", "S3")]
        for j in sorted(live_gtm, key=lambda j: (STR[j["strength"]], j["family"] != "exact_role")):
            if not best or STR[j["strength"]] < STR.get(best["strength"], 9):
                best = {"title": j["title"], "strength": j["strength"], "family": j["family"], "url": j["url"],
                        "comp": j["comp"]}
            break
        if "H1" in tags and best:
            pts["h1_role"] = {"S1": 20, "S2": 12, "S3": 6}.get(best["strength"], 0)
            if re.search(r"exact_role|hidden_gtm", best["family"] or ""):
                pts["h1_exact_or_hidden"] = 8
            why.append(f"hiring '{best['title']}'")
        # ---- H2: freshest in-window capital event
        b = h2.get(d)
        ev = sorted([e for e in fev.get(d, []) if e["stage"] != "series_b_plus" and e["event_date"]],
                    key=lambda e: e["event_date"], reverse=True)
        cap_date, cap_conf, cap_txt, cap_tier, cap_amt, cap_stage = None, None, "", "", None, None
        if b:
            cap_amt = int(float(b["amount_usd"])) if b.get("amount_usd") else None
            cap_stage = b.get("round_stage")
            cap_date, cap_conf = b["capital_event_date"], b["confidence"]
            cap_txt = f"{b['capital_event_date']} {b['round_label'] or b['round_stage']} {b['amount_band']}"
            cap_tier = b["h2_tier"]
        if ev and (not cap_date or ev[0]["event_date"] > cap_date or (cap_conf == "program_terms")):
            e = ev[0]
            cap_date, cap_conf = e["event_date"], e["confidence"]
            cap_amt, cap_stage = e["amount_usd"], e["stage"]
            cap_txt = f"{e['event_date']} {e['stage']} ${(e['amount_usd'] or 0) / 1e6:.1f}M ({e['source']})"
            cap_tier = "fresh_60d" if (TODAY - date.fromisoformat(cap_date)).days <= 60 else "in_window"
        if "H2" in tags and cap_date:
            days = (TODAY - date.fromisoformat(cap_date[:10])).days
            if cap_conf == "program_terms":
                pts["h2_capital"] = 8
            else:
                pts["h2_capital"] = 15 if days <= 60 else 10 if days <= 180 else 6 if days <= 365 else 3
            if cap_conf in ("headline_name_match", "company_attached_unnamed", "news_resolved"):
                pts["h2_weak_evidence"] = -4
            why.append(f"capital {cap_txt.strip()} ({days}d ago)")
        status = (fs.get(d) or {}).get("funding_window_status", "")
        if not status:
            status = "verified_in_window" if cap_date and cap_conf != "program_terms" else \
                "accelerator_check_only" if cap_conf == "program_terms" else "no_evidence"
        late = [e for e in fev.get(d, []) if e["stage"] == "series_b_plus" and e["confidence"] in STRONG]
        if "possible_past_series_a" in ((fs.get(d) or {}).get("flags") or "") or late:
            pts["possible_past_series_a"] = -15
            flags.append("possible_past_series_a_verify")
        # ---- H3
        t3 = (h3s.get(d) or {}).get("h3_tier") or (h3x.get(d) or {}).get("h3_tier") or (a or {}).get("h3_tier") or ""
        if "H3" in tags and t3 in ("A_verified", "B_likely"):
            pts["h3_tier"] = 10 if t3 == "A_verified" else 5
            why.append("all-technical founders" if t3 == "A_verified" else "technical founder(s)")
        # ---- size
        hc, band = c["headcount"], c["headcount_band"]
        if hc is not None:
            pts["headcount"] = 5 if 3 <= hc <= 20 else 2 if hc <= 35 else 0
        elif band:
            pts["headcount"] = 3 if band == "1-10" else 1
        # ---- H4..H10
        r4, r5, r6, r7, r8, r9, r10 = (x.get(d) for x in (h4, h5, h6, h7, h8, h9, h10))
        if r4 and "H4" in tags:
            pts["h4_launch"] = {"decay_window": 6, "pre_decay": 3, "post_launch": 2}[r4["launch_tier"]]
            why.append(f"launched {r4['days_since_launch']}d ago on {r4['channel']}")
        if r5 and "H5" in tags:
            pts["h5_outbound"] = 6 if r5["h5_strength"] == "S1" else 3
            why.append(f"outbound stack: {r5['evidence'][:60]}")
        if r6 and "H6" in tags:
            pts["h6_founder_led"] = 6 if r6["h6_strength"] == "S1" else 3
            why.append("founder-led sales")
        if r7 and "H7" in tags:
            m = float(r7["mrr_usd"] or 0)
            pts["h7_revenue"] = 8 if m >= 50000 else 6 if m >= 10000 else 3
            why.append(f"verified MRR ${m:,.0f}")
        if r8 and "H8" in tags:
            pts["h8_oss"] = 6 if r8["h8_strength"] == "S1" else 3
            why.append(f"OSS {int(float(r8['stars_total'])):,} stars")
        if r9 and "H9" in tags:
            pts["h9_warm"] = 15
            why.append(f"warm path via {r9['connection']}")
        if r10 and "H10" in tags:
            pts["h10_stale"] = 6 if r10["h10_strength"] == "S1" else 4
            why.append(f"GTM role open {r10['days_open']}d")
        # ---- penalties
        has_stage = bool(cap_date) or (c["batch_or_investor"] or "") != "" or c["funding_stage"]
        if not has_stage:
            pts["no_stage_evidence"] = -3
            flags.append("no_funding_or_stage_evidence")
        if not c["hq_country"]:
            pts["geo_unknown"] = -3
            flags.append("geo_unknown")
        s = scan.get(d) or {}
        soc = json.loads(s.get("social") or "{}") if s else {}
        site_flags = []
        if s and (not s.get("http_status") or s["http_status"] < 0 or s["http_status"] >= 400):
            site_flags.append("homepage_unreachable")
        if soc.get("parked"):
            site_flags.append("parked")
        if site_flags:
            pts["site_dead"] = -5
            flags += site_flags
        if resconf.get(d) == "medium" and len(json.loads(c["sources"] or "[]")) <= 1:
            flags.append("domain_match_medium_verify")
        sev = stage_evidence(c, cap_conf, cap_amt, cap_stage, tags)
        snips = " ".join((p.get("snippet") or "")[:200] for p in json.loads(c["live_signal_proof"])[:6])
        text = f"{c['one_liner'] or ''} {soc.get('desc') or ''} {(s.get('title') or '') if s else ''} {snips}"
        fitr = icp.fit(country=c["hq_country"], headcount=hc, band=band, sector_label=c["sector"], text=text,
                       stage_evidence=sev, possible_past_a="possible_past_series_a_verify" in flags)
        fl = founders.get(d, [])
        ceo = next((f for f in fl if re.search(r"\bceo\b|chief executive", f["role"] or "", re.I)), fl[0] if fl else None)
        rows.append({
            **fitr, "stage_evidence": sev,
            "rank_score": sum(pts.values()), "rank_breakdown": " ".join(f"{k}{v:+d}" for k, v in pts.items()),
            "priority_score": c["priority_score"], "signal_count": len(sig), "matched_hypotheses": "|".join(tags),
            "company_domain": d, "company_name": c["company_name"], "one_liner": c["one_liner"], "sector": c["sector"],
            "hq_country": c["hq_country"], "batch_or_investor": c["batch_or_investor"],
            "sources": "|".join(json.loads(c["sources"] or "[]")), "headcount": hc, "headcount_band": band,
            "why_now": "; ".join(why), "h1_best_role": (best or {}).get("title", "") if "H1" in tags else "",
            "h1_strength": (best or {}).get("strength", "") if "H1" in tags else "",
            "h1_family": (best or {}).get("family", "") if "H1" in tags else "",
            "h1_role_url": (best or {}).get("url", "") if "H1" in tags else "",
            "h1_comp": (best or {}).get("comp", "") if "H1" in tags else "",
            "gtm_roles_open": len([j for j in live_gtm if j["strength"] in ("S1", "S2")]),
            "open_roles_total": len(jobs.get(d, [])), "h2_tier": cap_tier if "H2" in tags else "",
            "h2_event": cap_txt if "H2" in tags else "", "h2_confidence": cap_conf if "H2" in tags else "",
            "funding_stage": c["funding_stage"], "funding_amount": c["funding_amount"], "funding_date": c["funding_date"],
            "funding_window_status": status, "h3_tier": t3,
            "h4_tier": (r4 or {}).get("launch_tier", ""), "h4_date": (r4 or {}).get("launch_date", ""),
            "h4_channel": (r4 or {}).get("channel", ""), "h5_strength": (r5 or {}).get("h5_strength", ""),
            "h5_evidence": (r5 or {}).get("evidence", ""), "h6_strength": (r6 or {}).get("h6_strength", ""),
            "h6_evidence": (r6 or {}).get("evidence", ""), "h7_mrr": (r7 or {}).get("mrr_usd", ""),
            "h7_segment": (r7 or {}).get("segment", ""), "h8_strength": (r8 or {}).get("h8_strength", ""),
            "h8_stars": (r8 or {}).get("stars_total", ""), "h9_connection": (r9 or {}).get("connection", ""),
            "h10_days_open": (r10 or {}).get("days_open", ""), "h10_role": (r10 or {}).get("role", ""),
            "site_status": s.get("http_status") if s else "", "site_flags": "|".join(site_flags),
            "tech_stack": " ".join(json.loads(s.get("tech") or "[]")) if s else "",
            "linkedin_company": soc.get("linkedin") or c["linkedin_url"] or "",
            "gtm_engineer_hook": (a or {}).get("gtm_engineer_hook", ""),
            "ceo_name": ceo["full_name"] if ceo else "", "ceo_role": (ceo or {}).get("role") or "",
            "ceo_linkedin": (ceo or {}).get("linkedin_url") or "",
            "ceo_x": f"@{ceo['x_handle']}" if ceo and ceo.get("x_handle") else "", "founders_count": len(fl),
            "resolution_confidence": resconf.get(d, ""), "flags": "|".join(flags)})
    rows.sort(key=lambda r: (-r["rank_score"], r["headcount"] or 99, r["company_domain"]))
    for i, r in enumerate(rows, 1):
        r["rank"] = i
    with open(OUT, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLS)
        w.writeheader()
        w.writerows(rows)
    combo = defaultdict(int)
    for r in rows:
        combo[r["signal_count"]] += 1
    print(json.dumps({"ranked_companies": len(rows), "by_signal_count": dict(sorted(combo.items()))}, indent=1))


if __name__ == "__main__":
    main()
