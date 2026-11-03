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
| H1 | `sublist_H1_job_arbitrage` | Live GTM job posts (Founding AE, SDR, Growth, Demand Gen, GTM Eng) |
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
