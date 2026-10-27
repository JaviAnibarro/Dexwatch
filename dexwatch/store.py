"""Penyimpanan SQLite: dedupe pool yang sudah pernah dilaporkan + histori."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time

from .pools import PoolInfo

_SCHEMA = """
CREATE TABLE IF NOT EXISTS pools (
    chain       TEXT NOT NULL,
    pool        TEXT NOT NULL,
    token       TEXT,
    symbol      TEXT,
    name        TEXT,
    dex         TEXT,
    created_block INTEGER,
    created_ts  INTEGER,
    first_seen  INTEGER,
    seen_at     INTEGER,
    liquidity_usd REAL,
    data        TEXT NOT NULL,
    PRIMARY KEY (chain, pool)
);
CREATE INDEX IF NOT EXISTS idx_pools_seen ON pools(seen_at DESC);
CREATE INDEX IF NOT EXISTS idx_pools_token ON pools(chain, token);

CREATE TABLE IF NOT EXISTS snapshots (
    chain        TEXT NOT NULL,
    pool         TEXT NOT NULL,
    ts           INTEGER NOT NULL,
    price_usd    REAL,
    liquidity_usd REAL,
    volume_h24   REAL,
    change_h24   REAL,
    PRIMARY KEY (chain, pool, ts)
);
CREATE INDEX IF NOT EXISTS idx_snap_ts ON snapshots(chain, ts);
"""


class Store:
    def __init__(self, path: str):
        self.path = path
        directory = os.path.dirname(os.path.abspath(path))
        if directory:
            os.makedirs(directory, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, timeout=30, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=30000")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self):
        with self._lock:
            self._conn.close()

    def seen(self, chain: str, pool: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM pools WHERE chain=? AND pool=?", (chain, pool.lower())
            ).fetchone()
        return bool(row)

    def save(self, info: PoolInfo):
        data = json.dumps(info.to_dict(), ensure_ascii=False)
        with self._lock:
            self._conn.execute(
                """INSERT INTO pools
                   (chain, pool, token, symbol, name, dex, created_block, created_ts,
                    first_seen, seen_at, liquidity_usd, data)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(chain, pool) DO UPDATE SET
                     seen_at=excluded.seen_at, liquidity_usd=excluded.liquidity_usd,
                     data=excluded.data""",
                (
                    info.chain,
                    info.pool.lower(),
                    info.token.lower() if info.token else "",
                    info.symbol,
                    info.name,
                    info.dex,
                    info.created_block,
                    info.created_ts,
                    info.first_seen or int(time.time()),
                    int(time.time()),
                    info.liquidity_usd,
                    data,
                ),
            )
            self._conn.commit()

    def recent(self, limit: int = 100, chain: str | None = None, min_liq: float = 0.0):
        query = "SELECT data FROM pools"
        params: list = []
        clauses = []
        if chain:
            clauses.append("chain=?")
            params.append(chain)
        if min_liq:
            clauses.append("COALESCE(liquidity_usd, 0) >= ?")
            params.append(min_liq)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY created_ts DESC, seen_at DESC LIMIT ?"
        params.append(limit)
        with self._lock:
            rows = self._conn.execute(query, params).fetchall()
        out = []
        for (raw,) in rows:
            try:
                out.append(PoolInfo.from_dict(json.loads(raw)))
            except (ValueError, TypeError):
                continue
        return out

    def find(self, chain: str, value: str):
        value = value.lower()
        with self._lock:
            row = self._conn.execute(
                """SELECT data FROM pools
                   WHERE chain=? AND (pool=? OR token=?) LIMIT 1""",
                (chain, value, value),
            ).fetchone()
        if row:
            try:
                return PoolInfo.from_dict(json.loads(row[0]))
            except (ValueError, TypeError):
                return None
        return None

    # ----------------------------------------------------------- snapshot --

    def add_snapshot(self, chain: str, pool: str, price_usd, liquidity_usd,
                     volume_h24=None, change_h24=None, ts: int | None = None):
        if not price_usd or price_usd <= 0:
            return
        with self._lock:
            self._conn.execute(
                """INSERT OR REPLACE INTO snapshots
                   (chain, pool, ts, price_usd, liquidity_usd, volume_h24, change_h24)
                   VALUES (?,?,?,?,?,?,?)""",
                (chain, pool.lower(), int(ts or time.time()), float(price_usd),
                 float(liquidity_usd or 0), float(volume_h24 or 0), float(change_h24 or 0)),
            )
            self._conn.commit()

    def last_snapshot_ts(self, chain: str, pool: str) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT MAX(ts) FROM snapshots WHERE chain=? AND pool=?",
                (chain, pool.lower()),
            ).fetchone()
        return int(row[0]) if row and row[0] else 0

    def prune_snapshots(self, chain: str, pool: str, keep: int = 240):
        with self._lock:
            self._conn.execute(
                """DELETE FROM snapshots WHERE chain=? AND pool=? AND ts NOT IN (
                       SELECT ts FROM snapshots WHERE chain=? AND pool=?
                       ORDER BY ts DESC LIMIT ?)""",
                (chain, pool.lower(), chain, pool.lower(), keep),
            )
            self._conn.commit()

    def history(self, chain: str, pool: str, limit: int = 120):
        with self._lock:
            rows = self._conn.execute(
                """SELECT ts, price_usd FROM snapshots
                   WHERE chain=? AND pool=? ORDER BY ts DESC LIMIT ?""",
                (chain, pool.lower(), limit),
            ).fetchall()
        return [{"ts": int(ts), "price": float(price)} for ts, price in reversed(rows)]

    def history_map(self, chain: str, since: int, per_pool: int = 60):
        """Ambil histori banyak pool sekaligus (satu query)."""
        with self._lock:
            rows = self._conn.execute(
                """SELECT pool, ts, price_usd FROM snapshots
                   WHERE chain=? AND ts>=? ORDER BY ts ASC""",
                (chain, int(since)),
            ).fetchall()
        out: dict = {}
        for pool, ts, price in rows:
            bucket = out.setdefault(pool, [])
            bucket.append({"ts": int(ts), "price": float(price)})
            if len(bucket) > per_pool:
                del bucket[0]
        return out

    def chains(self):
        with self._lock:
            rows = self._conn.execute("SELECT DISTINCT chain FROM pools").fetchall()
        return sorted(row[0] for row in rows if row and row[0])

    def count(self) -> int:
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) FROM pools").fetchone()
        return int(row[0]) if row else 0
