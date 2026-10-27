"""Dashboard web (gaya pump.fun/GMGN) di atas database dexwatch.

Server stdlib saja: menyajikan file statis, API feed ter-merge dengan data
pasar DexScreener, candlestick GeckoTerminal, dan sampler histori harga lokal.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .chains import CHAINS, get_chain
from .enrich import Enricher
from .market import DEXSCREENER_CHAIN, MarketData
from .pools import PoolInfo
from .rpc import Rpc
from .scanner import Scanner
from .store import Store

log = logging.getLogger("dexwatch.web")

STATIC_DIR = Path(__file__).parent / "static"
STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/app.css": ("app.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "application/javascript; charset=utf-8"),
    "/favicon.svg": ("favicon.svg", "image/svg+xml"),
}

SORTS = ("new", "liq", "volume", "mcap", "change", "holders")


def _num(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _merge(info: PoolInfo, market: dict | None, spark: list) -> dict:
    data = info.to_dict()
    if market:
        for key in ("price_usd", "liquidity_usd", "fdv_usd", "mcap_usd", "image",
                    "price_native"):
            if market.get(key):
                data[key] = market[key]
        data["change"] = market.get("change") or {}
        data["volume"] = market.get("volume") or {}
        data["txns"] = market.get("txns") or {}
        if market.get("socials"):
            data["socials"] = market["socials"]
        if market.get("websites"):
            data["websites"] = market["websites"]
    data.setdefault("change", {})
    data.setdefault("volume", {})
    data.setdefault("txns", {})
    data.setdefault("image", info.image)
    data.setdefault("socials", [])
    data.setdefault("websites", [])
    data["spark"] = spark
    data["age_seconds"] = max(0, int(time.time()) - (info.created_ts or 0)) if info.created_ts else 0
    return data


def _matches(item: dict, query: str, min_liq: float, hide_honeypot: bool,
             verified_only: bool) -> bool:
    if min_liq and _num(item.get("liquidity_usd")) < min_liq:
        return False
    if hide_honeypot and item.get("honeypot"):
        return False
    if verified_only and item.get("verified") is not True:
        return False
    if query:
        haystack = " ".join(str(item.get(key) or "") for key in
                            ("symbol", "name", "token", "pool", "dex", "quote_symbol"))
        if query not in haystack.lower():
            return False
    return True


def _sort_key(sort: str):
    if sort == "liq":
        return lambda i: _num(i.get("liquidity_usd"))
    if sort == "volume":
        return lambda i: _num((i.get("volume") or {}).get("h24"))
    if sort == "mcap":
        return lambda i: _num(i.get("mcap_usd"))
    if sort == "change":
        return lambda i: _num((i.get("change") or {}).get("h24"))
    if sort == "holders":
        return lambda i: _num(i.get("holders"))
    return lambda i: _num(i.get("created_ts"))


class Handler(BaseHTTPRequestHandler):
    store_path = "dexwatch.db"
    chain_key = "bsc"
    market: MarketData | None = None
    server_version = "dexwatch"

    def log_message(self, fmt, *args):  # senyap
        pass

    # ------------------------------------------------------------- response --

    def _send(self, code, body, content_type="application/json; charset=utf-8"):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, payload, code=200):
        self._send(code, json.dumps(payload, default=str))

    # ------------------------------------------------------------------ GET --

    def do_GET(self):  # noqa: N802
        parsed = urlparse(self.path)
        query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        if parsed.path in STATIC_FILES:
            return self._static(*STATIC_FILES[parsed.path])
        if parsed.path == "/api/feed":
            return self._feed(query)
        if parsed.path == "/api/chart":
            return self._chart(query)
        if parsed.path == "/api/pools":
            return self._pools(query)
        if parsed.path == "/api/meta":
            return self._meta()
        if parsed.path == "/api/health":
            return self._json({"ok": True, "time": int(time.time())})
        return self._json({"error": "not found"}, 404)

    def _static(self, name: str, content_type: str):
        path = STATIC_DIR / name
        try:
            body = path.read_bytes()
        except OSError:
            return self._json({"error": f"file {name} tidak ada"}, 404)
        self._send(200, body, content_type)

    # ---------------------------------------------------------------- feed --

    def _feed(self, query):
        chain = query.get("chain") if query.get("chain") in CHAINS else self.chain_key
        sort = query.get("sort") if query.get("sort") in SORTS else "new"
        try:
            limit = max(1, min(int(query.get("limit") or 120), 300))
        except ValueError:
            limit = 120
        min_liq = _num(query.get("min_liq"))
        q = (query.get("q") or "").strip().lower()
        hide_honeypot = query.get("hide_honeypot") in ("1", "true")
        verified_only = query.get("verified") in ("1", "true")

        store = Store(self.store_path)
        try:
            pools = store.recent(limit=300, chain=chain)
            spark = store.history_map(chain, since=int(time.time()) - 7200, per_pool=60)
            chains = store.chains()
        finally:
            store.close()

        if self.market:
            self.market.refresh_async(chain, [p.pool for p in pools[:80]])

        items = []
        for info in pools:
            item = _merge(
                info,
                self.market.cached(chain, info.pool) if self.market else None,
                spark.get(info.pool.lower(), []),
            )
            if _matches(item, q, min_liq, hide_honeypot, verified_only):
                items.append(item)

        items.sort(key=_sort_key(sort), reverse=True)
        items = items[:limit]
        self._json({
            "chain": chain,
            "chains": chains or [chain],
            "sort": sort,
            "count": len(items),
            "server_time": int(time.time()),
            "pools": items,
        })

    # --------------------------------------------------------------- chart --

    def _chart(self, query):
        chain = query.get("chain") if query.get("chain") in CHAINS else self.chain_key
        pool = (query.get("pool") or "").lower()
        tf = query.get("tf") or "minute"
        try:
            agg = int(query.get("agg") or 1)
        except ValueError:
            agg = 1
        source = "geckoterminal"
        candles = self.market.ohlcv(chain, pool, tf, limit=150, aggregate=agg) if self.market else None
        if not candles:
            source = "snapshot lokal"
            store = Store(self.store_path)
            try:
                history = store.history(chain, pool, limit=150)
            finally:
                store.close()
            candles = [
                {"ts": p["ts"], "open": p["price"], "high": p["price"],
                 "low": p["price"], "close": p["price"], "volume": 0}
                for p in history
            ]
        self._json({"candles": candles, "source": source, "tf": tf})

    # --------------------------------------------------------------- legacy --

    def _pools(self, query):
        chain = query.get("chain") if query.get("chain") in CHAINS else self.chain_key
        try:
            limit = max(1, min(int(query.get("limit") or 200), 1000))
        except ValueError:
            limit = 200
        store = Store(self.store_path)
        try:
            infos = store.recent(limit=limit, chain=chain)
        finally:
            store.close()
        self._json({"chain": chain, "count": len(infos),
                    "pools": [i.to_dict() for i in infos]})

    def _meta(self):
        store = Store(self.store_path)
        try:
            chains = store.chains()
            total = store.count()
        finally:
            store.close()
        self._json({
            "chain": self.chain_key,
            "chains": chains or [self.chain_key],
            "total_pools": total,
            "screener": DEXSCREENER_CHAIN,
        })


def _sampler(market: MarketData, store: Store, chain: str, interval: float):
    """Segarkan pasar + simpan titik histori untuk sparkline."""
    while True:
        try:
            pools = store.recent(limit=80, chain=chain)
            market.refresh(chain, [p.pool for p in pools])
            now = int(time.time())
            for info in pools:
                data = market.cached(chain, info.pool)
                if not data or not data.get("price_usd"):
                    continue
                if now - store.last_snapshot_ts(chain, info.pool) >= 45:
                    change = data.get("change") or {}
                    volume = data.get("volume") or {}
                    store.add_snapshot(
                        chain, info.pool, data["price_usd"], data.get("liquidity_usd"),
                        volume.get("h24"), change.get("h24"), ts=now,
                    )
                    store.prune_snapshots(chain, info.pool, keep=180)
        except Exception as exc:  # noqa: BLE001
            log.debug("sampler error: %s", exc)
        time.sleep(interval)


def serve(args) -> int:
    chain = get_chain(args.chain)
    store = Store(args.db)
    store.close()

    Handler.store_path = args.db
    Handler.chain_key = chain.key
    Handler.market = MarketData()

    if not args.no_sampler:
        sampler_store = Store(args.db)
        threading.Thread(
            target=_sampler,
            args=(Handler.market, sampler_store, chain.key, args.refresh),
            daemon=True,
        ).start()

    if args.scan:
        rpc = Rpc(args.rpc or chain.rpc, timeout=args.timeout)
        enricher = Enricher(
            chain, rpc,
            security=not args.no_security and not args.offline,
            offline=args.offline,
        )
        scanner_store = Store(args.db)
        scanner = Scanner(
            chain, rpc, enricher, scanner_store,
            dexs=[chain.dex(k) for k in args.dex.split(",")] if args.dex else None,
            workers=args.workers,
        )

        def loop():
            for _infos in scanner.watch(interval=args.interval):
                pass

        threading.Thread(target=loop, daemon=True).start()

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    url = f"http://{args.host}:{args.port}"
    print(f"dexwatch UI: {url}  (chain {chain.key} · db {args.db})")
    print("Ctrl+C untuk berhenti")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nberhenti.")
    finally:
        server.server_close()
    return 0
