"""Funding news (US + UK + EU outlets): every round announced 2025-05-01 -> today, keyed to a company domain.

Usage: python3 scripts/news_funding.py            (collect + parse -> .cache/news_rounds.jsonl)
Feeds are WordPress-style RSS paged with ?paged=N back past 2025-05-01 (stop when an entire page is older).
Raw items (title, link, date, categories, content HTML) -> raw_sources/H2_fresh_capital/news_rss/<feed>.jsonl.gz

Round extraction (title first, then lead paragraph):
  company = headline subject before raises/secures/lands/closes/bags/nabs/gets/announces ... (multi-company
            roundups are split on ';' and newline bullets when the article lists several rounds)
  amount  = first $/EUR/GBP figure (k/m/bn) -> USD (EUR x1.10, GBP x1.30)
  stage   = pre-seed / seed / Series A / Series B+ from headline + lead
  domain  = an outbound link in the article whose host matches the company brand (news_linked); otherwise the
            name goes to resolve_domains with the article's outbound hosts as hints (news_resolved)
  geo     = '<City>-based' / country words in the lead, else the outlet's home market as a weak hint
VC fund closes ('closes $X fund', 'Fund II') and acquisitions are excluded.
"""
from __future__ import annotations

import gzip
import html
import json
import re
import sys
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import http_fetch as hf  # noqa: E402
import icp_rules as icp  # noqa: E402
import pipeline_db as pdb  # noqa: E402

RAW = pdb.RAW_DIR / "H2_fresh_capital" / "news_rss"
OUT = pdb.ROOT / ".cache" / "news_rounds.jsonl"
START = "2025-05-01"
FEEDS = {  # name: (url template, home country hint)
    "techcrunch_venture": ("https://techcrunch.com/category/venture/feed/?paged={n}", None),
    "techcrunch_startups": ("https://techcrunch.com/category/startups/feed/?paged={n}", None),
    "eu_startups_funding": ("https://www.eu-startups.com/category/fundin/feed/?paged={n}", None),
    "uktn": ("https://www.uktech.news/feed?paged={n}", "GB"),
    "tech_funding_news": ("https://techfundingnews.com/feed/?paged={n}", None),
    "silicon_canals": ("https://siliconcanals.com/feed/?paged={n}", "NL"),
    "geekwire": ("https://www.geekwire.com/feed/?paged={n}", "US"),
    "alleywatch": ("https://www.alleywatch.com/feed/?paged={n}", "US"),
    "arcticstartup": ("https://arcticstartup.com/feed/?paged={n}", None),
    "maddyness_uk": ("https://www.maddyness.com/uk/feed/?paged={n}", "GB"),
    "crunchbase_news": ("https://news.crunchbase.com/feed/?paged={n}", None),
    "trending_topics": ("https://www.trendingtopics.eu/feed/?paged={n}", None),
    "deutsche_startups": ("https://www.deutsche-startups.de/feed/?paged={n}", "DE"),
    "silicon_republic": ("https://www.siliconrepublic.com/feed/?paged={n}", "IE"),
    "the_recursive": ("https://www.therecursive.com/feed/?paged={n}", None),
    "startupvalley_uk": ("https://www.startupvalley.news/uk/feed/?paged={n}", "GB"),
    "tech_eu": ("https://tech.eu/feed/?paged={n}", None),
}
NS = {"content": "http://purl.org/rss/1.0/modules/content/"}
VERB = r"(raises|raised|secures|secured|lands|landed|closes|closed|bags|nabs|snags|gets|receives|scores|grabs|" \
       r"picks up|pulls in|rakes in|announces|completes|attracts|banks|collects|hauls|wins|unveils .{0,40} with|" \
       r"emerges from stealth with|launches with|debuts with|sammelt|erhält|holt|lève|levée|haalt|ophaalt)"
FUNDING = re.compile(rf"\b{VERB}\b.*?(\$|€|£|eur|usd|gbp|million|\bm\b|seed|series|pre-seed|funding|round|"
                     r"investment|financing|millionen|mio)", re.I)
NOT_STARTUP = re.compile(r"\b(closes|launches|raises|announces)\b.{0,30}\b(fund|fund [ivx]+|vehicle|lp)\b|"
                         r"\b(acquires|acquired|acquisition|merger|ipo|spac|layoffs?|shuts down)\b", re.I)
AMT = re.compile(r"(?:(\$|€|£|us\$|usd|eur|gbp|chf|sek)\s?(\d+(?:[.,]\d+)?)\s?(k|m|mn|million|bn|billion|b)\b|"
                 r"(\d+(?:[.,]\d+)?)\s?(million|m|bn|billion|mio\.?|millionen)\s?(dollars|euros?|pounds|eur|usd|gbp|€|\$|£))",
                 re.I)
