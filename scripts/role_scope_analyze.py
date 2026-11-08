"""Role-scope research, step 2: parse, extract, cluster and score every GTM posting at ICP companies.

Reads  research/gtm_role_scope/corpus/postings.jsonl.gz (from role_corpus_collect.py)
       raw_sources/H1_job_arbitrage/ats_live/*.jsonl.gz (market comparison: GTM postings at large boards, >= 80 jobs)
Writes research/gtm_role_scope/extracted/  bullets.csv.gz (every responsibility / requirement line, tagged)
                                         postings_tagged.csv (per posting: archetype, seniority, comp, duties, ...)
       research/gtm_role_scope/analysis/   *.csv + summary.json (prevalence with 95% Wilson CI, lift vs market,
                                         co-occurrence, NMF topics, tools, skills, KPIs, intent, comp)
Method notes are written into analysis/summary.json["method"]. Deterministic (fixed seeds).
"""
from __future__ import annotations

import csv
import gzip
import json
import math
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import role_taxonomy as T  # noqa: E402
from h1_ingest import classify_job  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
BASE = ROOT / "research" / "gtm_role_scope"
CORPUS = BASE / "corpus" / "postings.jsonl.gz"
EXT, ANA = BASE / "extracted", BASE / "analysis"
GTM_FAMILIES = {"exact_role", "hidden_gtm", "growth", "gtm_generalist", "sales", "marketing", "revops", "partnerships",
                "presales", "devrel", "customer", "bizops_generalist"}
FX = {"$": 1.0, "usd": 1.0, "€": 1.08, "eur": 1.08, "£": 1.27, "gbp": 1.27, "chf": 1.13, "cad": 0.72, "ca$": 0.72,
      "sek": 0.095, "dkk": 0.145, "nok": 0.093, "pln": 0.25}  # fixed 2026 approximations, stated in method notes

# ---------------- parsing ----------------
BULLET = re.compile(r"^\s*(?:[-*•·▪◦–—►✔✓→]|\d{1,2}[.)]|[a-z][.)])\s+")
MD_HEAD = re.compile(r"^\s*(?:#{1,6}\s*|\*\*|__)(.+?)(?:\*\*|__)?\s*:?\s*$")
INLINE_HEADS = re.compile(r"(?<=[.!?])\s+(?=(?:What you'?ll (?:do|own|bring)|Responsibilities|Requirements|Qualifications|"
                          r"About you|Who you are|What we'?re looking for|Nice to have|Bonus|Benefits|What we offer|"
                          r"You will|You have|The role|About the role|In this role)\b)")


def classify_heading(h: str) -> str | None:
    h = re.sub(r"[*#_:]+", " ", h).strip().lower()
    if len(h) > 90:
        return None
    for name, rx in T.SECTION:
        if rx.search(h):
            return name
    return None


def is_heading(line: str) -> str | None:
    s = line.strip()
    if not s or len(s) > 90:
        return None
    m = MD_HEAD.match(s)
    if s.startswith("#") or (m and s.startswith(("**", "__"))) or (s.endswith(":") and len(s) < 70) or \
            (len(s) < 60 and not s.endswith(".") and not BULLET.match(s) and s[:1].isupper() and len(s.split()) <= 5):
        c = classify_heading(s)
        if c:
            return c
        if s.startswith("#") or (m and s.startswith(("**", "__"))):
            return "other"
        return "keep" if s.endswith(":") else None
    return None


def split_sections(text: str) -> list[tuple[str, str]]:
    """-> [(section, line)] for every content line. Lines before any heading are 'intro'."""
    if text.count("\n") < 3:  # flattened payload: recover breaks before known headings and bullet glyphs
        text = INLINE_HEADS.sub("\n", text)
        text = re.sub(r"\s+(?=[•·▪◦►✔✓]\s)", "\n", text)
    out, sec = [], "intro"
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        h = is_heading(line)
        if h == "keep":  # lead-in line ("You will:"), section unchanged
            continue
        if h:
            sec = h
            continue
        line = re.sub(r"\*\*|__", "", line)
        if BULLET.match(line) or len(line) < 400:
            out.append((sec, BULLET.sub("", line).strip()))
        else:  # long paragraph -> sentences
            for s in re.split(r"(?<=[.!?])\s+(?=[A-Z])", line):
                if len(s) > 15:
                    out.append((sec, s.strip()))
    return out


