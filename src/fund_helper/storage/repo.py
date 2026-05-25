"""Storage repositories backed by sqlite (primary) and parquet (export)."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from ..domain import Fund, FundType, NavSeries
from .db import tx


# ---------------------------------------------------------------- fund table

class FundRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def upsert(self, fund: Fund, source: str) -> None:
        self.conn.execute(
            """
            INSERT INTO fund(code, name, fund_type, source, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(code) DO UPDATE SET
              name=excluded.name,
              fund_type=excluded.fund_type,
              source=excluded.source,
              updated_at=excluded.updated_at
            """,
            (fund.code, fund.name, fund.fund_type.value, source,
             datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds")),
        )

    def get(self, code: str) -> Fund | None:
        row = self.conn.execute(
            "SELECT code, name, fund_type FROM fund WHERE code=?", (code,)
        ).fetchone()
        if not row:
            return None
        return Fund(code=row[0], name=row[1] or "",
                    fund_type=FundType(row[2]) if row[2] else FundType.OTHER)


# ---------------------------------------------------------------- nav (sqlite)

class NavRepo:
    """Primary NAV store: one row per (code, trade_date)."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    # --- read ---------------------------------------------------------------
    def load(self, code: str,
             start: str | None = None,
             end:   str | None = None) -> NavSeries | None:
        sql = ("SELECT trade_date, unit_nav, acc_nav, daily_return "
               "FROM nav_daily WHERE code = ?")
        params: list[object] = [code]
        if start:
            sql += " AND trade_date >= ?"
            params.append(start)
        if end:
            sql += " AND trade_date <= ?"
            params.append(end)
        sql += " ORDER BY trade_date"
        df = pd.read_sql_query(sql, self.conn, params=params)
        if df.empty:
            return None
        df["trade_date"] = pd.to_datetime(df["trade_date"])
        df = df.set_index("trade_date")
        df.index.name = "trade_date"
        return NavSeries(code=code, frame=df)

    def covered_dates(self, code: str, start: str, end: str) -> set[str]:
        rows = self.conn.execute(
            "SELECT trade_date FROM nav_daily "
            "WHERE code=? AND trade_date BETWEEN ? AND ?",
            (code, start, end),
        ).fetchall()
        return {r[0] for r in rows}

    def latest_fetched_at(self, code: str) -> datetime | None:
        row = self.conn.execute(
            "SELECT MAX(fetched_at) FROM nav_daily WHERE code=?", (code,)
        ).fetchone()
        if not row or not row[0]:
            return None
        try:
            return datetime.fromisoformat(row[0])
        except ValueError:
            return None

    def max_trade_date(self, code: str) -> str | None:
        row = self.conn.execute(
            "SELECT MAX(trade_date) FROM nav_daily WHERE code=?", (code,)
        ).fetchone()
        return row[0] if row and row[0] else None

    # --- write --------------------------------------------------------------
    def upsert_series(self, series: NavSeries, source: str) -> int:
        if series.frame.empty:
            return 0
        now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds")
        rows = []
        for ts, row in series.frame.iterrows():
            rows.append((
                series.code,
                ts.strftime("%Y-%m-%d"),
                _to_db_num(row.get("unit_nav")),
                _to_db_num(row.get("acc_nav")),
                _to_db_num(row.get("daily_return")),
                source,
                now,
            ))
        with tx(self.conn):
            self.conn.executemany(
                """
                INSERT INTO nav_daily
                  (code, trade_date, unit_nav, acc_nav, daily_return, source, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(code, trade_date) DO UPDATE SET
                  unit_nav     = excluded.unit_nav,
                  acc_nav      = excluded.acc_nav,
                  daily_return = excluded.daily_return,
                  source       = excluded.source,
                  fetched_at   = excluded.fetched_at
                """,
                rows,
            )
        return len(rows)

    def log_fetch(self, code: str, window_start: str, window_end: str,
                  rows_returned: int, source: str, status: str,
                  message: str | None = None) -> None:
        self.conn.execute(
            """
            INSERT INTO nav_fetch_log
              (code, window_start, window_end, rows_returned, source, status, message, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (code, window_start, window_end, rows_returned, source, status, message,
             datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds")),
        )


def _to_db_num(v) -> float | None:
    if v is None:
        return None
    try:
        fv = float(v)
    except (TypeError, ValueError):
        return None
    if pd.isna(fv):
        return None
    return fv


# ----------------------------------------------------- legacy parquet exporter

class HoldingRepo:
    """Top-10 holdings store (unchanged, sqlite)."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS holding (
                code TEXT, report_date TEXT, ticker TEXT, name TEXT,
                weight REAL, industry TEXT,
                PRIMARY KEY (code, report_date, ticker)
            )
        """)


class NavParquetRepo:
    """Compat helper: dump NAV to parquet for batch tooling."""

    def __init__(self, parquet_dir: str | Path) -> None:
        self.dir = Path(parquet_dir)
        self.dir.mkdir(parents=True, exist_ok=True)

    def path(self, code: str) -> Path:
        return self.dir / f"{code}.parquet"

    def save(self, series: NavSeries) -> None:
        series.frame.to_parquet(self.path(series.code))

    def load(self, code: str) -> NavSeries | None:
        p = self.path(code)
        if not p.exists():
            return None
        df = pd.read_parquet(p)
        return NavSeries(code, df)
