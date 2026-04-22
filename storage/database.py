"""
SQLite persistence layer.

Tables:
  trades            — every executed (or dry-run) order
  fgi_cache         — hourly FGI snapshots for history rebuilding
  portfolio_snapshots — periodic net-worth snapshots
  signal_outcomes   — closed-trade outcomes fed back to BayesianUpdater
  bayesian_state    — serialised BayesianUpdater parameters
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Generator

import os

logger = logging.getLogger(__name__)

_data_dir = Path(os.environ.get("DATA_DIR", Path(__file__).parent))
DB_PATH = _data_dir / "bot.db"


@contextmanager
def _conn() -> Generator[sqlite3.Connection, None, None]:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def init_db() -> None:
    with _conn() as con:
        con.executescript("""
            CREATE TABLE IF NOT EXISTS trades (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp       INTEGER NOT NULL,
                action          TEXT    NOT NULL,
                symbol          TEXT    NOT NULL,
                quantity        REAL    NOT NULL,
                price           REAL    NOT NULL,
                usd_amount      REAL    NOT NULL,
                fgi_value       INTEGER,
                z_score         REAL,
                confidence      REAL,
                order_id        TEXT,
                dry_run         INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS fgi_cache (
                timestamp       INTEGER PRIMARY KEY,
                value           INTEGER NOT NULL,
                label           TEXT    NOT NULL,
                source          TEXT    NOT NULL
            );

            CREATE TABLE IF NOT EXISTS portfolio_snapshots (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp       INTEGER NOT NULL,
                btc_balance     REAL    NOT NULL,
                usd_balance     REAL    NOT NULL,
                btc_price       REAL    NOT NULL,
                total_usd_value REAL    NOT NULL
            );

            CREATE TABLE IF NOT EXISTS signal_outcomes (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_id        INTEGER NOT NULL,
                action          TEXT    NOT NULL,
                entry_price     REAL    NOT NULL,
                exit_price      REAL,
                closed_at       INTEGER,
                success         INTEGER
            );

            CREATE TABLE IF NOT EXISTS bayesian_state (
                key             TEXT PRIMARY KEY,
                value           TEXT NOT NULL
            );
        """)
    logger.debug("Database initialised at %s", DB_PATH)


# ── Trades ────────────────────────────────────────────────────────────────────

def insert_trade(
    action: str,
    symbol: str,
    quantity: float,
    price: float,
    usd_amount: float,
    fgi_value: int,
    z_score: float,
    confidence: float,
    order_id: str,
    dry_run: bool,
) -> int:
    ts = int(time.time())
    with _conn() as con:
        cur = con.execute(
            """INSERT INTO trades
               (timestamp, action, symbol, quantity, price, usd_amount,
                fgi_value, z_score, confidence, order_id, dry_run)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (ts, action, symbol, quantity, price, usd_amount,
             fgi_value, z_score, confidence, order_id, int(dry_run)),
        )
        trade_id = cur.lastrowid or 0

    from kv import available as kv_available, kv_push_trade
    if kv_available():
        kv_push_trade({
            "id": trade_id, "timestamp": ts, "action": action,
            "symbol": symbol, "quantity": quantity, "price": price,
            "usd_amount": usd_amount, "fgi_value": fgi_value,
            "z_score": z_score, "confidence": confidence, "dry_run": dry_run,
        })
    return trade_id


def get_recent_trades(limit: int = 20) -> list[dict]:  # type: ignore[type-arg]
    with _conn() as con:
        rows = con.execute(
            "SELECT * FROM trades ORDER BY timestamp DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


# ── FGI cache ─────────────────────────────────────────────────────────────────

def cache_fgi(timestamp: int, value: int, label: str, source: str) -> None:
    with _conn() as con:
        con.execute(
            "INSERT OR REPLACE INTO fgi_cache (timestamp, value, label, source) VALUES (?,?,?,?)",
            (timestamp, value, label, source),
        )


def get_cached_fgi_history(days: int = 180) -> list[dict]:  # type: ignore[type-arg]
    cutoff = int(time.time()) - days * 86400
    with _conn() as con:
        rows = con.execute(
            "SELECT * FROM fgi_cache WHERE timestamp >= ? ORDER BY timestamp ASC",
            (cutoff,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_latest_cached_fgi() -> dict | None:  # type: ignore[type-arg]
    with _conn() as con:
        row = con.execute("SELECT * FROM fgi_cache ORDER BY timestamp DESC LIMIT 1").fetchone()
    return dict(row) if row else None


# ── Portfolio snapshots ───────────────────────────────────────────────────────

def insert_snapshot(
    btc_balance: float,
    usd_balance: float,
    btc_price: float,
) -> None:
    total = btc_balance * btc_price + usd_balance
    with _conn() as con:
        con.execute(
            """INSERT INTO portfolio_snapshots
               (timestamp, btc_balance, usd_balance, btc_price, total_usd_value)
               VALUES (?,?,?,?,?)""",
            (int(time.time()), btc_balance, usd_balance, btc_price, total),
        )


def get_snapshots(limit: int = 30) -> list[dict]:  # type: ignore[type-arg]
    with _conn() as con:
        rows = con.execute(
            "SELECT * FROM portfolio_snapshots ORDER BY timestamp DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


# ── Signal outcomes ───────────────────────────────────────────────────────────

def insert_outcome(trade_id: int, action: str, entry_price: float) -> int:
    with _conn() as con:
        cur = con.execute(
            "INSERT INTO signal_outcomes (trade_id, action, entry_price) VALUES (?,?,?)",
            (trade_id, action, entry_price),
        )
        return cur.lastrowid or 0


def close_outcome(outcome_id: int, exit_price: float, success: bool) -> None:
    with _conn() as con:
        con.execute(
            "UPDATE signal_outcomes SET exit_price=?, closed_at=?, success=? WHERE id=?",
            (exit_price, int(time.time()), int(success), outcome_id),
        )


def get_open_outcomes() -> list[dict]:  # type: ignore[type-arg]
    with _conn() as con:
        rows = con.execute(
            "SELECT * FROM signal_outcomes WHERE closed_at IS NULL"
        ).fetchall()
    return [dict(r) for r in rows]


# ── Bayesian state ────────────────────────────────────────────────────────────

def save_bayesian_state(state: dict[str, float]) -> None:
    # KV takes priority on Vercel; SQLite used locally
    from kv import available as kv_available, kv_set
    if kv_available():
        kv_set("bayesian_state", state)
        return
    with _conn() as con:
        con.execute(
            "INSERT OR REPLACE INTO bayesian_state (key, value) VALUES ('state', ?)",
            (json.dumps(state),),
        )


def load_bayesian_state() -> dict[str, float] | None:
    from kv import available as kv_available, kv_get
    if kv_available():
        return kv_get("bayesian_state")  # type: ignore[return-value]
    with _conn() as con:
        row = con.execute("SELECT value FROM bayesian_state WHERE key='state'").fetchone()
    if row:
        return json.loads(row["value"])  # type: ignore[no-any-return]
    return None
