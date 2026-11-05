# CLAUDE.md — GTM TAM pipeline (read first)

## Goal
Find every US / UK / Europe startup that fits the owner's ICP, score fit and timing, and hand over one workbook to work
from daily. The owner wants either a Founding GTM / Growth Engineer role or a $3-5K/mo retainer. Maximise TAM coverage
with **no silent omissions**: every record that is dropped is logged with a reason (rejected or pool CSVs).

## ICP = SAM hard filters (owner-defined, do not loosen)
- Geography: US, UK, Europe (EU/EEA/CH), remote-friendly.
- Stage: Seed to Series A (pre-seed kept, scored lower). Series B+ / acquired / public -> out.
- Headcount: 3-35 employees. A known count outside that range -> `Outside_SAM`. Unknown counts are kept but score lower.
- Sector: B2B SaaS, AI Agents, DevTools, Infra, Vertical AI, Hardware/Physical-AI beneficiaries.
  Excluded: biotech, pure hardware, consumer apps, agencies/services/funds, brick & mortar.
- `icp_rules.fit()` -> `icp_fit_score` 0-100 (geo 20 + stage 30 + headcount 25 + sector 25) -> A Core / B Strong /
  C Verify / D Long-shot, or `Outside SAM` with `outside_sam_reason`.

## Layout
| Path | What |
|---|---|
| `db/gtm_pipeline.db` | SQLite source of truth. Key = `company_domain` (normalized root, `pipeline_db.normalize_domain`). Tables: companies, founders, jobs, funding_events, site_scan, resolutions, company_emails, scores, tracking, outreach_log, ingestion_runs |
| `db/schema.sql` | Schema; `pipeline_db.init_db()` migrates older DBs |
| `scripts/` | One script per source/step (see run order). Shared: `pipeline_db`, `http_fetch` (cached, polite), `resolve_domains` (name -> verified domain), `icp_rules`, `gate_check` |
| `raw_sources/` | Unfiltered raw payloads per hypothesis (gzip) + run reports |
| `sublists/` | Per-hypothesis CSVs, rejected / pool / unresolved lists, `master_priority.csv` |
| `master/gtm_master.xlsx` | The workbook (Master, Dashboard, H0-H10 tabs, Contacts, Signals, Open_Roles, Funding_Events, Unresolved_TAM, Outside_SAM, Sources, Outreach_Log, Data_Dictionary) |
| `.cache/` | gitignored: HTTP cache, parsed intermediates. Deleting it only costs re-fetch time |

Hypotheses: H0 universe (weight 0) · H1 hiring GTM · H2 fresh capital · H3 technical founders · H4 launch decay ·
H5 outbound stack · H6 founder-led sales · H7 verified MRR · H8 OSS traction · H9 warm path (needs LinkedIn export) ·
H10 GTM role open 60+ days.

## Run
```bash
pip install -r requirements.txt
./scripts/run_all.sh            # full refresh, all live sources (first run 3-5 h; cached re-runs are much faster)
./scripts/run_pipeline_v2.sh    # finish only: site scan -> ATS 2nd pass -> tags -> emails -> workbook -> gates
python3 scripts/build_master.py # rebuild workbook only (syncs the owner's yellow cells + Outreach_Log into DB first)
python3 scripts/gate_check.py   # checker: must print GATE: PASS
```
Long steps: run in the background and poll the logs in `.cache/*.log`. Domain resolution and site scans are network-bound.
Use the 4-way shards (`--shard=i/4`). The DB uses `timeout=900`, so parallel writers wait for the lock instead of failing.

## Network hosts required
github.com, hn.algolia.com, efts.sec.gov, www.sec.gov, dns.google, api.ashbyhq.com, boards-api.greenhouse.io,
api.lever.co, apply.workable.com, *.recruitee.com, *.breezy.hr, *.teamtailor.com, api.getro.com + the VC board hosts,
trustmrr.com, the 17 news feeds in `news_funding.py`, and every company homepage (site scan / resolver).
SEC asks for a declared User-Agent (set in `http_fetch.SEC_UA`).

## Rules (keep them)
- Maker/checker: after any data step run `gate_check.py`; never commit a FAIL. Tagging must converge: two passes, then a
  third pass gives an identical fingerprint (`gate_check --fingerprint` / `--compare`).
- Idempotent upserts only (`pipeline_db.upsert_company`); never hand-edit tags. Proof for every tag.
- New companies from name-only sources need early-stage evidence (see `universe_ingest.gate`) or go to the pool CSV.
- Every name -> domain decision goes through `resolve_domains` and is stored in `resolutions` with evidence.
- Do not bypass access controls (CSRF / login / paywalls). Consider-hosted VC boards were skipped for that reason.
- Never execute code from cloned mirrors; parse their data files only.
- Workbook: Arial, formulas (no hardcoded results), yellow = owner input. `build_master.py` preserves owner edits.
- Commits: never include model identifiers; push to the working branch only; no PR unless asked.

## State at hand-off (2026-11-05)
12,816 companies in SAM (A 2,127 · B 5,251 · C 4,791 · D 647) + 941 Outside_SAM + 6,249 Unresolved_TAM.
Tags: H1 1,398 · H2 5,911 · H3 2,175 · H4 520 · H5 1,576 · H6 1,661 · H7 194 · H8 18 · H10 790.
Gate: PASS. Workbook recalculated, 0 formula errors.

## Backlog (in priority order)
1. **Rebuild with the two hand-off fixes**: `./scripts/run_pipeline_v2.sh`. One fix flags a reliable Series B+ event
   as `possible_past_series_a`; the other keeps the H3x sublist intact on reruns.
2. **Finish ATS coverage**: the Workable sweep (rate-limited, ~1 req/s) and the second ATS pass were stopped for time.
   Run `python3 scripts/ats_sweep.py --providers workable` (background), then
   `python3 scripts/ats_sweep.py --providers ashby,greenhouse,lever`, then `python3 scripts/universe_ingest.py ats`.
3. **Site scan the remaining ~3,000 domains**: `for i in 0 1 2 3; do python3 scripts/site_scan.py --to-cache --shard=$i/4 & done; wait; python3 scripts/site_scan.py --load`.
4. **H8 fix**: `signals_collect.h8()` star regex no longer matches GitHub org pages (stars found for 1 of 2,125 orgs).
   Parse the current markup or use `api.github.com/orgs/{org}/repos` with a token, then re-run `tag_signals.py`.
5. **Unresolved_TAM (6,249)**: retry `resolve_domains` with extra hints (Form D street/phone, founders' LinkedIn)
   or export the list to Clay ("find company domain").
6. **Headcount enrichment**: about 60% of companies have no exact headcount. A Clay or LinkedIn employee count moves
   many C -> B/A. Import with `clay_bridge.py` (the email flow) or extend it with a headcount column.
7. **H9**: drop the owner's LinkedIn `Connections.csv` at `inputs/linkedin_connections.csv` and re-run `tag_signals.py`.
8. **News coverage gaps**: tech.eu and Silicon Republic feeds ignore paging (latest items only); UKTN stops at 2026-01.
   Sifted / FinSMEs returned 403. Add other sources rather than circumventing them.
9. **Form D refresh**: when SEC publishes 2026Q2/Q3 bulk zips, add them to `raw_sources/H2_fresh_capital/sec_formd_bulk/`.
   `formd_collect.py` then uses EFTS only after the last bulk quarter.
10. **Precision review**: run `gate_check.py --sample 40` and eyeball `resolution_audit_sample` in
    `raw_sources/gate_report.json`. Medium-confidence domain matches (~3,000) are tier-capped at C until verified.
