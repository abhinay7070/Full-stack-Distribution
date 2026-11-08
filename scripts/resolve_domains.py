"""Entity -> company domain resolution with evidence (shared by Form D, ATS boards, news, HN hiring).

resolve(entities) where each entity = {key, name, hints: {city, state, country, persons[], text, domains[]}}
returns {key: {domain, score, confidence, evidence, status}} and caches every decision in the DB table
`resolutions` (re-runs are deterministic; delete a row to force re-resolution).

Method
  1 Candidate domains from the brand (legal suffixes stripped): core / core-without-generic-suffix x
    TLDs (.com .ai .io .co .dev .app .so .tech .xyz .inc .us .hq) + prefixes (get try use join go with hey)
    + suffixes (hq app ai labs). Hint domains (from a source) are tried first.
  2 DNS (dns.google DoH, A/CNAME) prunes non-existent names.
  3 Homepage fetch (cached; reused later by site_scan) and scoring:
      +3 brand in <title>/og:site_name     +1 brand in visible text (>=2x)     +3 legal name ("X Inc"/"X, Inc.")
      +2 a related person's surname (>=4 letters, uncommon) on the page       +1 issuer city on the page
      +1 hint domain (source-published)    -9 parked / for-sale page          -3 redirect to another site
      +3 issuer phone number on the page   +2 issuer street address on the page
      +1 public company directory (Clearbit autocomplete) lists this domain under the same brand
    Directory domains are only candidates: they still have to pass the page scoring above.
  4 Accept: score >= 4 (>= 6 when the brand is a common English word, wordfreq zipf >= 3.0).
    confidence high >= 6, medium 4-5. Ties between candidates -> 'ambiguous' (not accepted).
"""
from __future__ import annotations

import html
import json
import re
import sys
from pathlib import Path

from wordfreq import zipf_frequency

sys.path.insert(0, str(Path(__file__).resolve().parent))
import http_fetch as hf  # noqa: E402
import pipeline_db as pdb  # noqa: E402

LEGAL = re.compile(r"[,.]?\s+\b(inc|incorporated|corp|corporation|co|company|llc|l\.l\.c|ltd|limited|pbc|plc|gmbh|"
                   r"sas|sa|bv|b\.v|ab|oy|ag|srl|s\.r\.l|sl|aps|as)\b\.?", re.I)
GENERIC_SUFFIX = re.compile(r"\b(technologies|technology|tech|labs?|ai|a\.i\.|software|systems|solutions|group|"
                            r"holdings|hq|app|io|platforms?|digital|global|international|usa|us|intelligence|"
                            r"robotics|health|bio|data|analytics|studio|studios|ventures|networks?|cloud)\b", re.I)
TLDS = ["com", "ai", "io", "co", "dev", "app", "so", "tech", "xyz", "inc", "us", "hq", "sh", "run", "build", "health"]
PREFIX = ["get", "try", "use", "join", "go", "with", "hey", "meet"]
SUFFIX = ["hq", "app", "ai", "labs", "inc", "tech"]
PARKED = re.compile(r"(domain (name )?(is )?(for sale|available)|buy this domain|this domain may be for sale|"
                    r"hugedomains|dan\.com|afternic|sedo\.com|sedoparking|parkingcrew|bodis\.com|"
                    r"domain has expired|godaddy\.com/domainsearch|is parked|future home of|web hosting by|"
                    r"under construction|namecheap\.com/domains|squarespace - claim this domain)", re.I)
TAG = re.compile(r"<script.*?</script>|<style.*?</style>|<[^>]+>", re.S | re.I)


