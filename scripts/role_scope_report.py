"""Role-scope research, last step: render the PMF evidence report (self-contained HTML, inline SVG charts).

Reads (all full-population outputs; nothing sampled):
  research/gtm_role_scope/ml/duty_stats_ml.csv, postings_labels.csv.gz, intent_kpi_tools_ml.csv, classifier_agreement.csv,
  discovered_clusters.csv, summary_ml.json      (role_scope_ml.py)
  research/gtm_role_scope/analysis/unified_scope.csv, duty_clusters.csv           (role_scope_synthesize.py)
  research/gtm_role_scope/extracted/postings_tagged.csv (comp, posting dates), bullets.csv.gz (verbatim quotes)
Writes research/gtm_role_scope/report.html
"""
from __future__ import annotations

import csv
import gzip
import html
import json
import re
import sys
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import role_taxonomy as T  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
B = ROOT / "research" / "gtm_role_scope"
A, MLD = B / "analysis", B / "ml"
AS_OF = pd.Timestamp("2026-11-08")
e = html.escape
csv.field_size_limit(10 ** 7)


def rd(p):
    return list(csv.DictReader(open(p)))


scope = {r["duty_id"]: r for r in rd(A / "unified_scope.csv")}
clusters = rd(A / "duty_clusters.csv")
ikt = rd(MLD / "intent_kpi_tools_ml.csv")
agree = {r["duty_id"]: r for r in rd(MLD / "classifier_agreement.csv")}
disc = rd(MLD / "discovered_clusters.csv")
sm = json.load(open(MLD / "summary_ml.json"))
LAB = {d["id"]: d["label"] for d in T.DUTIES}
DEC = {i: scope[i]["decision_8o"] for i in scope}

L = pd.read_csv(MLD / "postings_labels.csv.gz", dtype=str, keep_default_na=False)
for c in ("core", "founding_title", "small_team", "scaled"):
    L[c] = L[c] == "True"
L["n_scope"] = L["n_scope"].astype(int)
for c in ("duties_ml", "duties_regex", "intent", "kpis", "tools"):
    L[c] = L[c].fillna("")
TAG = pd.read_csv(B / "extracted" / "postings_tagged.csv", usecols=["job_key", "comp_usd_mid", "posted_at", "founding_any", "icp_tier"])
I = L[(L.corpus == "icp") & (L.n_scope >= 3)].merge(TAG, left_on="key", right_on="job_key", how="left")
C = I[I.core].copy()
C["age"] = (AS_OF - pd.to_datetime(C.posted_at, errors="coerce")).dt.days
C["dset"] = C.duties_ml.str.split("|").apply(set)


def money(x):
    return f"${float(x) / 1000:.0f}K"


# ------------------------------------------------------------------ chart primitives
def svg(w, h, body, label):
    return f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="{e(label)}" class="chart">{body}</svg>'


def hbars(items, xmax, fmt, W=760, Lm=230, title="", ref=None, half=False):
    if half:
        W, Lm = 470, 200
        items = [((lab if len(lab) <= 30 else lab[:29] + "…"), v, tip, c) for lab, v, tip, c in items]
    rowh, R = 26, 58
    H = 18 + rowh * len(items) + (22 if ref else 0)
    X = lambda v: Lm + (W - Lm - R) * v / xmax  # noqa: E731
    b = []
    if ref:
        lo, hi, txt = ref
        y1 = 10 + rowh * len(items)
        b.append(f'<rect x="{X(lo):.1f}" y="6" width="{X(hi)-X(lo):.1f}" height="{y1-6}" class="band"/>'
                 f'<text x="{(X(lo)+X(hi))/2:.1f}" y="{y1+16}" class="axl" text-anchor="middle">{e(txt)}</text>')
    for k, (lab, v, tip, cls) in enumerate(items):
        y = 10 + k * rowh
        b.append(f'<text x="{Lm-10}" y="{y+13}" class="dl" text-anchor="end">{e(lab)}</text>'
                 f'<rect x="{Lm}" y="{y+3}" width="{max(2, X(v)-Lm):.1f}" height="14" rx="3" class="{cls}"><title>{e(tip)}</title></rect>'
                 f'<text x="{X(v)+6:.1f}" y="{y+14}" class="val">{fmt(v)}</text>')
    return svg(W, H, "".join(b), title)


