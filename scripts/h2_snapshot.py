"""Snapshot H2 raw sources into raw_sources/H2_fresh_capital/ (gzip, unfiltered).

Usage: python3 scripts/h2_snapshot.py <dir-with-cloned-source-repos>

Funding-news sites, Crunchbase and SEC EDGAR are not reachable from this environment, so
H2 is built on public GitHub mirrors. Files are copied byte-for-byte (gzipped; parquet kept
as-is) and manifest.json records each upstream commit SHA.
"""
import gzip
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "raw_sources" / "H2_fresh_capital"

# (local repo dir, upstream repo, [relative files or dir/], output subdir, description)
SOURCES = [
    ("ycapi", "devasheeshG/yc-api", ["companies/all.json"], "yc_directory",
     "All 6,362 YC companies with YC-attached news[] (title, url, date)"),
    ("Busineessfree_global-startup-funding", "Busineessfree/global-startup-funding",
     ["global_startup_funding_sample.csv"], "fliar_funding_sample",
     "Fliar Radar funding rounds 2019-2026-06, 25% company-hash sample, domain-keyed"),
    ("Busineessfree_us-startup-funding-2019-2026", "Busineessfree/us-startup-funding-2019-2026",
     ["us_startup_funding_2019_2026_sample.csv"], "fliar_funding_sample", "US subset sample"),
    ("Busineessfree_uk-startup-funding-2019-2026", "Busineessfree/uk-startup-funding-2019-2026",
     ["uk_startup_funding_2019_2026_sample.csv"], "fliar_funding_sample", "UK subset sample"),
    ("Busineessfree_ai-startup-funding", "Busineessfree/ai-startup-funding",
     ["ai_startup_funding_sample.csv"], "fliar_funding_sample", "AI subset sample"),
    ("brianboler_startupdashboard", "brianboler/startupdashboard", ["data/history/"], "startup_pulse_daily",
     "Daily snapshots 2026-07-08..: RSS headlines (TechCrunch, Techmeme, Sifted, ...) + SEC Form D filings"),
    ("LBB2005_formd-tracker", "LBB2005/formd-tracker",
     ["data/enrich/company_domains.parquet", "data/enrich/yc_matches.parquet"], "formd_tracker",
     "SEC Form D issuer CIK -> company domain / YC company"),
    ("Puraav_funded-and-sponsoring", "Puraav/funded-and-sponsoring", ["web/public/data/companies.json"],
     "formd_bay_area", "SF Bay Area startups with Form D raises 2023-10..2026-09 (latest date, total sold)"),
    ("landedjobs_recently-funded-ai-startups-hiring", "landedjobs/recently-funded-ai-startups-hiring",
     ["data/startups.json"], "landedjobs_recent_raises", "Recently funded AI startups (round, amount, date)"),
]


def copy(src: Path, target: Path) -> None:
    if src.suffix == ".parquet":
        shutil.copyfile(src, target.with_suffix(""))
        return
    with open(src, "rb") as fi, gzip.open(target, "wb", compresslevel=9) as fo:
        shutil.copyfileobj(fi, fo)


def main(ext: Path) -> None:
    manifest = {"snapshot_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "sources": []}
    for local, repo, files, sub, what in SOURCES:
        src = ext / local
        sha = subprocess.run(["git", "-C", str(src), "rev-parse", "HEAD"],
                             capture_output=True, text=True, check=True).stdout.strip()
        committed = subprocess.run(["git", "-C", str(src), "log", "-1", "--format=%cI"],
                                   capture_output=True, text=True, check=True).stdout.strip()
        dest = OUT / sub
        dest.mkdir(parents=True, exist_ok=True)
        written = []
        for rel in files:
            paths = sorted((src / rel).glob("*.json")) if rel.endswith("/") else [src / rel]
            for p in paths:
                target = dest / (p.name + ".gz")
                copy(p, target)
                written.append(str((target.with_suffix("") if p.suffix == ".parquet" else target).relative_to(ROOT)))
        manifest["sources"].append({"name": sub, "description": what, "repo": f"https://github.com/{repo}",
                                    "commit": sha, "commit_date": committed, "files": len(written),
                                    "paths": written if len(written) <= 5 else written[:2] + ["..."] + written[-2:]})
        print(f"{sub:<26} {sha[:10]} {committed}  {len(written)} files")
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")


if __name__ == "__main__":
    main(Path(sys.argv[1]).resolve())
