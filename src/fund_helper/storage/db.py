"""SQLite schema + connection helpers."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

SCHEMA_VERSION = "6"

DDL: list[str] = [
    """
    CREATE TABLE IF NOT EXISTS fund (
        code        TEXT PRIMARY KEY,
        name        TEXT,
        fund_type   TEXT,
        source      TEXT,
        updated_at  TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS nav_daily (
        code          TEXT NOT NULL,
        trade_date    TEXT NOT NULL,
        unit_nav      REAL,
        acc_nav       REAL,
        daily_return  REAL,
        source        TEXT NOT NULL,
        fetched_at    TEXT NOT NULL,
        PRIMARY KEY (code, trade_date)
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_nav_code_date ON nav_daily(code, trade_date)",
    """
    CREATE TABLE IF NOT EXISTS nav_fetch_log (
        code           TEXT NOT NULL,
        window_start   TEXT NOT NULL,
        window_end     TEXT NOT NULL,
        rows_returned  INTEGER NOT NULL,
        source         TEXT NOT NULL,
        status         TEXT NOT NULL,
        message        TEXT,
        fetched_at     TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS fund_realtime_snapshot (
        code          TEXT PRIMARY KEY,
        name          TEXT,
        nav_date      TEXT,
        unit_nav      REAL,
        estimate_nav  REAL,
        estimate_pct  REAL,
        estimate_time TEXT,
        source        TEXT NOT NULL,
        fetched_at    TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS index_meta (
        secid       TEXT PRIMARY KEY,
        name        TEXT,
        market      TEXT,
        group_key   TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS index_intraday (
        secid       TEXT NOT NULL,
        trade_date  TEXT NOT NULL,
        ts          TEXT NOT NULL,
        open        REAL,
        close       REAL,
        high        REAL,
        low         REAL,
        volume      REAL,
        amount      REAL,
        avg         REAL,
        fetched_at  TEXT NOT NULL,
        PRIMARY KEY (secid, ts)
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_idx_secid_date ON index_intraday(secid, trade_date)",
    """
    CREATE TABLE IF NOT EXISTS index_daily (
        secid       TEXT NOT NULL,
        trade_date  TEXT NOT NULL,
        open        REAL,
        close       REAL,
        high        REAL,
        low         REAL,
        volume      REAL,
        pct_change  REAL,
        fetched_at  TEXT NOT NULL,
        PRIMARY KEY (secid, trade_date)
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_idx_secid_d ON index_daily(secid, trade_date)",
    """
    CREATE TABLE IF NOT EXISTS index_snapshot (
        secid        TEXT PRIMARY KEY,
        name         TEXT,
        market       TEXT,
        trade_date   TEXT,
        pre_close    REAL,
        now          REAL,
        open         REAL,
        delta        REAL,
        pct          REAL,
        high         REAL,
        low          REAL,
        volume       REAL,
        amount       REAL,
        last_ts      TEXT,
        fetched_at   TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS market_flow_snapshot (
        scope       TEXT NOT NULL,
        item        TEXT NOT NULL,
        trade_date  TEXT,
        net_amount  REAL,
        net_pct     REAL,
        main_net_amount REAL,
        super_large_net_amount REAL,
        large_net_amount REAL,
        medium_net_amount REAL,
        small_net_amount REAL,
        up_count    INTEGER,
        flat_count  INTEGER,
        down_count  INTEGER,
        source      TEXT NOT NULL,
        fetched_at  TEXT NOT NULL,
        raw_json    TEXT,
        PRIMARY KEY (scope, item, source)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS market_margin_snapshot (
        scope              TEXT NOT NULL,
        trade_date         TEXT,
        financing_buy      REAL,
        financing_balance  REAL,
        securities_sell_volume REAL,
        securities_balance REAL,
        margin_balance     REAL,
        source             TEXT NOT NULL,
        fetched_at         TEXT NOT NULL,
        raw_json           TEXT,
        PRIMARY KEY (scope, source)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS index_valuation_snapshot (
        secid          TEXT NOT NULL,
        code           TEXT NOT NULL,
        name           TEXT NOT NULL,
        trade_date     TEXT,
        pe             REAL,
        pb             REAL,
        dividend_yield REAL,
        pe_percentile  REAL,
        pb_percentile  REAL,
        source         TEXT NOT NULL,
        fetched_at     TEXT NOT NULL,
        raw_json       TEXT,
        PRIMARY KEY (secid, source)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS fund_peer_rank_snapshot (
        code        TEXT NOT NULL,
        name        TEXT,
        category    TEXT NOT NULL,
        nav_date    TEXT,
        ret_1w      REAL,
        ret_1m      REAL,
        ret_3m      REAL,
        ret_6m      REAL,
        ret_1y      REAL,
        ret_ytd     REAL,
        rank_1w     INTEGER,
        rank_1m     INTEGER,
        rank_3m     INTEGER,
        rank_6m     INTEGER,
        rank_1y     INTEGER,
        rank_ytd    INTEGER,
        total       INTEGER,
        source      TEXT NOT NULL,
        fetched_at  TEXT NOT NULL,
        raw_json    TEXT,
        PRIMARY KEY (code, category)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS trading_calendar (
        exchange    TEXT NOT NULL,
        trade_date  TEXT NOT NULL,
        source      TEXT NOT NULL,
        fetched_at  TEXT NOT NULL,
        PRIMARY KEY (exchange, trade_date)
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_trcal_exchange_date ON trading_calendar(exchange, trade_date)",
    """
    CREATE TABLE IF NOT EXISTS fund_top_holding (
        fund_code   TEXT NOT NULL,
        season      TEXT NOT NULL,
        rank        INTEGER NOT NULL,
        stock_code  TEXT NOT NULL,
        stock_name  TEXT,
        pct_nav     REAL,
        shares      REAL,
        market_value REAL,
        fetched_at  TEXT NOT NULL,
        PRIMARY KEY (fund_code, season, rank)
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_fth_stock ON fund_top_holding(stock_code)",
    """
    CREATE TABLE IF NOT EXISTS stock_meta (
        stock_code  TEXT PRIMARY KEY,
        stock_name  TEXT,
        market      TEXT,
        updated_at  TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS stock_daily (
        stock_code  TEXT NOT NULL,
        trade_date  TEXT NOT NULL,
        open        REAL,
        close       REAL,
        high        REAL,
        low         REAL,
        volume      REAL,
        amount      REAL,
        pct_change  REAL,
        fetched_at  TEXT NOT NULL,
        PRIMARY KEY (stock_code, trade_date)
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_sd_code_date ON stock_daily(stock_code, trade_date)",
    """
    CREATE TABLE IF NOT EXISTS stock_fetch_log (
        stock_code     TEXT NOT NULL,
        window_start   TEXT NOT NULL,
        window_end     TEXT NOT NULL,
        rows_returned  INTEGER NOT NULL,
        source         TEXT NOT NULL,
        status         TEXT NOT NULL,
        message        TEXT,
        fetched_at     TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS sector_snapshot (
        category   TEXT NOT NULL,
        label      TEXT NOT NULL,
        name       TEXT NOT NULL,
        companies  INTEGER,
        avg_price  REAL,
        delta      REAL,
        pct        REAL,
        total_vol  REAL,
        total_amt  REAL,
        leader_code TEXT,
        leader_name TEXT,
        leader_pct  REAL,
        leader_price REAL,
        leader_delta REAL,
        fetched_at TEXT NOT NULL,
        PRIMARY KEY (category, label)
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_sec_cat_pct ON sector_snapshot(category, pct DESC)",
    """
    CREATE TABLE IF NOT EXISTS sector_daily (
        category   TEXT NOT NULL,
        label      TEXT NOT NULL,
        trade_date TEXT NOT NULL,
        open       REAL,
        close      REAL,
        high       REAL,
        low        REAL,
        volume     REAL,
        amount     REAL,
        pct_change REAL,
        fetched_at TEXT NOT NULL,
        PRIMARY KEY (category, label, trade_date)
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_secd_label_date ON sector_daily(category, label, trade_date DESC)",
    """
    CREATE TABLE IF NOT EXISTS sector_favorite (
        category   TEXT NOT NULL,
        label      TEXT NOT NULL,
        name       TEXT NOT NULL,
        sort_order INTEGER NOT NULL DEFAULT 0,
        added_at   TEXT NOT NULL,
        PRIMARY KEY (category, label)
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_secfav_order ON sector_favorite(sort_order ASC, added_at ASC)",
    """
    CREATE TABLE IF NOT EXISTS news_item (
        id              TEXT PRIMARY KEY,
        category        TEXT NOT NULL,
        source          TEXT NOT NULL,
        title           TEXT NOT NULL,
        content         TEXT,
        url             TEXT,
        published_at    TEXT NOT NULL,
        sentiment       INTEGER NOT NULL DEFAULT 0,
        pos_hits        TEXT,
        neg_hits        TEXT,
        relevance_score REAL NOT NULL DEFAULT 0,
        themes          TEXT,
        kw_hits         TEXT,
        fetched_at      TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_news_cat_pub ON news_item(category, published_at DESC)",
    "CREATE INDEX IF NOT EXISTS ix_news_fetched ON news_item(fetched_at DESC)",
    """
    CREATE TABLE IF NOT EXISTS company_news_match (
        company_key    TEXT NOT NULL,
        company_code   TEXT,
        company_name   TEXT NOT NULL,
        company_source TEXT NOT NULL,
        fund_codes     TEXT,
        exposure       REAL,
        news_id        TEXT NOT NULL,
        title          TEXT NOT NULL,
        source         TEXT,
        url            TEXT,
        published_at   TEXT,
        category       TEXT,
        sentiment      INTEGER NOT NULL DEFAULT 0,
        score          REAL NOT NULL DEFAULT 0,
        matched_terms  TEXT,
        topics         TEXT,
        impact_note    TEXT,
        fetched_at     TEXT NOT NULL,
        raw_json       TEXT,
        PRIMARY KEY (company_key, news_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_company_news_pub ON company_news_match(published_at DESC, score DESC)",
    "CREATE INDEX IF NOT EXISTS ix_company_news_company ON company_news_match(company_key, score DESC)",
    """
    CREATE TABLE IF NOT EXISTS meta_kv (
        k TEXT PRIMARY KEY,
        v TEXT
    )
    """,
]


def connect(db_path: str | Path) -> sqlite3.Connection:
    p = Path(db_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(p, isolation_level=None, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    init_schema(conn)
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    for stmt in DDL:
        conn.execute(stmt)
    # backfill columns for pre-existing news_item tables (additive only)
    _existing_cols = {r[1] for r in conn.execute("PRAGMA table_info(news_item)").fetchall()}
    for col, ddl in (
        ("relevance_score", "REAL NOT NULL DEFAULT 0"),
        ("themes",          "TEXT"),
        ("kw_hits",         "TEXT"),
    ):
        if col not in _existing_cols:
            conn.execute(f"ALTER TABLE news_item ADD COLUMN {col} {ddl}")
    _idx_cols = {r[1] for r in conn.execute("PRAGMA table_info(index_snapshot)").fetchall()}
    for col, ddl in (
        ("open", "REAL"),
        ("volume", "REAL"),
        ("amount", "REAL"),
    ):
        if col not in _idx_cols:
            conn.execute(f"ALTER TABLE index_snapshot ADD COLUMN {col} {ddl}")
    conn.execute(
        "INSERT INTO meta_kv(k, v) VALUES('schema_version', ?) "
        "ON CONFLICT(k) DO UPDATE SET v=excluded.v",
        (SCHEMA_VERSION,),
    )


@contextmanager
def tx(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Light wrapper for explicit transactions (since we use isolation_level=None)."""
    conn.execute("BEGIN")
    try:
        yield conn
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