def scope_lines(secs: list[tuple[str, str]]) -> tuple[list[str], list[str], str]:
    """Responsibility lines, requirement lines (incl. nice-to-have), and which basis was used."""
    resp = [l for s, l in secs if s == "responsibilities"]
    req = [l for s, l in secs if s in ("requirements", "nice_to_have")]
    if resp:
        return resp, req, "sectioned"
    keep = [l for s, l in secs if s in ("intro", "other")]
    # no explicit responsibility section: drop the first 2 intro lines (usually 'About <company>') and boilerplate
    return keep[2:] if len(keep) > 4 else keep, req, "unsectioned"


# ---------------- helpers ----------------
def archetype(title: str) -> str:
    for name, rx in T.ARCHETYPES:
        if rx.search(title or ""):
            return name
    return "other_gtm"


def seniority(title: str) -> str:
    for name, rx in T.SENIORITY:
        if rx.search(title or ""):
            return name
    return "mid"


COMP = re.compile(r"(ca\$|\$|€|£|usd|eur|gbp|chf|cad|sek|dkk|nok|pln)\s?(\d[\d,.]*)\s?(k)?\s*(?:[-–—to]+\s*(?:ca\$|\$|€|£|usd|eur|gbp|chf|cad)?\s?"
                  r"(\d[\d,.]*)\s?(k)?)?", re.I)


def comp_usd(comp: str | None, text: str) -> float | None:
    """Annual base midpoint in USD from the comp field, else from the text. None if hourly / unparseable."""
    for src in (comp or "", text[:6000] if text else ""):
        for m in COMP.finditer(src):
            cur = m.group(1).lower()

            def num(v, k):
                if not v:
                    return None
                v = v.replace(",", "")
                if v.count(".") > 1 or (v.count(".") == 1 and len(v.split(".")[1]) == 3):
                    v = v.replace(".", "")
                try:
                    x = float(v)
                except ValueError:
                    return None
                return x * 1000 if k or x < 1000 else x
            lo = num(m.group(2), m.group(3))
            hi = num(m.group(4), m.group(5) or m.group(3)) or lo
            if not lo:
                continue
            mid = (lo + hi) / 2 * FX.get(cur, 1.0)
            ctx = src[m.end():m.end() + 25].lower()
            if "hour" in ctx or "/hr" in ctx or "month" in ctx:
                continue
            if 25_000 <= mid <= 600_000:
                return round(mid)
    return None


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return 0.0, 0.0
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    r = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (c - r) / d, (c + r) / d


def two_prop_z(k1, n1, k2, n2) -> float:
    p = (k1 + k2) / (n1 + n2) if n1 + n2 else 0
    se = math.sqrt(p * (1 - p) * (1 / n1 + 1 / n2)) if 0 < p < 1 and n1 and n2 else 0
    return ((k1 / n1) - (k2 / n2)) / se if se else 0.0


def match_any(rxs: dict, text: str) -> list[str]:
    return [k for k, rx in rxs.items() if rx.search(text)]


DUTY_RX = {d["id"]: d["re"] for d in T.DUTIES}
DUTY_META = {d["id"]: d for d in T.DUTIES}


# ---------------- market comparison corpus ----------------
def market_corpus(icp_keys: set[str], cap: int = 12000) -> list[dict]:
    rows = []
    for p in ("ashby", "greenhouse", "lever"):
        with gzip.open(ROOT / "raw_sources" / "H1_job_arbitrage" / "ats_live" / f"{p}.jsonl.gz", "rt") as fh:
            for line in fh:
                d = json.loads(line)
                jobs = d.get("jobs") or []
                if len(jobs) < 80:
                    continue
                for j in jobs:
                    k = f"{p}:{d['slug']}:{j.get('id')}"
                    if k in icp_keys or not j.get("description"):
                        continue
                    c = classify_job(j.get("title") or "", None, "")
                    if c["family"] in GTM_FAMILIES:
                        rows.append({"key": k, "title": j.get("title"), "family": c["family"], "text": j["description"],
                                     "board": d["slug"]})
    rows.sort(key=lambda r: r["key"])
    rng = np.random.default_rng(7)
    if len(rows) > cap:
        idx = sorted(rng.choice(len(rows), cap, replace=False))
        rows = [rows[i] for i in idx]
    return rows


