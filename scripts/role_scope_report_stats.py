"""Role-scope research, step 4: extra cuts for the strategy report (segments, pain, willingness to pay, hard-to-fill).

Reads extracted/postings_tagged.csv + analysis/unified_scope.csv; writes analysis/report_stats.json.
Core usable postings only (>= 3 responsibility lines, core growth-hire set). Two-proportion z for every split.
"""
from __future__ import annotations
import json, math, sys
from pathlib import Path
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import role_taxonomy as T  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
B = ROOT / "research" / "gtm_role_scope"
AS_OF = pd.Timestamp("2026-11-08")


def z2(k1, n1, k2, n2):
    p = (k1 + k2) / (n1 + n2) if n1 + n2 else 0
    se = math.sqrt(p * (1 - p) * (1 / n1 + 1 / n2)) if 0 < p < 1 and n1 and n2 else 0
    return round(((k1 / n1) - (k2 / n2)) / se, 1) if se else 0.0


def main():
    p = pd.read_csv(B / "extracted" / "postings_tagged.csv")
    u = p[p.n_resp_lines >= 3].copy()
    u["age"] = (AS_OF - pd.to_datetime(u.posted_at, errors="coerce")).dt.days
    for col in ("duties", "tools", "intent", "kpis"):
        u[col] = u[col].fillna("")
    c = u[u.core].copy()
    ids = [d["id"] for d in T.DUTIES]
    lab = {d["id"]: d["label"] for d in T.DUTIES}
    has = lambda df, i: df.duties.str.split("|").apply(lambda s: i in s)  # noqa: E731

    def split(a, b, name_a, name_b):
        out = []
        for i in ids:
            ka, kb = int(has(a, i).sum()), int(has(b, i).sum())
            out.append({"id": i, "duty": lab[i], name_a: round(100 * ka / len(a), 1), name_b: round(100 * kb / len(b), 1),
                        "z": z2(ka, len(a), kb, len(b))})
        return {"n": {name_a: len(a), name_b: len(b)}, "rows": out}

    geo = c.hq_country.fillna("")
    stats = {
        "segments": {
            "us_vs_euuk": split(c[geo == "US"], c[(geo != "US") & (geo != "")], "us", "euuk"),
            "small_vs_rest": split(c[c.small_team == True], c[c.small_team != True], "small", "rest"),  # noqa: E712
            "stale_vs_fresh": split(c[c.age > 60], c[c.age <= 60], "stale", "fresh"),
        },
        "pain": {
            "core_with_date": int(c.age.notna().sum()),
            "median_age_days": float(c.age.median()),
            "pct_open_60": round(100 * (c.age > 60).mean(), 1),
            "pct_open_90": round(100 * (c.age > 90).mean(), 1),
            "age_by_archetype": {k: float(v) for k, v in c.groupby("archetype").age.median().dropna().items()},
            "companies_core": int(c.company_domain.nunique()),
            "companies_2plus_core": int((c.groupby("company_domain").size() >= 2).sum()),
            "budget_usd_m": round(float(c.comp_usd_mid.median()) * len(c) / 1e6, 1),
        },
        "comp": {
            "by_archetype": {k: int(v) for k, v in c.groupby("archetype").comp_usd_mid.median().dropna().items()},
            "n_by_archetype": {k: int(v) for k, v in c.groupby("archetype").comp_usd_mid.count().items()},
            "us": float(c[geo == "US"].comp_usd_mid.median()), "euuk": float(c[(geo != "US") & (geo != "")].comp_usd_mid.median()),
            "founding": float(c[c.founding_any].comp_usd_mid.median()),
        },
        "premium": [],
        "stage_mix": c.funding_stage.fillna("unknown").value_counts().to_dict(),
        "seniority": c.seniority.value_counts().to_dict(),
        "archetypes": c.archetype.value_counts().to_dict(),
    }
    cc = c[c.comp_usd_mid.notna()]
    for i in ids:
        m = has(cc, i)
        if m.sum() >= 30 and (~m).sum() >= 30:
            stats["premium"].append({"id": i, "duty": lab[i], "with": int(cc[m].comp_usd_mid.median()),
                                     "without": int(cc[~m].comp_usd_mid.median()), "n_with": int(m.sum())})
    (B / "analysis" / "report_stats.json").write_text(json.dumps(stats, indent=1))
    for k, v in stats["segments"].items():
        top = sorted(v["rows"], key=lambda r: -abs(r["z"]))[:6]
        print(k, v["n"], [(r["id"], r["z"]) for r in top])
    print(json.dumps(stats["pain"], indent=0)[:600])
    print(sorted(stats["premium"], key=lambda r: -(r["with"] - r["without"]))[:6])


if __name__ == "__main__":
    main()
