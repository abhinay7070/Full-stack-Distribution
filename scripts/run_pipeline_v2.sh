#!/usr/bin/env bash
# Final v2 sequence after collectors/ingests: scan -> ATS second pass -> tags -> emails -> score -> workbook -> gates.
# Each step is idempotent; gate_check exits non-zero on any hard failure.
set -euo pipefail
cd "$(dirname "$0")/.."
log() { echo "[$(date -u +%H:%M:%S)] $*"; }
log "site scan (new domains, 4 shards)";
for i in 0 1 2 3; do python3 scripts/site_scan.py --to-cache --shard=$i/4 & done; wait
log "site scan load";                     python3 scripts/site_scan.py --load
log "ATS second pass (homepage slugs)";   python3 scripts/ats_sweep.py --providers ashby,greenhouse,lever
log "ATS ingest";                         python3 scripts/universe_ingest.py ats
log "site scan (domains added by ATS)";
for i in 0 1 2 3; do python3 scripts/site_scan.py --to-cache --shard=$i/4 & done; wait
python3 scripts/site_scan.py --load
log "GitHub orgs (H8)";                   python3 scripts/signals_collect.py h8 "${EXT:-}"
log "gate: before tags";                  python3 scripts/gate_check.py --fingerprint .cache/fp_before_tags.json
log "tag H3x-H10";                        python3 scripts/tag_signals.py
log "tag again (idempotency check)";      python3 scripts/gate_check.py --fingerprint .cache/fp_tags1.json
python3 scripts/tag_signals.py >/dev/null
python3 scripts/gate_check.py --fingerprint .cache/fp_tags2.json >/dev/null
python3 scripts/gate_check.py --compare .cache/fp_tags1.json .cache/fp_tags2.json
log "emails";                             python3 scripts/enrich_emails.py
log "workbook";                           python3 scripts/build_master.py
log "recalc";                             python3 /mnt/skills/public/xlsx/scripts/recalc.py master/gtm_master.xlsx 600
log "gate: final";                        python3 scripts/gate_check.py --sample 25
log "done"
