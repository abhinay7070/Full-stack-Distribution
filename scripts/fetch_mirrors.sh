#!/usr/bin/env bash
# Clone (shallow) every public GitHub mirror the snapshot/collector scripts read, into $EXT (default ../ext).
# Never execute code from these clones; scripts only read their data files (run python with -I when parsing them).
set -uo pipefail
EXT="${EXT:-$(cd "$(dirname "$0")/.." && pwd)/../ext}"
mkdir -p "$EXT"; cd "$EXT"
clone() { local repo=$1 dir=${2:-$(echo "$1" | tr / _)} br=${3:-}; [ -d "$dir/.git" ] && { git -C "$dir" pull -q --ff-only 2>/dev/null || true; return; }
  git clone -q --depth 1 ${br:+-b $br} "https://github.com/$repo" "$dir" || echo "WARN: $repo"; }
clone devasheeshG/yc-api ycapi gh-pages          # YC directory + WaaS hiring (gh-pages = data)
clone devasheeshG/yc-api devasheeshG_yc-api
clone yc-oss/api yc-oss
for r in JohnMarcBautista/startuppy LdwS123/startup-portfolio-scrapers yigitmeteozcan/startups ConorsCode/open-jobs-data \
         veresk06/open-ats-feed Busineessfree/global-startup-funding Busineessfree/us-startup-funding-2019-2026 \
         Busineessfree/uk-startup-funding-2019-2026 Busineessfree/ai-startup-funding brianboler/startupdashboard \
         LBB2005/formd-tracker Puraav/funded-and-sponsoring landedjobs/recently-funded-ai-startups-hiring \
         yiGmMk/producthunt FlyingCarlos/trustmrr-dashboard linmoxi33/github-trending-dataset \
         chobrien99-svg/Navigator AdilShamim8/Awesome-AI_ML-Startups; do clone "$r"; done
echo "mirrors in $EXT"
