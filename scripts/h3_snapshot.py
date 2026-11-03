"""Snapshot H3 raw sources into raw_sources/H3_technical_founders/ (gzip, unfiltered).

Usage: python3 scripts/h3_snapshot.py <dir-with-cloned-source-repos>

Each source is a public GitHub mirror of an accelerator directory/API. The clone
step is done with `git clone --depth 1` (see README "H3 sources"); this script
only copies the files byte-for-byte (gzipped) and writes manifest.json with the
upstream commit SHA so every run is reproducible.
"""
import gzip
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "raw_sources" / "H3_technical_founders"

YC_BATCHES = ["winter-2025", "spring-2025", "summer-2025", "fall-2025",
              "winter-2026", "spring-2026", "summer-2026", "fall-2026"]

# (local repo dir, upstream repo, branch, [relative files], output subdir)
SOURCES = [
    ("ycapi", "devasheeshG/yc-api", "gh-pages",
     ["meta.json"] + [f"batches/{b}.json" for b in YC_BATCHES], "yc_devasheeshG"),
    ("yc-oss", "yc-oss/api", "main",
     [f"batches/{b}.json" for b in YC_BATCHES], "yc_oss_crosscheck"),
    ("JohnMarcBautista_startuppy", "JohnMarcBautista/startuppy", "main",
     ["data.json"], "speedrun_startuppy"),
    ("LdwS123_startup-portfolio-scrapers", "LdwS123/startup-portfolio-scrapers", "main",
     ["data/a16z.csv", "data/entrepreneur_first.csv"], "ldws123_founders"),
    ("yigitmeteozcan_startups", "yigitmeteozcan/startups", "main",
     ["data/by-source/ef.json", "data/by-source/techstars.json"], "portfolios_yigitmeteozcan"),
]


def main(ext: Path) -> None:
    manifest = {"snapshot_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "sources": []}
    for local, repo, branch, files, sub in SOURCES:
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
        manifest["sources"].append({"name": sub, "repo": f"https://github.com/{repo}", "branch": branch,
                                    "commit": sha, "commit_date": committed, "files": written})
        print(f"{sub:<28} {sha[:10]} {committed}  {len(written)} files")
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")


if __name__ == "__main__":
    main(Path(sys.argv[1]).resolve())
