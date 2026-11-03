# GTM Lead Ingestion Pipeline

Identifies, enriches, and scores US/UK/EU Seed–Series A B2B startups (3–35 employees, $1M–$15M raised May 2025–Nov 2026) for Founding GTM / Growth Engineer outreach.

## Layout

| Path | Purpose |
|---|---|
| `db/schema.sql` | Source of truth for the schema |
| `db/gtm_pipeline.db` | SQLite master DB (keyed by `company_domain`) |
| `scripts/pipeline_db.py` | Shared helpers: domain normalization, idempotent upserts, run audit |
| `scripts/init_db.py` | Creates/validates the DB (safe to re-run) |
| `raw_sources/H*/` | Unfiltered raw payloads per hypothesis, written before any filtering |
| `sublists/` | Per-hypothesis filtered exports (`sublist_H*_*.csv`) |

## Hypotheses

| ID | Sub-list | Signal |
|---|---|---|
| H1 | `sublist_H1_job_arbitrage` | Live GTM job posts (Founding AE, SDR, Growth, Demand Gen, GTM Eng) + hidden/paradox roles |
| H2 | `sublist_H2_fresh_capital` | Seed/Series A announced in last 60 days |
| H3 | `sublist_H3_technical_founders` | YC W25–S26, a16z Speedrun, HF0, Sequoia Arc; 100% technical founders, GTM headcount ≤ 1 |
| H4 | `sublist_H4_launch_decay` | Product Hunt Top 10 / Show HN launch 14–45 days ago |
| H5 | `sublist_H5_outbound_stack` | Setting up Clay/Instantly/Apollo outbound |
| H6 | `sublist_H6_founder_led_sales` | CEO running founder-led sales on X/LinkedIn |

## Setup

```bash
pip install -r requirements.txt
python3 scripts/init_db.py
```

## H3 — Technical founders (run 2026-11-03)

```bash
python3 scripts/h3_snapshot.py <dir-with-cloned-sources>   # freeze raw sources (gzip) + manifest.json
python3 scripts/h3_ingest.py                               # filter, classify, upsert DB, write CSVs
```

| Source | Upstream (public GitHub mirror) | Cohorts |
|---|---|---|
| Y Combinator | `devasheeshG/yc-api` (gh-pages), cross-checked against `yc-oss/api` | W25, X25, S25, F25, W26, X26, S26, F26 |
| a16z Speedrun | `JohnMarcBautista/startuppy` + founders from `LdwS123/startup-portfolio-scrapers` | SR004–SR007 |
| Entrepreneur First | `yigitmeteozcan/startups` + domains from `LdwS123/startup-portfolio-scrapers` | 2025–2026 |
| Techstars | `yigitmeteozcan/startups` | 2025–2026 programs |

Commit SHAs are in `raw_sources/H3_technical_founders/manifest.json`; run counts in `h3_run_report.json` and the `ingestion_runs` table.

**H3 tiers** (`h3_tier` column)

| Tier | Rule |
|---|---|
| `A_verified` | Every founder's title/bio shows a technical background; ≤1 GTM-function founder |
| `B_likely` | ≥1 technical founder, rest have no background signal; no non-technical founder |
| `C_unverified` | Source publishes no founder background (Techstars, Speedrun SR007, part of EF) |

Records that fail ICP or H3 are written to `sublists/sublist_H3_rejected.csv` with the reason, not to the DB.
`gtm_headcount_founders` counts founders only (lower bound); non-founder staff roles are not published by these sources.

## H1 — Job arbitrage (run 2026-11-03)

```bash
python3 scripts/h1_snapshot.py <dir-with-cloned-sources>   # freeze raw sources (gzip) + manifest.json
python3 scripts/h1_ats_probe.py                            # live Ashby/Greenhouse/Lever/Workable sweep (needs hosts allowed)
python3 scripts/h1_ingest.py                               # classify roles, ICP, upsert DB, write CSVs
```

| Source | Upstream | Use |
|---|---|---|
| YC Work at a Startup | `devasheeshG/yc-api` `companies/hiring.json` (refreshed 2026-11-03) | All 1,505 hiring YC companies, all batches, full `jobs[]` |
| Live ATS APIs | `api.ashbyhq.com`, `boards-api.greenhouse.io`, `api.lever.co`, `apply.workable.com` | `h1_ats_probe.py`; **blocked by network policy on 2026-11-03** (logged in `ingestion_runs`) |
| ATS mirror | `ConorsCode/open-jobs-data` (38k postings, ~380 large companies, daily) | Strict name + domain-slug match to universe |
| ATS board index | `veresk06/open-ats-feed` (18,164 live boards, as of 2026-09-03) | Slug discovery → watchlist (counts only, no titles) |
| Curated jobs | `JohnMarcBautista/startuppy` `jobs.json` | Domain match to universe |
| VC job boards | `LdwS123/startup-portfolio-scrapers` Getro export | Active-job counts → watchlist |

**Role taxonomy** (`family` / `strength`; reads past literal titles)

