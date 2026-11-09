"""Role-scope research, full-data ML layer (replaces every sample-based step of role_scope_analyze.py).

Population, no sampling anywhere:
  ICP     = every GTM posting in research/gtm_role_scope/corpus/postings.jsonl.gz (6,183; DB jobs + YC WaaS)
  MARKET  = every other GTM-family posting with text in raw_sources/H1_job_arbitrage/ats_live/*.jsonl.gz (all boards,
            all providers), i.e. the rest of the swept job market. `scaled` flag = board with >= 80 open jobs.

Steps
  1. Parse every posting into lines (same section parser as role_scope_analyze.py). Scope lines = responsibilities,
     or body lines when a posting has no responsibilities heading.
  2. Embed every unique line (sentence-transformers/all-MiniLM-L6-v2 via fastembed, 384-d, L2-normalised; cached in .cache/).
  3. Duty labels, two independent instruments on every line:
       regex  = role_taxonomy.py patterns (transparent, keyword-based)
       ml     = one-vs-rest logistic regression on embeddings, trained on the regex labels of ICP lines (weak
                supervision), scored OUT-OF-FOLD with 5-fold CV grouped by company, so no line is scored by a model that saw
                its own company. Market lines are scored by the model fit on all ICP lines.
     Primary label = ml (p >= 0.5): it keeps paraphrases the regex misses and drops keyword hits whose meaning differs.
     Both are reported; a conclusion is only called robust when both instruments agree on its direction.
  4. Unsupervised discovery (no taxonomy): k-means on all unique ICP scope-line embeddings, k chosen on the full data by
     Davies-Bouldin (lower = better) over k in {20, 30, 40, 50, 60, 80}; clusters described by class-based TF-IDF terms,
     the 5 lines nearest the centroid, the dominant duty and the share of lines with no duty (= taxonomy gaps / boilerplate).
  5. Posting-level and company-level prevalence for every duty, segments (founding, team <= 35, US vs EU/UK, stage),
     ICP vs full market and ICP vs scaled boards, two-proportion z, and a company-cluster bootstrap (1,000 resamples of
     companies, all postings of a drawn company kept) for the demand index and its rank.
Outputs: research/gtm_role_scope/ml/*.csv|json   (see README).
"""
from __future__ import annotations

import csv
import gzip
import hashlib
import json
import math
import re
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import role_taxonomy as T  # noqa: E402
from h1_ingest import classify_job  # noqa: E402
from role_scope_analyze import archetype, scope_lines, seniority, split_sections  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
BASE = ROOT / "research" / "gtm_role_scope"
OUT = BASE / "ml"
CACHE = ROOT / ".cache" / "role_ml"
GTM = {"exact_role", "hidden_gtm", "growth", "gtm_generalist", "sales", "marketing", "revops", "partnerships",
       "presales", "devrel", "customer", "bizops_generalist"}
IDS = [d["id"] for d in T.DUTIES]
LAB = {d["id"]: d["label"] for d in T.DUTIES}
RX = [d["re"] for d in T.DUTIES]
SEED = 7


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w%$€£+#/&.-]+", " ", s.lower())).strip()


def z2(k1, n1, k2, n2):
    p = (k1 + k2) / (n1 + n2) if n1 + n2 else 0
    se = math.sqrt(p * (1 - p) * (1 / n1 + 1 / n2)) if 0 < p < 1 and n1 and n2 else 0
    return round(((k1 / n1) - (k2 / n2)) / se, 2) if se else 0.0


def wilson(k, n, z=1.96):
    if not n:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    r = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (100 * (c - r) / d, 100 * (c + r) / d)


