"""Role-scope research, step 3: turn the measured duty prevalence into the unified 8-o agent scope.

Reads  research/gtm_role_scope/analysis/duty_prevalence.csv, founding_vs_nonfounding.csv, lift_vs_market.csv,
       duty_cooccurrence_core.csv, extracted/postings_tagged.csv
Writes research/gtm_role_scope/analysis/unified_scope.csv   (one row per duty: demand index, priority, 8-o decision,
                                                            build stage, target Gap Meter level)
       research/gtm_role_scope/analysis/duty_clusters.csv   (data-driven duty bundles: hierarchical clustering on
                                                            co-occurrence in core postings)

Demand index (0-100) = mean of three normalised shares (each divided by its column max):
  pct_core (share of core growth-hire postings), pct_companies (share of ICP companies asking for it in any GTM
  posting), pct_founding (share of first-hire / founding postings).
Priority: Must >= 55, Should 30-55, Could < 30.
Decisions (DECISION below) are judgment calls that apply blueprint s.9 / s.13; they are data in this file so they
can be argued with and changed. Everything else is computed.
"""
from __future__ import annotations

import csv
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
ANA = ROOT / "research" / "gtm_role_scope" / "analysis"
EXT = ROOT / "research" / "gtm_role_scope" / "extracted"

# duty -> (8-o decision, blueprint build stage, target Gap Meter level at that stage, why)
# decision: OWN = 8-o does it · ASSIST = 8-o prepares, founder/human does it · OUT = not in scope (blueprint s.13 / rule)
DECISION = {
    "L1": ("OWN", 1, 2, "Brain: ICP + personas are the input to every bet"),
    "L2": ("ASSIST", 2, 1, "Brain ingests call notes / replies; founder runs the conversations"),
    "L3": ("OWN", 1, 2, "Brain: messaging per persona; founder approves"),
    "L4": ("OWN", 2, 2, "Brain: competitor facts with source + date"),
    "L5": ("OWN", 1, 2, "Brain: product facts from site/docs; Reply sends objections back"),
    "D1": ("OWN", 2, 2, "Lab: the GTM plan is the bet ledger"),
    "D2": ("OWN", 1, 2, "Lab + Playbooks: the repeatable motion is the product"),
    "D3": ("OWN", 1, 2, "Lab: audience x trigger x angle bets with kill/scale rules"),
    "D4": ("ASSIST", 3, 1, "Lab proposes channels; email first, others are human"),
    "D5": ("ASSIST", 3, 1, "Lab reports pipeline vs target; budget stays with founder"),
    "F1": ("OWN", 1, 3, "Radar: lists from rented data"),
    "F2": ("OWN", 1, 3, "Radar: rented enrichment + verification"),
    "F3": ("OWN", 1, 3, "Radar: why-now signals (the moat input)"),
    "F4": ("OWN", 1, 3, "Radar: account research -> one-line why now"),
    "F5": ("OWN", 2, 2, "Reply/Radar: score and route inbound + audit leads"),
    "F6": ("OWN", 1, 2, "Outcome metric of Radar + Voice: qualified pipeline"),
    "R1": ("OWN", 1, 2, "Voice: sequences; human approves every send (rule 6)"),
    "R2": ("OWN", 1, 2, "Voice: drafts graded against scorecard, founder taste learned"),
    "R3": ("ASSIST", 2, 1, "LinkedIn automation is Not Doing; Voice drafts call scripts / DMs for the human"),
    "R4": ("OWN", 1, 3, "Rented sending infra, separate domains, volume caps (guardrail)"),
    "R5": ("OUT", None, 0, "Content generation is Not Doing (s.13); public proof later"),
    "R6": ("OUT", None, 0, "Ads are Not Doing (s.13)"),
    "R7": ("ASSIST", 3, 1, "Radar can list event attendees as a signal; attending is human"),
    "R8": ("OWN", 2, 2, "Voice: nurture sequences for 'not now' replies"),
    "R9": ("OUT", None, 0, "Partner programs: not a growth-hire core duty for this ICP yet"),
    "R10": ("ASSIST", 2, 1, "Outreach goes out in the founder's voice; no content product"),
    "B1": ("OWN", 1, 2, "Reply: sort + draft every reply"),
    "B2": ("OWN", 1, 2, "Reply: qualify against ICP + bet before booking"),
    "B3": ("OWN", 1, 2, "Reply: book on the founder's calendar (the Stage 1 finish line)"),
    "B4": ("OWN", 2, 2, "Reply: objection playbook, learned from edits"),
    "B5": ("OWN", 1, 3, "Reply: meeting brief to the founder before every demo"),
    "C1": ("ASSIST", 2, 1, "Founder runs demos; 8-o writes the pre-call brief"),
    "C2": ("OUT", None, 0, "Closing stays with the founder (the job is booking demos)"),
    "C3": ("ASSIST", 2, 1, "Memory tracks stage + next step; founder owns the deal"),
    "C4": ("OUT", None, 0, "Pricing / RFPs: founder"),
    "P1": ("OUT", None, 0, "Post-sale: not the growth hire's job at seed"),
    "P2": ("OUT", None, 0, "Post-sale: not the growth hire's job at seed"),
    "M1": ("OWN", 2, 3, "Friday Memo is the dashboard"),
    "M2": ("OWN", 2, 2, "Lab: funnel rates per bet (sent -> reply -> meeting -> qualified)"),
    "M3": ("OWN", 2, 3, "Friday Memo: learnings to founder"),
    "S1": ("OWN", 1, 3, "Memory + CRM sync: every touch logged automatically"),
    "S2": ("OWN", 1, 3, "Connections: rented integrations"),
    "S3": ("OWN", 1, 3, "8-o is the internal tool / agent the posting asks a human to build"),
    "S4": ("OWN", 2, 2, "Connections: 8-o picks and wires the rented stack"),
    "S5": ("OWN", 2, 3, "Playbooks + Manual Log are the documentation"),
    "S6": ("OUT", None, 0, "No team to enable at 3-35 people with no GTM hire"),
    "O1": ("OUT", None, 0, "People management: human"),
    "O2": ("OUT", None, 0, "Exec presence: founder"),
}