def alnum(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def brand_cores(name: str) -> tuple[str, str, str]:
    """(legal_core_words, core_full, core_short) e.g. 'Urza Systems, Inc.' -> ('urza systems','urzasystems','urza')."""
    n = html.unescape(name or "")
    n = re.sub(r"\(.*?\)|\bd/?b/?a\b.*$|\bf/?k/?a\b.*$", " ", n, flags=re.I)
    n = re.sub(r"[™®]", "", n)
    words = LEGAL.sub(" ", n).strip(" ,.-")
    words = re.sub(r"\s+", " ", words).strip()
    full = alnum(words)
    short = alnum(GENERIC_SUFFIX.sub(" ", words)) or full
    return words.lower(), full, short


def directory_domains(name: str) -> list[str]:
    """Domains a public company directory lists for this exact brand (Clearbit autocomplete, no key)."""
    words, full, short = brand_cores(name)
    if len(short) < 3:
        return []
    from urllib.parse import quote
    r = hf.fetch(f"https://autocomplete.clearbit.com/v1/companies/suggest?query={quote(words or name)}", timeout=15,
                 retries=2)
    try:
        rows = r.json() if r.status == 200 else []
    except ValueError:
        return []
    out = []
    for x in rows if isinstance(rows, list) else []:
        n = alnum(brand_cores(x.get("name") or "")[1])
        if x.get("domain") and n and n in (full, short):
            out.append(pdb.normalize_domain(x["domain"]))
    return [d for d in dict.fromkeys(out) if d]


def candidates(name: str, hint_domains: list[str] | None = None) -> tuple[list[str], list[str]]:
    """(primary, extended) candidate lists; extended is only tried when primary finds nothing acceptable."""
    words, full, short = brand_cores(name)
    prim = [d for d in (hint_domains or []) if d]
    ext = []
    for core in dict.fromkeys([short, full]):
        if len(core) < 3:
            continue
        prim += [f"{core}.{t}" for t in ("com", "ai", "io", "co", "dev", "app", "so")]
        prim += [f"{p}{core}.com" for p in ("get", "try", "use", "join")] + [f"{core}hq.com"]
        ext += [f"{core}.{t}" for t in TLDS if t not in ("com", "ai", "io", "co", "dev", "app", "so")]
        ext += [f"{p}{core}.com" for p in PREFIX if p not in ("get", "try", "use", "join")]
        ext += [f"{core}{s}.com" for s in SUFFIX if s != "hq"] + [f"{p}{core}.ai" for p in ("get", "try", "use")]
    ws = re.findall(r"[a-z0-9]+", words)
    if len(ws) >= 2:
        ext += [f"{'-'.join(ws)}.com", f"{'-'.join(ws)}.ai"]
    prim = list(dict.fromkeys(prim))
    return prim, [d for d in dict.fromkeys(ext) if d not in prim]


TECHY = re.compile(r"\b(ai|software|platform|api|saas|automat\w*|agents?|data|cloud|developers?|enterprise|"
                   r"workflow|llm|model|analytics|infrastructure|security|teams?)\b", re.I)


def dns_exists(domain: str) -> bool:
    r = hf.fetch(f"https://dns.google/resolve?name={domain}&type=A", timeout=10, retries=2)
    if r.status != 200:
        return False
    try:
        d = r.json()
    except ValueError:
        return False
    return d.get("Status") == 0 and bool(d.get("Answer"))


PAGE_TIMEOUT = int(__import__("os").environ.get("RESOLVE_PAGE_TIMEOUT", "10"))  # slow-site retry pass: 25
PAGE_RETRIES = int(__import__("os").environ.get("RESOLVE_PAGE_RETRIES", "0"))


def page(domain: str) -> dict:
    r = hf.fetch(f"https://{domain}/", timeout=PAGE_TIMEOUT, retries=PAGE_RETRIES, max_bytes=1_500_000)
    if 400 <= r.status or (r.status <= 0 and "Timeout" not in (r.text or "")):
        r2 = hf.fetch(f"https://www.{domain}/", timeout=PAGE_TIMEOUT, retries=PAGE_RETRIES, max_bytes=1_500_000)
        if 0 < r2.status < 400:
            r = r2
    t = (r.text or "")[:600_000]
    title = " ".join(re.findall(r"<title[^>]*>(.*?)</title>", t, re.S | re.I)[:1])
    og = " ".join(re.findall(r'<meta[^>]+property=["\']og:(?:site_name|title)["\'][^>]+content=["\']([^"\']+)', t, re.I))
    desc = " ".join(re.findall(r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']+)', t, re.I))
    text = html.unescape(TAG.sub(" ", t))
    text = re.sub(r"\s+", " ", text)
    final = pdb.normalize_domain(r.final_url) if r.final_url else None
    return {"status": r.status, "title": html.unescape(f"{title} {og}").strip()[:300], "desc": html.unescape(desc)[:400],
            "text": text[:60000], "final": final, "raw": t}


def score_page(name: str, domain: str, p: dict, hints: dict) -> tuple[int, list[str]]:
    words, full, short = brand_cores(name)
    ev, s = [], 0
    if not (0 < p["status"] < 400):
        return -99, [f"http {p['status']}"]
    blob = f"{p['title']} {p['desc']} {p['text']}"
    if PARKED.search(blob[:20000]) and len(p["text"]) < 6000:
        return -9, ["parked/for-sale"]
    if p["final"] and p["final"] != domain and p["final"] not in (hints.get("domains") or []):
        s -= 3
        ev.append(f"redirects to {p['final']}")
    at = alnum(p["title"])
    if short and (short in at or full in at):
        s += 3
        ev.append("brand in title")
    ab = alnum(p["text"])
    if short and ab.count(short) >= 2:
        s += 1
        ev.append("brand in text")
    if words and re.search(rf"\b{re.escape(words)}\s*,?\s*(inc|corp|corporation|llc|ltd|limited|pbc)\b\.?", blob, re.I):
        s += 3
        ev.append("legal name on page")
    for per in hints.get("persons") or []:
        ln = (per or "").strip().lower()
        if len(ln) >= 4 and zipf_frequency(ln, "en") < 3.5 and re.search(rf"\b{re.escape(ln)}\b", blob.lower()):
            s += 2
            ev.append(f"person '{ln}'")
            break
    city = (hints.get("city") or "").strip().lower()
    if len(city) >= 4 and city in blob.lower():
        s += 1
        ev.append(f"city '{city}'")
    if domain in (hints.get("domains_src", hints.get("domains")) or []):
        s += 1
        ev.append("source-published domain")
    ph = re.sub(r"\D", "", hints.get("phone") or "")[-10:]
    if len(ph) == 10 and ph in re.sub(r"\D", "", blob):
        s += 3
        ev.append("issuer phone on page")
    st = re.match(r"\s*(\d+)\s+([A-Za-z]{4,})", hints.get("street") or "")
    if st and re.search(rf"\b{st.group(1)}\s+{re.escape(st.group(2))}", blob, re.I):
        s += 2
        ev.append("issuer street on page")
    if domain in (hints.get("directory") or []) and domain not in (hints.get("domains_src") or []):
        lab = alnum(domain.split(".")[0])
        if lab in (full, short):
            s += 1
            ev.append("directory listing")
        elif not any(x.startswith(("person", "city", "issuer", "source-published", "legal name")) for x in ev):
            s -= 2  # same brand, different label (e.g. brandindustries.com): needs independent corroboration
            ev.append("directory-only, uncorroborated")
    if hints.get("tech") and len(TECHY.findall(blob[:20000])) >= 3:
        s += 1
        ev.append("tech product page")
    return s, ev


def _db(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS resolutions (
        entity_key TEXT PRIMARY KEY, name TEXT, domain TEXT, score INTEGER, confidence TEXT, status TEXT,
        evidence TEXT, tried INTEGER, resolved_at TEXT)""")


def resolve(entities: list[dict], workers: int = 24, refresh: bool = False, directory: bool = True) -> dict[str, dict]:
    conn = pdb.connect()
    _db(conn)
    done = {} if refresh else {r["entity_key"]: dict(r) for r in conn.execute("SELECT * FROM resolutions")}
    todo = [e for e in entities if e["key"] not in done]
    print(f"resolve: {len(entities)} entities, {len(todo)} new", file=sys.stderr)

    from concurrent.futures import ThreadPoolExecutor

    def one(e):
        hints = dict(e.get("hints") or {})
        if directory:
            hints["directory"] = directory_domains(e["name"])
        hints["domains_src"] = list(hints.get("domains") or [])
        prim, ext = candidates(e["name"], hints["domains_src"] + (hints.get("directory") or []))
        _, full, short = brand_cores(e["name"])
        common = zipf_frequency(short, "en") >= 3.0 or len(short) <= 3
        need = 6 if common else 4
        best_by_site: dict[str, tuple] = {}
        tried = 0
        for stage in (prim, ext):
            hf.fetch_many([f"https://dns.google/resolve?name={d}&type=A" for d in stage], workers=8, timeout=10,
                          retries=2)  # warm the DNS cache in parallel; dns_exists below reads it
            for d in stage:
                tried += 1
                if not dns_exists(d):
                    continue
                p = page(d)
                site = p["final"] if p["final"] and 0 < p["status"] < 400 else pdb.normalize_domain(d)
                sc, ev = score_page(e["name"], site if site else d, p, hints)
                if site and (site not in best_by_site or sc > best_by_site[site][0]):
                    best_by_site[site] = (sc, ev, d)
                if sc >= need + 2:
                    break
            if any(v[0] >= need for v in best_by_site.values()):
                break
        scored = sorted(((v[0], k, v[1]) for k, v in best_by_site.items()), key=lambda x: -x[0])
        if not scored or scored[0][0] < need:
            best = scored[0] if scored else (None, None, [])
            return e, {"domain": None, "score": best[0], "confidence": None,
                       "status": "unresolved" if scored else "no_dns_candidate",
                       "evidence": json.dumps({"best": best[1], "why": best[2]}), "tried": tried}
        if len(scored) > 1 and scored[1][0] == scored[0][0]:
            # prefer the site whose title carries the full legal core (e.g. "coval technologies")
            return e, {"domain": None, "score": scored[0][0], "confidence": None, "status": "ambiguous",
                       "evidence": json.dumps({"tie": [scored[0][1], scored[1][1]], "why": scored[0][2]}),
                       "tried": tried}
        sc, d, ev = scored[0]
        return e, {"domain": d, "score": sc, "confidence": "high" if sc >= need + 2 else "medium",
                   "status": "resolved", "evidence": json.dumps(ev), "tried": tried}

    n = 0
    with ThreadPoolExecutor(workers) as ex:
        for e, res in ex.map(one, todo):
            conn.execute("INSERT OR REPLACE INTO resolutions VALUES (?,?,?,?,?,?,?,?,?)",
                         (e["key"], e["name"], res["domain"], res["score"], res["confidence"], res["status"],
                          res["evidence"], res["tried"], pdb.utcnow()))
            done[e["key"]] = {"entity_key": e["key"], "name": e["name"], **res}
            n += 1
            conn.commit()  # short write transactions: parallel jobs never wait long for the lock
            if n % 200 == 0:
                print(f"  resolved {n}/{len(todo)}", file=sys.stderr)
    conn.commit()
    return {e["key"]: done[e["key"]] for e in entities}
