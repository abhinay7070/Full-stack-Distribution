-- gtm_pipeline.db schema
-- Unique key across the pipeline: companies.company_domain (normalized root domain, e.g. "acme.ai").

PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------------------
-- companies: one row per normalized root domain. Hypothesis hits are merged
-- into matched_hypotheses (JSON array), never duplicated as rows.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS companies (
    company_domain          TEXT PRIMARY KEY
                            CHECK (company_domain = lower(company_domain) AND company_domain <> ''),
    company_name            TEXT,
    one_liner               TEXT,
    hq_country              TEXT,                       -- ISO 3166-1 alpha-2 (US, GB, DE, ...)
    sector                  TEXT,                       -- B2B SaaS | AI Agents | DevTools | Infra | Vertical AI | Fintech
    batch_or_investor       TEXT,                       -- e.g. "YC W26", "a16z Speedrun", "Seedcamp"
    funding_stage           TEXT CHECK (funding_stage IN ('pre_seed','seed','series_a','unknown') OR funding_stage IS NULL),
    funding_amount          INTEGER CHECK (funding_amount IS NULL OR funding_amount >= 0),  -- USD, whole dollars
    funding_date            TEXT,                       -- ISO 8601 date (YYYY-MM-DD)
    headcount               INTEGER CHECK (headcount IS NULL OR headcount >= 0),
    gtm_headcount           INTEGER CHECK (gtm_headcount IS NULL OR gtm_headcount >= 0),   -- sales + marketing + growth
    matched_hypotheses      TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(matched_hypotheses) AND json_type(matched_hypotheses) = 'array'),
    live_signal_proof       TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(live_signal_proof) AND json_type(live_signal_proof) = 'array'),
                            -- [{hypothesis, source, url, snippet, captured_at}]
    priority_score          INTEGER NOT NULL DEFAULT 0,
    status                  TEXT NOT NULL DEFAULT 'new'
                            CHECK (status IN ('new','enriched','qualified','disqualified',
                                              'contacted','replied','meeting','won','lost')),
    disqualification_reason TEXT,                       -- set when a hard ICP filter fails (row is kept, not deleted)
    first_seen_at           TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    last_updated_at         TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE INDEX IF NOT EXISTS idx_companies_status   ON companies(status);
CREATE INDEX IF NOT EXISTS idx_companies_priority ON companies(priority_score DESC);

-- ---------------------------------------------------------------------------
-- founders: decision makers per company. Dedup key = (company_domain, normalized name).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS founders (
    founder_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    company_domain      TEXT NOT NULL REFERENCES companies(company_domain) ON UPDATE CASCADE ON DELETE CASCADE,
    full_name           TEXT NOT NULL,
    full_name_norm      TEXT NOT NULL,                  -- lowercase, collapsed whitespace, no accents
    role                TEXT,                           -- e.g. "Co-Founder & CEO"
    is_technical        INTEGER CHECK (is_technical IN (0,1) OR is_technical IS NULL),  -- used by H3
    linkedin_url        TEXT,
    x_handle            TEXT,                           -- stored without leading "@"
    verified_email      TEXT,
    verification_status TEXT NOT NULL DEFAULT 'unverified'
                        CHECK (verification_status IN ('unverified','valid','invalid','catch_all','risky','unknown')),
    source_url          TEXT,
    first_seen_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    last_updated_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    UNIQUE (company_domain, full_name_norm)
);

CREATE INDEX IF NOT EXISTS idx_founders_domain ON founders(company_domain);

-- ---------------------------------------------------------------------------
-- ingestion_runs: recall audit log. One row per (hypothesis, source, query).
-- Lets us prove every source was paginated to exhaustion and reconcile counts.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ingestion_runs (
    run_id              INTEGER PRIMARY KEY AUTOINCREMENT,
    hypothesis          TEXT NOT NULL CHECK (hypothesis IN ('H1','H2','H3','H4','H5','H6')),
    source              TEXT NOT NULL,                  -- e.g. "yc_waas", "wellfound", "finsmes"
    query               TEXT NOT NULL,                  -- exact query/filter/URL template used
    raw_path            TEXT,                           -- raw_sources/<H>/... file(s) written
    expected_total      INTEGER,                        -- total reported by the source, if it reports one
    pages_fetched       INTEGER NOT NULL DEFAULT 0,
    records_raw         INTEGER NOT NULL DEFAULT 0,     -- before any filter
    records_unique      INTEGER NOT NULL DEFAULT 0,     -- after domain dedup
    records_icp_pass    INTEGER NOT NULL DEFAULT 0,
    stop_reason         TEXT,                           -- 'exhausted' | 'total_reached' | 'error' | ...
    started_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    completed_at        TEXT
);

-- ---------------------------------------------------------------------------
-- Tracking layer (written by scripts/build_master.py; edited by the user in
-- master/gtm_master.xlsx and synced back on every rebuild).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS scores (
    company_domain      TEXT PRIMARY KEY REFERENCES companies(company_domain) ON UPDATE CASCADE ON DELETE CASCADE,
    rank                INTEGER,
    rank_score          INTEGER,
    score_signals       INTEGER,   -- 25 x number of hypotheses matched
    score_hiring        INTEGER,   -- H1 role points
    score_capital       INTEGER,   -- H2 recency points (incl. weak-evidence penalty)
    score_founders      INTEGER,   -- H3 tier points
    score_size          INTEGER,   -- headcount fit points
    score_penalties     INTEGER,   -- unverified funding / unknown geo / possible past Series A
    icp_tier            TEXT,      -- A_high_confidence | B_likely_fit | C_needs_enrichment | D_review
    wave                TEXT,      -- outreach wave (see README)
    computed_at         TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tracking (
    company_domain      TEXT PRIMARY KEY REFERENCES companies(company_domain) ON UPDATE CASCADE ON DELETE CASCADE,
    stage               TEXT NOT NULL DEFAULT 'Not started',
    next_action         TEXT,
    next_action_date    TEXT,      -- YYYY-MM-DD
    notes               TEXT,
    updated_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE TABLE IF NOT EXISTS outreach_log (
    touch_id            INTEGER PRIMARY KEY AUTOINCREMENT,
    touch_date          TEXT NOT NULL,   -- YYYY-MM-DD
    company_domain      TEXT NOT NULL,
    contact_name        TEXT,
    channel             TEXT,            -- LinkedIn | Email | X | Job application | Intro | Call | Other
    touch_type          TEXT,            -- First touch | Follow-up | Reply | Call | Proposal | Other
    angle               TEXT,
    outcome             TEXT,            -- Sent | No reply | Replied - positive | Replied - negative | Call booked | Not a fit
    next_step           TEXT,
    notes               TEXT,
    UNIQUE (touch_date, company_domain, contact_name, channel, touch_type)
);
CREATE INDEX IF NOT EXISTS idx_outreach_domain ON outreach_log(company_domain);