def quadrant():
    W, H, Lm, R, Tp, Bt = 760, 480, 56, 20, 24, 48
    pts = [(i, float(r["pct_core"]), float(r["founding_lift"])) for i, r in scope.items() if r["founding_lift"]]
    xmax = max(10, int(max(p[1] for p in pts) / 10 + 1) * 10)
    ymin, ymax = 0.3, max(2.8, round(max(p[2] for p in pts) + 0.2, 1))
    X = lambda v: Lm + (W - Lm - R) * v / xmax  # noqa: E731
    Y = lambda v: Tp + (H - Tp - Bt) * (1 - (v - ymin) / (ymax - ymin))  # noqa: E731
    xm = float(np.median([p[1] for p in pts]))
    b = []
    for v in range(0, xmax + 1, 10):
        b.append(f'<line x1="{X(v):.1f}" x2="{X(v):.1f}" y1="{Tp}" y2="{H-Bt}" class="grid"/>'
                 f'<text x="{X(v):.1f}" y="{H-Bt+18}" class="tick" text-anchor="middle">{v}%</text>')
    yv = 0.5
    while yv <= ymax:
        b.append(f'<line x1="{Lm}" x2="{W-R}" y1="{Y(yv):.1f}" y2="{Y(yv):.1f}" class="grid"/>'
                 f'<text x="{Lm-8}" y="{Y(yv)+4:.1f}" class="tick" text-anchor="end">{yv:.1f}×</text>')
        yv += 0.5
    b.append(f'<line x1="{Lm}" x2="{W-R}" y1="{Y(1):.1f}" y2="{Y(1):.1f}" class="ref"/>'
             f'<line x1="{X(xm):.1f}" x2="{X(xm):.1f}" y1="{Tp}" y2="{H-Bt}" class="ref"/>')
    for txt, x, y, a in (("FIRST-HIRE SPECIALS", Lm + 8, Tp + 14, "start"), ("CORE OF THE JOB", W - R - 8, Tp + 14, "end"),
                         ("PERIPHERAL", Lm + 8, H - Bt - 8, "start"), ("LATER-HIRE TABLE STAKES", W - R - 8, H - Bt - 8, "end")):
        b.append(f'<text x="{x}" y="{y}" class="qlab" text-anchor="{a}">{txt}</text>')
    b.append(f'<text x="{(Lm+W-R)/2}" y="{H-8}" class="axl" text-anchor="middle">% of core growth-hire postings asking for the duty</text>'
             f'<text transform="translate(14 {(Tp+H-Bt)/2}) rotate(-90)" class="axl" text-anchor="middle">first-hire vs later-hire lift</text>')
    # label the 8 highest-demand duties and the 3 strongest first-hire lifts; avoid label-box overlap
    top = sorted(scope.values(), key=lambda r: -float(r["demand_index"]))[:8]
    hi = sorted(pts, key=lambda p: -p[2])[:3]
    show = {r["duty_id"] for r in top} | {p[0] for p in hi}
    boxes = []
    labels = []
    for i, x, y in sorted(pts, key=lambda p: p[0] in show):
        d = DEC[i].lower()
        cy = Y(min(max(y, ymin), ymax))
        b.append(f'<g class="pt"><circle cx="{X(x):.1f}" cy="{cy:.1f}" r="6" class="m-{d}"/>'
                 f'<title>{e(LAB[i])}: {x:.1f}% of core postings; first-hire lift {y:.2f}×; z {scope[i]["founding_z"]}; {DEC[i]}</title></g>')
        if i in show:
            labels.append((i, X(x), cy))
    for i, px, py in sorted(labels, key=lambda t: t[2]):
        txt = LAB[i] if len(LAB[i]) <= 40 else LAB[i][:38] + "…"
        wdt = 6.3 * len(txt)
        right = px + 10 + wdt < W - R
        x0 = px + 10 if right else px - 10 - wdt
        ly = py + 4
        for step in (0, 14, -14, 28, -28, 42, -42, 56):
            cand = py + 4 + step
            if not any(not (x0 + wdt < bx0 or x0 > bx1) and abs(cand - by) < 13 for bx0, bx1, by in boxes):
                ly = cand
                break
        boxes.append((x0, x0 + wdt, ly))
        if abs(ly - (py + 4)) > 1:
            b.append(f'<line x1="{px:.1f}" y1="{py:.1f}" x2="{(x0 if right else x0 + wdt):.1f}" y2="{ly - 4:.1f}" class="ref"/>')
        b.append(f'<text x="{x0 if right else x0 + wdt:.1f}" y="{ly:.1f}" class="dl" text-anchor="{"start" if right else "end"}">{e(txt)}</text>')
    return svg(W, H, "".join(b), "Duty prevalence versus first-hire lift")


