# GTM role-scope research

Question: *what do seed-to-Series-A founders actually hire a growth / GTM person to do, and which part of that should the 8-o
agent do?* Read **[SCOPE.md](SCOPE.md)** for the answer. Everything else here is raw data and intermediate output, kept so it
can be re-synthesised later.

## Files

| Path | What | Rows |
|---|---|---|
| `report.html` | **PMF evidence report** (open in a browser): verdict, reverse breakdown, patterns, pain and budget, ranked scope, 8-o translation, directional blueprint, confidence | — |
| `SCOPE.md` | **Unified scope** as text, generated from the same data: bundles, intent/KPIs, all 48 duties with CIs, robust differences, tools, method | — |
| `corpus/postings.jsonl.gz` | **Raw data**: one JSON per posting, full description text + company / ICP fields + provenance (`text_source`) | 6,183 |
| `corpus/postings_index.csv` | Same rows without text (open in a spreadsheet) | 6,183 |
| `corpus/collect_report.json` | Where each text came from | — |
| `extracted/bullets.csv.gz` | Every responsibility / requirement / nice-to-have line, with duty tags | 112,896 |
| `extracted/postings_tagged.csv` | Per posting: archetype, core flag, founding flag, seniority, comp (USD), duties, tools, skills, traits, KPIs, intent | 6,183 |
| `analysis/unified_scope.csv` | One row per duty: prevalence + 95% CI, company share, founding lift, market lift, demand index, priority, 8-o decision, stage, Gap target | 48 |
| `analysis/duty_prevalence.csv` | Duty prevalence: all / core / founding / small team / company-level | 48 |
| `analysis/archetype_duty_matrix.csv` | % of postings per archetype asking for each duty, median comp | 15 |
| `analysis/duty_clusters.csv` | Data-driven duty bundles (hierarchical clustering on co-occurrence) | 16 |
| `analysis/duty_cooccurrence_core.csv` | Duty pairs with φ correlation | ≤ 300 |
| `analysis/founding_vs_nonfounding.csv` | Lift + z for first-hire postings | 48 |
| `analysis/lift_vs_market.csv` | ICP startups vs scaled-company postings (duties, tools, skills, traits, KPIs, intent) | 125 |
| `analysis/nmf_topics_core.csv` | 30 unsupervised topics over core responsibility lines, with examples and taxonomy coverage | 30 |
| `analysis/{tools,skills,traits,kpis,intent}_prevalence.csv` | Requirement-side tallies, core + all | — |
| `analysis/summary.json` | Counts, comp, experience, bundle size, method notes (keyword pass) | — |
| `ml/postings_labels.csv.gz` | **Every posting, both corpora** (6,183 ICP + 30,399 market): archetype, flags, duties (ML and keyword), intent, KPIs, tools | 36,582 |
| `ml/duty_stats_ml.csv` | Per duty: prevalence (ML / keyword), company share, segments with z for both instruments, robust flag, demand index with bootstrap CI, rank CI, P(Must) | 48 |
| `ml/classifier_agreement.csv` | Embedding classifier vs keywords: AUC, threshold, precision, recall, F1 per duty | 48 |
| `ml/label_disagreements.csv.gz` | Every line where the two instruments disagree (audit trail) | — |
| `ml/discovered_clusters.csv` | Bottom-up themes (k-means on all unique ICP responsibility lines) with terms, nearest lines, matched duties | 80 |
| `ml/kmeans_grid.csv`, `ml/intent_kpi_tools_ml.csv`, `ml/summary_ml.json` | Model selection, intent/KPI/tool shares vs market, run summary | — |

Terms: **core** = SDR/BDR, GTM generalist / founding GTM, growth, demand gen, GTM engineer, RevOps, or any founding / first-hire
title. **Usable** = ≥ 3 responsibility lines. **Demand index** is defined in `scripts/role_scope_synthesize.py`.

## Reproduce

```bash
pip install -r requirements.txt   # includes scikit-learn, scipy, fastembed
python3 scripts/role_corpus_collect.py     # ~25 min (1,365 ATS boards + ~580 YC pages; HTTP cache in .cache/)
python3 scripts/role_scope_analyze.py      # ~8 min  (parsing + keyword pass)
python3 scripts/role_scope_ml.py           # ~75 min first run (embeds 546K lines; cached in .cache/role_ml), ~15 min after
python3 scripts/role_scope_synthesize.py   # scope + bundles from the ML stats
python3 scripts/role_scope_report.py       # report.html
python3 scripts/role_scope_scope_md.py     # SCOPE.md
```

To change what counts as a duty, edit `scripts/role_taxonomy.py`. To change the 8-o decisions or stages, edit `DECISION` in
`scripts/role_scope_synthesize.py`. Re-run the last two steps afterwards, then update `SCOPE.md` with the new numbers.
