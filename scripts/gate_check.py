"""Checker gate: hard invariants + soft warnings + audit samples. Run after every maker step.

Usage: python3 scripts/gate_check.py [--fingerprint out.json] [--compare a.json b.json] [--sample N]
Hard failures (exit 1): invalid tag arrays, unknown hypotheses, priority_score != sum of tag weights, a tag with no
proof for that hypothesis, non-normalized or shared-host domains, alias domains present, orphan founders / jobs /
funding_events / site_scan rows, non-ICP country, run with records_icp_pass > records_raw.
Warnings: reliable Series B+ event on a live company, parked or dead homepage, medium-confidence domain resolutions.
Fingerprint = sha1 over (domain, tags, priority_score, #proofs) for idempotency checks (compare two runs).
Report: raw_sources/gate_report.json
"""
from __future__ import annotations

import hashlib
import json
import random
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import h3_ingest as h3  # noqa: E402
import pipeline_db as pdb  # noqa: E402

REPORT = pdb.RAW_DIR / "gate_report.json"


def fingerprint(conn) -> dict:
    rows = conn.execute("SELECT company_domain, matched_hypotheses, priority_score, live_signal_proof, status "
                        "FROM companies ORDER BY company_domain").fetchall()
    h = hashlib.sha1()
    for r in rows:
        h.update(f"{r[0]}|{r[1]}|{r[2]}|{len(json.loads(r[3]))}|{r[4]}\n".encode())
    tags = Counter(t for r in rows for t in json.loads(r[1]))
    return {"companies": len(rows), "sha1": h.hexdigest(), "tags": dict(sorted(tags.items(), key=lambda x: int(x[0][1:])))}


def main() -> None:
    a = sys.argv[1:]
    conn = pdb.connect()
    if "--compare" in a:
        i = a.index("--compare")
        x, y = json.loads(Path(a[i + 1]).read_text()), json.loads(Path(a[i + 2]).read_text())
        same = x["sha1"] == y["sha1"]
        print(json.dumps({"idempotent": same, "before": x, "after": y}, indent=1))
        sys.exit(0 if same else 1)
    if "--fingerprint" in a:
        out = Path(a[a.index("--fingerprint") + 1])
        out.write_text(json.dumps(fingerprint(conn)))
        print(out.read_text())
        return
    n_sample = int(a[a.index("--sample") + 1]) if "--sample" in a else 15
    hard, warn = Counter(), Counter()
    examples = {}

    def fail(k, ex):
        hard[k] += 1
        examples.setdefault(k, ex)

    live = set()
    for r in conn.execute("SELECT * FROM companies"):
        d = r["company_domain"]
        if r["status"] != "disqualified":
            live.add(d)
        try:
            tags = json.loads(r["matched_hypotheses"])
            proofs = json.loads(r["live_signal_proof"])
        except ValueError:
            fail("invalid_json", d)
            continue
        if any(t not in pdb.HYPOTHESES for t in tags):
            fail("unknown_hypothesis", d)
        if len(tags) != len(set(tags)):
            fail("duplicate_tag", d)
        if r["priority_score"] != sum(pdb.HYPOTHESIS_WEIGHTS.get(t, 0) for t in tags):
            fail("priority_score_mismatch", f"{d} {tags} {r['priority_score']}")
        ph = {p.get("hypothesis") for p in proofs}
        for t in tags:
            if t not in ph:
                fail(f"tag_without_proof:{t}", d)
        if pdb.normalize_domain(d) != d:
            fail("domain_not_normalized", d)
        if d in pdb.DOMAIN_ALIASES:
            fail("alias_domain_present", d)
        if r["hq_country"] and r["hq_country"] not in h3.ALLOWED and r["status"] != "disqualified":
            fail("non_icp_country", f"{d} {r['hq_country']}")
    for tbl in ("founders", "jobs", "funding_events", "site_scan", "company_emails", "tracking", "scores"):
        try:
            n = conn.execute(f"SELECT COUNT(*) FROM {tbl} t LEFT JOIN companies c USING (company_domain) "
                             f"WHERE c.company_domain IS NULL").fetchone()[0]
        except Exception:  # noqa: BLE001
            continue
        if n:
            fail(f"orphans:{tbl}", n)
    for r in conn.execute("SELECT * FROM ingestion_runs"):
        if (r["records_icp_pass"] or 0) > (r["records_raw"] or 0) and r["records_raw"]:
            fail("run_reconciliation", r["run_id"])
    # warnings
    for r in conn.execute("SELECT f.company_domain, f.detail FROM funding_events f JOIN companies c USING(company_domain) "
                          "WHERE f.stage='series_b_plus' AND c.status!='disqualified' AND f.confidence IN "
                          "('form_d','news_linked','domain_keyed')"):
        warn["series_b_plus_event_on_live_company"] += 1
        examples.setdefault("series_b_plus_event_on_live_company", f"{r[0]}: {r[1][:120]}")
    for r in conn.execute("SELECT company_domain, http_status, social FROM site_scan"):
        try:
            parked = json.loads(r["social"] or "{}").get("parked")
        except ValueError:
            parked = False
        if parked:
            warn["parked_homepage"] += 1
        if not r["http_status"] or r["http_status"] < 0 or r["http_status"] >= 400:
            warn["homepage_unreachable"] += 1
    warn["medium_confidence_resolutions"] = conn.execute(
        "SELECT COUNT(*) FROM resolutions WHERE status='resolved' AND confidence='medium'").fetchone()[0]
    # audit sample: newly resolved companies (maker output a human should eyeball)
    random.seed(len(live))
    res = conn.execute("SELECT entity_key, name, domain, score, confidence, evidence FROM resolutions "
                       "WHERE status='resolved'").fetchall()
    sample = [dict(r) for r in random.sample(res, min(n_sample, len(res)))]
    rep = {"run_at": pdb.utcnow(), "fingerprint": fingerprint(conn), "hard_failures": dict(hard),
           "warnings": dict(warn), "examples": examples, "resolution_audit_sample": sample,
           "resolution_status": dict(conn.execute("SELECT status, COUNT(*) FROM resolutions GROUP BY 1").fetchall())}
    REPORT.write_text(json.dumps(rep, indent=1, default=str) + "\n")
    print(json.dumps({k: rep[k] for k in ("fingerprint", "hard_failures", "warnings", "resolution_status")}, indent=1))
    if hard:
        print("GATE: FAIL", file=sys.stderr)
        sys.exit(1)
    print("GATE: PASS", file=sys.stderr)


if __name__ == "__main__":
    main()