def dumbbell():
    rows = sorted(scope.values(), key=lambda r: -abs(float(r["small_team_z"])))[:10]
    rows.sort(key=lambda r: -float(r["small_team_z"]))
    W, rowh, Lm, R = 760, 30, 300, 92
    H = 34 + rowh * len(rows)
    xmax = max(50, int(max(max(float(r["small_team_pct"]), float(r["rest_pct"])) for r in rows) / 10 + 1) * 10)
    X = lambda v: Lm + (W - Lm - R) * v / xmax  # noqa: E731
    b = [f'<text x="{Lm}" y="14" class="tick">0%</text><text x="{W-R}" y="14" class="tick" text-anchor="end">{xmax}%</text>']
    for k, r in enumerate(rows):
        y = 32 + k * rowh
        a, c = float(r["small_team_pct"]), float(r["rest_pct"])
        rob = " ✓" if r["small_team_robust"] == "True" else ""
        b.append(f'<text x="{Lm-10}" y="{y+4}" class="dl" text-anchor="end">{e(r["duty"][:42])}</text>'
                 f'<line x1="{Lm}" x2="{W-R}" y1="{y}" y2="{y}" class="grid"/>'
                 f'<line x1="{X(min(a,c)):.1f}" x2="{X(max(a,c)):.1f}" y1="{y}" y2="{y}" class="link"/>'
                 f'<circle cx="{X(c):.1f}" cy="{y}" r="5" class="m-rest"><title>larger or unknown: {c}%</title></circle>'
                 f'<circle cx="{X(a):.1f}" cy="{y}" r="6" class="m-own"><title>≤ 35 people: {a}%</title></circle>'
                 f'<text x="{W-R+8}" y="{y+4}" class="tick">z {float(r["small_team_z"]):+.1f}{rob}</text>')
    return svg(W, H, "".join(b), "Small team versus rest")


ARCH = [("sdr_bdr", "SDR / BDR"), ("gtm_generalist_lead", "Founding GTM"), ("growth", "Growth"),
        ("demand_gen", "Demand gen"), ("gtm_engineer", "GTM engineer"), ("revops", "RevOps"), ("ae_closer", "Founding AE")]


def heatmap():
    stages = ["LEARN", "DECIDE", "FIND", "REACH", "REPLY", "IMPROVE", "SYSTEMS", "CLOSE"]
    sid = {s: {d["id"] for d in T.DUTIES if d["stage"] == s} for s in stages}
    cw, ch, Lm, Tp = 74, 34, 200, 30
    W, H = Lm + cw * len(stages) + 6, Tp + ch * len(ARCH) + 6
    b = [f'<text x="{Lm+cw*k+cw/2}" y="18" class="tick" text-anchor="middle">{s}</text>' for k, s in enumerate(stages)]
    for r, (a, nm) in enumerate(ARCH):
        sub = C[C.archetype == a]
        b.append(f'<text x="{Lm-10}" y="{Tp+r*ch+ch/2+4}" class="dl" text-anchor="end">{nm} · n {len(sub):,}</text>')
        for k, s in enumerate(stages):
            v = 100 * sub.dset.apply(lambda x: bool(x & sid[s])).mean() if len(sub) else 0
            b.append(f'<g><rect x="{Lm+k*cw+1}" y="{Tp+r*ch+1}" width="{cw-2}" height="{ch-2}" rx="3" class="m-own" '
                     f'fill-opacity="{0.08 + 0.92 * v / 100:.2f}"/><text x="{Lm+k*cw+cw/2}" y="{Tp+r*ch+ch/2+4}" '
                     f'text-anchor="middle" class="{"cellt-in" if v >= 55 else "cellt"}">{v:.0f}</text>'
                     f'<title>{nm}, {s}: {v:.0f}% of postings ask for at least one {s} duty</title></g>')
    return svg(W, H, "".join(b), "Role type by job stage")


