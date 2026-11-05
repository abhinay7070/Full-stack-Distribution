"""One ICP gate for every source (maker); returns keep/reject + reason + flags so the checker can audit it.

ICP: US / UK / EU-EEA-CH B2B software or AI (incl. physical AI with a software/autonomy signal), pre-seed to
Series A, 2-35 people (11-50 band kept with a flag when no exact count), founded / cohort year >= 2021.
Rejected: biotech/pharma, agencies/services, consumer (unless a B2B hint), raw hardware, Series B+, acquired,
public, exited, > 50 people, outside geography.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import h3_ingest as h3  # noqa: E402

LATE = {"series_b", "series_c", "series_d", "series_e", "series_f", "series_g", "series_h", "series_i",
        "series_j", "series_c_plus", "private_equity", "ipo", "acquisition", "acquired", "public", "late_stage",
        "growth", "series_b_plus"}
EARLY = {"pre_seed", "seed", "series_a", "angel", "accelerator"}
BAND_OK = {"1-10": "1-10", "11-50": "11-50"}
GETRO_BAND = {1: "1-10", 2: "11-50", 3: "51-200", 4: "201-1000", 5: "1001-5000", 6: "5001+"}


def norm_stage(s: str | None) -> str:
    s = (s or "").strip().lower().replace("-", "_").replace(" ", "_")
    s = {"preseed": "pre_seed", "series_unknown": "unknown", "other": "unknown", "undisclosed": "unknown",
         "unknown_stage": "unknown", "early": "seed"}.get(s, s)
    return s or "unknown"


def check(*, country: str | None, headcount: int | None = None, band: str | None = None, stage: str | None = None,
          industry: str = "", sub: str = "", tags: list[str] | None = None, text: str = "",
          year: int | None = None, min_year: int = 2021, status: str | None = None) -> tuple[bool, str, list[str], str]:
    """-> (keep, reject_reason, flags, sector_label)."""
    flags: list[str] = []
    if status and status.lower() in ("inactive", "acquired", "public", "dead", "closed", "exited"):
        return False, f"status:{status.lower()}", flags, ""
    st = norm_stage(stage)
    if st in LATE:
        return False, f"stage:{st}", flags, ""
    if st == "unknown":
        flags.append("stage_unknown")
    if country is None:
        flags.append("geo_unknown")
    elif country not in h3.ALLOWED:
        return False, f"geo:{country}", flags, ""
    if headcount is not None:
        if headcount > 50:
            return False, f"headcount:{headcount}", flags, ""
        if headcount > 35:
            flags.append("headcount_36_50")
    elif band:
        if band not in BAND_OK:
            return False, f"headcount_band:{band}", flags, ""
        if band == "11-50":
            flags.append("headcount_band_11_50")
    else:
        flags.append("headcount_unknown")
    if year is not None and year < min_year:
        return False, f"too_old:{year}", flags, ""
    keep, label, why = h3.sector_decision(industry or "", sub or "", tags or [], text or "")
    if not keep:
        return False, why, flags, ""
    return True, "", flags, label


def country_of(*texts: str | None) -> str | None:
    for t in texts:
        c = h3.country_from_text(t) if t else None
        if c:
            return c
    return None


def stage_from_text(t: str | None) -> str | None:
    t = (t or "").lower()
    for pat, st in ((r"series\s*[b-k]\b", "series_b_plus"), (r"series\s*a\b", "series_a"),
                    (r"pre[- ]?seed", "pre_seed"), (r"\bseed\b", "seed")):
        if re.search(pat, t):
            return st
    return None
