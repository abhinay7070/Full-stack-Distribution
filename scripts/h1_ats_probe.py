"""H1 live ATS sweep: find each known company's public job board and save every posting.

Usage: python3 scripts/h1_ats_probe.py [--workers 8] [--limit N]

For every company_domain in db/gtm_pipeline.db, slug candidates are inferred from the
domain and company name ("acme-labs.ai", "Acme Labs" -> acme-labs, acmelabs, acme, acmeai,
acmehq, ...). Slugs already known to exist on a vendor (open-ats-feed token index, snapshotted
under raw_sources/H1_job_arbitrage/open_ats_feed/) are tried first. Each candidate is probed on:

  Ashby       GET https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true
  Greenhouse  GET https://boards-api.greenhouse.io/v1/boards/{slug}/jobs   (+ /v1/boards/{slug} for name)
  Lever       GET https://api.lever.co/v0/postings/{slug}?mode=json&skip=N&limit=100  (paginated)
  Workable    GET https://apply.workable.com/api/v1/widget/accounts/{slug}

Each vendor returns the full board in one response (Lever is paginated to exhaustion), so a
hit is complete by construction. A hit is accepted only if the board can be tied to the
company (board name ~ company name, or the company domain appears in the payload); weaker
hits are saved with confidence="slug_only" and are NOT used as H1 evidence.

Output (read by h1_ingest.py):
  raw_sources/H1_job_arbitrage/ats_live/{provider}/{slug}.json.gz   raw payloads
  raw_sources/H1_job_arbitrage/ats_live/index.json                  domain -> boards

If the environment's network policy blocks a vendor host, the run is recorded in
ingestion_runs with stop_reason="network_policy_denied" and nothing is fabricated.
"""
from __future__ import annotations

import argparse
import gzip
import json
import re
import ssl
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pipeline_db as pdb  # noqa: E402

RAW = pdb.RAW_DIR / "H1_job_arbitrage"
LIVE = RAW / "ats_live"
UA = "gtm-pipeline-ats-probe/1.0"

PROVIDERS = {
    "ashby": "https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true",
    "greenhouse": "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs",
    "lever": "https://api.lever.co/v0/postings/{slug}?mode=json&skip={skip}&limit=100",
    "workable": "https://apply.workable.com/api/v1/widget/accounts/{slug}",
}
PREFLIGHT = {
    "ashby": "https://api.ashbyhq.com/posting-api/job-board/ashby",
    "greenhouse": "https://boards-api.greenhouse.io/v1/boards/greenhouse/jobs",
    "lever": "https://api.lever.co/v0/postings/lever?mode=json&limit=1",
    "workable": "https://apply.workable.com/api/v1/widget/accounts/workable",
}
GENERIC_SUFFIX = ("labs", "hq", "ai", "app", "inc", "io", "tech", "co", "dev", "systems", "technologies")


def http_json(url: str, timeout: int = 20):
    """Return (status, parsed_json_or_None). status 0 = transport/policy failure."""
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ssl.create_default_context()) as r:
            return r.status, json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        return e.code, None
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError, json.JSONDecodeError):
        return 0, None


def slug_candidates(domain: str, name: str | None) -> list[str]:
    label = domain.split(".")[0]
    tld = domain.split(".", 1)[1].replace(".", "")
    cands = [label, label.replace("-", ""), f"{label}{tld}", f"{label}-{tld}", f"{label}hq", f"{label}labs",
             f"{label}inc", f"{label}-labs", f"get{label}", f"try{label}", f"{label}app"]
    if name:
        n = re.sub(r"[^a-z0-9 ]", "", name.lower()).strip()
        if n:
            cands += [n.replace(" ", ""), n.replace(" ", "-")]
            base = re.sub(rf"\s+({'|'.join(GENERIC_SUFFIX)})$", "", n)
            cands += [base.replace(" ", ""), base.replace(" ", "-")]
    seen, out = set(), []
    for c in cands:
        if c and len(c) >= 2 and c not in seen:
            seen.add(c)
            out.append(c)
    return out


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def fetch_board(provider: str, slug: str):
    """Return (jobs list, board_name or None, raw payload) or None if no board."""
    if provider == "lever":
        jobs, skip = [], 0
        while True:
            st, data = http_json(PROVIDERS["lever"].format(slug=slug, skip=skip))
            if st != 200 or not isinstance(data, list):
                return None if skip == 0 else (jobs, None, jobs)
            jobs += data
            if len(data) < 100:
                break
            skip += 100
        return (jobs, None, jobs) if jobs else None
    st, data = http_json(PROVIDERS[provider].format(slug=slug))
    if st != 200 or not isinstance(data, dict):
        return None
    jobs = data.get("jobs") or []
    board_name = data.get("name")
    if provider == "greenhouse":
        st2, meta = http_json(f"https://boards-api.greenhouse.io/v1/boards/{slug}")
        if st2 == 200 and isinstance(meta, dict):
            board_name = meta.get("name")
    return jobs, board_name, data