BLOCK_ORDER = ["Brain", "Lab", "Radar", "Voice", "Reply", "Memory", "Connections", "Playbooks"]


def stacked_blocks():
    agg = {}
    for r in scope.values():
        blk = r["block"] if not r["block"].startswith("(") else "Founder / out of scope"
        agg.setdefault(blk, Counter())[r["decision_8o"]] += float(r["demand_index"])
    keys = [k for k in BLOCK_ORDER if k in agg] + ["Founder / out of scope"]
    tot = max(sum(v.values()) for v in agg.values())
    W, Lm, R, rowh = 760, 200, 60, 30
    H = 16 + rowh * len(keys)
    X = lambda v: (W - Lm - R) * v / tot  # noqa: E731
    b = []
    for k, blk in enumerate(keys):
        y, x = 8 + k * rowh, Lm
        b.append(f'<text x="{Lm-10}" y="{y+15}" class="dl" text-anchor="end">{blk}</text>')
        for dn in ("OWN", "ASSIST", "OUT"):
            v = agg[blk].get(dn, 0)
            if v:
                b.append(f'<rect x="{x:.1f}" y="{y+4}" width="{max(1, X(v)-2):.1f}" height="16" rx="3" class="m-{dn.lower()}">'
                         f'<title>{blk}, {dn}: summed demand index {v:.0f}</title></rect>')
                x += X(v)
        b.append(f'<text x="{x+6:.1f}" y="{y+16}" class="val">{sum(agg[blk].values()):.0f}</text>')
    return svg(W, H, "".join(b), "Founder demand by 8-o block")


# ------------------------------------------------------------------ content
def pill(d, c=None):
    return f'<span class="pill {(c or d).lower()}">{d}</span>'


def ikt_row(field, item):
    return next(r for r in ikt if r["field"] == field and r["item"] == item)


def tree():
    want = {c["cluster"]: set(c["duties"].split()) for c in clusters[:6]}
    ex = {}
    for r in csv.DictReader(gzip.open(B / "extracted" / "bullets.csv.gz", "rt")):
        if r["core"] != "True" or r["section"] != "responsibilities" or not (70 < len(r["line"]) < 190):
            continue
        if re.search(r"[^\x00-ɏ–-‟]", r["line"]):
            continue
        ds = set(r["duties"].split("|")) - {""}
        for cid, s in want.items():
            if cid not in ex and len(ds & s) >= min(3, len(s)):
                ex[cid] = r["line"]
    out = []
    for c in clusters[:6]:
        ids = sorted(c["duties"].split(), key=lambda i: -float(scope[i]["pct_core"]))
        name = name_cluster(set(ids))
        chips = "".join(f'<span class="chip c-{DEC[i].lower()}" title="{e(LAB[i])}: {scope[i]["pct_core"]}% of core postings; {DEC[i]}">'
                        f'{e(LAB[i])}<b>{float(scope[i]["pct_core"]):.0f}%</b></span>' for i in ids)
        q = f'<p class="verb">“{e(ex[c["cluster"]])}”</p>' if c["cluster"] in ex else ""
        out.append(f'<div class="node"><div class="nh"><span class="nn">{name}</span><span class="nbar"><i style="width:'
                   f'{float(c["pct_core_postings_any"])}%"></i></span><b class="nv">{c["pct_core_postings_any"]}%</b></div>'
                   f'<div class="chips">{chips}</div>{q}</div>')
    return "".join(out)


def name_cluster(ids):
    rules = [({"F1", "R1", "B2"}, "Pipeline engine"), ({"D3", "M2"}, "Experiments & growth channels"),
             ({"M1", "S2"}, "Systems, data & measurement"), ({"D2"}, "Strategy & playbook"), ({"C2"}, "Closing"),
             ({"O1"}, "Team building"), ({"S6"}, "Team building"), ({"P2"}, "Post-sale")]
    for need, nm in rules:
        if need <= ids:
            return nm
    return " · ".join(LAB[i].split(",")[0] for i in sorted(ids)[:2])


