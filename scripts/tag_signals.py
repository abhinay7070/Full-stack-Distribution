"""Derive H3 (expanded), H4, H5, H6, H7, H8, H9, H10 from stored evidence (deterministic, idempotent).

Usage: python3 scripts/tag_signals.py
Evidence: jobs, site_scan, company_emails, founders, .cache/{launches,trustmrr,github_orgs,trending}.jsonl,
          raw_sources/H0_icp_universe/yc_all.json.gz, inputs/linkedin_connections.csv (H9, optional)
Writes tags via pipeline_db.upsert_company (proof per hypothesis) and sublists/sublist_H{n}_*.csv.

H3  100%-technical founding team (h3 rules) for every universe company with founder titles/bios
H4  launch (Show HN / Launch HN / YC Launch / Product Hunt top) in the last 120 days:
    pre_decay < 14d | decay_window 14-45d | post_launch 46-120d
H5  outbound stack being built: S1 intent/outbound tooling on site (RB2B, Warmly, Koala, Clearbit Reveal,
    Dealfront, Apollo tracker, Common Room, Unify, 6sense, Demandbase, ZoomInfo WebSights, HockeyStack, Vector,
    Chili Piper, Default, Qualified) or a job that names outbound tools / asks to build outbound;
    S2 outbound-motion hire (GTM engineer, SDR/BDR, RevOps). CRM/MA-only (HubSpot, Marketo, Pardot, Salesforce) is
    recorded as context, not a tag.
H6  founder-led sales: S1 a founder's personal booking link, a founder-published email inviting customers, or job
    text saying sales is founder-led / first sales hire takes over from founders; S2 inferred: <= 10 people,
    all-technical founders, no GTM founder, no GTM role posted.
H7  verified revenue (TrustMRR, Stripe/LemonSqueezy-connected) >= $5K MRR, B2B category, ICP geo or unknown.
    Companies not yet in the universe are admitted as segment 'bootstrapped_revenue'.
H8  open-source traction: org stars (sum of listed repos) >= 2,000 or a repo trending in the last 120 days -> S1;
    300-1,999 -> S2.
H9  warm path: a 1st-degree LinkedIn connection currently at the company (needs inputs/linkedin_connections.csv).
H10 stale GTM role: S1/S2 GTM role first published >= 60 days ago and still live (>= 90 days -> S1).
"""
from __future__ import annotations

import csv
import gzip
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import h3_ingest as h3  # noqa: E402
import icp_rules as icp  # noqa: E402
import pipeline_db as pdb  # noqa: E402
import resolve_domains as rd  # noqa: E402

CACHE = pdb.ROOT / ".cache"
TODAY = date.today()
SUB = pdb.SUBLIST_DIR
INTENT = {"rb2b", "warmly", "koala", "clearbit_reveal", "leadfeeder_dealfront", "apollo_tracker", "common_room", "unify",
          "6sense", "demandbase", "zoominfo_websights", "hockeystack", "vector", "chili_piper", "default_com", "qualified"}
CRM = {"hubspot", "marketo", "pardot", "salesforce_web", "spf:hubspot", "spf:salesforce", "customer_io", "attio"}
OUT_TOOLS = {"clay", "apollo", "instantly", "smartlead", "lemlist", "outreach", "salesloft", "heyreach", "amplemarket",
             "unify", "common room", "rb2b", "warmly", "koala", "clearbit", "zoominfo", "sales navigator",
             "la growth machine", "regie", "lavender", "6sense"}
B2C_CATS = re.compile(r"e-?commerce|mobile app|games?|gaming|health ?& ?fitness|entertainment|dating|social media|"
                      r"travel|food|lifestyle|education|crypto|nft|adult|news", re.I)


def jl(p: Path) -> list[dict]:
    return [json.loads(x) for x in open(p)] if p.exists() else []


