"""Homepage + DNS scan for every company domain -> site_scan table (+ ATS slugs discovered on careers links).

Usage: python3 scripts/site_scan.py [--only-new]
Per domain: GET https://<domain>/ (shared cache with resolve_domains), DNS via dns.google (MX, TXT incl. SPF).
Detects (pattern -> tool):
  visitor-ID / outbound intent  rb2b, warmly, koala, clearbit reveal, leadfeeder/dealfront, apollo tracker,
                                common room, unify, 6sense, demandbase, zoominfo websights, hockeystack, vector
  routing / chat                chili piper, default, qualified, drift, intercom, crisp
  marketing automation / CRM    hubspot, marketo, pardot, salesforce (SPF), customer.io, mailchimp, sendgrid ...
  analytics                     segment, posthog, mixpanel, amplitude, ga4, linkedin insight, meta pixel
  booking links                 calendly / cal.com / savvycal / hubspot meetings / zcal / tidycal (+ the slug)
  github orgs, linkedin company, x handle, emails, ATS embeds (ashby/greenhouse/lever/workable/... slugs)
Liveness: http status, parked/for-sale detection, redirect target.
"""
from __future__ import annotations

import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import http_fetch as hf  # noqa: E402
import pipeline_db as pdb  # noqa: E402
import resolve_domains as rd  # noqa: E402

TECH = {
    # H5 S1: intent / outbound infrastructure
    "rb2b": r"rb2b\.com|b2bjsstore", "warmly": r"warmly\.ai|opps-widget\.getwarmly", "koala": r"getkoala\.com|cdn\.koala",
    "clearbit_reveal": r"clearbitjs\.com|clearbitscripts\.com|tag\.clearbit", "leadfeeder_dealfront": r"lftracker|leadfeeder|dealfront",
    "apollo_tracker": r"assets\.apollo\.io|apollo\.io/micro", "common_room": r"cr-relay\.com|commonroom",
    "unify": r"unifyintent|unify\.com/js|tag\.unifygtm", "6sense": r"j\.6sc\.co|6sense", "demandbase": r"tag\.demandbase\.com",
    "zoominfo_websights": r"ws\.zoominfo\.com|zi-scripts", "hockeystack": r"hockeystack", "vector": r"cdn\.vector\.co",
    "chili_piper": r"chilipiper\.com", "default_com": r"default\.com/(js|sdk)|import\.default\.com", "qualified": r"js\.qualified\.com",
    # chat / support
    "drift": r"driftt\.com|js\.drift\.com", "intercom": r"widget\.intercom\.io|intercomcdn", "crisp": r"client\.crisp\.chat",
    # marketing automation / CRM
    "hubspot": r"js\.hs-scripts\.com|js\.hsforms\.net|hs-analytics|hubspot", "marketo": r"munchkin|mktoforms",
    "pardot": r"pi\.pardot\.com|pardot", "salesforce_web": r"force\.com/servlet|salesforce", "customer_io": r"customer\.io|customerioforms",
    "mailchimp": r"chimpstatic|list-manage\.com", "attio": r"attio\.com", "clay": r"clay\.com/|clay\.run",
    # analytics
    "segment": r"cdn\.segment\.com|segment\.io", "posthog": r"posthog", "mixpanel": r"mixpanel", "amplitude": r"amplitude\.com",
    "ga4": r"gtag\(|googletagmanager\.com/gtag", "gtm": r"googletagmanager\.com/gtm", "linkedin_insight": r"snap\.licdn\.com",
    "meta_pixel": r"connect\.facebook\.net", "reddit_pixel": r"redditstatic\.com/ads", "x_pixel": r"static\.ads-twitter\.com",
    # site builders
    "webflow": r"webflow", "framer": r"framer\.com|framerusercontent", "nextjs": r"/_next/", "wordpress": r"wp-content",
}
TECH_RE = {k: re.compile(v, re.I) for k, v in TECH.items()}
BOOK = re.compile(r"https?://(?:www\.)?(calendly\.com/[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)?|cal\.com/[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)?|"
                  r"savvycal\.com/[A-Za-z0-9_.-]+|meetings(?:-[a-z0-9]+)?\.hubspot\.com/[A-Za-z0-9_.-]+|zcal\.co/[A-Za-z0-9_.-]+|"
                  r"tidycal\.com/[A-Za-z0-9_.-]+)", re.I)
