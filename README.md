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