def write(name: str, rows: list[dict]) -> None:
    p = SUB / name
    if not rows:
        p.write_text("company_domain\n")
        return
    keys = list(dict.fromkeys(k for r in rows for k in r))
    with open(p, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        w.writerows(rows)


def main() -> None:
    conn = pdb.init_db()
    comp = {r["company_domain"]: dict(r) for r in conn.execute("SELECT * FROM companies WHERE status!='disqualified'")}
    founders = defaultdict(list)
    for f in conn.execute("SELECT * FROM founders"):
        founders[f["company_domain"]].append(dict(f))
    brand = defaultdict(set)
    for d, c in comp.items():
        if c["company_name"]:
            _, full, short = rd.brand_cores(c["company_name"])
            if len(full) >= 4:
                brand[full].add(d)
    stats = Counter()

    def tag(d, h, src, url, snippet):
        pdb.upsert_company(conn, d, h, proof={"source": src, "url": url, "snippet": snippet[:300]})
        stats[h] += 0  # ensure key

    # ---------------- H3 expanded (YC bios for every universe YC company; title-only elsewhere)
    yc = {pdb.normalize_domain(r.get("website")): r for r in json.load(gzip.open(
        pdb.RAW_DIR / "H0_icp_universe" / "yc_all.json.gz", "rt")) if r.get("website")}
    h3rows = []
    for d, c in comp.items():
        if '"H3"' in c["matched_hypotheses"]:
            prev = [p for p in json.loads(c["live_signal_proof"]) if p.get("source") == "tag_signals:h3_rules"]
            if prev:  # keep rows tagged by earlier runs in the sublist (idempotent output)
                m = re.match(r"\[H3\|(\w+)\]\s*(.*)", prev[0].get("snippet") or "")
                h3rows.append({"company_domain": d, "company_name": c["company_name"],
                               "h3_tier": m.group(1) if m else "", "reason": m.group(2) if m else "", "founders": ""})
            continue
        fl = []
        if d in yc:
            fl = [{"name": f["full_name"], "title": f.get("title") or "", "bio": f.get("founder_bio") or ""}
                  for f in yc[d].get("founders") or [] if str(f.get("is_active", True)) != "False"]
        if not fl:
            fl = [{"name": f["full_name"], "title": f["role"] or "", "bio": ""} for f in founders.get(d, [])
                  if re.search(r"founder|ceo|cto|chief|president", f["role"] or "", re.I)]
        if not fl:
            continue
        cls = []
        for f in fl:
            tc, is_gtm, why = h3.classify_founder(f["title"], f["bio"])
            cls.append({**f, "tech_class": tc, "is_gtm": is_gtm, "evidence": why})
        tier, reason, gtm = h3.h3_verdict(cls)
        if tier in ("A_verified", "B_likely"):
            tag(d, "H3", "tag_signals:h3_rules", yc[d].get("url") if d in yc else f"https://{d}",
                f"[H3|{tier}] {reason}")
            h3rows.append({"company_domain": d, "company_name": c["company_name"], "h3_tier": tier, "reason": reason,
                           "founders": "; ".join(f"{f['name']} ({f['title']})" for f in fl)})
    write("sublist_H3_expanded.csv", h3rows)
    stats["H3_expanded"] = len(h3rows)

    # ---------------- H4 launches
    rows4 = []
    best4 = {}
    for L in jl(CACHE / "launches.jsonl"):
        if not L.get("date"):
            continue
        age = (TODAY - date.fromisoformat(L["date"])).days
        if age < 0 or age > 120:
            continue
        d = L.get("domain") if L.get("domain") in comp else None
        if not d and L.get("name"):
            hits = brand.get(rd.brand_cores(L["name"])[1], set())
            d = next(iter(hits)) if len(hits) == 1 else None
        if not d:
            continue
        tier = "pre_decay" if age < 14 else "decay_window" if age <= 45 else "post_launch"
        rank = {"decay_window": 0, "pre_decay": 1, "post_launch": 2}[tier]
        if d not in best4 or (rank, age) < (best4[d][0], best4[d][1]):
            best4[d] = (rank, age, tier, L)
    for d, (_, age, tier, L) in best4.items():
        tag(d, "H4", L["source"], L.get("hn_url") or L.get("url"), f"[H4|{tier}|{L['date']}] {L['title'][:150]} "
                                                                     f"({L.get('points') or 0} pts, {age}d ago)")
        rows4.append({"company_domain": d, "company_name": comp[d]["company_name"], "launch_tier": tier,
                      "launch_date": L["date"], "days_since_launch": age, "channel": L["source"],
                      "title": L["title"], "points": L.get("points"), "url": L.get("hn_url") or L.get("url")})
    write("sublist_H4_launch_decay.csv", sorted(rows4, key=lambda r: r["days_since_launch"]))
    stats["H4"] = len(rows4)

    # ---------------- H5 / H6 inputs
    scan = {r["company_domain"]: dict(r) for r in conn.execute("SELECT * FROM site_scan")}
    jobs = defaultdict(list)
    for j in conn.execute("SELECT * FROM jobs"):
        jobs[j["company_domain"]].append(dict(j))
    emails = {r["company_domain"]: dict(r) for r in conn.execute("SELECT * FROM company_emails")}
    rows5, rows6 = [], []
    for d, c in comp.items():
        s = scan.get(d) or {}
        tech = set(json.loads(s.get("tech") or "[]"))
        intent = sorted(tech & INTENT)
        crm = sorted(tech & CRM)
        js = jobs.get(d, [])
        tool_jobs = []
        for j in js:
            sig = json.loads(j["text_signals"]) if j.get("text_signals") else {}
            t = set(sig.get("tools") or []) & OUT_TOOLS
            if t or sig.get("outbound"):
                tool_jobs.append((j, sorted(t), sig.get("outbound")))
        roles = [j for j in js if j["family"] in ("exact_role", "revops") or
                 re.search(r"\b(sdr|bdr|sales development|business development rep|outbound)\b", j["title"] or "", re.I)]
        strength, ev, url = None, "", f"https://{d}"
        if intent or tool_jobs:
            strength = "S1"
            parts = []
            if intent:
                parts.append("site runs " + ", ".join(intent))
            if tool_jobs:
                j, t, ob = tool_jobs[0]
                parts.append(f"job '{j['title'][:60]}' names {', '.join(t) or ob}")
                url = j["url"] or url
            ev = "; ".join(parts)
        elif roles:
            strength, ev, url = "S2", f"outbound-motion hire: {roles[0]['title'][:80]}", roles[0]["url"] or url
        if strength:
            tag(d, "H5", "tag_signals:h5", url, f"[H5|{strength}] {ev}" + (f" | CRM/MA: {', '.join(crm)}" if crm else ""))
            rows5.append({"company_domain": d, "company_name": c["company_name"], "h5_strength": strength,
                          "evidence": ev, "intent_tools": " ".join(intent), "crm_ma": " ".join(crm),
                          "tool_jobs": " | ".join(f"{j['title'][:50]}: {','.join(t)}" for j, t, _ in tool_jobs[:3]),
                          "url": url})
        # H6
        fl = founders.get(d, [])
        firsts = {re.sub(r"[^a-z]", "", (f["full_name"] or "").split()[0].lower()) for f in fl if f["full_name"]}
        firsts = {x for x in firsts if len(x) >= 3}
        book = json.loads(s.get("booking_links") or "[]")
        fbook = [b for b in book if any(fn in b.lower() for fn in firsts) or re.search(r"founder|ceo", b, re.I)]
        e = emails.get(d) or {}
        fl_jobs = [j for j in js if j.get("text_signals") and "founder_led" in j["text_signals"]] + \
                  [j for j in js if j["source"] == "hn_whoishiring" and re.search(
                      r"founder[- ]led|work directly with (our|the) (ceo|founders?)", j.get("description_snip") or "", re.I)]
        s6, ev6, url6 = None, "", f"https://{d}"
        if fbook:
            s6, ev6, url6 = "S1", f"founder booking link {fbook[0]}", f"https://{fbook[0]}"
        elif e.get("email_confidence") in ("published_ceo", "published_founder"):
            s6, ev6, url6 = "S1", f"founder publishes own email for customers ({e['email']})", e.get("source_url") or url6
        elif fl_jobs:
            s6, ev6, url6 = "S1", f"job says sales is founder-led: '{fl_jobs[0]['title'][:60]}'", fl_jobs[0]["url"] or url6
        else:
            gtm_roles = [j for j in js if j["strength"] in ("S1", "S2")]
            small = (c["headcount"] is not None and c["headcount"] <= 10) or c["headcount_band"] == "1-10"
            if small and '"H3"' in c["matched_hypotheses"] and not gtm_roles and fl:
                s6, ev6 = "S2", "inferred: <=10 people, technical founders, no GTM hire or GTM role"
        if s6:
            tag(d, "H6", "tag_signals:h6", url6, f"[H6|{s6}] {ev6}")
            rows6.append({"company_domain": d, "company_name": c["company_name"], "h6_strength": s6, "evidence": ev6,
                          "url": url6})
    write("sublist_H5_outbound_stack.csv", rows5)
    write("sublist_H6_founder_led_sales.csv", rows6)
    stats["H5"], stats["H6"] = len(rows5), len(rows6)

    # ---------------- H7 TrustMRR
    rows7 = []
    for r in jl(CACHE / "trustmrr.jsonl"):
        d = r.get("domain")
        mrr = r.get("mrr") or 0
        if not d or mrr < 5000 or B2C_CATS.search(r.get("category") or ""):
            continue
        ctry = (r.get("country") or "").upper()[:2] or None
        ctry = {"UK": "GB"}.get(ctry, ctry)
        if ctry and ctry not in h3.ALLOWED:
            continue
        new = d not in comp
        if new:
            keep, why, flags, label = icp.check(country=ctry, stage="unknown", text=f"{r.get('desc') or ''} {r.get('category') or ''}",
                                                tags=[r.get("category") or ""])
            if not keep:
                continue
        g = None
        if r.get("mrr_90d_ago"):
            g = round(100 * (mrr - r["mrr_90d_ago"]) / max(r["mrr_90d_ago"], 1))
        tag(d, "H7", "trustmrr", r.get("url"), f"[H7|{'new_bootstrapped' if new else 'universe'}] verified MRR "
                                                f"${mrr:,.0f} ({r.get('mrr_date')}){f', {g:+d}% vs 90d' if g is not None else ''}"
                                                f"; {r.get('category') or ''}")
        if new:
            conn.execute("UPDATE companies SET company_name=COALESCE(company_name, ?), hq_country=COALESCE(hq_country, ?), "
                         "sources=? WHERE company_domain=?", (r["name"], ctry, json.dumps(["trustmrr"]), d))
        rows7.append({"company_domain": d, "company_name": r["name"], "segment": "bootstrapped_revenue" if new else "universe",
                      "mrr_usd": mrr, "mrr_date": r.get("mrr_date"), "growth_90d_pct": g, "category": r.get("category"),
                      "country": ctry, "for_sale": r.get("for_sale"), "url": r.get("url")})
    write("sublist_H7_revenue_no_gtm.csv", sorted(rows7, key=lambda x: -x["mrr_usd"]))
    stats["H7"] = len(rows7)

    # ---------------- H8 GitHub
    trend = defaultdict(list)
    for t in jl(CACHE / "trending.jsonl"):
        if t.get("repo") and "/" in t["repo"] and (t.get("date") or "") >= (TODAY - timedelta(days=120)).isoformat():
            trend[t["repo"].split("/")[0].lower()].append(t)
    rows8 = []
    for g in jl(CACHE / "github_orgs.jsonl"):
        tot = g.get("stars_total_seen") or 0
        tr = trend.get(g["org"])
        s8 = "S1" if tot >= 2000 or tr else "S2" if tot >= 300 else None
        if not s8:
            continue
        for d in g["domains"]:
            if d not in comp:
                continue
            ev = f"github.com/{g['org']}: {tot:,} stars (top {g['top_repo']} {g['top_stars']:,})" + \
                 (f"; trending {tr[-1]['repo']} {tr[-1]['date']}" if tr else "")
            tag(d, "H8", "github", f"https://github.com/{g['org']}", f"[H8|{s8}] {ev}")
            rows8.append({"company_domain": d, "company_name": comp[d]["company_name"], "h8_strength": s8, "org": g["org"],
                          "stars_total": tot, "top_repo": g["top_repo"], "top_stars": g["top_stars"],
                          "trending": tr[-1]["repo"] if tr else "", "url": f"https://github.com/{g['org']}"})
    write("sublist_H8_oss_traction.csv", sorted(rows8, key=lambda x: -x["stars_total"]))
    stats["H8"] = len(rows8)

    # ---------------- H10 stale GTM roles
    rows10 = []
    recent = (TODAY - timedelta(days=10)).isoformat()
    for d, js in jobs.items():
        if d not in comp:
            continue
        st = [j for j in js if j["strength"] in ("S1", "S2") and j["posted_at"] and j["source"].startswith("ats:")
              and (TODAY - date.fromisoformat(j["posted_at"][:10])).days >= 60 and (j["last_seen_at"] or "") >= recent]
        if not st:
            continue
        j = min(st, key=lambda x: x["posted_at"])
        age = (TODAY - date.fromisoformat(j["posted_at"][:10])).days
        s10 = "S1" if age >= 90 else "S2"
        tag(d, "H10", j["source"], j["url"], f"[H10|{s10}|{j['posted_at']}] '{j['title'][:80]}' open {age} days ({j['family']})")
        rows10.append({"company_domain": d, "company_name": comp[d]["company_name"], "h10_strength": s10,
                       "role": j["title"], "family": j["family"], "posted_at": j["posted_at"], "days_open": age,
                       "stale_gtm_roles": len(st), "url": j["url"]})
    write("sublist_H10_stale_gtm_roles.csv", sorted(rows10, key=lambda x: -x["days_open"]))
    stats["H10"] = len(rows10)

    # ---------------- H9 warm paths (optional input)
    rows9 = []
    inp = pdb.ROOT / "inputs" / "linkedin_connections.csv"
    if inp.exists():
        lines = inp.read_text(encoding="utf-8-sig").splitlines()
        start = next((i for i, l in enumerate(lines) if l.lower().startswith("first name")), 0)
        for r in csv.DictReader(lines[start:]):
            co = r.get("Company") or ""
            hits = brand.get(rd.brand_cores(co)[1], set()) if co else set()
            if len(hits) == 1:
                d = next(iter(hits))
                nm = f"{r.get('First Name', '')} {r.get('Last Name', '')}".strip()
                tag(d, "H9", "linkedin_connections", r.get("URL") or "", f"[H9|1st-degree] {nm}, {r.get('Position', '')}")
                rows9.append({"company_domain": d, "company_name": comp[d]["company_name"], "connection": nm,
                              "position": r.get("Position"), "connected_on": r.get("Connected On"), "url": r.get("URL")})
    write("sublist_H9_warm_paths.csv", rows9)
    stats["H9"] = len(rows9)
    stats["H9_input_present"] = int(inp.exists())
    conn.commit()
    print(json.dumps(dict(stats), indent=1))


if __name__ == "__main__":
    main()
