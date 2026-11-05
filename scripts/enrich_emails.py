"""Email enrichment: one outreach email per company, from public evidence already in raw_sources/.

Usage: python3 scripts/enrich_emails.py      (then python3 scripts/build_master.py)

No paid finder (Clay, Apollo, Hunter, ...) is reachable from this environment, and DNS/SMTP are
blocked, so nothing here is mailbox-verified. Instead every snapshot in raw_sources/ is scanned for
email addresses the companies published themselves (YC Launch posts, company descriptions, founder
bios, Q&A, job posts). Only addresses on the company's own domain are kept.

Pick order (first hit wins; `email_confidence` says which rule fired):
  1 published_ceo            CEO's own address appears in a public source
  2 published_founder        another founder's own address appears
  3 derived_from_pattern     CEO address built from the naming pattern of a published personal
                             address on the same domain (e.g. alex@ seen -> first@ -> ben@)
  4 published_founders_inbox founders@ / founder@ / ceo@ published by the company
  5 published_generic        hi@ / hello@ / team@ / contact@ ... published by the company
  6 guess_first_at_domain    no evidence: {ceo_first}@domain, the most common pattern at
                             <35-person startups. UNVERIFIED - run through a verifier before sending.
`email_alt` keeps the next-best different address. Rows with source_type 'clay' (clay_bridge.py import)
or 'manual' (typed into the workbook's yellow `email` cell) are never overwritten.
"""
from __future__ import annotations

import csv
import gzip
import io
import json
import re
import sys
import unicodedata
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pipeline_db as pdb  # noqa: E402

RAW = pdb.ROOT / "raw_sources"
REPORT = RAW / "email_enrichment_report.json"
EMAIL = re.compile(r"(?<![\w.+-])([A-Za-z0-9][A-Za-z0-9._%+-]{0,63})@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,10})")
FOUNDERS_INBOX = {"founders", "founder", "ceo", "cofounders", "foundingteam", "founding"}
GENERIC = {"hi", "hello", "hey", "team", "contact", "info", "sales", "partners", "partnerships", "gtm", "demo",
           "start", "general", "inquiries", "enquiries", "biz", "business", "us", "talk", "connect", "help",
           "support", "feedback", "mailbox", "ask", "outreach", "engage", "yc", "launchyc", "launch", "pilot",
           "pilots", "founding-team"}
GENERIC_RANK = {"founders": 0}  # lower = better inside generic: listed order
GENERIC_ORDER = ["hi", "hello", "hey", "team", "contact", "sales", "partners", "partnerships", "gtm", "demo",
                 "talk", "connect", "general", "inquiries", "enquiries", "biz", "business", "us", "start", "info",
                 "feedback", "help", "support"]
EXCLUDE = re.compile(r"^(jobs?|careers?|hiring|recruit\w*|talent|apply|privacy|security|legal|billing|invoice\w*|"
                     r"no-?reply|do-?not-?reply|abuse|admin|postmaster|webmaster|press|media|investors?|ir|dpo|"
                     r"compliance|unsubscribe|bounce\w*|mailer-daemon|notifications?|alerts?|test|example|email|"
                     r"name|you|your\w*|user|firstname|first\.last|john|jane)$", re.I)
NAME_PARTS_STRIP = re.compile(r"[^a-z]")


def fold(s: str, german: bool = False) -> str:
    s = (s or "").lower()
    if german:
        s = s.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()


def name_parts(full: str, german: bool = False) -> tuple[str, str]:
    full = re.sub(r"\(.*?\)", " ", full or "")
    first_raw = fold(full, german).split()[0] if full.split() else ""
    toks = [NAME_PARTS_STRIP.sub("", t) for t in fold(full, german).split()]
    toks = [t for t in toks if t and t not in {"dr", "mr", "ms", "mrs", "jr", "sr", "ii", "iii", "phd"}]
    if not toks:
        return "", ""
    first = NAME_PARTS_STRIP.sub("", first_raw) or toks[0]  # "Pierre-Louis" -> "pierrelouis"
    return first, toks[-1] if len(toks) > 1 else ""


PATTERNS = {  # name -> local part
    "first": lambda f, l: f,
    "first.last": lambda f, l: f"{f}.{l}" if l else "",
    "firstlast": lambda f, l: f"{f}{l}" if l else "",
    "flast": lambda f, l: f"{f[0]}{l}" if l else "",
    "first_last": lambda f, l: f"{f}_{l}" if l else "",
    "f.last": lambda f, l: f"{f[0]}.{l}" if l else "",
    "firstl": lambda f, l: f"{f}{l[0]}" if l else "",
    "last": lambda f, l: l,
}