def confidence(domain: str, name: str | None, board_name: str | None, payload) -> str:
    if board_name and name and (norm(board_name) in norm(name) or norm(name) in norm(board_name)):
        return "name_match"
    blob = json.dumps(payload)[:2_000_000].lower()
    if domain in blob:
        return "domain_in_payload"
    return "slug_only"


def probe_company(domain: str, name: str | None, known: dict[str, set], providers: list[str]) -> list[dict]:
    hits = []
    cands = slug_candidates(domain, name)
    for provider in providers:
        ordered = sorted(cands, key=lambda c: c not in known.get(provider, set()))
        for slug in ordered:
            res = fetch_board(provider, slug)
            if res is None:
                continue
            jobs, board_name, payload = res
            conf = confidence(domain, name, board_name, payload)
            path = LIVE / provider / f"{slug}.json.gz"
            path.parent.mkdir(parents=True, exist_ok=True)
            with gzip.open(path, "wt", encoding="utf-8") as f:
                json.dump(payload, f)
            hits.append({"provider": provider, "slug": slug, "board_name": board_name, "jobs": len(jobs),
                         "confidence": conf, "raw_path": str(path.relative_to(pdb.ROOT)),
                         "fetched_at": pdb.utcnow()})
            if conf != "slug_only":
                break
            time.sleep(0.05)
    return hits


def load_known_tokens() -> dict[str, set]:
    p = RAW / "open_ats_feed" / "tokens.json.gz"
    if not p.exists():
        return {}
    with gzip.open(p, "rt") as f:
        return {k: set(v) for k, v in json.load(f).items()}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    conn = pdb.init_db()
    run_id = pdb.start_run(conn, "H1", "ats_live_probe",
                           "slug inference x {ashby,greenhouse,lever,workable} for every companies.company_domain",
                           raw_path=str(LIVE.relative_to(pdb.ROOT)))
    blocked = {}
    for provider, url in PREFLIGHT.items():
        st, _ = http_json(url, timeout=15)
        if st == 0:
            blocked[provider] = url.split("/")[2]
    if len(blocked) == len(PREFLIGHT):
        report = {"status": "network_policy_denied", "checked_at": pdb.utcnow(), "blocked_hosts": blocked,
                  "fix": "Allow these hosts in the environment's Network access settings, then re-run."}
        LIVE.mkdir(parents=True, exist_ok=True)
        (LIVE / "probe_status.json").write_text(json.dumps(report, indent=2) + "\n")
        pdb.finish_run(conn, run_id, stop_reason=f"network_policy_denied:{','.join(sorted(blocked.values()))}")
        print(json.dumps(report, indent=2))
        return

    rows = conn.execute("SELECT company_domain, company_name FROM companies ORDER BY company_domain").fetchall()
    if args.limit:
        rows = rows[: args.limit]
    known = load_known_tokens()
    providers = [p for p in PROVIDERS if p not in blocked]
    index, done = {}, 0
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(probe_company, r["company_domain"], r["company_name"], known, providers):
                r["company_domain"]
                for r in rows}
        for fut in as_completed(futs):
            d = futs[fut]
            index[d] = fut.result()
            done += 1
            if done % 100 == 0:
                print(f"probed {done}/{len(rows)}", flush=True)
    LIVE.mkdir(parents=True, exist_ok=True)
    (LIVE / "index.json").write_text(json.dumps(index, indent=1, sort_keys=True) + "\n")
    (LIVE / "probe_status.json").write_text(json.dumps({"status": "ok" if not blocked else "partial",
                                                        "checked_at": pdb.utcnow(), "providers": providers,
                                                        "blocked_hosts": blocked}) + "\n")
    boards = sum(len(v) for v in index.values())
    pdb.finish_run(conn, run_id, expected_total=len(rows), records_raw=boards,
                   records_unique=sum(1 for v in index.values() if v), pages_fetched=len(rows),
                   stop_reason="exhausted" if not blocked else f"partial:blocked={','.join(sorted(blocked))}")
    print(f"companies probed {len(rows)}, boards found {boards}")


if __name__ == "__main__":
    main()
