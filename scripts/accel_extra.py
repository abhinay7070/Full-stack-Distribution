"""E4/E5: extra accelerator / seed-fund portfolio pages (Seedcamp, AI Grant, Betaworks).

Each page is fetched (cached), snapshotted to raw_sources/H0_icp_universe/accel_extra/, and every outbound company
website on it is a candidate. Membership alone is NOT stage proof (these lists include unicorns and exits), so each
domain goes through universe_ingest.gate(): already in the universe -> source added; homepage states an early stage
-> admitted; otherwise pooled (with its domain) in sublist_unverified_stage_pool.csv; late-stage / institution ->
rejected with a reason. Run after universe_ingest (the HN step rewrites the pool file).
"""
from __future__ import annotations

import gzip
import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import http_fetch as hf  # noqa: E402
import pipeline_db as pdb  # noqa: E402
import universe_ingest as ui  # noqa: E402

PAGES = {"seedcamp": "https://seedcamp.com/our-companies/", "aigrant": "https://aigrant.com/",
         "betaworks": "https://www.betaworks.com/companies"}
RAW = pdb.RAW_DIR / "H0_icp_universe" / "accel_extra"
NOISE = re.compile(r"(google|gstatic|twitter|x\.com|linkedin|facebook|instagram|youtube|youtu\.be|medium\.com|"
                   r"apple\.com|github|clarity\.ms|vimeo|calendly|typeform|notion\.|cloudfront|jsdelivr|cdn|wordpress|"
                   r"wp\.com|substack|tiktok|spotify|bit\.ly|bitly\.com|t\.co$|crunchbase|techcrunch|forbes|bloomberg|"
                   r"ft\.com|wired|sifted|eu-startups|webflow|hubspot|mailchimp|gravatar|w3\.org|schema\.org|"
                   r"seedcamp|aigrant|betaworks|perplexity\.ai|openai|anthropic)", re.I)


def domains(html: str) -> list[str]:
    out = []
    for h in re.findall(r'href="(https?://[^"]+)"', html):
        d = pdb.normalize_domain(h)
        if d and not NOISE.search(d) and "." in d:
            out.append(d)
    return list(dict.fromkeys(out))


def main() -> None:
    RAW.mkdir(parents=True, exist_ok=True)
    conn = pdb.init_db()
    uni = ui.existing_icp(conn)
    run = pdb.start_run(conn, "H0", "accel_extra", "Seedcamp / AI Grant / Betaworks portfolio pages; universe gate",
                        "raw_sources/H0_icp_universe/accel_extra")
    st, pool, rej, seen = Counter(), [], [], {}
    for prog, url in PAGES.items():
        r = hf.fetch(url, timeout=30, retries=2, max_bytes=6_000_000)
        if r.status != 200:
            st[f"page_{prog}_{r.status}"] += 1
            continue
        with gzip.open(RAW / f"{prog}.html.gz", "wt") as f:
            f.write(r.text)
        for d in domains(r.text):
            seen.setdefault(d, set()).add(prog)
    ui.prefetch_homepages([d for d in seen if d not in uni])
    for d, progs in sorted(seen.items()):
        label = "/".join(sorted(progs))
        if d in uni:
            st["already_in_universe"] += 1
            ui_h0 = __import__("h0_universe")
            for p in progs:
                ui_h0.add_source(conn, d, f"accel:{p}")
            continue
        p = ui.rd.page(d)
        text = f"{p['title']} {p['desc']} {p['text'][:20000]}" if 0 < p["status"] < 400 else ""
        if not text:
            st["homepage_unreachable"] += 1
            rej.append({"source": f"accel:{label}", "name": d, "domain": d, "reason": f"homepage_http_{p['status']}"})
            continue
        country = ui.icp.country_of(text[:3000])
        dec, why, flags, sector = ui.gate(text, country, None, None, d, p["title"][:80], False)
        if dec == "reject":
            st["reject"] += 1
            rej.append({"source": f"accel:{label}", "name": p["title"][:80] or d, "domain": d, "reason": why})
            continue
        if dec == "pool":
            st["pool"] += 1
            pool.append({"source": f"accel:{label}", "name": (p["title"] or d)[:80], "domain": d, "reason": why,
                         "date": "", "url": f"https://{d}", "evidence": f"{label} portfolio; {p['desc'][:120]}"})
            continue
        st["admit"] += 1
        pdb.upsert_company(conn, d, "H0", proof={"source": f"accel:{label}", "url": PAGES[sorted(progs)[0]],
                                                 "snippet": f"[universe|{label}] {'; '.join(flags)}"[:300]})
        conn.execute("UPDATE companies SET company_name=COALESCE(company_name, ?), one_liner=COALESCE(one_liner, ?), "
                     "sector=COALESCE(sector, ?), batch_or_investor=COALESCE(batch_or_investor, ?) WHERE company_domain=?",
                     ((p["title"] or d).split("|")[0].split(" - ")[0].strip()[:100], p["desc"][:300] or None,
                      sector or None, label, d))
        __import__("h0_universe").add_source(conn, d, f"accel:{label}")
    conn.commit()
    pdb.finish_run(conn, run, records_raw=len(seen), records_unique=len(seen), records_icp_pass=st["admit"],
                   stop_reason="exhausted")
    ui.write_rows(ui.POOL, pool, append=True)
    ui.write_rows(ui.REJ, rej, append=True)
    rep = {"run_at": pdb.utcnow(), "domains": len(seen), **st}
    (RAW / "run_report.json").write_text(json.dumps(rep, indent=1) + "\n")
    print(json.dumps(rep, indent=1))


if __name__ == "__main__":
    main()