def match_person(local: str, founders: list[dict]) -> tuple[dict | None, str | None]:
    """Exact naming pattern first (reusable to derive the CEO's address), then nickname = 3+ letter
    prefix of exactly one founder's first name (cam@ -> Cameron; person-specific, not reused)."""
    lc = local.lower()
    for german in (False, True):
        for fd in founders:
            f, l = name_parts(fd["full_name"], german)
            if not f:
                continue
            for pat, fn in PATTERNS.items():
                v = fn(f, l)
                if v and lc == v and not (pat == "last" and len(v) < 3):
                    return fd, pat
    if len(lc) >= 3 and lc.isalpha():
        def given(fd):  # every name token except the surname ("Ai Daniil Bekirov" -> ai, daniil)
            toks = [NAME_PARTS_STRIP.sub("", t) for t in fold(re.sub(r"\(.*?\)", " ", fd["full_name"])).split()]
            return toks[:-1] or toks
        hits = [fd for fd in founders if any(t.startswith(lc) for t in given(fd))]
        if len(hits) == 1:
            return hits[0], "nickname"
    return None, None


def iter_sources():
    """Yield (text, source_label, source_url, yc_website) for every string in every raw snapshot."""
    for p in sorted(RAW.rglob("*.gz")):
        rel = str(p.relative_to(pdb.ROOT))
        raw = gzip.open(p).read().decode("utf-8", "ignore")
        if "@" not in raw:
            continue
        try:
            data = json.loads(raw)
        except ValueError:
            data = None
        if data is None:  # CSV or other text: scan whole file
            yield raw, rel, None, None
            continue
        recs = data if isinstance(data, list) else data.get("companies") if isinstance(data, dict) else None
        if not isinstance(recs, list):
            yield raw, rel, None, None
            continue
        for r in recs:
            if not isinstance(r, dict):
                continue
            site, page = r.get("website"), r.get("url") if isinstance(r.get("url"), str) else None
            for lch in r.get("launches") or []:
                if isinstance(lch, dict) and "@" in (lch.get("body") or ""):
                    yield lch["body"], f"{rel}:launch", lch.get("url") or page, site
            for k, v in r.items():
                if k == "launches":
                    continue
                s = json.dumps(v, ensure_ascii=False) if not isinstance(v, str) else v
                if "@" in s:
                    yield s, f"{rel}:{k}", page, site


