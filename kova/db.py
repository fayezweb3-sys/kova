"""SQLite storage for every Kova source, joined on the DIN.

One file, zero setup. All SQL is plain enough to move to Postgres later.
Each source loader replaces its own tables inside a transaction, and before it
does, kova.changes snapshots the old rows so the difference becomes change
events (what watchlist alerts are made of).
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
-- Health Canada Drug Product Database: one row per product (drug_code), derived from raw_dpd_* tables
CREATE TABLE IF NOT EXISTS product (
    drug_code        TEXT PRIMARY KEY,
    din              TEXT,
    brand_name       TEXT,
    descriptor       TEXT,
    company          TEXT,
    class            TEXT,
    dataset          TEXT,             -- marketed | approved | dormant | cancelled
    current_status   TEXT,             -- e.g. MARKETED, CANCELLED POST MARKET
    status_date      TEXT,
    first_marketed   TEXT,
    form_key         TEXT,
    route_key        TEXT,
    schedules        TEXT,
    atc_code         TEXT,
    atc_name         TEXT,
    ingredient_key   TEXT,
    equivalence_key  TEXT,             -- ingredients@strength # form # route
    moiety_key       TEXT,             -- ingredients only, for "same drug, other strengths/forms"
    n_ingredients    INTEGER
);
CREATE INDEX IF NOT EXISTS idx_product_din   ON product(din);
CREATE INDEX IF NOT EXISTS idx_product_brand ON product(brand_name);
CREATE INDEX IF NOT EXISTS idx_product_equiv ON product(equivalence_key);
CREATE INDEX IF NOT EXISTS idx_product_moiety ON product(moiety_key);

CREATE TABLE IF NOT EXISTS product_ingredient (
    drug_code     TEXT NOT NULL,
    moiety        TEXT NOT NULL,
    ingredient    TEXT,
    concentration TEXT
);
CREATE INDEX IF NOT EXISTS idx_pi_moiety ON product_ingredient(moiety);
CREATE INDEX IF NOT EXISTS idx_pi_code   ON product_ingredient(drug_code);

CREATE TABLE IF NOT EXISTS status_history (
    drug_code    TEXT NOT NULL,
    status       TEXT,
    history_date TEXT,
    is_current   INTEGER
);
CREATE INDEX IF NOT EXISTS idx_sh_code ON status_history(drug_code);

-- Ontario Drug Benefit Formulary (XML data extract): one row per DIN/PIN listing
CREATE TABLE IF NOT EXISTS odb_listing (
    din               TEXT PRIMARY KEY,
    is_pin            INTEGER,
    name              TEXT,
    generic_name      TEXT,
    generic_id        TEXT,
    strength          TEXT,
    dosage_form       TEXT,
    group_id          TEXT,          -- pcg9 id: products in one group are interchangeable candidates
    group_size        INTEGER,
    manufacturer_id   TEXT,
    manufacturer      TEXT,
    unit_price        REAL,          -- individualPrice
    amount_odb_pays   REAL,          -- amountMOHLTCPays, when it differs from the price
    daily_cost        REAL,
    listing_date      TEXT,
    benefit           INTEGER,       -- 0 when notABenefit
    interchangeable   INTEGER,
    limited_use       INTEGER,
    lu_codes          TEXT,          -- comma-separated reason-for-use codes
    temporary         INTEGER,
    flags             TEXT,          -- raw sec3/sec3b/sec3bEAP/sec12/dinStatus attributes
    therapeutic_class TEXT,          -- pcg2 name
    formulary_date    TEXT
);
CREATE INDEX IF NOT EXISTS idx_odb_group ON odb_listing(group_id);
CREATE INDEX IF NOT EXISTS idx_odb_generic ON odb_listing(generic_id);

-- BC PharmaCare downloadable data file: one row per DIN per plan per period
CREATE TABLE IF NOT EXISTS bc_coverage (
    din              TEXT NOT NULL,
    plan             TEXT,
    plan_desc        TEXT,
    eff_date         TEXT,
    end_date         TEXT,
    benefit_groups   TEXT,
    lca_ind          TEXT,
    pay_generic      TEXT,
    brand_name       TEXT,
    manufacturer     TEXT,
    generic_name     TEXT,
    dosage_form      TEXT,
    fed_reg_code     TEXT,
    max_price        REAL,
    lca_price        REAL,
    rdp_cat          TEXT,
    rdp_subcat       TEXT,
    rdp_price        REAL,
    max_days_supply  INTEGER,
    qty_limit        INTEGER,
    list_date        TEXT,
    limited_use      TEXT,
    is_current       INTEGER
);
CREATE INDEX IF NOT EXISTS idx_bc_din ON bc_coverage(din);

-- BC Low Cost Alternative and Reference Drug Program files
CREATE TABLE IF NOT EXISTS bc_lca (
    din            TEXT NOT NULL,
    lca_category   TEXT,
    chemical_name  TEXT,
    drug_name      TEXT,
    manufacturer   TEXT,
    full_partial   TEXT,
    plan_b_only    TEXT,
    rdp            TEXT,
    special_auth   TEXT,
    provisional    TEXT,
    max_price      REAL,
    lca_price      REAL
);
CREATE INDEX IF NOT EXISTS idx_bclca_din ON bc_lca(din);
CREATE INDEX IF NOT EXISTS idx_bclca_cat ON bc_lca(lca_category);

CREATE TABLE IF NOT EXISTS bc_rdp (
    din          TEXT NOT NULL,
    rdp_category TEXT,
    generic_name TEXT,
    drug_name    TEXT,
    manufacturer TEXT,
    rdp          TEXT
);
CREATE INDEX IF NOT EXISTS idx_bcrdp_din ON bc_rdp(din);

-- Health Product Shortages Canada: shortage and discontinuation reports
CREATE TABLE IF NOT EXISTS shortage_report (
    id                 TEXT PRIMARY KEY,
    kind               TEXT,         -- shortage | discontinuance
    din                TEXT,
    status             TEXT,         -- e.g. active_confirmed, anticipated_shortage, resolved
    is_open            INTEGER,
    brand_name         TEXT,
    common_name        TEXT,
    ingredients        TEXT,
    strength           TEXT,
    dosage_form        TEXT,
    route              TEXT,
    company            TEXT,
    atc_code           TEXT,
    tier_3             INTEGER,
    late_submission    INTEGER,
    reason             TEXT,
    anticipated_start  TEXT,
    actual_start       TEXT,
    estimated_end      TEXT,
    actual_end         TEXT,
    discontinued_on    TEXT,
    comments           TEXT,
    created            TEXT,
    updated            TEXT
);
CREATE INDEX IF NOT EXISTS idx_sr_din ON shortage_report(din);

-- Health Canada recalls and safety alerts (health products only)
CREATE TABLE IF NOT EXISTS recall (
    nid          TEXT PRIMARY KEY,
    title        TEXT,
    url          TEXT,
    organization TEXT,
    category     TEXT,
    recall_class TEXT,
    issue        TEXT,
    product      TEXT,
    published    TEXT,
    updated      TEXT,
    text         TEXT
);
CREATE TABLE IF NOT EXISTS recall_din (
    nid   TEXT NOT NULL,
    din   TEXT NOT NULL,
    how   TEXT                          -- 'din' when the DIN is printed in the notice, 'name' when matched by product name
);
CREATE INDEX IF NOT EXISTS idx_rd_din ON recall_din(din);

-- Bookkeeping
CREATE TABLE IF NOT EXISTS source_run (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    source    TEXT NOT NULL,
    run_at    TEXT NOT NULL,
    origin    TEXT,
    rows      INTEGER,
    version   TEXT                       -- the source's own date or edition, when it has one
);

CREATE TABLE IF NOT EXISTS change_event (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    at        TEXT NOT NULL,
    source    TEXT NOT NULL,
    kind      TEXT NOT NULL,             -- shortage | recall | coverage | generic | status
    din       TEXT,
    summary   TEXT NOT NULL,
    detail    TEXT                       -- JSON
);
CREATE INDEX IF NOT EXISTS idx_ce_din ON change_event(din);

CREATE TABLE IF NOT EXISTS watchlist (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,
    email      TEXT,
    webhook    TEXT,
    secret     TEXT,
    kinds      TEXT NOT NULL DEFAULT 'shortage,recall,coverage,generic,status',
    created    TEXT NOT NULL,
    last_event INTEGER NOT NULL DEFAULT 0   -- highest change_event id already delivered
);
CREATE TABLE IF NOT EXISTS watch_item (
    watchlist_id INTEGER NOT NULL REFERENCES watchlist(id) ON DELETE CASCADE,
    din          TEXT NOT NULL,
    PRIMARY KEY (watchlist_id, din)
);
"""


def connect(path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    return conn


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def log_run(conn: sqlite3.Connection, source: str, origin: str, rows: int, version: str | None = None) -> None:
    conn.execute("INSERT INTO source_run (source, run_at, origin, rows, version) VALUES (?,?,?,?,?)",
                 (source, now(), origin, rows, version))


def last_runs(conn: sqlite3.Connection) -> dict[str, dict]:
    out = {}
    for r in conn.execute("""SELECT source, run_at, rows, version, origin FROM source_run s
                             WHERE id = (SELECT MAX(id) FROM source_run t WHERE t.source = s.source)"""):
        out[r["source"]] = dict(r)
    return out
