"""Snapshot H1 raw sources into raw_sources/H1_job_arbitrage/ (gzip, unfiltered).

Usage: python3 scripts/h1_snapshot.py <dir-with-cloned-source-repos>

Sources are public GitHub mirrors of job feeds (the vendor ATS APIs themselves are
not reachable from this environment; see README "H1 sources"). Files are copied
byte-for-byte (gzipped) and manifest.json records each upstream commit SHA.
"""
import gzip
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "raw_sources" / "H1_job_arbitrage"

# (local repo dir, upstream repo, branch, [relative files], output subdir, what it is)
SOURCES = [
    ("ycapi", "devasheeshG/yc-api", "gh-pages",
     ["meta.json", "companies/hiring.json"], "yc_waas",
     "YC Work at a Startup: every YC company flagged hiring, with full jobs[] (refreshed daily)"),
    ("ConorsCode_open-jobs-data", "ConorsCode/open-jobs-data", "main",
     ["companies.json", "data/jobs.json", "data/summary.json"], "open_jobs_data",
     "Daily normalized dump of Greenhouse/Lever/Ashby/Workable/... boards for ~380 companies"),
    ("veresk06_open-ats-feed", "veresk06/open-ats-feed", "main",
     ["docs/data/all.csv", "data/tokens.json", "data/role-census-titles.json"], "open_ats_feed",
     "Board-token index (Common Crawl -> verified against vendor APIs): 18k live boards + posting counts"),
    ("JohnMarcBautista_startuppy", "JohnMarcBautista/startuppy", "main",
     ["jobs.json"], "startuppy_jobs", "Curated startup job list (Ashby/Greenhouse URLs)"),
    ("LdwS123_startup-portfolio-scrapers", "LdwS123/startup-portfolio-scrapers", "main",
     ["data/getro_portfolios.csv"], "getro_portfolios",
     "Getro VC job-board portfolios (Antler, Techstars, Accel, GC, Insight, Point Nine): active-job counts"),
]


def main(ext: Path) -> None:
    manifest = {"snapshot_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "sources": []}
    for local, repo, branch, files, sub, what in SOURCES:
        src = ext / local
        sha = subprocess.run(["git", "-C", str(src), "rev-parse", "HEAD"],
                             capture_output=True, text=True, check=True).stdout.strip()
        committed = subprocess.run(["git", "-C", str(src), "log", "-1", "--format=%cI"],
                                   capture_output=True, text=True, check=True).stdout.strip()
        dest = OUT / sub
        dest.mkdir(parents=True, exist_ok=True)
        written = []
        for rel in files:
            target = dest / (Path(rel).name + ".gz")
            with open(src / rel, "rb") as fi, gzip.open(target, "wb", compresslevel=9) as fo:
                shutil.copyfileobj(fi, fo)
            written.append(str(target.relative_to(ROOT)))
        manifest["sources"].append({"name": sub, "description": what, "repo": f"https://github.com/{repo}",
                                    "branch": branch, "commit": sha, "commit_date": committed,
                                    "files": written})
        print(f"{sub:<18} {sha[:10]} {committed}  {len(written)} files")
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")


if __name__ == "__main__":
    main(Path(sys.argv[1]).resolve())