GH = re.compile(r"github\.com/([A-Za-z0-9-]{2,39})(?:/([A-Za-z0-9_.-]+))?", re.I)
LI = re.compile(r"linkedin\.com/company/([A-Za-z0-9_%.-]+)", re.I)
XH = re.compile(r"(?:twitter|x)\.com/([A-Za-z0-9_]{2,15})\b", re.I)
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,10}")
ATS = {"ashby": r"jobs\.ashbyhq\.com/([A-Za-z0-9._-]+)", "greenhouse": r"(?:boards|job-boards)\.(?:eu\.)?greenhouse\.io/([A-Za-z0-9_-]+)",
       "greenhouse_embed": r"greenhouse\.io/embed/job_board(?:/js)?\?for=([A-Za-z0-9_-]+)", "lever": r"jobs\.(?:eu\.)?lever\.co/([A-Za-z0-9._-]+)",
       "workable": r"apply\.workable\.com/([A-Za-z0-9_-]+)", "recruitee": r"([a-z0-9-]+)\.recruitee\.com",
       "teamtailor": r"([a-z0-9-]+)\.teamtailor\.com", "breezy": r"([a-z0-9-]+)\.breezy\.hr", "yc": r"ycombinator\.com/companies/([a-z0-9-]+)"}
ATS_RE = {k: re.compile(v, re.I) for k, v in ATS.items()}
GH_SKIP = {"features", "pricing", "about", "login", "sponsors", "orgs", "topics", "marketplace", "apps", "site", "enterprise",
           "github", "collections", "trending", "readme", "join", "contact", "security", "customer-stories"}
X_SKIP = {"intent", "share", "home", "search", "hashtag", "i", "x", "twitter", "privacy", "tos"}
TXT_TOKENS = re.compile(r"^(hubspot-developer-verification|atlassian-domain-verification|google-site-verification|"
                        r"facebook-domain-verification|docusign|stripe-verification|zoom-domain-verification|"
                        r"slack-domain-verification|openai-domain-verification|anthropic-domain-verification|"
                        r"miro-verification|loom-site-verification|apple-domain-verification|adobe-idp-site-verification|"
                        r"onetrust-domain-verification|whimsical|mongodb-site-verification|postman-domain-verification|"
                        r"cursor-domain-verification|figma-domain-verification|notion-domain-verification|"
                        r"salesforce|pardot|sendinblue|brevo|mailjet|klaviyo|zoho-verification|dropbox-domain-verification|"
                        r"ms=|MS=|apollo|outreach|salesloft|gong|lemlist|instantly|smartlead|clay)", re.I)
SPF_TOOLS = {"hubspot": "hubspotemail|_spf\\.hubspot", "salesforce": "_spf\\.salesforce", "sendgrid": "sendgrid",
             "mailgun": "mailgun", "mailchimp_mandrill": "mcsv\\.net|mandrillapp", "postmark": "mtasv\\.net",
             "amazonses": "amazonses", "customer_io": "customer\\.io", "zendesk": "zendesk", "intercom": "intercom",
             "google_workspace": "_spf\\.google", "microsoft365": "spf\\.protection\\.outlook", "zoho": "zoho",
             "sparkpost": "sparkpost", "brevo": "sendinblue|brevo", "klaviyo": "klaviyo", "freshdesk": "freshdesk"}


def dns(domain: str, typ: str) -> list[str]:
    r = hf.fetch(f"https://dns.google/resolve?name={domain}&type={typ}", timeout=10, retries=2)
    try:
        d = r.json()
    except ValueError:
        return []
    return [a.get("data", "").strip('"') for a in d.get("Answer") or [] if a.get("type") in (15, 16)]


def scan(domain: str) -> dict:
    p = rd.page(domain)
    raw = (p.get("raw") or "")[:400_000]  # head + body start carries scripts, meta, nav/footer links
    tech = sorted(k for k, rx in TECH_RE.items() if rx.search(raw))
    gh = []
    for m in GH.finditer(raw):
        o = m.group(1).lower()
        if o not in GH_SKIP and o not in gh:
            gh.append(o)
    li = next((m.group(1) for m in LI.finditer(raw)), None)
    xh = next((m.group(1) for m in XH.finditer(raw) if m.group(1).lower() not in X_SKIP), None)
    book = sorted({m.group(1) for m in BOOK.finditer(raw)})
    emails = sorted({e.lower() for e in EMAIL.findall(raw) if not re.search(r"\.(png|jpe?g|gif|svg|webp)$|sentry|example|"
                                                                           r"wixpress|@2x", e, re.I)})[:20]
    ats = {k: sorted({m.group(1).lower() for m in rx.finditer(raw)} - {"www", "app", "api", "careers", "jobs"})
           for k, rx in ATS_RE.items()}
    mx = dns(domain, "MX")
    txt = dns(domain, "TXT")
    spf = next((t for t in txt if t.lower().startswith("v=spf1")), None)
    spf_tools = sorted(k for k, v in SPF_TOOLS.items() if spf and re.search(v, spf, re.I))
    tokens = sorted({TXT_TOKENS.match(t).group(1).lower().rstrip("=") for t in txt if TXT_TOKENS.match(t)})
    parked = bool(rd.PARKED.search((p.get("text") or "")[:20000])) and len(p.get("text") or "") < 6000
    return {"company_domain": domain, "http_status": p["status"], "final_url": p.get("final"), "title": p["title"][:200],
            "tech": json.dumps(tech + [f"spf:{t}" for t in spf_tools]), "github_orgs": json.dumps(gh[:5]),
            "booking_links": json.dumps(book[:10]), "social": json.dumps({"linkedin": li, "x": xh, "parked": parked,
                                                                         "ats": {k: v for k, v in ats.items() if v},
                                                                         "desc": p.get("desc", "")[:300]}),
            "emails": json.dumps(emails), "mx": json.dumps(sorted({m.split()[-1].rstrip('.').lower() for m in mx})[:5]),
            "spf": spf, "txt_verifications": json.dumps(tokens), "scanned_at": pdb.utcnow()}


