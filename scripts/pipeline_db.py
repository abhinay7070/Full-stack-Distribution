"""Shared database helpers for the GTM lead pipeline.

Every hypothesis ingestion script imports from here so that domain
normalization, upserts, and run auditing behave identically across H1-H6.
"""
from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import tldextract

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "db" / "gtm_pipeline.db"
SCHEMA_PATH = ROOT / "db" / "schema.sql"
RAW_DIR = ROOT / "raw_sources"
SUBLIST_DIR = ROOT / "sublists"

HYPOTHESES = {
    "H0": "H0_icp_universe",        # firmographic ICP fit, no live signal yet (weight 0: membership, not a signal)
    "H1": "H1_job_arbitrage",
    "H2": "H2_fresh_capital",
    "H3": "H3_technical_founders",
    "H4": "H4_launch_decay",
    "H5": "H5_outbound_stack",
    "H6": "H6_founder_led_sales",
    "H7": "H7_revenue_no_gtm",
    "H8": "H8_oss_traction",
    "H9": "H9_warm_paths",
    "H10": "H10_stale_gtm_roles",
}

# Points added to priority_score the first time a company matches a hypothesis.
# Re-ingesting the same hypothesis never adds points again (idempotent).
HYPOTHESIS_WEIGHTS = {h: (0 if h == "H0" else 1) for h in HYPOTHESES}
SIGNAL_HYPOTHESES = [h for h in HYPOTHESES if h != "H0"]

# Offline public-suffix snapshot: deterministic, no network call at import time.
_extract = tldextract.TLDExtract(suffix_list_urls=())

# Domains that host many companies; a URL on these is NOT a company domain.
SHARED_HOSTS = {
    "linkedin.com", "x.com", "twitter.com", "github.com", "medium.com",
    "substack.com", "notion.site", "ycombinator.com", "wellfound.com",
    "angel.co", "producthunt.com", "crunchbase.com", "google.com",
    "vercel.app", "netlify.app", "herokuapp.com", "webflow.io", "framer.website",
}


# Same company published under two domains: alias -> canonical. Applied by normalize_domain,
# so every ingest maps the alias to one row (build_master.py folds any existing alias row in).
DOMAIN_ALIASES = {
    "joinpasito.com": "pasito.ai",
}


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def normalize_domain(value: str | None) -> str | None:
    """Return the registrable root domain ("https://app.Acme.co.uk/x" -> "acme.co.uk").

    Returns None if the value is empty or resolves to a shared host
    (LinkedIn, GitHub, Vercel, ...) that cannot identify a single company.
    """
    if not value:
        return None
    v = value.strip().lower()
    if "://" not in v:
        v = "http://" + v
    host = (urlparse(v).hostname or "").strip(".")
    if not host:
        return None
    ext = _extract(host)
    if not ext.suffix or not ext.domain:
        return None
    root = f"{ext.domain}.{ext.suffix}"
    if root in SHARED_HOSTS:
        return None
    return DOMAIN_ALIASES.get(root, root)


def normalize_name(name: str) -> str:
    n = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", n).strip().lower()


def connect(db_path: Path = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=900)  # parallel ingest jobs wait for the write lock instead of failing
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def migrate(conn: sqlite3.Connection) -> None:
    """v2: widen ingestion_runs.hypothesis CHECK to every key in HYPOTHESES (SQLite cannot ALTER a CHECK)."""
    row = conn.execute("SELECT sql FROM sqlite_master WHERE name='ingestion_runs'").fetchone()
    if row and "'H10'" not in row["sql"]:
        conn.executescript("""
            ALTER TABLE ingestion_runs RENAME TO ingestion_runs_v1;
        """)
        conn.executescript(SCHEMA_PATH.read_text())
        conn.execute("INSERT INTO ingestion_runs SELECT * FROM ingestion_runs_v1")
        conn.execute("DROP TABLE ingestion_runs_v1")
        conn.commit()
    conn.executescript(SCHEMA_PATH.read_text())
    have = {r[1] for r in conn.execute("PRAGMA table_info(companies)")}
    for col in ("headcount_band", "founded_year", "linkedin_url", "sources"):
        if col not in have:
            conn.execute(f"ALTER TABLE companies ADD COLUMN {col} TEXT")
    if "score_motion" not in {r[1] for r in conn.execute("PRAGMA table_info(scores)")}:
        conn.execute("ALTER TABLE scores ADD COLUMN score_motion INTEGER")
    conn.commit()


def init_db(db_path: Path = DB_PATH) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(db_path)
    migrate(conn)
    return conn


# Columns a hypothesis script may set. Non-null incoming values overwrite;
# null incoming values never erase existing data.
_COMPANY_FIELDS = (
    "company_name", "one_liner", "hq_country", "sector", "batch_or_investor",
    "funding_stage", "funding_amount", "funding_date", "headcount", "gtm_headcount",
    "headcount_band", "founded_year", "linkedin_url",
)