def main() -> None:
    conn = pdb.connect()
    conn.executescript((pdb.ROOT / "db" / "schema.sql").read_text())
    live = {r["company_domain"] for r in conn.execute("SELECT company_domain FROM companies WHERE status!='disqualified'")}
    founders = defaultdict(list)
    for f in conn.execute("SELECT * FROM founders ORDER BY founder_id"):
        founders[f["company_domain"]].append(dict(f))
    manual = {r["company_domain"] for r in conn.execute("SELECT company_domain FROM company_emails WHERE source_type!='pipeline'")}

    found: dict[str, dict[str, dict]] = defaultdict(dict)  # domain -> email -> evidence
    scanned = 0
    for text, label, url, site in iter_sources():
        scanned += 1
        for m in EMAIL.finditer(text):
            local, host = m.group(1).strip("._-").split("+")[0], m.group(2).lower().rstrip(".")
            dom = pdb.normalize_domain(host)
            if dom not in live or EXCLUDE.match(local) or "%" in local or len(local) < 2:
                continue
            em = f"{local.lower()}@{host}"
            if em not in found[dom]:
                a = max(0, m.start() - 90)
                found[dom][em] = {"source": label, "url": url, "snippet": " ".join(text[a:m.end() + 30].split())}

    # homepage-published addresses (site_scan) count as public evidence too
    try:
        for r in conn.execute("SELECT company_domain, emails FROM site_scan"):
            d = r["company_domain"]
            if d not in live:
                continue
            for em in json.loads(r["emails"] or "[]"):
                local, _, host = em.partition("@")
                if pdb.normalize_domain(host) == d and not EXCLUDE.match(local) and len(local) >= 2:
                    found[d].setdefault(em.lower(), {"source": "homepage", "url": f"https://{d}",
                                                     "snippet": f"published on https://{d}"})
    except Exception as e:  # noqa: BLE001
        print("site_scan emails unavailable:", e, file=sys.stderr)
    stats = defaultdict(int)
    out = []
    for d in sorted(live):
        if d in manual:
            stats["clay_or_manual_kept"] += 1
            continue
        fl = founders[d]
        ceo = next((f for f in fl if re.search(r"\bceo\b|chief executive", f["role"] or "", re.I)), fl[0] if fl else None)
        cands = []  # (rank, sub, email, contact, role, confidence, evidence)
        pattern = None
        for em, ev in found[d].items():
            local = em.split("@")[0]
            person, pat = match_person(local, fl)
            if person:
                is_ceo = ceo is not None and person["founder_id"] == ceo["founder_id"]
                cands.append((1 if is_ceo else 2, 0, em, person["full_name"], person["role"],
                              "published_ceo" if is_ceo else "published_founder", ev))
                if pat != "nickname":
                    pattern = pattern or pat
            elif local in FOUNDERS_INBOX:
                cands.append((4, 0, em, "founders inbox", "", "published_founders_inbox", ev))
            elif local in GENERIC:
                cands.append((5, GENERIC_ORDER.index(local) if local in GENERIC_ORDER else 99, em, "company inbox",
                              "", "published_generic", ev))
            else:
                stats["unmatched_personal_seen"] += 1
                # a non-founder personal address still reveals the domain's pattern if it is first-name-only shaped
        if ceo:
            f, l = name_parts(ceo["full_name"])
            host = d
            if pattern and f and not any(c[0] == 1 for c in cands):
                v = PATTERNS[pattern](f, l)
                if v:
                    cands.append((3, 0, f"{v}@{host}", ceo["full_name"], ceo["role"], "derived_from_pattern",
                                  {"source": f"pattern '{pattern}' from published founder address", "url": None,
                                   "snippet": ", ".join(found[d])}))
            if f and not cands:
                cands.append((6, 0, f"{f}@{host}", ceo["full_name"], ceo["role"], "guess_first_at_domain",
                              {"source": "no public address; most common startup pattern", "url": None, "snippet": ""}))
        if not cands:
            stats["none_no_founder_on_file"] += 1
            continue
        cands.sort(key=lambda c: (c[0], c[1], c[2]))
        best = cands[0]
        alt = next((c for c in cands[1:] if c[2] != best[2]), None)
        stats[best[5]] += 1
        out.append((d, best[2], best[3], best[4], best[5], best[6]["source"], best[6]["url"], best[6]["snippet"][:300],
                    alt[2] if alt else None, alt[5] if alt else None, json.dumps(sorted(found[d])), pdb.utcnow()))

    conn.executemany(
        "INSERT INTO company_emails (company_domain, email, contact_name, contact_role, email_confidence, source_type, "
        "source_url, evidence, email_alt, email_alt_confidence, all_published, found_at) "
        "VALUES (?,?,?,?,?,'pipeline',?,?,?,?,?,?) ON CONFLICT(company_domain) DO UPDATE SET email=excluded.email, "
        "contact_name=excluded.contact_name, contact_role=excluded.contact_role, "
        "email_confidence=excluded.email_confidence, source_url=excluded.source_url, evidence=excluded.evidence, "
        "email_alt=excluded.email_alt, email_alt_confidence=excluded.email_alt_confidence, "
        "all_published=excluded.all_published, found_at=excluded.found_at WHERE company_emails.source_type = 'pipeline'",
        [(r[0], r[1], r[2], r[3], r[4], r[6] or r[5], r[7], r[8], r[9], r[10], r[11]) for r in out])
    conn.execute("DELETE FROM company_emails WHERE source_type='pipeline' AND company_domain NOT IN "
                 "(SELECT company_domain FROM companies WHERE status!='disqualified')")
    conn.commit()
    report = {"run_at": pdb.utcnow(), "companies": len(live), "text_blocks_scanned": scanned,
              "companies_with_published_address": sum(1 for d in live if found[d]),
              "published_addresses_on_company_domain": sum(len(v) for v in found.values()),
              "by_confidence": dict(sorted(stats.items(), key=lambda x: -x[1])),
              "verification": "none - DNS, SMTP and finder APIs are blocked in this environment"}
    REPORT.write_text(json.dumps(report, indent=1) + "\n")
    print(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
