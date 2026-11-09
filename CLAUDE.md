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

## State at hand-off (2026-11-09, v4)
14,455 companies in the DB: 13,361 in SAM (A 2,120 · B 5,443 · C 5,284 · D 514) + 1,093 Outside_SAM + 1 disqualified.
Unresolved_TAM 9,710 rows (9,095 unique): 5,380 already have a verified domain but no Seed/A proof (ATS 4,241 ·
HN 1,139) + names without a domain (news 1,699 · Form D 1,632 · HN 999).
Rings: R1 881 · R2 1,563 · R3 5,119 · Enrich 5,284 · Review 514. Outreach targets (R1+R2) 2,444, reachable now 1,376.
Tags: H1 1,882 · H2 6,209 · H3 2,181 · H4 526 · H5 2,061 · H6 1,689 · H7 194 · H8 18 · H10 1,146.
Gate: PASS (idempotent tags). Workbook recalculated: 55,551 formulas, 0 errors.
v4 added: Common Crawl board discovery (`cc_ats_discover.py`, CC-MAIN-2026-43 + 2026-39: 16,814 boards, 10,429 new),
SmartRecruiters + Personio providers, 19,883 boards with jobs (8 providers), small boards (<=10 roles) resolved even
without stage text (7,907 boards mapped to domains), raw ATS files split into <45 MB parts.
v3 added: resolver directory candidates + Form D phone/street evidence (+504 matches), Ashby/Greenhouse/Lever second pass.

## Backlog (in priority order)
1. **Workable** (~11,600 boards: 6,897 known + 4,716 found in Common Crawl): apply.workable.com answers 429 to this
   environment (2026-11-08 Retry-After 70069 s; re-checked 2026-11-09 12:52 UTC: Retry-After 45111 s, i.e. ~01:30 UTC
   2026-11-10). Each probe may restart the window, so do not poll it. After it lifts: `python3 scripts/ats_sweep.py --providers workable`
   (keep RATE 1.2 s or slower), then `python3 scripts/universe_ingest.py ats`, then the tag/build/gate tail of
   `run_pipeline_v2.sh`. Do not work around the limit.
2. **H8**: GitHub pages and api.github.com are blocked in cloud sessions (scoped to this repo). Run
   `python3 scripts/signals_collect.py h8` from a machine with normal GitHub access (fix the star regex for the current
   org-page markup or use the API with a token), then re-run `tag_signals.py`.
3. **Unresolved_TAM (9,095 unique)**: (a) 5,380 have a verified domain but no Seed/A proof: Clay / Crunchbase stage or
   funding lookup promotes them to Master; (b) ~4,300 names without a domain: many are non-ICP (institutions, stealth,
   non-tech Form D issuers). Export to Clay ("find company domain") and import, or add founder LinkedIn hints.
4. **Headcount enrichment**: about 78% of companies have no exact headcount band. A Clay or LinkedIn employee count moves
   many C -> B/A. Import with `clay_bridge.py` (the email flow) or extend it with a headcount column.
5. **H9**: drop the owner's LinkedIn `Connections.csv` at `inputs/linkedin_connections.csv` and re-run `tag_signals.py`.
6. **News coverage gaps**: tech.eu and Silicon Republic feeds ignore paging (latest items only); UKTN stops at 2026-01.
   Sifted / FinSMEs returned 403. Add other sources rather than circumventing them.
7. **Form D refresh**: when SEC publishes 2026Q2/Q3 bulk zips, add them to `raw_sources/H2_fresh_capital/sec_formd_bulk/`.
   `formd_collect.py` then uses EFTS only after the last bulk quarter.
8. **Precision review**: run `gate_check.py --sample 40` and eyeball `resolution_audit_sample` in
   `raw_sources/gate_report.json`. Medium-confidence domain matches (5,511) are tier-capped at C until verified;
   104 live companies carry a Series B+ event (flagged `possible_past_series_a`).
9. **Common Crawl index service**: index.commoncrawl.org resets connections from cloud sessions; `cc_ats_discover.py`
   reads the parquet index on data.commoncrawl.org instead. Add newer crawls as they publish (argv: crawl ids).
10. **Site-scan hang guard**: `site_scan.py --to-cache` can hang on a pathological page (regex holds the GIL). The
   per-domain runner used in v3 runs one process per domain with `timeout 60`; fold that into `site_scan.py`.

## Future expansion (coverage now ~45-55% of an estimated 25-35K SAM, plus 5,380 domain-verified but stage-unproven)
| # | Lever | Status / expected gain | Needs owner? |
|---|---|---|---|
| E1 | Common Crawl board discovery | DONE (v4): 16,814 boards, 10,429 new. Add more crawls for a few % more | no |
| E2 | More ATS providers | DONE for SmartRecruiters + Personio (v4). Rippling / BambooHR / JazzHR not yet: +200-600 | no |
| E3 | UK Companies House API: SIC 62/63 incorporations 2021+ with a funding or hiring signal | +500-1,500 | free API key, saved as an environment secret |
| E4 | VC portfolio pages outside Getro (a16z, Sequoia, Accel, Index, Seedcamp, LocalGlobe, Point Nine, ...) | +500-1,500 (bespoke per site) | no |
| E5 | More accelerators (South Park Commons, HF0, Neo, Station F, Seedcamp) | +300-800 | no |
| E6 | Clay: stage/funding for the 5,380 pooled domains + domain for ~4,300 names + headcount | +1,500-3,500 promoted; C -> A/B | Clay credits |
| E7 | Workable sweep after the 429 block lifts (backlog 1) | +300-800 | no |
Realistic ceiling: ~16-19K in Master with E4/E5/E7; ~18-22K adding E3/E6. 100% needs a paid funding database
(Crunchbase / Dealroom). Out of scope by rule: Wellfound, LinkedIn scraping, Consider boards (login / anti-bot walls).