# ------------------------------------------------------------------ 1. postings -> lines
def load_postings():
    icp = []
    for l in gzip.open(BASE / "corpus" / "postings.jsonl.gz", "rt"):
        p = json.loads(l)
        hc = p.get("headcount")
        a = archetype(p["title"])
        icp.append({"key": p["job_key"], "corpus": "icp", "company": p["company_domain"], "title": p["title"],
                    "family": p["family"], "archetype": a, "seniority": seniority(p["title"]),
                    "founding_title": bool(T.FOUNDING.search(p["title"] or "")),
                    "core": a in T.CORE_ARCHETYPES or bool(T.FOUNDING.search(p["title"] or "")),
                    "small_team": bool(hc and 0 < int(hc) <= 35), "country": p.get("hq_country") or "",
                    "stage": p.get("funding_stage") or "", "tier": p.get("icp_tier") or "", "scaled": False,
                    "text": p.get("text") or ""})
    keys = {p["key"] for p in icp}
    mk = []
    for prov in ("ashby", "greenhouse", "lever", "teamtailor", "recruitee", "breezy"):
        for line in gzip.open(ROOT / "raw_sources" / "H1_job_arbitrage" / "ats_live" / f"{prov}.jsonl.gz", "rt"):
            d = json.loads(line)
            jobs = d.get("jobs") or []
            for j in jobs:
                k = f"{prov}:{d['slug']}:{j.get('id')}"
                txt = j.get("description") or ""
                if k in keys or len(txt) < 200:
                    continue
                c = classify_job(j.get("title") or "", None, "")
                if c["family"] not in GTM:
                    continue
                a = archetype(j.get("title") or "")
                mk.append({"key": k, "corpus": "market", "company": f"{prov}:{d['slug']}", "title": j.get("title") or "",
                           "family": c["family"], "archetype": a, "seniority": seniority(j.get("title") or ""),
                           "founding_title": bool(T.FOUNDING.search(j.get("title") or "")),
                           "core": a in T.CORE_ARCHETYPES or bool(T.FOUNDING.search(j.get("title") or "")),
                           "small_team": False, "country": "", "stage": "", "tier": "", "scaled": len(jobs) >= 80,
                           "text": txt})
    return icp, mk


def to_lines(posts):
    """-> postings (with line index ranges) and a flat list of (posting_idx, section, text)."""
    lines = []
    for pi, p in enumerate(posts):
        secs = split_sections(p["text"])
        resp, req, basis = scope_lines(secs)
        p["basis"] = basis
        p["n_scope"] = len(resp)
        p["first_line"] = len(lines)
        for l in resp:
            lines.append((pi, "scope", l[:500]))
        for l in req:
            lines.append((pi, "req", l[:500]))
        p["last_line"] = len(lines)
        full = "\n".join(l for s, l in secs if s not in ("benefits", "process", "company"))
        p["intent"] = [k for k, rx in T.INTENT.items() if rx.search(full)]
        p["kpis"] = [k for k, rx in T.KPIS.items() if rx.search(full)]
        p["tools"] = [k for k, rx in T.TOOLS.items() if rx.search("\n".join(resp + req))]
        p.pop("text")
    return lines


# ------------------------------------------------------------------ 2. embeddings (cached per unique normalised line)
def embed(texts: list[str]) -> np.ndarray:
    from fastembed import TextEmbedding
    CACHE.mkdir(parents=True, exist_ok=True)
    h = hashlib.sha1("\n".join(texts).encode()).hexdigest()[:16]
    f = CACHE / f"emb_{len(texts)}_{h}.npy"
    if f.exists():
        return np.load(f)
    m = TextEmbedding("sentence-transformers/all-MiniLM-L6-v2")
    out = np.zeros((len(texts), 384), dtype=np.float32)
    step = 20000
    for s in range(0, len(texts), step):
        out[s:s + step] = np.array(list(m.embed(texts[s:s + step], batch_size=256)), dtype=np.float32)
        log(f"embedded {min(s + step, len(texts)):,}/{len(texts):,}")
    out /= np.linalg.norm(out, axis=1, keepdims=True) + 1e-9
    np.save(f, out)
    return out