def bucket_pct(ids):
    return 100 * C.dset.apply(lambda s: bool(s & ids)).mean()


def scorecard(pain):
    sm_ = {i: scope[i] for i in ("M3", "D2", "B1")}
    take = ikt_row("intent", "Take GTM over from the founders")
    ai = ikt_row("intent", "AI-native / automation expected")
    kq = ikt_row("kpis", "Qualified pipeline / SQLs / opportunities")
    km = ikt_row("kpis", "Meetings / demos booked")
    pe = clusters[0]
    rows = [
        ("The problem exists at scale",
         f'{pain["companies"]:,} ICP companies have an open core growth-hire role; {pain["companies_2"]:,} have two or more.', "VALIDATED", "own"),
        ("It is painful and unsolved",
         f'{pain["open60"]}% of dated core roles have been open more than 60 days, {pain["open90"]}% more than 90 (median {pain["med_age"]:.0f} days, n {pain["n_dated"]:,}).',
         "VALIDATED", "own"),
        ("Founders pay for it",
         f'${pain["budget"]}M of annual base salary is posted for {len(C):,} roles. Median {money(pain["med_comp"])}; founding roles {money(pain["found_comp"])}.',
         "VALIDATED", "own"),
        ("Founders want it off their plate",
         f'"Take GTM over from the founders": {take["pct_icp_core"]}% of ICP core postings vs {take["pct_market_core"]}% in the rest of the market (z {take["z"]}).',
         "VALIDATED", "own"),
        ("The job is the 8-o loop",
         f'{pe["pct_core_postings_any"]}% of core postings ask for the pipeline-engine bundle ({len(pe["duties"].split())} duties from ICP to booked meeting).',
         "VALIDATED", "own"),
        ("The closer to the ICP, the closer to 8-o",
         "At ≤ 35 people: " + "; ".join(f'{LAB[i].split(",")[0].lower()} {r["small_team_pct"]}% vs {r["rest_pct"]}% (z {float(r["small_team_z"]):+.1f})'
                                        for i, r in sm_.items()) + ".", "VALIDATED", "own"),
        ("An AI doing the job is acceptable",
         f'{ai["pct_icp_core"]}% of ICP core postings already expect AI / automation in the role ({ai["pct_market_core"]}% elsewhere). Acceptance of a fully agentic hire is not measured by postings.',
         "PARTIAL", "assist"),
        ("Founders buy 'meetings booked'",
         f'{km["pct_icp_core"]}% name meetings booked; {kq["pct_icp_core"]}% name qualified pipeline.', "REFRAME", "assist"),
        ("Founders accept a hire that does not close",
         f'Closing is asked for at {scope["C2"]["pct_companies"]}% of ICP companies. 8-o does not close.', "GAP", "out"),
    ]
    return "".join(f'<tr><td class="h">{e(h)}</td><td>{e(ev)}</td><td>{pill(v, c)}</td></tr>' for h, ev, v, c in rows)


def must_list():
    out = []
    for pr, rng in (("Must", "≥ 55"), ("Should", "30–55")):
        items = sorted((r for r in scope.values() if r["priority"] == pr), key=lambda r: -float(r["demand_index"]))
        lis = "".join(f'<li><span class="di" title="95% interval {r["demand_ci95"]}, rank {r["rank"]} (95% {r["rank_ci95"]}), '
                      f'P(Must) {float(r["p_must"]):.2f}">{float(r["demand_index"]):.0f}</span><span>{e(r["duty"])}'
                      f' <span class="mute" style="font-size:12px">[{r["demand_ci95"]}]</span></span>{pill(r["decision_8o"])}</li>'
                      for r in items)
        out.append(f'<div><h3>{pr} have <span class="mute">· demand index {rng}</span></h3><ol class="ml">{lis}</ol></div>')
    return "".join(out)


