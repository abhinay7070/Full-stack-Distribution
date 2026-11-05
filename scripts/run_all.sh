#!/usr/bin/env bash
# Full refresh of the v2 pipeline (all live sources). Every step is idempotent and cached (.cache/http);
# a re-run only fetches what is new. Needs open network access (see CLAUDE.md). ~3-5 h first run, much less after.
set -euo pipefail
cd "$(dirname "$0")/.."
export EXT="${EXT:-$(pwd)/../ext}"
log() { echo "[$(date -u +%H:%M:%S)] $*"; }
log "mirrors";        ./scripts/fetch_mirrors.sh
log "H0 universe";    python3 scripts/h0_universe.py "$EXT"
log "Form D";         python3 scripts/formd_collect.py && python3 scripts/formd_ingest.py
log "HN hiring";      python3 scripts/hn_hiring.py && python3 scripts/universe_ingest.py hn
log "news";           python3 scripts/news_funding.py && python3 scripts/universe_ingest.py news
log "launches/MRR";   python3 scripts/signals_collect.py h4 "$EXT" && python3 scripts/signals_collect.py h7 "$EXT"
log "site scan 1";    for i in 0 1 2 3; do python3 scripts/site_scan.py --to-cache --shard=$i/4 & done; wait
python3 scripts/site_scan.py --load
log "ATS sweep";      for p in ashby greenhouse lever recruitee,breezy,teamtailor workable; do python3 scripts/ats_sweep.py --providers $p & done; wait
log "ATS ingest";     python3 scripts/universe_ingest.py ats
log "finish";         ./scripts/run_pipeline_v2.sh
