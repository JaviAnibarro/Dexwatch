"""Memperkaya data pool: metadata token, reserve, harga, likuiditas, keamanan.

Sumber:
  1. On-chain  -> sumber utama (nama, simbol, supply, reserve, LP, harga native)
  2. DexScreener API -> harga USD, likuiditas USD, FDV, sosial media
  3. GoPlus API -> jumlah holder, pajak beli/jual, indikasi honeypot

Semua sumber eksternal bersifat opsional: kalau gagal, data on-chain tetap tampil.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import urllib.error
import urllib.request

from . import abi
from .mathx import v2_amounts, v3_amounts, v3_price0_in_1
from .pools import NewPool, PoolInfo

log = logging.getLogger("dexwatch.enrich")

_UA = "Mozilla/5.0 (compatible; dexwatch/1.0)"
_ZERO = "0x0000000000000000000000000000000000000000"

DEXSCREENER_CHAIN = {
    "bsc": "bsc",
    "ethereum": "ethereum",
    "base": "base",
    "arbitrum": "arbitrum",
}


def http_json(url: str, timeout: float = 12.0, retries: int = 2):
    """GET JSON sederhana; None kalau 404/kosong, lempar error kalau gagal total."""
    last = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            if exc.code == 429:
                time.sleep(1.0 + attempt)
                last = exc
                continue
            last = exc
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            last = exc
            time.sleep(0.2 + attempt * 0.4)
    raise RuntimeError(f"API gagal: {url} ({last})")


class Cache:
    def __init__(self):
        self._data = {}
        self._lock = threading.Lock()

    def get(self, key, ttl):
        with self._lock:
            item = self._data.get(key)
        if not item:
            return None
        value, expires = item
        if expires < time.time():
            return None
        return value

    def put(self, key, value, ttl):
        with self._lock:
            self._data[key] = (value, time.time() + ttl)


class Enricher:
    def __init__(self, chain, rpc, security: bool = True, offline: bool = False):
        self.chain = chain
        self.rpc = rpc
        self.security = security
        self.offline = offline
        self.cache = Cache()
        self._native_usd = None
        self._native_usd_at = 0.0
        self._lock = threading.Lock()
        self._goplus_lock = threading.Lock()
        self._next_goplus = 0.0
        self.goplus_interval = 1.2

    # --------------------------------------------------------------- RPC utils

    def _call(self, to: str, data: str):
        try:
            result = self.rpc.eth_call(to, data)
        except Exception as exc:  # noqa: BLE001 - token aneh sering revert
            log.debug("call %s %s gagal: %s", to, data[:10], exc)
            return None
        if not result or result == "0x":
            return None
        return result

    def _batch_calls(self, calls):
        """calls: list[(to, data)] -> list[str|None] sejajar."""
        results = self.rpc.batch(
            [("eth_call", [{"to": to, "data": data}, "latest"]) for to, data in calls]
        )
        out = []
        for item in results:
            if isinstance(item, Exception) or not item or item == "0x":
                out.append(None)
            else:
                out.append(item)
        return out

    # ------------------------------------------------------------- token meta

    def token_meta(self, address: str) -> dict:
        address = abi.norm_address(address)
        cached = self.cache.get(("meta", address), 600)
        if cached:
            return cached
        results = self._batch_calls([
            (address, abi.SEL_NAME),
            (address, abi.SEL_SYMBOL),
            (address, abi.SEL_DECIMALS),
            (address, abi.SEL_TOTAL_SUPPLY),
        ])
        name, symbol, decimals_raw, supply_raw = results
        try:
            decimals = abi.decode_uint(decimals_raw) if decimals_raw else 18
            if decimals > 36:
                decimals = 18
        except ValueError:
            decimals = 18
        meta = {
            "name": abi.decode_string(name) if name else "",
            "symbol": abi.decode_string(symbol) if symbol else "",
            "decimals": decimals,
            "total_supply_raw": abi.decode_uint(supply_raw) if supply_raw else 0,
        }
        if not meta["name"] and not meta["symbol"]:
            meta["name"] = "(tanpa nama)"
        self.cache.put(("meta", address), meta, 600)
        return meta

    def quote_decimals(self, address: str) -> int:
        return self.token_meta(address)["decimals"]

    # ------------------------------------------------------------ native USD

    def native_usd(self):
        """Harga 1 native token dalam USD.

        Diutamakan dari pool stable di on-chain (deterministik), lalu DexScreener.
        """
        with self._lock:
            if self._native_usd is not None and time.time() - self._native_usd_at < 120:
                return self._native_usd
        price = None
        if not self.offline:
            price = self._native_usd_onchain() or self._native_usd_api()
        if price and price > 0:
            with self._lock:
                self._native_usd = price
                self._native_usd_at = time.time()
        return self._native_usd

    def _native_usd_onchain(self):
        """Cari pair wrapped/stable lewat factory.getPair, hitung dari reserve."""
        wrapped = self.chain.wrapped
        for dex in self.chain.dexes:
            if dex.kind != "v2":
                continue
            for stable in self.chain.stables:
                try:
                    result = self._call(dex.factory, abi.get_pair(wrapped, stable))
                    if not result:
                        continue
                    pair = abi.decode_address(result)
                    if pair == _ZERO:
                        continue
                    reserves = self._call(pair, abi.SEL_GET_RESERVES)
                    token0 = abi.decode_address(self._call(pair, abi.SEL_TOKEN0) or "0x")
                    if not reserves or not token0:
                        continue
                    r0 = abi.decode_uint(reserves, 0)
                    r1 = abi.decode_uint(reserves, 1)
                    d_wrapped = self.quote_decimals(wrapped)
                    d_stable = self.quote_decimals(stable)
                    if token0 == abi.norm_address(wrapped):
                        amount_wrapped, amount_stable = r0 / 10 ** d_wrapped, r1 / 10 ** d_stable
                    else:
                        amount_wrapped, amount_stable = r1 / 10 ** d_wrapped, r0 / 10 ** d_stable
                    if amount_wrapped > 0 and amount_stable > 0:
                        return amount_stable / amount_wrapped
                except Exception as exc:  # noqa: BLE001
                    log.debug("native_usd onchain gagal: %s", exc)
        return None

    def _native_usd_api(self):
        chain_id = DEXSCREENER_CHAIN.get(self.chain.key)
        if not chain_id:
            return None
        try:
            data = http_json(
                f"https://api.dexscreener.com/latest/dex/tokens/{self.chain.wrapped}"
            )
        except RuntimeError:
            return None
        pairs = (data or {}).get("pairs") or []
        best = None
        for pair in pairs:
            base = (pair.get("baseToken") or {}).get("address", "").lower()
            quote = (pair.get("quoteToken") or {}).get("address", "").lower()
            is_stable_quote = quote in {s.lower() for s in self.chain.stables}
            try:
                liq = float((pair.get("liquidity") or {}).get("usd") or 0)
                price = float(pair.get("priceUsd") or 0)
            except (TypeError, ValueError):
                continue
            if base == self.chain.wrapped.lower() and is_stable_quote and price > 0:
                if best is None or liq > best[0]:
                    best = (liq, price)
        return best[1] if best else None

    # --------------------------------------------------------- DexScreener API

    def dexscreener_pair(self, pool: str):
        chain_id = DEXSCREENER_CHAIN.get(self.chain.key)
        if not chain_id or self.offline:
            return None
        key = ("ds", self.chain.key, pool.lower())
        cached = self.cache.get(key, 60)
        if cached is not None:
            return cached or None
        try:
            data = http_json(
                f"https://api.dexscreener.com/latest/dex/pairs/{chain_id}/{pool}"
            )
        except RuntimeError as exc:
            log.debug("dexscreener pair gagal: %s", exc)
            return None
        pairs = (data or {}).get("pairs") or []
        pair = pairs[0] if pairs else None
        self.cache.put(key, pair or {}, 60 if pair else 15)
        return pair

    def dexscreener_token(self, token: str, ttl: int = 60):
        chain_id = DEXSCREENER_CHAIN.get(self.chain.key)
        if not chain_id or self.offline:
            return []
        key = ("dst", self.chain.key, token.lower())
        cached = self.cache.get(key, ttl)
        if cached is not None:
            return cached
        try:
            data = http_json(
                f"https://api.dexscreener.com/latest/dex/tokens/{token}"
            )
        except RuntimeError:
            return []
        pairs = (data or {}).get("pairs") or []
        self.cache.put(key, pairs, ttl)
        return pairs

    # ---------------------------------------------------------------- GoPlus --

    def goplus(self, token: str):
        if not self.security or self.offline:
            return None
        key = ("goplus", self.chain.key, token.lower())
        cached = self.cache.get(key, 600)
        if cached is not None:
            return cached or None
        with self._goplus_lock:
            wait = self._next_goplus - time.time()
            if wait > 0:
                time.sleep(wait)
            self._next_goplus = time.time() + self.goplus_interval
        try:
            data = http_json(
                "https://api.gopluslabs.io/api/v1/token_security/"
                f"{self.chain.chain_id}?contract_addresses={token}",
                retries=1,
            )
        except RuntimeError as exc:
            log.debug("goplus gagal: %s", exc)
            self.cache.put(key, {}, 30)
            return None
        if isinstance(data, dict) and data.get("code") in (4029, 4028):
            log.debug("goplus rate limit")
            self.cache.put(key, {}, 30)
            return None
        result = (data or {}).get("result") or {}
        info = result.get(token.lower())
        self.cache.put(key, info or {}, 600 if info else 120)
        return info

    # --------------------------------------------------------------- numeric --

    def _apply_security(self, info: PoolInfo, token: str):
        data = self.goplus(token)
        if not data:
            return
        info.sources.append("goplus")
        try:
            if data.get("holder_count") not in (None, ""):
                info.holders = int(float(data["holder_count"]))
        except (TypeError, ValueError):
            pass
        for field_name, key in (("buy_tax", "buy_tax"), ("sell_tax", "sell_tax")):
            try:
                value = data.get(key)
                if value not in (None, ""):
                    setattr(info, field_name, round(float(value) * 100, 2))
            except (TypeError, ValueError):
                pass
        honeypot = data.get("is_honeypot")
        if honeypot not in (None, ""):
            info.honeypot = str(honeypot) == "1"
        verified = data.get("is_open_source")
        if verified not in (None, ""):
            info.verified = str(verified) == "1"
        if data.get("owner_address") and data["owner_address"] != _ZERO:
            info.owner = data["owner_address"]
        if data.get("creator_address"):
            info.creator = data["creator_address"]
        holders = data.get("holders") or []
        percents = []
        for holder in holders[:10]:
            try:
                percents.append(float(holder.get("percent") or 0) * 100)
            except (TypeError, ValueError):
                continue
        if percents:
            info.top10_pct = round(sum(percents), 2)

    def _apply_dexscreener(self, info: PoolInfo, pair: dict):
        if not pair:
            return
        info.sources.append("dexscreener")
        liquidity = pair.get("liquidity") or {}
        try:
            usd = float(liquidity.get("usd") or 0)
            if usd > 0:
                info.liquidity_usd = usd
        except (TypeError, ValueError):
            pass
        try:
            price = float(pair.get("priceUsd") or 0)
            if price > 0:
                info.price_usd = price
        except (TypeError, ValueError):
            pass
        for attr, key in (("fdv_usd", "fdv"), ("mcap_usd", "marketCap")):
            try:
                value = float(pair.get(key) or 0)
                if value > 0:
                    setattr(info, attr, value)
            except (TypeError, ValueError):
                pass
        created = pair.get("pairCreatedAt")
        if created and not info.created_ts:
            try:
                info.created_ts = int(int(created) / 1000)
            except (TypeError, ValueError):
                pass
        info_block = pair.get("info") or {}
        if info_block.get("imageUrl"):
            info.image = info_block["imageUrl"]
        info.websites = [
            w.get("url") for w in (info_block.get("websites") or []) if w.get("url")
        ]
        info.socials = [
            {"type": s.get("type") or s.get("platform") or "", "url": s.get("url")}
            for s in (info_block.get("socials") or [])
            if s.get("url")
        ]
        if (pair.get("labels") or []) and "v3" in pair["labels"]:
            info.raw["dexscreener_labels"] = pair["labels"]

    # --------------------------------------------------------------- enrich --

    def enrich(self, new_pool: NewPool) -> PoolInfo:
        chain = self.chain
        info = PoolInfo(
            chain=chain.key,
            chain_name=chain.name,
            dex=new_pool.dex,
            dex_name=new_pool.dex_name,
            kind=new_pool.kind,
            pool=abi.norm_address(new_pool.pool),
            token0=abi.norm_address(new_pool.token0),
            token1=abi.norm_address(new_pool.token1),
            fee_tier=new_pool.fee_tier or None,
            created_block=new_pool.block,
            created_tx=new_pool.tx,
            created_ts=new_pool.ts,
            first_seen=int(time.time()),
        )

        if chain.is_quote(new_pool.token0):
            info.quote, info.token = info.token0, info.token1
        elif chain.is_quote(new_pool.token1):
            info.quote, info.token = info.token1, info.token0
        else:
            info.token, info.quote = info.token0, info.token1
            info.notes.append("kedua token bukan wrapped/stable")

        meta = self.token_meta(info.token)
        info.name = meta["name"]
        info.symbol = meta["symbol"]
        info.decimals = meta["decimals"]
        if meta["total_supply_raw"]:
            info.total_supply = meta["total_supply_raw"] / 10 ** info.decimals
        quote_meta = self.token_meta(info.quote)
        info.quote_symbol = quote_meta["symbol"] or "?"

        if new_pool.kind == "v3":
            self._enrich_v3(info)
        else:
            self._enrich_v2(info)

        # --- USD & data pasar dari DexScreener
        pair = self.dexscreener_pair(info.pool)
        if pair:
            self._apply_dexscreener(info, pair)
            if info.price_usd and info.total_supply:
                if not info.fdv_usd:
                    info.fdv_usd = info.price_usd * info.total_supply

        # --- keamanan dari GoPlus
        self._apply_security(info, info.token)

        # --- angka turunan
        if info.price_usd and info.total_supply and not info.mcap_usd:
            info.mcap_usd = info.price_usd * info.total_supply
        if info.liquidity_usd is None and info.reserve_quote:
            quote_usd = self._quote_usd(info)
            if quote_usd:
                info.liquidity_usd = info.reserve_quote * quote_usd * 2
        if info.sources:
            info.sources = sorted(set(info.sources))
        return info

    def _quote_usd(self, info: PoolInfo):
        """Harga 1 unit quote token dalam USD.

        Stable dianggap $1, wrapped memakai harga native, selain itu tidak diketahui.
        """
        if info.quote.lower() == self.chain.wrapped.lower():
            return self.native_usd()
        if info.quote.lower() in {s.lower() for s in self.chain.stables}:
            return 1.0
        return None

    def _enrich_v2(self, info: PoolInfo):
        pool = info.pool
        results = self._batch_calls([
            (pool, abi.SEL_TOKEN0),
            (pool, abi.SEL_TOKEN1),
            (pool, abi.SEL_GET_RESERVES),
            (pool, abi.SEL_TOTAL_SUPPLY),
            (pool, abi.balance_of(abi.DEAD)),
        ])
        token0_raw, token1_raw, reserves, lp_supply, dead_balance = results

        if token0_raw:
            info.token0 = abi.decode_address(token0_raw)
        if token1_raw:
            info.token1 = abi.decode_address(token1_raw)

        if not reserves:
            info.notes.append("reserve tidak terbaca")
            return
        r0 = abi.decode_uint(reserves, 0)
        r1 = abi.decode_uint(reserves, 1)
        d0 = self.token_meta(info.token0)["decimals"]
        d1 = self.token_meta(info.token1)["decimals"]
        info.raw.update({"reserve0_raw": str(r0), "reserve1_raw": str(r1), "blockTimestampLast": abi.decode_uint(reserves, 2)})

        a0, a1, price0_in_1 = v2_amounts(r0, r1, d0, d1)
        if info.token == info.token0:
            info.reserve_token, info.reserve_quote = a0, a1
        else:
            info.reserve_token, info.reserve_quote = a1, a0

        if info.reserve_token and info.reserve_quote:
            info.price_native = info.reserve_quote / info.reserve_token

        info.sources.append("onchain-v2")

        # LP & burn
        if lp_supply:
            info.lp_supply = abi.decode_uint(lp_supply) / 10 ** 18
            if dead_balance and info.lp_supply:
                burned = abi.decode_uint(dead_balance) / 10 ** 18
                info.lp_burned_pct = min(100.0, round(burned / info.lp_supply * 100, 2))

        # USD on-chain fallback
        quote_usd = self._quote_usd(info)
        if quote_usd and info.reserve_quote:
            info.liquidity_usd = info.reserve_quote * quote_usd * 2
            native = self.native_usd()
            if info.quote.lower() == self.chain.wrapped.lower():
                info.liquidity_native = info.reserve_quote
            elif native:
                info.liquidity_native = info.reserve_quote / native
            if info.price_native:
                info.price_usd = info.price_native * quote_usd

    def _enrich_v3(self, info: PoolInfo):
        pool = info.pool
        results = self._batch_calls([
            (pool, abi.SEL_TOKEN0),
            (pool, abi.SEL_TOKEN1),
            (pool, abi.SEL_FEE),
            (pool, abi.SEL_SLOT0),
            (pool, abi.SEL_LIQUIDITY),
        ])
        token0_raw, token1_raw, fee_raw, slot0, liquidity_raw = results

        if token0_raw:
            info.token0 = abi.decode_address(token0_raw)
        if token1_raw:
            info.token1 = abi.decode_address(token1_raw)
        if fee_raw:
            fee = abi.decode_uint(fee_raw)
            info.fee_tier = round(fee / 1_000_000 * 100, 4)

        if not slot0 or not liquidity_raw:
            info.notes.append("state V3 tidak terbaca")
            return

        sqrt_price = abi.decode_uint(slot0, 0)
        tick = abi.decode_int(slot0, 1, bits=24)
        liquidity = abi.decode_uint(liquidity_raw)
        info.raw.update({
            "sqrtPriceX96": str(sqrt_price),
            "tick": tick,
            "liquidity": str(liquidity),
        })

        if sqrt_price == 0:
            info.notes.append("pool V3 belum punya harga (belum ada likuiditas)")
            return

        d0 = self.token_meta(info.token0)["decimals"]
        d1 = self.token_meta(info.token1)["decimals"]

        # harga token0 dalam token1 + reserve virtual dari likuiditas aktif
        price0_in_1 = v3_price0_in_1(sqrt_price, d0, d1)
        amount0, amount1 = v3_amounts(liquidity, sqrt_price, d0, d1)

        if info.token == info.token0:
            info.reserve_token, info.reserve_quote = amount0, amount1
            info.price_native = price0_in_1
        else:
            info.reserve_token, info.reserve_quote = amount1, amount0
            info.price_native = (1 / price0_in_1) if price0_in_1 else None

        info.sources.append("onchain-v3")
        quote_usd = self._quote_usd(info)
        if quote_usd:
            # nilai total pool dalam quote, lalu USD
            if info.quote == info.token1:
                total_quote = amount1 + (amount0 * price0_in_1 if price0_in_1 else 0.0)
            else:
                total_quote = amount0 + (amount1 / price0_in_1 if price0_in_1 else 0.0)
            info.liquidity_usd = total_quote * quote_usd
            if info.price_native:
                info.price_usd = info.price_native * quote_usd
            native = self.native_usd()
            if info.quote.lower() == self.chain.wrapped.lower():
                info.liquidity_native = total_quote
            elif native:
                info.liquidity_native = total_quote / native
        info.notes.append("likuiditas V3 = perkiraan dari liquidity aktif")
