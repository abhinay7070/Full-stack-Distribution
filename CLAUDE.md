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

## State at hand-off (2026-11-15, v5)
14,762 companies in the DB: 13,651 in SAM (A 2,117 · B 5,440 · C 5,561 · D 533) + 1,110 Outside_SAM + 1 disqualified.
Unresolved_TAM 12,535 rows (11,880 unique): 8,205 have a verified domain but no Seed/A proof (ATS 6,804 · HN 1,132 ·
accelerators 269) + names without a domain (news 1,699 · Form D 1,632 · HN 999).
Rings: R1 906 · R2 1,522 · R3 5,129 · Enrich 5,561 · Review 533. Outreach targets (R1+R2) 2,428, reachable now 1,366.
Tags: H1 2,083 · H2 6,209 · H3 2,187 · H4 528 · H5 2,168 · H6 1,694 · H7 194 · H8 18 · H10 1,265.
Gate: PASS (idempotent tags). Workbook recalculated: 56,711 formulas, 0 errors.
v5 added: Gem, Rippling, Pinpoint, Dover providers (Dover's careers API returns the company's own domain), Common
Crawl over 4 crawls (CC-MAIN-2026-43/39/34/30), 107,830 boards probed / 26,285 with jobs / 11,818 mapped to domains,
accelerator portfolios (`accel_extra.py`: Seedcamp, AI Grant, Betaworks; 542 domains), parallel homepage prefetch for
the stage gate, pool/rejected CSVs now replace a source's own rows on re-run (no duplicates).
v4: Common Crawl discovery, SmartRecruiters + Personio, small boards resolved. v3: resolver directory + Form D evidence.

## Backlog (in priority order)
1. **Workable** (8,425 unique boards: open-ats-feed + Common Crawl): apply.workable.com answers 429 to this cloud
   environment every time it is checked (2026-11-08 Retry-After 70069 s; 2026-11-09 45111 s; 2026-11-15 65168 s), so it
   looks like a standing block on the egress address, not a window that expires. Run it from another network (a local
   machine) instead. After it lifts: `python3 scripts/ats_sweep.py --providers workable`
   (keep RATE 1.2 s or slower), then `python3 scripts/universe_ingest.py ats`, then the tag/build/gate tail of
   `run_pipeline_v2.sh`. Do not work around the limit.
2. **H8**: GitHub pages and api.github.com are blocked in cloud sessions (scoped to this repo). Run
   `python3 scripts/signals_collect.py h8` from a machine with normal GitHub access (fix the star regex for the current
   org-page markup or use the API with a token), then re-run `tag_signals.py`.
3. **Unresolved_TAM (11,880 unique)**: (a) 8,205 have a verified domain but no Seed/A proof: Clay / Crunchbase stage or
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
   `raw_sources/gate_report.json`. Medium-confidence domain matches (6,053) are tier-capped at C until verified;
   104 live companies carry a Series B+ event (flagged `possible_past_series_a`).
9. **Common Crawl index service**: index.commoncrawl.org resets connections from cloud sessions; `cc_ats_discover.py`
   reads the parquet index on data.commoncrawl.org instead. Add newer crawls as they publish (argv: crawl ids).
10. **DB size**: `db/gtm_pipeline.db` is 83 MB (GitHub hard limit 100 MB; VACUUM does not shrink it). Before the next
   big expansion, either commit a gzip copy (`db/gtm_pipeline.db.gz`, ~25 MB) and gitignore the raw file, or trim
   `jobs.description_snip` / `site_scan` raw columns.
11. **Site-scan hang guard**: `site_scan.py --to-cache` can hang on a pathological page (regex holds the GIL). The
   per-domain runner used in v3 runs one process per domain with `timeout 60`; fold that into `site_scan.py`.

## Future expansion (Master ~40-55% of an estimated 25-35K SAM; free discovery levers are now mostly exhausted)
| # | Lever | Status / expected gain | Needs owner? |
|---|---|---|---|
| E1 | Common Crawl board discovery | DONE (v4-v5, 4 crawls). New crawls add a few % | no |
| E2 | More ATS providers | DONE: SmartRecruiters, Personio, Gem, Rippling, Pinpoint, Dover. Left: BambooHR / JazzHR (SMB-heavy, low ICP share) | no |
| E3 | UK Companies House API: SIC 62/63 incorporations 2021+ with a funding or hiring signal | +500-1,500 | free API key, saved as an environment secret |
| E4/E5 | VC / accelerator portfolio pages | Seedcamp / AI Grant / Betaworks DONE (v5): 542 domains, 12 admitted, 269 pooled: membership is rarely stage proof. More sites = low yield | no |
| E6 | Clay / Crunchbase: stage proof for the 8,205 pooled domains + domains for ~4,300 names + headcount | +2,000-4,500 promoted; C -> A/B | Clay credits / export |
| E7 | Workable (8,425 boards), from a non-blocked network | +300-800 | run locally |
Realistic ceiling: ~14.5-15K in Master with E3/E7; ~17-20K with E6. Past that needs a paid funding database
(Crunchbase / Dealroom). Out of scope by rule: Wellfound, LinkedIn scraping, Consider boards (login / anti-bot walls).