# ------------------------------------------------------------------ main
def main():
    OUT.mkdir(parents=True, exist_ok=True)
    icp, mk = load_postings()
    log(f"postings icp={len(icp):,} market={len(mk):,} (scaled boards {sum(p['scaled'] for p in mk):,})")
    posts = icp + mk
    lines = to_lines(posts)
    log(f"lines {len(lines):,}")
    uniq, uidx = {}, np.zeros(len(lines), dtype=np.int64)
    utext = []
    for i, (_, _, t) in enumerate(lines):
        k = norm(t)
        if k not in uniq:
            uniq[k] = len(utext)
            utext.append(t)
        uidx[i] = uniq[k]
    log(f"unique lines {len(utext):,}")
    E = embed(utext)

    # regex labels per unique line
    R = np.zeros((len(utext), len(IDS)), dtype=bool)
    for u, t in enumerate(utext):
        for j, rx in enumerate(RX):
            if rx.search(t):
                R[u, j] = True
    log("regex labels done")

    # ---------------- 3. weak-supervised classifier, out-of-fold by company on ICP scope lines
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import roc_auc_score
    is_icp_line = np.array([posts[pi]["corpus"] == "icp" for pi, _, _ in lines])
    is_scope = np.array([s == "scope" for _, s, _ in lines])
    train_rows = np.where(is_icp_line & is_scope)[0]
    tr_u = np.unique(uidx[train_rows])                       # unique ICP scope lines
    comp_of_u = {}
    for i in train_rows:
        comp_of_u.setdefault(uidx[i], posts[lines[i][0]]["company"])
    comps = sorted(set(comp_of_u.values()))
    rng = np.random.default_rng(SEED)
    fold_of_comp = dict(zip(comps, rng.integers(0, 5, len(comps))))
    fold = np.array([fold_of_comp[comp_of_u[u]] for u in tr_u])
    Xtr, Ytr = E[tr_u], R[tr_u]
    P_oof = np.zeros((len(tr_u), len(IDS)), dtype=np.float32)
    P_all = np.zeros((len(utext), len(IDS)), dtype=np.float32)
    metrics = []
    for j, did in enumerate(IDS):
        y = Ytr[:, j]
        if y.sum() < 20:
            metrics.append({"duty_id": did, "duty": LAB[did], "regex_pos_lines": int(y.sum()), "note": "too few seeds"})
            continue
        for f in range(5):
            m = LogisticRegression(C=2.0, max_iter=400, class_weight=None)
            m.fit(Xtr[fold != f], y[fold != f])
            P_oof[fold == f, j] = m.predict_proba(Xtr[fold == f])[:, 1]
        m = LogisticRegression(C=2.0, max_iter=400).fit(Xtr, y)
        P_all[:, j] = m.predict_proba(E)[:, 1]
        pred = P_oof[:, j] >= 0.5
        tp, fp, fn = int((pred & y).sum()), int((pred & ~y).sum()), int((~pred & y).sum())
        metrics.append({"duty_id": did, "duty": LAB[did], "regex_pos_lines": int(y.sum()),
                        "ml_pos_lines": int(pred.sum()), "agree_both": tp, "ml_only": fp, "regex_only": fn,
                        "auc_vs_regex": round(float(roc_auc_score(y, P_oof[:, j])), 3),
                        "precision_vs_regex": round(tp / max(1, tp + fp), 3), "recall_vs_regex": round(tp / max(1, tp + fn), 3)})
        log(f"clf {did} seeds={int(y.sum())} auc={metrics[-1]['auc_vs_regex']}")
    P_all[tr_u] = P_oof                                       # ICP scope lines keep their out-of-fold score
    ML = P_all >= 0.5
    with open(OUT / "classifier_agreement.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["duty_id", "duty", "regex_pos_lines", "ml_pos_lines", "agree_both", "ml_only",
                                           "regex_only", "auc_vs_regex", "precision_vs_regex", "recall_vs_regex", "note"])
        w.writeheader()
        w.writerows(metrics)

    # disagreement examples (all of them are written; the CSV is the audit trail)
    with gzip.open(OUT / "label_disagreements.csv.gz", "wt", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["duty_id", "kind", "p_ml", "line"])
        for j, did in enumerate(IDS):
            for k, u in enumerate(tr_u):
                r, p = R[u, j], P_oof[k, j]
                if r and p < 0.5:
                    w.writerow([did, "regex_only", round(float(p), 3), utext[u]])
                elif p >= 0.5 and not r:
                    w.writerow([did, "ml_only", round(float(p), 3), utext[u]])

    # ---------------- 4. unsupervised discovery on all unique ICP scope lines
    from sklearn.cluster import KMeans
    from sklearn.metrics import calinski_harabasz_score, davies_bouldin_score
    Xc = E[tr_u]
    grid = []
    best = None
    for k in (20, 30, 40, 50, 60, 80):
        km = KMeans(n_clusters=k, n_init=4, random_state=SEED).fit(Xc)
        db = davies_bouldin_score(Xc, km.labels_)
        ch = calinski_harabasz_score(Xc, km.labels_)
        grid.append({"k": k, "davies_bouldin": round(db, 4), "calinski_harabasz": round(ch, 1), "inertia": round(km.inertia_, 1)})
        log(f"kmeans k={k} DB={db:.3f} CH={ch:.0f}")
        if best is None or db < best[1]:
            best = (k, db, km)
    k, _, km = best
    from sklearn.feature_extraction.text import TfidfVectorizer
    docs = defaultdict(list)
    for i, u in enumerate(tr_u):
        docs[km.labels_[i]].append(utext[u])
    vec = TfidfVectorizer(stop_words="english", ngram_range=(1, 2), min_df=3, sublinear_tf=True)
    M = vec.fit_transform([" ".join(docs[c]) for c in range(k)])
    terms = np.array(vec.get_feature_names_out())
    post_of_u = defaultdict(set)
    for i in train_rows:
        post_of_u[uidx[i]].add(lines[i][0])
    topics = []
    for c in range(k):
        mem = np.where(km.labels_ == c)[0]
        d = Xc[mem] @ km.cluster_centers_[c]
        near = mem[np.argsort(-d)[:5]]
        dut = Counter(IDS[j] for i in mem for j in np.where(ML[tr_u[i]])[0])
        none = float(np.mean(~ML[tr_u[mem]].any(1)))
        pst = set().union(*(post_of_u[tr_u[i]] for i in mem))
        topics.append({"cluster": c, "lines": len(mem), "postings": len(pst),
                       "pct_icp_postings": round(100 * len(pst) / len(icp), 1),
                       "terms": ", ".join(terms[M[c].toarray()[0].argsort()[::-1][:10]]),
                       "top_duties": ", ".join(f"{a}({b})" for a, b in dut.most_common(3)),
                       "pct_lines_no_duty": round(100 * none, 1),
                       "nearest_lines": " || ".join(utext[tr_u[i]][:160] for i in near)})
    topics.sort(key=lambda r: -r["postings"])
    for f, rows in (("kmeans_grid.csv", grid), ("discovered_clusters.csv", topics)):
        with open(OUT / f, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
    gap_lines = sum(t["lines"] for t in topics if t["pct_lines_no_duty"] >= 60)

    # ---------------- 5. posting-level duty sets (ML primary, regex sensitivity)
    for p in posts:
        rng_ = range(p["first_line"], p["last_line"])
        scope_u = [uidx[i] for i in rng_ if lines[i][1] == "scope"]
        p["ml"] = set(IDS[j] for j in np.where(ML[scope_u].any(0))[0]) if scope_u else set()
        p["rx"] = set(IDS[j] for j in np.where(R[scope_u].any(0))[0]) if scope_u else set()
    I = [p for p in icp if p["n_scope"] >= 3]
    Ic = [p for p in I if p["core"]]
    Mk = [p for p in mk if p["n_scope"] >= 3]
    Ms = [p for p in Mk if p["scaled"]]
    log(f"usable icp={len(I):,} core={len(Ic):,} market={len(Mk):,} scaled={len(Ms):,}")

    def pct(rows, did, inst="ml"):
        return 100 * sum(did in p[inst] for p in rows) / max(1, len(rows))

    def comp_pct(rows, did, inst="ml"):
        by = defaultdict(list)
        for p in rows:
            by[p["company"]].append(did in p[inst])
        return 100 * sum(any(v) for v in by.values()) / max(1, len(by))

    def comp_weighted(rows, did, inst="ml"):     # each company weight 1, split across its postings
        by = defaultdict(list)
        for p in rows:
            by[p["company"]].append(did in p[inst])
        return 100 * float(np.mean([np.mean(v) for v in by.values()])) if by else 0

    seg = {
        "founding": ([p for p in I if p["founding_title"] or "First GTM / sales / growth hire" in p["intent"]],
                     [p for p in I if not (p["founding_title"] or "First GTM / sales / growth hire" in p["intent"])]),
        "small_team": ([p for p in Ic if p["small_team"]], [p for p in Ic if not p["small_team"]]),
        "us_vs_euuk": ([p for p in Ic if p["country"] == "US"], [p for p in Ic if p["country"] not in ("", "US")]),
        "seed_vs_seriesA": ([p for p in Ic if p["stage"] in ("pre_seed", "seed")], [p for p in Ic if p["stage"] == "series_a"]),
        "icp_vs_market": (I, Mk),
        "icp_core_vs_market_core": (Ic, [p for p in Mk if p["core"]]),
        "icp_vs_scaled": (I, Ms),
    }
    rows = []
    for did in IDS:
        r = {"duty_id": did, "duty": LAB[did], "stage": next(d["stage"] for d in T.DUTIES if d["id"] == did),
             "pct_core_ml": round(pct(Ic, did), 1), "pct_core_regex": round(pct(Ic, did, "rx"), 1),
             "ci95_core_ml": "%.1f-%.1f" % wilson(sum(did in p["ml"] for p in Ic), len(Ic)),
             "pct_all_ml": round(pct(I, did), 1), "pct_companies_ml": round(comp_pct(I, did), 1),
             "pct_company_weighted_ml": round(comp_weighted(I, did), 1)}
        for name, (a, b) in seg.items():
            for inst in ("ml", "rx"):
                ka, kb = sum(did in p[inst] for p in a), sum(did in p[inst] for p in b)
                r[f"{name}_a_{inst}"] = round(100 * ka / max(1, len(a)), 1)
                r[f"{name}_b_{inst}"] = round(100 * kb / max(1, len(b)), 1)
                r[f"{name}_z_{inst}"] = z2(ka, len(a), kb, len(b))
            r[f"{name}_robust"] = (np.sign(r[f"{name}_z_ml"]) == np.sign(r[f"{name}_z_rx"])
                                   and abs(r[f"{name}_z_ml"]) >= 3 and abs(r[f"{name}_z_rx"]) >= 3)
        rows.append(r)

    # demand index (same definition as role_scope_synthesize) + company-cluster bootstrap of value and rank
    fset = seg["founding"][0]

    def demand(core_rows, all_rows, f_rows):
        pc = np.array([pct(core_rows, d) for d in IDS])
        pco = np.array([comp_pct(all_rows, d) for d in IDS])
        pf = np.array([pct(f_rows, d) for d in IDS])
        return 100 * (pc / pc.max() + pco / pco.max() + pf / pf.max()) / 3

    di = demand(Ic, I, fset)
    by_comp = defaultdict(list)
    for p in I:
        by_comp[p["company"]].append(p)
    cks = list(by_comp)
    # precompute per-company duty counts for a fast bootstrap
    cidx = {c: i for i, c in enumerate(cks)}
    nC, nD = len(cks), len(IDS)
    core_cnt = np.zeros((nC, nD)); core_n = np.zeros(nC)
    f_cnt = np.zeros((nC, nD)); f_n = np.zeros(nC)
    any_d = np.zeros((nC, nD))
    fkeys = {p["key"] for p in fset}
    for p in I:
        ci = cidx[p["company"]]
        v = np.array([d in p["ml"] for d in IDS], dtype=float)
        any_d[ci] = np.maximum(any_d[ci], v)
        if p["core"]:
            core_cnt[ci] += v; core_n[ci] += 1
        if p["key"] in fkeys:
            f_cnt[ci] += v; f_n[ci] += 1
    B = 1000
    rng = np.random.default_rng(SEED)
    boot = np.zeros((B, nD)); ranks = np.zeros((B, nD))
    for b in range(B):
        w = np.bincount(rng.integers(0, nC, nC), minlength=nC).astype(float)
        pc = (w @ core_cnt) / max(1, w @ core_n)
        pco = (w @ any_d) / max(1, w.sum())
        pf = (w @ f_cnt) / max(1, w @ f_n)
        v = 100 * (pc / pc.max() + pco / pco.max() + pf / pf.max()) / 3
        boot[b] = v
        ranks[b] = (-v).argsort().argsort() + 1
    for j, r in enumerate(rows):
        r["demand_index_ml"] = round(float(di[j]), 1)
        r["demand_ci95"] = "%.1f-%.1f" % tuple(np.percentile(boot[:, j], [2.5, 97.5]))
        r["rank"] = int((-di).argsort().argsort()[j] + 1)
        r["rank_ci95"] = "%d-%d" % tuple(np.percentile(ranks[:, j], [2.5, 97.5]).astype(int))
        r["p_must"] = round(float((boot[:, j] >= 55).mean()), 3)
        r["priority_ml"] = "Must" if di[j] >= 55 else ("Should" if di[j] >= 30 else "Could")
    # regex-vs-ml rank agreement
    from scipy.stats import spearmanr
    rho = spearmanr([r["pct_core_ml"] for r in rows], [r["pct_core_regex"] for r in rows]).correlation
    with open(OUT / "duty_stats_ml.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)

    # posting-level table (all postings, both corpora) for any later cut
    with gzip.open(OUT / "postings_labels.csv.gz", "wt", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["key", "corpus", "company", "title", "family", "archetype", "core", "founding_title", "small_team",
                    "country", "stage", "tier", "scaled", "n_scope", "basis", "duties_ml", "duties_regex", "intent", "kpis", "tools"])
        for p in posts:
            w.writerow([p["key"], p["corpus"], p["company"], p["title"], p["family"], p["archetype"], p["core"], p["founding_title"],
                        p["small_team"], p["country"], p["stage"], p["tier"], p["scaled"], p["n_scope"], p["basis"],
                        "|".join(sorted(p["ml"])), "|".join(sorted(p["rx"])), "|".join(p["intent"]), "|".join(p["kpis"]),
                        "|".join(p["tools"])])

    # intent / KPI / tools: full ICP-core vs full market-core
    mcore = [p for p in Mk if p["core"]]
    req = []
    for field, keys in (("intent", T.INTENT), ("kpis", T.KPIS), ("tools", T.TOOLS)):
        for k in keys:
            a = sum(k in p[field] for p in Ic); b = sum(k in p[field] for p in mcore)
            req.append({"field": field, "item": k, "pct_icp_core": round(100 * a / len(Ic), 1),
                        "ci95": "%.1f-%.1f" % wilson(a, len(Ic)), "pct_market_core": round(100 * b / max(1, len(mcore)), 1),
                        "z": z2(a, len(Ic), b, len(mcore))})
    with open(OUT / "intent_kpi_tools_ml.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(req[0]))
        w.writeheader()
        w.writerows(req)

    summary = {
        "population": {"icp_postings": len(icp), "market_postings": len(mk), "market_scaled_postings": sum(p["scaled"] for p in mk),
                       "market_boards": len({p["company"] for p in mk}), "lines": len(lines), "unique_lines": len(utext),
                       "icp_scope_unique_lines": int(len(tr_u)), "usable_icp": len(I), "usable_icp_core": len(Ic),
                       "usable_market": len(Mk), "usable_market_core": len(mcore), "usable_scaled": len(Ms),
                       "icp_companies": len(by_comp), "segments_n": {k: [len(a), len(b)] for k, (a, b) in seg.items()}},
        "classifier": {"model": "sentence-transformers/all-MiniLM-L6-v2 + one-vs-rest logistic regression (C=2)",
                       "cv": "5-fold grouped by company, out-of-fold scores for every ICP scope line",
                       "median_auc_vs_regex": float(np.median([m["auc_vs_regex"] for m in metrics if "auc_vs_regex" in m])),
                       "spearman_core_prevalence_ml_vs_regex": round(float(rho), 3)},
        "discovery": {"k_selected": k, "grid": grid, "lines_in_low_coverage_clusters": gap_lines,
                      "pct_scope_lines_with_ml_duty": round(100 * float(ML[tr_u].any(1).mean()), 1),
                      "pct_scope_lines_with_regex_duty": round(100 * float(R[tr_u].any(1).mean()), 1)},
        "bootstrap": {"resamples": B, "unit": "company (all postings of a drawn company kept)", "seed": SEED},
    }
    (OUT / "summary_ml.json").write_text(json.dumps(summary, indent=1))
    log(json.dumps(summary, indent=1)[:3000])


if __name__ == "__main__":
    main()