BLOCK_TXT = {
    "Brain": ("Know the ICP, personas, positioning, product and competitors", "Fact store with source + date; founder corrections stick; messaging per persona"),
    "Lab": ("Own the strategy, build the playbook, run experiments, report what works", "Bet ledger (audience × trigger × angle), kill/scale rules, weekly Friday Memo with funnel per bet"),
    "Radar": ("Build lists, research accounts, find signals, generate pipeline", "Rented data + enrichment, why-now signal watchers, one-line reason per lead, verification"),
    "Voice": ("Run outbound sequences, write copy, nurture, protect deliverability", "Drafts graded against a scorecard, founder approves every send, separate domains and volume caps"),
    "Reply": ("Follow up, qualify, handle objections, book, hand off", "Inbox triage, qualification against ICP, calendar booking, pre-call brief to the founder"),
    "Memory": ("Keep the CRM clean and true", "Every touch logged automatically; HubSpot sync first"),
    "Connections": ("Automate workflows, integrate and own the stack", "Rented integrations; 8-o is the automation the posting asks a human to build"),
    "Playbooks": ("Document processes", "Editable playbooks + Manual Log; enablement is out (no team to train)"),
}


def block_table():
    rows = []
    for blk in BLOCK_ORDER:
        ds = [r for r in scope.values() if r["block"] == blk]
        if not ds:
            continue
        ids = {r["duty_id"] for r in ds}
        top = sorted(ds, key=lambda r: -float(r["demand_index"]))[:3]
        ev = f'{bucket_pct(ids):.1f}% of core postings ask for ≥ 1 of its {len(ds)} duties. Top: ' + \
             "; ".join(f'{r["duty"].split(",")[0].lower()} {r["pct_core"]}%' for r in top)
        job, do = BLOCK_TXT[blk]
        rows.append(f'<tr><td class="h">{blk}</td><td>{e(job)}</td><td>{e(do)}</td><td>{e(ev)}</td></tr>')
    return "".join(rows)


def gap_chart():
    w = np.array([float(r["demand_index"]) for r in scope.values()])
    items = []
    for st, nm in ((1, "Stage 1 · First demo"), (2, "Stage 2 · Pay & stay"), (3, "Stage 3 · Investors")):
        lv = np.array([int(r["target_gap_level"]) if r["build_stage"] and int(float(r["build_stage"])) <= st else 0
                       for r in scope.values()])
        v = 100 * (lv * w).sum() / (3 * w.sum())
        items.append((nm, v, f"{nm}: {v:.1f}% of the posted job, demand-weighted", "m-own"))
    return hbars(items, 100, lambda v: f"{v:.0f}%", title="Gap Meter target", half=True)


def stages_cards():
    defs = {1: ("First demo", "Done when 8-o books a qualified demo for 8-o."),
            2: ("Clients pay and stay", "Done when clients renew and refer."),
            3: ("Investors lean in", "Done when the pitch numbers are real and improving.")}
    out = []
    for st in (1, 2, 3):
        ds = sorted((r for r in scope.values() if r["build_stage"] and int(float(r["build_stage"])) == st),
                    key=lambda r: -float(r["demand_index"]))
        lis = "".join(f'<li>{e(r["duty"])} {pill(r["decision_8o"])} <span class="mute">L{r["target_gap_level"]}</span></li>' for r in ds)
        out.append(f'<div class="card"><h3><span class="pill own">STAGE {st}</span>{defs[st][0]}</h3>'
                   f'<p class="mute" style="font-size:13px">{defs[st][1]} {len(ds)} duties, ordered by founder demand.</p><ul>{lis}</ul></div>')
    return "".join(out)


def leave_rows():
    ds = sorted((r for r in scope.values() if r["decision_8o"] in ("OUT", "ASSIST")), key=lambda r: -float(r["demand_index"]))
    return "".join(f'<tr><td>{e(r["duty"])} {pill(r["decision_8o"])}</td><td class="num">{float(r["demand_index"]):.0f}</td>'
                   f'<td>{e(r["rationale"])}</td></tr>' for r in ds)