| Strength | Families |
|---|---|
| S1 | `exact_role` (GTM/Growth Engineer, Growth Hacker), `hidden_gtm` (Founding Business Lead, First Business Hire, Head of Everything, Member of Nontechnical Staff), `gtm_generalist`, `sales`, `marketing`, `growth`, `revops`, `partnerships`, `devrel` at DevTools/Infra |
| S2 | `presales`, `customer`, `bizops_generalist` (Chief of Staff, Founder's Associate), DevRel elsewhere, ATS role field Sales/Marketing with a non-standard title |
| S3 | Paradox rule: any other non-engineering role at a 3–20 person, 100%-technical (H3 `A_verified`), 0-GTM-founder team with no GTM role posted |

Internships are downgraded one level; `[PAUSED]` postings and "General Application" posts are skipped; engineer-noun titles (e.g. "Communications Engineer") are engineering unless the GTM word qualifies the noun ("GTM Engineer").

**ICP for H1**: Active; YC stage not `Growth`; US/UK/EU (unknown kept, flagged); headcount 2–35 (unknown kept, flagged); H3 sector rules; YC batch ≥ 2022 (2022–2024 flagged `funding_window_unverified` until H2 adds funding data).

**Outputs**

| File | Grain |
|---|---|
| `sublists/sublist_H1_job_arbitrage.csv` | One row per matched company: best role, comp, reasoning, GTM-Engineer hook, CEO contact |
| `sublists/sublist_H1_job_postings.csv` | One row per matched posting |
| `sublists/h1_x_h3_priority.csv` | Master ranking: companies tagged H1 **and** H3, sorted by `rank_score` (breakdown column explains every point) |
| `sublists/sublist_H1_rejected.csv` | Every dropped company with `stage` (`1_icp_filter` / `2_role_filter`) and reason |
| `sublists/sublist_H1_ats_watchlist.csv` | Hiring but titles not visible (known ATS board or Getro count) → resolve with `h1_ats_probe.py` |

Companies first seen in H1 that pass the H3 founder rules on evidence (tier A/B) are also tagged H3 (proof source `h1_ingest:h3_rules`). Reconciliation and counts: `raw_sources/H1_job_arbitrage/h1_run_report.json`.

## H2 — Fresh capital (run 2026-11-03)

```bash
python3 scripts/h2_snapshot.py <dir-with-cloned-sources>   # freeze raw sources + manifest.json
python3 scripts/h2_ingest.py                               # funding-event ledger -> H2 tags, funding columns, CSVs
python3 scripts/master_rank.py                             # cross-hypothesis ranking
```

**Scope: every pre-seed / seed / Series A raise, no dollar cap.** Amounts are banded (`<$1M`, `$1-15M`, `>$15M`) instead of filtered. Series B or later on reliable evidence → `disqualification_reason` + `status='disqualified'`. An undisclosed-stage round ≥ $40M is treated as past Series A.

| Source | Upstream | Evidence |
|---|---|---|
| YC directory `news[]` | `devasheeshG/yc-api` `companies/all.json` (6,362 companies) | `company_attached` (headline names the company) / `company_attached_unnamed` |
| YC standard deal | program terms | `program_terms`: $500K pre-seed at batch start (date = quarter start, approximate) |
| Fliar Radar rounds | `Busineessfree/*-startup-funding*` (25% company sample, to 2026-06-12, **CC BY-NC 4.0**) | `domain_keyed` |
| Daily RSS headlines + Form D | `brianboler/startupdashboard` (2026-07-08 → 2026-11-02; top ~60 headlines / ~40 filings per day) | `headline_name_match`, `form_d` |
| SEC Form D, Bay Area | `Puraav/funded-and-sponsoring` | `form_d` |
| Form D CIK → domain | `LBB2005/formd-tracker` | link only; must be corroborated by the issuer's legal name |
| Recent raises list | `landedjobs/recently-funded-ai-startups-hiring` | `headline_name_match` |

**H2 tag**: `fresh_60d` = any capital event in the last 60 days (incl. a YC batch that started within 60 days); `in_window` = an external raise dated 2025-05-01 → run date. Every company gets `funding_window_status` in `sublist_H2_funding_status_all.csv`: `verified_in_window`, `accelerator_check_only`, `external_raise_before_window_only`, `past_series_a`, `no_evidence`.

**New companies** are added only with a domain and an ICP pass (US/UK/EU; B2B/software signal; non-ICP keywords such as medical, consumer, games, real estate rejected; founded ≥ 2016; YC directory ICP verdict overrides other sources). Rounds with an undisclosed stage, unmatched headlines and new Form D issuers go to `sublist_H2_watchlist.csv`.

| File | Grain |
|---|---|
| `sublists/sublist_H2_fresh_capital.csv` | One row per H2-tagged company: best capital event, confidence, evidence URL, all events |
| `sublists/sublist_H2_funding_status_all.csv` | Every company in the DB: funding verification status |
| `sublists/sublist_H2_rejected.csv` | New-company candidates rejected, with reason |
| `sublists/sublist_H2_watchlist.csv` | Relevant signals that could not be keyed to a domain / stage |
| `sublists/master_priority.csv` | All non-disqualified companies ranked across H1-H3 (`rank_breakdown` explains every point) |