def read(name):
    with open(ANA / name, newline="") as fh:
        return list(csv.DictReader(fh))


def main() -> None:
    prev = {r["duty_id"]: r for r in read("duty_prevalence.csv")}
    fvn = {r["duty_id"]: r for r in read("founding_vs_nonfounding.csv")}
    lift = {r["duty_id"]: r for r in read("lift_vs_market.csv")}
    mx = {k: max(float(r[k]) for r in prev.values()) for k in ("pct_core", "pct_companies", "pct_founding")}
    rows = []
    for i, r in prev.items():
        di = 100 * np.mean([float(r[k]) / mx[k] for k in mx])
        dec, stage, gap, why = DECISION[i]
        rows.append({"duty_id": i, "stage": r["stage"], "block": r["block"], "duty": r["duty"],
                     "pct_core": r["pct_core"], "ci95_core": r["ci95_core"], "pct_companies": r["pct_companies"],
                     "pct_founding": r["pct_founding"], "founding_lift": fvn[i]["lift"], "founding_z": fvn[i]["z"],
                     "icp_vs_market_lift": lift[i]["lift"], "icp_vs_market_z": lift[i]["z"],
                     "demand_index": round(di, 1),
                     "priority": "Must" if di >= 55 else ("Should" if di >= 30 else "Could"),
                     "decision_8o": dec, "build_stage": stage or "", "target_gap_level": gap, "rationale": why})
    rows.sort(key=lambda x: -x["demand_index"])
    with open(ANA / "unified_scope.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)

    # data-driven bundles: average-linkage clustering on (1 - phi) between duty vectors in core postings
    from scipy.cluster.hierarchy import fcluster, linkage
    from scipy.spatial.distance import squareform
    with open(EXT / "postings_tagged.csv", newline="") as fh:
        core = [r for r in csv.DictReader(fh) if r["core"] == "True" and int(r["n_resp_lines"]) >= 3]
    ids = list(prev)
    X = np.array([[i in r["duties"].split("|") for i in ids] for r in core], dtype=float)
    keep = X.std(0) > 0
    ids = [i for i, k in zip(ids, keep) if k]
    X = X[:, keep]
    C = np.corrcoef(X.T)
    D = np.clip(1 - C, 0, 2)
    np.fill_diagonal(D, 0)
    Z = linkage(squareform(D, checks=False), "average")
    lab = fcluster(Z, t=0.86, criterion="distance")
    out = []
    for c in sorted(set(lab)):
        mem = [ids[j] for j in range(len(ids)) if lab[j] == c]
        idx = [ids.index(m) for m in mem]
        sub = C[np.ix_(idx, idx)]
        cohesion = float(sub[np.triu_indices(len(idx), 1)].mean()) if len(idx) > 1 else 1.0
        share = float((X[:, idx].sum(1) > 0).mean())
        out.append({"cluster": int(c), "size": len(mem), "duties": " ".join(mem),
                    "labels": " | ".join(prev[m]["duty"] for m in mem),
                    "mean_phi": round(cohesion, 3), "pct_core_postings_any": round(100 * share, 1)})
    out.sort(key=lambda r: -r["pct_core_postings_any"])
    with open(ANA / "duty_clusters.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(out[0]))
        w.writeheader()
        w.writerows(out)
    for r in rows:
        print(f"{r['duty_id']:4} {r['demand_index']:5} {r['priority']:6} {r['decision_8o']:6} S{r['build_stage'] or '-'} "
              f"L{r['target_gap_level']} {r['duty']}")
    for r in out:
        print(r["cluster"], r["size"], r["pct_core_postings_any"], r["mean_phi"], r["labels"])


if __name__ == "__main__":
    main()
