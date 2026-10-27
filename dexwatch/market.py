"""Sumber data pasar untuk UI: DexScreener (harga, volume, gambar) dan
GeckoTerminal (candlestick). Semua respons di-cache supaya hemat rate limit.
"""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from .enrich import http_json

log = logging.getLogger("dexwatch.market")

DEXSCREENER_CHAIN = {
    "bsc": "bsc",
    "ethereum": "ethereum",
    "base": "base",
    "arbitrum": "arbitrum",
}

GECKO_NETWORK = {
    "bsc": "bsc",
    "ethereum": "eth",
    "base": "base",
    "arbitrum": "arbitrum",
}


def _num(value, default=0.0):
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _pair_payload(pair: dict) -> dict:
    """Ringkas field DexScreener yang dipakai UI."""
    info = pair.get("info") or {}
    socials = []
    for item in info.get("socials") or []:
        url = item.get("url")
        if url:
            socials.append({
                "type": (item.get("type") or item.get("platform") or "").lower(),
                "url": url,
            })
    websites = [w.get("url") for w in (info.get("websites") or []) if w.get("url")]
    txns = pair.get("txns") or {}
    return {
        "price_usd": _num(pair.get("priceUsd")),
        "price_native": _num(pair.get("priceNative")),
        "liquidity_usd": _num((pair.get("liquidity") or {}).get("usd")),
        "fdv_usd": _num(pair.get("fdv")),
        "mcap_usd": _num(pair.get("marketCap")),
        "change": {k: _num(v) for k, v in (pair.get("priceChange") or {}).items()},
        "volume": {k: _num(v) for k, v in (pair.get("volume") or {}).items()},
        "txns": {
            k: {"buys": int(_num(v.get("buys"))), "sells": int(_num(v.get("sells")))}
            for k, v in txns.items() if isinstance(v, dict)
        },
        "image": info.get("imageUrl"),
        "socials": socials,
        "websites": websites,
        "labels": pair.get("labels") or [],
        "pair_created_at": int(_num(pair.get("pairCreatedAt"))),
    }


class MarketData:
    def __init__(self, ttl: float = 25.0, chart_ttl: float = 45.0, timeout: float = 10.0):
        self.ttl = ttl
        self.chart_ttl = chart_ttl
        self.timeout = timeout
        self._cache = {}
        self._charts = {}
        self._lock = threading.Lock()
        self._inflight = set()
        self._pending = set()
        self._executor = ThreadPoolExecutor(max_workers=6, thread_name_prefix="market")

    # ------------------------------------------------------------- caching --

    def _get(self, key, ttl):
        with self._lock:
            item = self._cache.get(key)
        if not item:
            return None
        value, expires = item
        return value if expires > time.time() else None

    def _put(self, key, value, ttl):
        with self._lock:
            self._cache[key] = (value, time.time() + ttl)

    def cached(self, chain: str, pool: str):
        return self._get((chain, pool.lower()), self.ttl)

    # --------------------------------------------------------- DexScreener --

    def fetch_pair(self, chain: str, pool: str):
        chain_id = DEXSCREENER_CHAIN.get(chain)
        if not chain_id or not pool:
            return None
        try:
            data = http_json(
                f"https://api.dexscreener.com/latest/dex/pairs/{chain_id}/{pool}",
                timeout=self.timeout,
                retries=1,
            )
        except RuntimeError as exc:
            log.debug("dexscreener pair gagal %s: %s", pool, exc)
            return None
        pairs = (data or {}).get("pairs") or []
        return _pair_payload(pairs[0]) if pairs else None

    def pair(self, chain: str, pool: str):
        key = (chain, pool.lower())
        cached = self._get(key, self.ttl)
        if cached is not None:
            return cached
        fresh = self.fetch_pair(chain, pool)
        self._put(key, fresh, self.ttl if fresh else 10)
        return fresh

    def refresh(self, chain: str, pools, workers: int = 6):
        """Segarkan banyak pool sekaligus (hanya yang cache-nya kedaluwarsa)."""
        candidates = []
        for pool in pools:
            pool = pool.lower()
            if self._get((chain, pool), self.ttl) is None:
                candidates.append(pool)
        candidates = list(dict.fromkeys(candidates))[:60]

        todo = []
        with self._lock:
            for pool in candidates:
                key = (chain, pool)
                if key in self._inflight:
                    continue
                self._inflight.add(key)
                todo.append(pool)
        if not todo:
            return

        def work(pool):
            try:
                return pool, self.fetch_pair(chain, pool)
            except Exception as exc:  # noqa: BLE001
                log.debug("refresh %s gagal: %s", pool, exc)
                return pool, None

        try:
            with ThreadPoolExecutor(max_workers=min(workers, len(todo))) as ex:
                for pool, data in ex.map(work, todo):
                    self._put((chain, pool), data, self.ttl if data else 10)
        finally:
            with self._lock:
                for pool in todo:
                    self._inflight.discard((chain, pool))

    def refresh_async(self, chain: str, pools):
        with self._lock:
            if chain in self._pending:
                return
            self._pending.add(chain)
        self._executor.submit(self._refresh_job, chain, list(pools))

    def _refresh_job(self, chain: str, pools):
        try:
            self.refresh(chain, pools)
        finally:
            with self._lock:
                self._pending.discard(chain)

    # ------------------------------------------------------ GeckoTerminal --

    def ohlcv(self, chain: str, pool: str, timeframe: str = "minute",
              limit: int = 120, aggregate: int = 1):
        network = GECKO_NETWORK.get(chain)
        if not network:
            return None
        tf = timeframe if timeframe in ("minute", "hour", "day") else "minute"
        limit = max(10, min(int(limit), 300))
        try:
            agg = int(aggregate)
        except (TypeError, ValueError):
            agg = 1
        agg = agg if agg in (1, 5, 15, 4, 60) else 1
        key = (chain, pool.lower(), tf, agg, limit)
        cached = self._get(key, self.chart_ttl)
        if cached is not None:
            return cached
        try:
            data = http_json(
                "https://api.geckoterminal.com/api/v2/networks/"
                f"{network}/pools/{pool}/ohlcv/{tf}?aggregate={agg}&limit={limit}",
                timeout=self.timeout,
                retries=1,
            )
        except RuntimeError as exc:
            log.debug("ohlcv gagal %s: %s", pool, exc)
            self._put(key, None, 20)
            return None
        rows = ((data or {}).get("data") or {}).get("attributes", {}).get("ohlcv_list") or []
        candles = [
            {
                "ts": int(row[0]),
                "open": float(row[1]),
                "high": float(row[2]),
                "low": float(row[3]),
                "close": float(row[4]),
                "volume": float(row[5]),
            }
            for row in rows
            if len(row) >= 6
        ]
        self._put(key, candles, self.chart_ttl)
        return candles