FX = {"$": 1, "us$": 1, "usd": 1, "dollars": 1, "€": 1.1, "eur": 1.1, "euro": 1.1, "euros": 1.1, "£": 1.3,
      "gbp": 1.3, "pounds": 1.3, "chf": 1.13, "sek": 0.095}
BASED = re.compile(r"\b([A-Z][a-zA-ZÀ-ſ.\- ]{2,30})-based\b")
NEWS_HOSTS = {"techcrunch.com", "eu-startups.com", "uktech.news", "techfundingnews.com", "siliconcanals.com",
              "geekwire.com", "alleywatch.com", "arcticstartup.com", "maddyness.com", "crunchbase.com",
              "trendingtopics.eu", "deutsche-startups.de", "siliconrepublic.com", "therecursive.com",
              "startupvalley.news", "tech.eu", "linkedin.com", "x.com", "twitter.com", "facebook.com",
              "instagram.com", "youtube.com", "wp.com", "wordpress.com", "gravatar.com", "feedburner.com",
              "bit.ly", "t.co", "google.com", "apple.com", "medium.com", "substack.com", "prnewswire.com",
              "businesswire.com", "globenewswire.com", "bloomberg.com", "reuters.com", "ft.com", "wsj.com",
              "cnbc.com", "forbes.com", "sifted.eu", "pitchbook.com", "dealroom.co", "github.com", "eventbrite.com",
              "lu.ma", "techcrunch.events", "tcdisrupt.com", "beehiiv.com", "mailchi.mp", "list-manage.com"}


def collect_feed(name: str) -> list:
    tpl = FEEDS[name][0]
    items, n = [], 1
    while n <= 700:
        r = hf.fetch(tpl.format(n=n), max_age=3600 * 12 if n <= 3 else None, timeout=30, retries=2)
        if r.status != 200 or "<item" not in r.text:
            break
        try:
            root = ET.fromstring(r.text.encode("utf-8", "replace"))
        except ET.ParseError:
            txt = re.sub(r"&(?!amp;|lt;|gt;|quot;|apos;|#)", "&amp;", r.text)
            try:
                root = ET.fromstring(txt.encode("utf-8", "replace"))
            except ET.ParseError:
                break
        page = []
        for it in root.iter("item"):
            try:
                d = parsedate_to_datetime(it.findtext("pubDate") or "").date().isoformat()
            except (TypeError, ValueError):
                d = None
            page.append({"title": html.unescape(it.findtext("title") or ""), "link": it.findtext("link"),
                         "date": d, "cats": [c.text for c in it.findall("category") if c.text],
                         "content": (it.findtext("content:encoded", namespaces=NS) or it.findtext("description")
                                     or "")[:30000]})
        if items and page and page[0]["link"] == items[-len(page)]["link"]:
            break  # feed ignores ?paged=: same page again
        items += page
        if not page or all((p["date"] or "9999") < START for p in page):
            break
        n += 1
    with gzip.open(RAW / f"{name}.jsonl.gz", "wt") as f:
        for it in items:
            f.write(json.dumps(it) + "\n")
    print(f"{name}: {n} pages, {len(items)} items, oldest {min((i['date'] or '9') for i in items) if items else '-'}",
          file=sys.stderr, flush=True)
    return items


def collect() -> dict[str, list]:
    from concurrent.futures import ThreadPoolExecutor
    RAW.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(len(FEEDS)) as ex:
        return dict(zip(FEEDS, ex.map(collect_feed, FEEDS)))


def to_usd(m) -> int | None:
    if m.group(2):
        cur, val, mult = m.group(1).lower(), m.group(2), m.group(3).lower()
    else:
        val, mult, cur = m.group(4), m.group(5).lower(), m.group(6).lower()
    try:
        v = float(val.replace(",", "."))
    except ValueError:
        return None
    k = 1e3 if mult == "k" else 1e9 if mult in ("bn", "billion", "b") else 1e6
    return int(v * k * FX.get(cur, 1))


def subjects(title: str) -> list[str]:
    t = re.sub(r"^(exclusive|breaking|funding|scoop|news)\s*[:\-–|]\s*", "", title, flags=re.I)
    m = re.match(rf"^(.{{2,90}}?)\s*,?\s+(?:has\s+|have\s+|just\s+)?{VERB}\b", t, re.I)
    if not m:
        return []
    s = m.group(1).strip(" '\"‘’“”")
    # strip descriptors: "London-based AI startup Acme" -> "Acme"; "Acme, the AI startup," -> "Acme"
    s = re.sub(r"^.*?\b(startup|start-up|scaleup|scale-up|company|platform|firm|provider|maker|developer|"
               r"specialist|unicorn|spinout|spin-out|fintech|insurtech|healthtech|proptech|edtech|biotech|"
               r"medtech|legaltech|regtech|climate tech|cleantech|deeptech|saas)\s+", "", s, flags=re.I)
    s = re.sub(r",.*$", "", s).strip()
    s = re.sub(r"^(the|a|an)\s+", "", s, flags=re.I)
    if not s or len(s) > 60 or len(s.split()) > 6:
        return []
    return [s]