# ---------------- main ----------------
def main() -> None:
    EXT.mkdir(parents=True, exist_ok=True)
    ANA.mkdir(parents=True, exist_ok=True)
    posts = [json.loads(l) for l in gzip.open(CORPUS, "rt")]
    print(f"postings: {len(posts)}", flush=True)
    tagged, bullets = [], []
    for p in posts:
        text = p.get("text") or ""
        secs = split_sections(text)
        resp, req, basis = scope_lines(secs)
        resp_set = set(resp)
        resp_txt, req_txt = "\n".join(resp), "\n".join(req)
        full_txt = "\n".join(l for s, l in secs if s not in ("benefits", "process", "company"))
        a = archetype(p["title"])
        rec = {k: p.get(k) for k in ("job_key", "company_domain", "company_name", "source", "title", "family", "strength",
                                      "icp_tier", "headcount", "headcount_band", "funding_stage", "hq_country", "sector",
                                      "location", "posted_at", "url", "text_source", "text_chars")}
        hc = p.get("headcount")
        rec.update({
            "archetype": a, "core": a in T.CORE_ARCHETYPES or bool(T.FOUNDING.search(p["title"] or "")),
            "founding_title": bool(T.FOUNDING.search(p["title"] or "")),
            "founding_any": bool(T.FOUNDING.search(p["title"] or "") or T.INTENT["First GTM / sales / growth hire"].search(full_txt)),
            "seniority": seniority(p["title"]), "comp_usd_mid": comp_usd(p.get("comp"), text),
            "small_team": bool(hc and 0 < int(hc) <= 35), "scope_basis": basis, "n_resp_lines": len(resp),
            "n_req_lines": len(req),
            "duties": "|".join(k for k, rx in DUTY_RX.items() if rx.search(resp_txt)),
            "duties_fulltext": "|".join(k for k, rx in DUTY_RX.items() if rx.search(full_txt)),
            "tools": "|".join(match_any(T.TOOLS, req_txt + "\n" + resp_txt)),
            "skills": "|".join(match_any(T.SKILLS, req_txt)),
            "traits": "|".join(match_any(T.TRAITS, full_txt)),
            "kpis": "|".join(match_any(T.KPIS, full_txt)),
            "intent": "|".join(match_any(T.INTENT, full_txt)),
            "years_exp": (lambda m: int(m.group(1)) if m else None)(
                re.search(r"(\d{1,2})\+?\s*(?:-\s*\d+\s*)?years?", req_txt or full_txt, re.I)),
        })
        if rec["years_exp"] is not None and rec["years_exp"] > 20:
            rec["years_exp"] = None
        tagged.append(rec)
        for sec, line in secs:
            if sec in ("responsibilities", "requirements", "nice_to_have") or (basis == "unsectioned" and line in resp_set):
                bullets.append({"job_key": p["job_key"], "archetype": a, "core": rec["core"], "section": sec
                                if sec in ("responsibilities", "requirements", "nice_to_have") else "unsectioned",
                                "line": line[:600],
                                "duties": "|".join(k for k, rx in DUTY_RX.items() if rx.search(line))})

    # ---- write extracted
    cols = list(tagged[0].keys())
    with open(EXT / "postings_tagged.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows(tagged)
    with gzip.open(EXT / "bullets.csv.gz", "wt", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["job_key", "archetype", "core", "section", "line", "duties"])
        w.writeheader()
        w.writerows(bullets)
    print(f"bullets: {len(bullets)}", flush=True)

    usable = [t for t in tagged if t["n_resp_lines"] >= 3]
    core = [t for t in usable if t["core"]]
    by_company = defaultdict(list)
    for t in usable:
        by_company[t["company_domain"]].append(t)

    # ---- duty prevalence: posting-level (all / core / founding / small team) + company-level
    def prev(rows, field, key):
        k = sum(key in (r[field] or "").split("|") for r in rows)
        lo, hi = wilson(k, len(rows))
        return k, len(rows), k / len(rows) if rows else 0, lo, hi

    founding = [t for t in usable if t["founding_any"]]
    small = [t for t in usable if t["small_team"]]
    duty_rows = []
    for d in T.DUTIES:
        i = d["id"]
        a_k, a_n, a_p, a_lo, a_hi = prev(usable, "duties", i)
        c_k, c_n, c_p, c_lo, c_hi = prev(core, "duties", i)
        f_k, f_n, f_p, _, _ = prev(founding, "duties", i)
        s_k, s_n, s_p, _, _ = prev(small, "duties", i)
        comp_k = sum(any(i in (r["duties"] or "").split("|") for r in rs) for rs in by_company.values())
        duty_rows.append({"duty_id": i, "stage": d["stage"], "block": d["block"], "duty": d["label"],
                          "postings_all": a_k, "pct_all": round(100 * a_p, 1), "ci95_all": f"{100*a_lo:.1f}-{100*a_hi:.1f}",
                          "pct_core": round(100 * c_p, 1), "ci95_core": f"{100*c_lo:.1f}-{100*c_hi:.1f}",
                          "pct_founding": round(100 * f_p, 1), "pct_small_team": round(100 * s_p, 1),
                          "companies": comp_k, "pct_companies": round(100 * comp_k / len(by_company), 1)})

    # ---- per archetype x duty matrix
    archs = Counter(t["archetype"] for t in usable)
    mat = []
    for a, n in archs.most_common():
        rows = [t for t in usable if t["archetype"] == a]
        row = {"archetype": a, "postings": n, "companies": len({r["company_domain"] for r in rows}),
               "core": a in T.CORE_ARCHETYPES,
               "median_comp_usd": (int(np.median([r["comp_usd_mid"] for r in rows if r["comp_usd_mid"]]))
                                   if any(r["comp_usd_mid"] for r in rows) else None),
               "comp_n": sum(1 for r in rows if r["comp_usd_mid"])}
        for d in T.DUTIES:
            row[d["id"]] = round(100 * sum(d["id"] in r["duties"].split("|") for r in rows) / n, 1)
        mat.append(row)

    # ---- market comparison (same basis: full text, 6,000-char cap, both sides)
    mk = market_corpus({p["job_key"] for p in posts})
    print(f"market comparison postings: {len(mk)}", flush=True)
    icp_full = [p for p in posts if (p.get("text") or "")]
    def ft(rows):  # duties on capped flattened full text, for a like-for-like comparison
        return [{k for k, rx in DUTY_RX.items() if rx.search((r.get("text") or "")[:6000])} for r in rows]
    icp_sets, mk_sets = ft(icp_full), ft(mk)
    lift_rows = []
    for d in T.DUTIES:
        i = d["id"]
        k1, n1 = sum(i in s for s in icp_sets), len(icp_sets)
        k2, n2 = sum(i in s for s in mk_sets), len(mk_sets)
        p1, p2 = k1 / n1, (k2 / n2 if n2 else 0)
        lift_rows.append({"duty_id": i, "duty": d["label"], "stage": d["stage"], "pct_icp": round(100 * p1, 1),
                          "pct_market": round(100 * p2, 1), "lift": round(p1 / p2, 2) if p2 else None,
                          "z": round(two_prop_z(k1, n1, k2, n2), 1)})
    for name, rxs in (("tool", T.TOOLS), ("skill", T.SKILLS), ("trait", T.TRAITS), ("intent", T.INTENT), ("kpi", T.KPIS)):
        for k, rx in rxs.items():
            k1 = sum(bool(rx.search((r.get("text") or "")[:6000])) for r in icp_full)
            k2 = sum(bool(rx.search(r["text"][:6000])) for r in mk)
            p1, p2 = k1 / len(icp_full), (k2 / len(mk) if mk else 0)
            lift_rows.append({"duty_id": f"{name}:{k}", "duty": k, "stage": name.upper(), "pct_icp": round(100 * p1, 1),
                              "pct_market": round(100 * p2, 1), "lift": round(p1 / p2, 2) if p2 else None,
                              "z": round(two_prop_z(k1, len(icp_full), k2, len(mk)), 1)})

    # ---- founding vs non-founding within ICP (what changes when it is the first hire)
    nonf = [t for t in usable if not t["founding_any"]]
    fvn = []
    for d in T.DUTIES:
        i = d["id"]
        k1 = sum(i in t["duties"].split("|") for t in founding)
        k2 = sum(i in t["duties"].split("|") for t in nonf)
        fvn.append({"duty_id": i, "duty": d["label"], "pct_founding": round(100 * k1 / max(1, len(founding)), 1),
                    "pct_non_founding": round(100 * k2 / max(1, len(nonf)), 1),
                    "lift": round((k1 / max(1, len(founding))) / (k2 / len(nonf)), 2) if k2 else None,
                    "z": round(two_prop_z(k1, len(founding), k2, len(nonf)), 1)})

    # ---- co-occurrence (core): phi coefficient between duties
    ids = [d["id"] for d in T.DUTIES]
    X = np.array([[i in t["duties"].split("|") for i in ids] for t in core], dtype=float)
    co = []
    if len(X):
        for a in range(len(ids)):
            for b in range(a + 1, len(ids)):
                x, y = X[:, a], X[:, b]
                if x.std() == 0 or y.std() == 0:
                    continue
                phi = float(np.corrcoef(x, y)[0, 1])
                both = int((x * y).sum())
                if both >= 25:
                    co.append({"a": ids[a], "b": ids[b], "a_label": DUTY_META[ids[a]]["label"],
                               "b_label": DUTY_META[ids[b]]["label"], "both": both, "phi": round(phi, 3)})
    co.sort(key=lambda r: -r["phi"])
    # duties per posting distribution (bundle size)
    bundle = Counter(int(x.sum()) for x in X) if len(X) else Counter()

    # ---- unsupervised: NMF topics over core responsibility lines (data-driven check of the taxonomy)
    from sklearn.decomposition import NMF
    from sklearn.feature_extraction.text import TfidfVectorizer
    core_keys = {t["job_key"] for t in core}
    lines = [b for b in bullets if b["job_key"] in core_keys and b["section"] in ("responsibilities", "unsectioned")
             and 25 <= len(b["line"]) <= 400]
    seen, uniq = set(), []
    for b in lines:  # same company often reuses lines across postings
        key = re.sub(r"\W+", " ", b["line"].lower()).strip()
        if key not in seen:
            seen.add(key)
            uniq.append(b)
    vec = TfidfVectorizer(stop_words="english", ngram_range=(1, 2), min_df=8, max_df=0.25, sublinear_tf=True)
    M = vec.fit_transform([b["line"] for b in uniq])
    K = 30
    nmf = NMF(n_components=K, random_state=7, init="nndsvda", max_iter=400)
    W = nmf.fit_transform(M)
    terms = np.array(vec.get_feature_names_out())
    top = W.argmax(1)
    topics = []
    for k in range(K):
        members = [i for i in range(len(uniq)) if top[i] == k and W[i, k] > 0]
        members.sort(key=lambda i: -W[i, k])
        dut = Counter(d for i in members for d in uniq[i]["duties"].split("|") if d)
        unmatched = sum(1 for i in members if not uniq[i]["duties"])
        topics.append({"topic": k, "lines": len(members),
                       "postings": len({uniq[i]["job_key"] for i in members}),
                       "top_terms": ", ".join(terms[nmf.components_[k].argsort()[::-1][:12]]),
                       "mapped_duties": ", ".join(f"{d}({c})" for d, c in dut.most_common(4)),
                       "pct_lines_unmatched": round(100 * unmatched / max(1, len(members)), 1),
                       "examples": " || ".join(uniq[i]["line"][:180] for i in members[:5])})
    topics.sort(key=lambda r: -r["lines"])
    coverage = sum(1 for b in uniq if b["duties"]) / max(1, len(uniq))

    # ---- tools / skills / traits / KPIs / intent prevalence (core + all), company-level too
    def tally(field, keys, rows):
        out = []
        for k in keys:
            n = sum(k in (r[field] or "").split("|") for r in rows)
            lo, hi = wilson(n, len(rows))
            out.append({"item": k, "postings": n, "pct": round(100 * n / max(1, len(rows)), 1),
                        "ci95": f"{100*lo:.1f}-{100*hi:.1f}"})
        return sorted(out, key=lambda r: -r["pct"])
    tallies = {}
    for field, rxs in (("tools", T.TOOLS), ("skills", T.SKILLS), ("traits", T.TRAITS), ("kpis", T.KPIS), ("intent", T.INTENT)):
        tallies[field] = {"core": tally(field, rxs, core), "all": tally(field, rxs, usable)}

    # ---- comp + experience + seniority
    comps = [t["comp_usd_mid"] for t in core if t["comp_usd_mid"]]
    yrs = [t["years_exp"] for t in core if t["years_exp"] is not None]
    summary = {
        "method": {
            "corpus": "GTM-family postings (h1_ingest taxonomy) at companies in db/gtm_pipeline.db, plus YC WaaS roles",
            "text": "full posting text re-fetched live from Ashby / Greenhouse / Lever / YC WaaS where still open; else raw "
                    "6,000-char sweep payload; HN posts = full founder comment",
            "scope_basis": "duties are matched on the responsibilities section only (headings detected); postings with no "
                           "responsibilities heading use body lines minus company intro / benefits / process sections",
            "usable_rule": ">= 3 responsibility lines",
            "core": "archetype in " + ", ".join(sorted(T.CORE_ARCHETYPES)) + ", or a founding / first-hire title",
            "prevalence_ci": "Wilson score interval, 95%",
            "market_comparison": "GTM postings at boards with >= 80 open jobs (scaled companies), random 12,000, seed 7; "
                                 "lift and two-proportion z on the same basis (flattened full text, 6,000-char cap)",
            "company_level": "a company counts once per duty if any of its usable GTM postings asks for it",
            "nmf": f"TF-IDF (1-2 grams, min_df 8, max_df 0.25) over unique core responsibility lines; NMF k={K}, seed 7",
            "fx": "fixed rates to USD: EUR 1.08, GBP 1.27, CHF 1.13, CAD 0.72; annual base midpoints 25K-600K only",
            "limits": "regex taxonomy => precision/recall not 100% (see taxonomy_coverage); postings != demand weight; "
                      "closed postings use capped text; headcount known for a minority of companies",
        },
        "counts": {"postings": len(posts), "usable": len(usable), "core": len(core), "founding_any": len(founding),
                   "small_team_known": len(small), "companies": len(by_company), "bullets": len(bullets),
                   "core_unique_resp_lines": len(uniq), "market_postings": len(mk),
                   "text_source": dict(Counter(p["text_source"] for p in posts)),
                   "scope_basis": dict(Counter(t["scope_basis"] for t in usable))},
        "taxonomy_coverage_core_lines": round(100 * coverage, 1),
        "core_archetypes": dict(Counter(t["archetype"] for t in core).most_common()),
        "core_seniority": dict(Counter(t["seniority"] for t in core).most_common()),
        "core_comp_usd": {"n": len(comps), "p25": int(np.percentile(comps, 25)) if comps else None,
                          "median": int(np.median(comps)) if comps else None,
                          "p75": int(np.percentile(comps, 75)) if comps else None},
        "core_years_exp": {"n": len(yrs), "median": float(np.median(yrs)) if yrs else None,
                           "dist": dict(sorted(Counter(min(y, 10) for y in yrs).items()))},
        "core_bundle_size": {"median": float(np.median([int(x.sum()) for x in X])) if len(X) else None,
                             "dist": dict(sorted(bundle.items()))},
        "geo_core": dict(Counter((t["hq_country"] or "unknown") for t in core).most_common(12)),
        "tier_core": dict(Counter((t["icp_tier"] or "none") for t in core).most_common()),
    }

    def wcsv(name, rows):
        if not rows:
            return
        with open(ANA / name, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
    wcsv("duty_prevalence.csv", duty_rows)
    wcsv("archetype_duty_matrix.csv", mat)
    wcsv("lift_vs_market.csv", lift_rows)
    wcsv("founding_vs_nonfounding.csv", fvn)
    wcsv("duty_cooccurrence_core.csv", co[:300])
    wcsv("nmf_topics_core.csv", topics)
    for f, d in tallies.items():
        wcsv(f"{f}_prevalence.csv", [dict(r, scope=s) for s in ("core", "all") for r in d[s]])
    (ANA / "summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary["counts"], indent=2))
    print("coverage", summary["taxonomy_coverage_core_lines"])


if __name__ == "__main__":
    main()
