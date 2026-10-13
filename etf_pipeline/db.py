"""Database access: engine, schema bootstrap, and fast bulk-load helpers."""
from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Iterator, Sequence

import numpy as np
import pandas as pd
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import Connection

from .config import get_settings

log = logging.getLogger(__name__)

DB_DIR = Path(__file__).resolve().parent.parent / "db"


@lru_cache
def get_engine() -> Engine:
    return create_engine(get_settings().database_url, pool_pre_ping=True, future=True)


def wait_for_db(timeout: int = 60) -> None:
    """Block until Postgres accepts connections (containers start in parallel)."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            with get_engine().connect() as conn:
                conn.execute(text("SELECT 1"))
            return
        except Exception as exc:  # noqa: BLE001 - any connection error means "not yet"
            if time.monotonic() > deadline:
                raise RuntimeError(f"Database not reachable after {timeout}s") from exc
            log.info("Waiting for database...")
            time.sleep(2)


def ensure_schema() -> None:
    """Apply db/schema.sql and db/views.sql. Both are idempotent."""
    with get_engine().begin() as conn, conn.connection.cursor() as cur:
        for name in ("schema.sql", "views.sql"):
            cur.execute((DB_DIR / name).read_text())  # no params -> '%' in comments is safe
    log.info("Schema ensured")


@contextmanager
def transaction() -> Iterator[Connection]:
    with get_engine().begin() as conn:
        yield conn


def read_sql(sql: str, params: dict | None = None, **kwargs) -> pd.DataFrame:
    with get_engine().connect() as conn:
        return pd.read_sql(text(sql), conn, params=params, **kwargs)


# --------------------------------------------------------------- reference ids --
def source_id(conn: Connection, name: str) -> int:
    return conn.execute(text("SELECT source_id FROM data_source WHERE name = :n"), {"n": name}).scalar_one()


def asset_ids(conn: Connection) -> dict[str, int]:
    rows = conn.execute(text("SELECT symbol, asset_id FROM asset")).all()
    return {sym: aid for sym, aid in rows}


def upsert_asset(conn: Connection, symbol: str, **attrs) -> int:
    cols = ["symbol", *attrs.keys()]
    updates = ", ".join(f"{c} = COALESCE(EXCLUDED.{c}, asset.{c})" for c in attrs) or "symbol = EXCLUDED.symbol"
    sql = (
        f"INSERT INTO asset ({', '.join(cols)}) VALUES ({', '.join(':' + c for c in cols)}) "
        f"ON CONFLICT (symbol) DO UPDATE SET {updates}, updated_at = now() RETURNING asset_id"
    )
    return conn.execute(text(sql), {"symbol": symbol, **attrs}).scalar_one()


# ------------------------------------------------------------------ bulk load --
def _clean(df: pd.DataFrame) -> list[tuple]:
    """DataFrame -> list of tuples with NaN/NaT/inf mapped to None and numpy scalars unboxed."""
    out = df.replace([np.inf, -np.inf], np.nan).astype(object)
    out = out.where(pd.notna(out), None)
    rows = []
    for rec in out.itertuples(index=False, name=None):
        rows.append(tuple(v.item() if isinstance(v, np.generic) else v for v in rec))
    return rows


def upsert_df(
    conn: Connection, table: str, df: pd.DataFrame, key: Sequence[str], update: bool = True
) -> int:
    """INSERT ... ON CONFLICT (key) DO UPDATE (or DO NOTHING when update=False)."""
    if df.empty:
        return 0
    cols = list(df.columns)
    non_key = [c for c in cols if c not in key]
    action = (
        "DO UPDATE SET " + ", ".join(f"{c} = EXCLUDED.{c}" for c in non_key)
        if non_key and update
        else "DO NOTHING"
    )
    sql = (
        f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))}) "
        f"ON CONFLICT ({', '.join(key)}) {action}"
    )
    rows = _clean(df[cols])
    with conn.connection.cursor() as cur:  # raw psycopg cursor: pipelined executemany
        cur.executemany(sql, rows)
    return len(rows)


def copy_df(conn: Connection, table: str, df: pd.DataFrame) -> int:
    """Append df to table with Postgres COPY — ~50x faster than INSERT for big model outputs."""
    if df.empty:
        return 0
    cols = list(df.columns)
    rows = _clean(df)
    with conn.connection.cursor() as cur:
        with cur.copy(f"COPY {table} ({', '.join(cols)}) FROM STDIN") as copy:
            for row in rows:
                copy.write_row(row)
    return len(rows)


def replace_table(conn: Connection, table: str, df: pd.DataFrame, where: str = "", params: dict | None = None) -> int:
    """Delete matching rows then COPY df in, inside the caller's transaction."""
    conn.execute(text(f"DELETE FROM {table} {('WHERE ' + where) if where else ''}"), params or {})
    return copy_df(conn, table, df)


# ------------------------------------------------------------------- model runs --
def start_run(run_type: str) -> int:
    with transaction() as conn:
        return conn.execute(
            text("INSERT INTO model_run (run_type) VALUES (:t) RETURNING run_id"), {"t": run_type}
        ).scalar_one()


def finish_run(run_id: int, status: str, as_of: date | None = None, message: str | None = None) -> None:
    with transaction() as conn:
        conn.execute(
            text(
                "UPDATE model_run SET status = :s, finished_at = now(), "
                "as_of_date = COALESCE(:d, as_of_date), message = :m WHERE run_id = :r"
            ),
            {"s": status, "d": as_of, "m": message, "r": run_id},
        )


def prune_runs(keep: int) -> None:
    """Drop all but the newest `keep` runs per type (child rows cascade)."""
    with transaction() as conn:
        conn.execute(
            text(
                """
                DELETE FROM model_run WHERE run_id IN (
                    SELECT run_id FROM (
                        SELECT run_id, row_number() OVER (PARTITION BY run_type ORDER BY run_id DESC) AS rn
                        FROM model_run
                    ) ranked WHERE rn > :keep
                )
                """
            ),
            {"keep": keep},
        )
        conn.execute(text("DELETE FROM live_quote WHERE quote_ts < now() - interval '30 days'"))


def log_ingestion(conn: Connection, dataset: str, rows: int, status: str, source: str | None, message: str | None = None) -> None:
    conn.execute(
        text(
            "INSERT INTO ingestion_log (source_id, dataset, rows_loaded, status, message) "
            "VALUES ((SELECT source_id FROM data_source WHERE name = :src), :ds, :n, :st, :msg)"
        ),
        {"src": source, "ds": dataset, "n": rows, "st": status, "msg": (message or "")[:1000]},
    )