def upsert_company(conn: sqlite3.Connection, domain_or_url: str, hypothesis: str,
                   proof: dict | None = None, **fields) -> str | None:
    """Insert or merge a company. Returns the normalized domain (None if unusable).

    - New domain: row created with matched_hypotheses=[hypothesis].
    - Existing domain, new hypothesis: tag appended, priority_score += weight.
    - Existing domain, same hypothesis: no score change (idempotent).
    - proof is appended to live_signal_proof unless an identical url+hypothesis exists.
    """
    if hypothesis not in HYPOTHESES:
        raise ValueError(f"unknown hypothesis {hypothesis!r}")
    unknown = set(fields) - set(_COMPANY_FIELDS)
    if unknown:
        raise ValueError(f"unknown company fields: {sorted(unknown)}")

    domain = normalize_domain(domain_or_url)
    if domain is None:
        return None

    now = utcnow()
    row = conn.execute(
        "SELECT matched_hypotheses, live_signal_proof, priority_score FROM companies WHERE company_domain = ?",
        (domain,),
    ).fetchone()

    if proof is not None:
        proof = {"hypothesis": hypothesis, "captured_at": now, **proof}

    if row is None:
        cols = ["company_domain", "matched_hypotheses", "live_signal_proof", "priority_score"]
        vals = [domain, json.dumps([hypothesis]), json.dumps([proof] if proof else []),
                HYPOTHESIS_WEIGHTS[hypothesis]]
        for k, v in fields.items():
            if v is not None:
                cols.append(k)
                vals.append(v)
        conn.execute(
            f"INSERT INTO companies ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", vals
        )
        return domain

    tags = json.loads(row["matched_hypotheses"])
    proofs = json.loads(row["live_signal_proof"])
    score = row["priority_score"]
    if hypothesis not in tags:
        tags.append(hypothesis)
        tags.sort(key=lambda h: int(h[1:]))
        score += HYPOTHESIS_WEIGHTS[hypothesis]
    if proof and not any(p.get("url") == proof.get("url") and p.get("hypothesis") == hypothesis
                         for p in proofs):
        proofs.append(proof)

    sets = ["matched_hypotheses = ?", "live_signal_proof = ?", "priority_score = ?", "last_updated_at = ?"]
    vals = [json.dumps(tags), json.dumps(proofs), score, now]
    for k, v in fields.items():
        if v is not None:
            sets.append(f"{k} = ?")
            vals.append(v)
    vals.append(domain)
    conn.execute(f"UPDATE companies SET {', '.join(sets)} WHERE company_domain = ?", vals)
    return domain


_FOUNDER_FIELDS = ("role", "is_technical", "linkedin_url", "x_handle",
                   "verified_email", "verification_status", "source_url")


def upsert_founder(conn: sqlite3.Connection, company_domain: str, full_name: str, **fields) -> None:
    unknown = set(fields) - set(_FOUNDER_FIELDS)
    if unknown:
        raise ValueError(f"unknown founder fields: {sorted(unknown)}")
    if fields.get("x_handle"):
        fields["x_handle"] = fields["x_handle"].lstrip("@")
    data = {k: v for k, v in fields.items() if v is not None}
    cols = ["company_domain", "full_name", "full_name_norm", *data]
    vals = [company_domain, full_name.strip(), normalize_name(full_name), *data.values()]
    updates = ", ".join([f"{k} = excluded.{k}" for k in data] + ["last_updated_at = excluded.last_updated_at"])
    cols.append("last_updated_at")
    vals.append(utcnow())
    conn.execute(
        f"INSERT INTO founders ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))}) "
        f"ON CONFLICT(company_domain, full_name_norm) DO UPDATE SET {updates}",
        vals,
    )


def start_run(conn: sqlite3.Connection, hypothesis: str, source: str, query: str,
              raw_path: str | None = None) -> int:
    cur = conn.execute(
        "INSERT INTO ingestion_runs (hypothesis, source, query, raw_path) VALUES (?, ?, ?, ?)",
        (hypothesis, source, query, raw_path),
    )
    conn.commit()
    return cur.lastrowid


def finish_run(conn: sqlite3.Connection, run_id: int, **counts) -> None:
    allowed = {"expected_total", "pages_fetched", "records_raw", "records_unique",
               "records_icp_pass", "stop_reason", "raw_path"}
    unknown = set(counts) - allowed
    if unknown:
        raise ValueError(f"unknown run fields: {sorted(unknown)}")
    sets = [f"{k} = ?" for k in counts] + ["completed_at = ?"]
    conn.execute(f"UPDATE ingestion_runs SET {', '.join(sets)} WHERE run_id = ?",
                 [*counts.values(), utcnow(), run_id])
    conn.commit()