def links(content: str) -> list[str]:
    out = []
    for u in re.findall(r'href=["\']([^"\']+)["\']', content or ""):
        d = pdb.normalize_domain(html.unescape(u))
        if d and d not in NEWS_HOSTS and not any(d.endswith("." + h) for h in NEWS_HOSTS):
            out.append(d)
    return list(dict.fromkeys(out))


def parse(items: dict[str, list]) -> list[dict]:
    import resolve_domains as rd
    rounds, seen = [], set()
    for feed, its in items.items():
        its = [i for i in its if not (i["link"] in seen or seen.add(i["link"]))]
        home = FEEDS[feed][1]
        for it in its:
            if not it["date"] or it["date"] < START:
                continue
            title = it["title"]
            if not FUNDING.search(title) or NOT_STARTUP.search(title):
                continue
            subs = subjects(title)
            if not subs:
                continue
            body = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", it["content"])))[:3000]
            lead = body[:900]
            am = AMT.search(title) or AMT.search(lead)
            amt = to_usd(am) if am else None
            stage = icp.stage_from_text(title) or icp.stage_from_text(lead)
            hosts = links(it["content"])
            name = subs[0]
            _, full, short = rd.brand_cores(name)
            dom = next((h for h in hosts if short and (short in re.sub(r"[^a-z0-9]", "", h.split(".")[0])
                                                       or re.sub(r"[^a-z0-9]", "", h.split(".")[0]) in full)
                        and len(short) >= 3), None)
            bm = BASED.search(lead)
            country = icp.country_of(bm.group(1)) if bm else None
            if not country and bm:
                country = {"london": "GB", "berlin": "DE", "paris": "FR", "amsterdam": "NL", "stockholm": "SE",
                           "munich": "DE", "dublin": "IE", "madrid": "ES", "barcelona": "ES", "copenhagen": "DK",
                           "helsinki": "FI", "oslo": "NO", "zurich": "CH", "vienna": "AT", "lisbon": "PT",
                           "milan": "IT", "tallinn": "EE", "warsaw": "PL", "brussels": "BE", "san francisco": "US",
                           "new york": "US", "boston": "US", "austin": "US", "seattle": "US", "los angeles": "US",
                           "cambridge": "GB", "manchester": "GB", "edinburgh": "GB", "hamburg": "DE",
                           "prague": "CZ", "vilnius": "LT", "bucharest": "RO", "budapest": "HU", "athens": "GR",
                           "palo alto": "US", "chicago": "US", "denver": "US", "miami": "US", "toronto": "CA",
                           "tel aviv": "IL", "bengaluru": "IN", "bangalore": "IN", "singapore": "SG"}.get(
                    bm.group(1).strip().lower())
            if not country:
                cw = re.search(r"\b(US|U\.S\.|American|UK|British|German|French|Dutch|Swedish|Danish|Finnish|"
                               r"Norwegian|Spanish|Italian|Irish|Swiss|Austrian|Belgian|Polish|Estonian|"
                               r"Portuguese|Israeli|Indian|Canadian|Singaporean|Australian|Brazilian)\b", lead)
                if cw:
                    country = {"US": "US", "U.S.": "US", "American": "US", "UK": "GB", "British": "GB",
                               "German": "DE", "French": "FR", "Dutch": "NL", "Swedish": "SE", "Danish": "DK",
                               "Finnish": "FI", "Norwegian": "NO", "Spanish": "ES", "Italian": "IT", "Irish": "IE",
                               "Swiss": "CH", "Austrian": "AT", "Belgian": "BE", "Polish": "PL", "Estonian": "EE",
                               "Portuguese": "PT", "Israeli": "IL", "Indian": "IN", "Canadian": "CA",
                               "Singaporean": "SG", "Australian": "AU", "Brazilian": "BR"}[cw.group(1)]
            rounds.append({"feed": feed, "date": it["date"], "title": title, "url": it["link"], "company": name,
                           "domain": dom, "hint_hosts": hosts[:8], "amount_usd": amt, "stage": stage,
                           "country": country or None, "country_hint": home, "lead": lead[:600]})
    return rounds


def main() -> None:
    items = collect()
    rounds = parse(items)
    with open(OUT, "w") as f:
        for r in rounds:
            f.write(json.dumps(r) + "\n")
    print(json.dumps({"items": sum(len(v) for v in items.values()), "funding_rounds": len(rounds),
                      "with_linked_domain": sum(1 for r in rounds if r["domain"]),
                      "by_stage": {s: sum(1 for r in rounds if r["stage"] == s) for s in
                                   ("pre_seed", "seed", "series_a", "series_b_plus", None)}}, default=str))


if __name__ == "__main__":
    main()