def conf_rows():
    p = sm["population"]
    c = sm["classifier"]
    d = sm["discovery"]
    aucs = [float(r["auc_vs_regex"]) for r in agree.values() if r.get("auc_vs_regex")]
    rob_small = sum(r["small_team_robust"] == "True" for r in scope.values())
    rob_f = sum(r["founding_robust"] == "True" for r in scope.values())
    must = [r for r in scope.values() if r["priority"] == "Must"]
    stable = sum(float(r["p_must"]) >= 0.95 for r in must)
    rows = [
        ("Population", f'All {p["icp_postings"]:,} ICP GTM postings ({p["icp_companies"]:,} companies with ≥ 1 usable posting) and all '
                       f'{p["market_postings"]:,} other GTM postings from {p["market_boards"]:,} job boards. No sampling.'),
        ("Lines read", f'{p["lines"]:,} lines, {p["unique_lines"]:,} unique, every one embedded and labelled.'),
        ("Two labelling instruments", f'Keyword taxonomy (48 duties) and an embedding classifier trained on it, scored out-of-fold with '
                                      f'5-fold cross-validation grouped by company. Median AUC vs keywords {np.median(aucs):.3f} '
                                      f'(min {min(aucs):.3f}). Duty rankings agree: Spearman ρ {c["spearman_core_prevalence_ml_vs_regex"]}.'),
        ("Coverage", f'{d["pct_scope_lines_with_ml_duty"]}% of ICP responsibility lines carry at least one duty (keywords alone: '
                     f'{d["pct_scope_lines_with_regex_duty"]}%). The rest is mostly location, culture and language text (see themes below).'),
        ("Ranking stability", f'1,000 company-level bootstrap resamples. {stable} of {len(must)} Must-have duties stay Must-have in '
                              f'≥ 95% of resamples. Each duty\'s 95% interval is shown in the scope list.'),
        ("Robust differences", f'A segment difference is called robust only if both instruments agree in direction with |z| ≥ 3. '
                               f'First hire vs later: {rob_f} duties. Team ≤ 35 vs rest: {rob_small} duties.'),
        ("Bottom-up check", f'K-means on all {p["icp_scope_unique_lines"]:,} unique ICP responsibility lines, k = {d["k_selected"]} '
                            f'(best Davies–Bouldin of k ∈ 20–80). Themes with no matching duty are listed below for review.'),
        ("Limits", "Postings state what founders say they want, not what the hire ends up doing. Headcount is known for a minority of "
                   "companies. The classifier learns from keyword seeds, so a duty the taxonomy never names can only show up in the "
                   "bottom-up themes."),
    ]
    return "".join(f'<tr><td class="h">{e(a)}</td><td>{e(b)}</td></tr>' for a, b in rows)


def disc_rows():
    out = []
    for r in disc[:18]:
        out.append(f'<tr><td><b>{e(r["terms"][:70])}</b><br><span class="mute" style="font-size:12px">'
                   f'“{e(r["nearest_lines"].split(" || ")[0][:140])}”</span></td><td class="num">{r["pct_icp_postings"]}%</td>'
                   f'<td style="font-size:12.5px">{e(", ".join(LAB.get(x.split("(")[0], x) for x in r["top_duties"].split(", ") if x)[:120])}</td>'
                   f'<td class="num">{r["pct_lines_no_duty"]}%</td></tr>')
    return "".join(out)