def scan_to_cache() -> None:
    """Network phase only (no DB writes): .cache/site_scan.jsonl; load with --load."""
    import sqlite3
    ro = sqlite3.connect(f"file:{pdb.DB_PATH}?mode=ro", uri=True)
    doms = [r[0] for r in ro.execute("SELECT company_domain FROM companies WHERE status!='disqualified'")]
    shard = next((a.split("=")[1] for a in sys.argv if a.startswith("--shard=")), None)  # e.g. --shard=2/4
    out = pdb.ROOT / ".cache" / (f"site_scan.{shard.replace('/', 'of')}.jsonl" if shard else "site_scan.jsonl")
    done = set()
    for f in (pdb.ROOT / ".cache").glob("site_scan*.jsonl"):
        done |= {json.loads(x)["company_domain"] for x in open(f)}
    if shard:
        i, n = map(int, shard.split("/"))
        doms = [d for k, d in enumerate(sorted(doms)) if k % n == i]
    todo = [d for d in doms if d not in done]
    print(f"scan_to_cache: {len(todo)} domains", file=sys.stderr)
    n = 0
    with open(out, "a") as f, ThreadPoolExecutor(32) as ex:
        for row in ex.map(scan, todo):
            f.write(json.dumps(row) + "\n")
            n += 1
            if n % 500 == 0:
                f.flush()
                print(f"  {n}/{len(todo)}", file=sys.stderr)


def load_cache(conn) -> int:
    live = {r[0] for r in conn.execute("SELECT company_domain FROM companies")}
    n = 0
    lines = [x for f in sorted((pdb.ROOT / ".cache").glob("site_scan*.jsonl")) for x in open(f)]
    for x in lines:
        row = json.loads(x)
        if row["company_domain"] not in live:
            continue
        cols = list(row)
        conn.execute(f"INSERT OR REPLACE INTO site_scan ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                     list(row.values()))
        n += 1
    conn.commit()
    return n


def main() -> None:
    if "--to-cache" in sys.argv:
        scan_to_cache()
        return
    conn = pdb.init_db()
    if "--load" in sys.argv:
        print(json.dumps({"loaded": load_cache(conn)}))
        return
    only_new = "--only-new" in sys.argv
    done = {r[0] for r in conn.execute("SELECT company_domain FROM site_scan")} if only_new else set()
    doms = [r[0] for r in conn.execute("SELECT company_domain FROM companies WHERE status!='disqualified'")
            if r[0] not in done]
    print(f"scanning {len(doms)} domains", file=sys.stderr)
    cols = None
    n = 0
    with ThreadPoolExecutor(32) as ex:
        for row in ex.map(scan, doms):
            cols = cols or list(row)
            conn.execute(f"INSERT OR REPLACE INTO site_scan ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                         list(row.values()))
            n += 1
            if n % 500 == 0:
                conn.commit()
                print(f"  {n}/{len(doms)}", file=sys.stderr)
    conn.commit()
    st = conn.execute("SELECT COUNT(*), SUM(http_status BETWEEN 200 AND 399), SUM(tech LIKE '%rb2b%' OR tech LIKE '%warmly%' "
                      "OR tech LIKE '%koala%' OR tech LIKE '%apollo_tracker%' OR tech LIKE '%clearbit%' OR "
                      "tech LIKE '%leadfeeder%' OR tech LIKE '%common_room%' OR tech LIKE '%unify%'), "
                      "SUM(booking_links != '[]'), SUM(github_orgs != '[]') FROM site_scan").fetchone()
    print(json.dumps({"scanned": st[0], "live": st[1], "intent_tools": st[2], "booking_links": st[3], "github": st[4]}))


if __name__ == "__main__":
    main()
