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


# --------------------------------------------------------------------------- ICP fit score (0-100) + SAM class
# Your SAM: US / UK / Europe (remote-friendly) | Seed -> Series A | 3-35 employees |
# B2B SaaS, AI Agents, DevTools, Infra, Vertical AI, Hardware/Physical-AI beneficiaries.
# Hard exclusions: biotech, pure hardware, consumer apps, agencies/services, brick & mortar.
BRICK = re.compile(r"\b(restaurant|cafe|coffee shop|bakery|retail store|boutique|salon|spa\b|gym\b|fitness studio|"
                   r"dental (clinic|practice)|medical clinic|real estate (brokerage|agency)|property management company|"
                   r"construction company|general contractor|franchise|hotel|winery|brewery|distillery|food truck|"
                   r"grocery|laundromat|car wash|daycare|farm\b|apparel brand|clothing brand)\b", re.I)
CONSUMER_X = re.compile(r"\b(consumer app|dating|for (kids|parents|families|couples|teens|gamers|fans|singles)|"
                        r"mobile game|social network for|personal finance app|fitness app|meditation app|"
                        r"direct-to-consumer|d2c brand|dtc brand)\b", re.I)
BIO_X = re.compile(r"\b(therapeutic|drug (discovery|development|candidate)|biotech|antibod|gene (therapy|editing)|"
                   r"cell therapy|clinical[- ]stage|small molecule|pharmaceutical|oncology|vaccine)\w*", re.I)
SERVICE_X = re.compile(r"\b(marketing agency|creative agency|dev(elopment)? shop|staffing agency|recruiting agency|"
                       r"consultancy|we are an agency|digital agency|outsourc\w+ (team|development)|hedge fund|"
                       r"asset management firm|proprietary trading|prop trading|investment firm|law firm|"
                       r"accounting firm)\b", re.I)
SECTOR_CAT = {"AI Agents": "AI Agents", "DevTools": "DevTools", "Infra": "Infra", "Vertical AI": "Vertical AI",
              "B2B SaaS": "B2B SaaS", "Industrial / Physical AI": "Physical AI", "Fintech": None}
AI_WORD = re.compile(r"\b(ai|a\.i\.|llm|agent\w*|machine learning|ml|genai|copilot|autonomous)\b", re.I)


def sector_category(label: str | None, text: str) -> str:
    if label == "Fintech":
        return "Vertical AI" if AI_WORD.search(text or "") else "B2B SaaS"
    return SECTOR_CAT.get(label or "", None) or ("Vertical AI" if AI_WORD.search(text or "") else "B2B SaaS")


def fit(*, country: str | None, headcount: int | None, band: str | None, sector_label: str | None, text: str,
        stage_evidence: str, possible_past_a: bool = False) -> dict:
    """stage_evidence: verified_seed_a | verified_pre_seed | accelerator_in_window | vc_listed_early |
    accelerator_older | weak_name_match | text_only | none."""
    hard = []
    t = text or ""
    if BIO_X.search(t):
        hard.append("sector:biotech")
    if CONSUMER_X.search(t):
        hard.append("sector:consumer_app")
    if SERVICE_X.search(t):
        hard.append("sector:agency_services_or_fund")
    soft = re.search(r"\b(software|saas|platform|ai\b|api|app for|tool|automat\w+|agent\w*|analytics|data|"
                     r"cloud|marketplace|os\b|operating system|copilot|llm|ml\b)", t, re.I)
    if BRICK.search(t) and not soft:
        hard.append("sector:brick_and_mortar")
    if headcount is not None and headcount < 3:
        hard.append(f"headcount:{headcount}_below_3")
    if headcount is not None and headcount > 35:
        hard.append(f"headcount:{headcount}_above_35")
    if possible_past_a:
        hard.append("stage:possible_past_series_a")
    geo = 20 if country in h3.ALLOWED else 8 if country is None else 0
    stage = {"verified_seed_a": 30, "verified_pre_seed": 24, "accelerator_in_window": 24, "vc_listed_early": 20,
             "accelerator_older": 16, "weak_name_match": 15, "text_only": 12, "none": 5}.get(stage_evidence, 5)
    if possible_past_a:
        stage = 0
    if headcount is not None:
        size = 25 if 3 <= headcount <= 35 else 0
    elif band == "1-10":
        size = 15
    elif band == "11-50":
        size = 15
    else:
        size = 8
    cat = sector_category(sector_label, t)
    sector = 25 if cat in ("AI Agents", "DevTools", "Infra", "Vertical AI", "Physical AI") else 22 if cat == "B2B SaaS" else 10
    if hard and any(h.startswith("sector") for h in hard):
        sector = 0
    score = geo + stage + size + sector
    if hard:
        cls = "Outside SAM"
    elif score >= 80 and geo == 20 and stage >= 20 and size >= 15 and sector >= 22:
        cls = "A · Core ICP"
    elif score >= 65 and stage >= 15:
        cls = "B · Strong fit"
    elif score >= 50:
        cls = "C · Probable - verify"
    else:
        cls = "D · Long-shot"
    return {"icp_fit_score": score, "fit_geo": geo, "fit_stage": stage, "fit_headcount": size, "fit_sector": sector,
            "icp_class": cls, "sector_category": cat, "outside_sam_reason": "|".join(hard)}