def build():
    dated = C[C.age.notna()]
    comp = C[C.comp_usd_mid.notna()]
    by_c = C.groupby("company")
    pain = {"companies": C.company.nunique(), "companies_2": int((by_c.size() >= 2).sum()),
            "open60": round(100 * (dated.age > 60).mean(), 1), "open90": round(100 * (dated.age > 90).mean(), 1),
            "med_age": float(dated.age.median()), "n_dated": len(dated),
            "budget": round(float(comp.comp_usd_mid.median()) * len(C) / 1e6, 1), "med_comp": float(comp.comp_usd_mid.median()),
            "found_comp": float(C[C.founding_any == True].comp_usd_mid.median())}  # noqa: E712
    nm = dict(ARCH)
    ages = sorted(((nm[a], float(g.age.median()), len(g)) for a, g in dated.groupby("archetype") if a in nm), key=lambda x: -x[1])
    age_svg = hbars([(f"{n} · n {k:,}", v, f"{n}: median {v:.0f} days open (n {k:,})", "m-own") for n, v, k in ages],
                    max(130, max(a[1] for a in ages) + 10), lambda v: f"{v:.0f} d", title="Median days open", half=True)
    cs = sorted(((nm[a], float(g.comp_usd_mid.median()), len(g)) for a, g in comp.groupby("archetype") if a in nm), key=lambda x: -x[1])
    comp_svg = hbars([(f"{n} · n {k:,}", v / 1000, f"{n}: median base ${v:,.0f} (n {k:,})", "m-own") for n, v, k in cs], 200,
                     lambda v: f"${v:.0f}K", title="Median base salary", ref=(36, 60, "8-o at $3–5K / month"), half=True)
    prem = []
    for i in LAB:
        m = comp.dset.apply(lambda s: i in s)
        if m.sum() >= 30 and (~m).sum() >= 30:
            prem.append((i, float(comp[m].comp_usd_mid.median()), float(comp[~m].comp_usd_mid.median()), int(m.sum())))
    prem.sort(key=lambda r: r[1] - r[2])
    sel = prem[:4] + prem[-4:]
    prem_svg = hbars([(LAB[i][:36], (w - wo) / 1000 + 60, f"{LAB[i]}: ${w:,.0f} with vs ${wo:,.0f} without (n {n:,})",
                       "m-own" if w > wo else "m-assist") for i, w, wo, n in sel], 120, lambda v: f"{v-60:+.0f}K",
                     title="Salary premium by duty")
    kp = [r for r in ikt if r["field"] == "kpis"]
    it = [r for r in ikt if r["field"] == "intent"]
    tl = sorted((r for r in ikt if r["field"] == "tools"), key=lambda r: -float(r["pct_icp_core"]))[:10]
    mk = lambda rows, xm: hbars([(r["item"][:38], float(r["pct_icp_core"]),  # noqa: E731
                                  f'{r["item"]}: {r["pct_icp_core"]}% of ICP core (95% CI {r["ci95"]}); rest of market {r["pct_market_core"]}% (z {r["z"]})',
                                  "m-own") for r in sorted(rows, key=lambda r: -float(r["pct_icp_core"]))], xm,
                                lambda v: f"{v:.1f}%", half=True)
    tiers = C.groupby("company").agg(t=("icp_tier", "first"), f=("founding_any", "max"))
    ab = tiers.t.fillna("").str[:1].isin(["A", "B"])
    p = sm["population"]
    rep = {
        "DATE": "2026-11-08", "N_POST": f'{p["icp_postings"]:,}', "N_COMP": f'{p["icp_companies"]:,}', "N_MKT": f'{p["market_postings"]:,}',
        "N_LINES": f'{p["lines"]:,}', "N_CORE": f"{len(C):,}", "N_CO_CORE": f'{pain["companies"]:,}', "BUDGET": pain["budget"],
        "OPEN60": pain["open60"], "MED_AGE": f'{pain["med_age"]:.0f}',
        "TAKEOVER": f'{float(ikt_row("intent", "Take GTM over from the founders")["pct_icp_core"]) / max(0.1, float(ikt_row("intent", "Take GTM over from the founders")["pct_market_core"])):.1f}',
        "MED_COMP": money(pain["med_comp"]), "FOUND_COMP": money(pain["found_comp"]),
        "AB": f"{int(ab.sum()):,}", "ABF": f"{int((ab & (tiers.f == True)).sum()):,}",  # noqa: E712
        "SCORECARD": scorecard(pain), "TREE": tree(), "QUAD": quadrant(), "SMALL": dumbbell(), "HEAT": heatmap(),
        "N_SMALL": f'{(C.small_team == True).sum():,}', "N_REST": f'{(C.small_team != True).sum():,}',  # noqa: E712
        "AGE": age_svg, "COMP": comp_svg, "PREM": prem_svg, "KP": mk(kp, 50), "INTENT": mk(it, 50), "TOOLS": mk(tl, 30),
        "MUST": must_list(), "BLOCKS": stacked_blocks(), "BLOCKTABLE": block_table(), "GAP": gap_chart(),
        "STAGES": stages_cards(), "LEAVE": leave_rows(), "CONF": conf_rows(), "DISC": disc_rows(),
        "N_SCOPE_U": f'{p["icp_scope_unique_lines"]:,}', "K": sm["discovery"]["k_selected"],
    }
    tpl = (Path(__file__).resolve().parent / "role_scope_report.tpl.html").read_text()
    for k, v in rep.items():
        tpl = tpl.replace("{{" + k + "}}", str(v))
    left = re.findall(r"\{\{[A-Z_0-9]+\}\}", tpl)
    assert not left, left
    (B / "report.html").write_text(tpl)
    print("written", B / "report.html", f"{len(tpl):,} bytes")


if __name__ == "__main__":
    build()
