"""Inti scanner: ambil log pool baru, perkaya, simpan, dan laporkan."""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import abi, pools
from .chains import Chain, Dex
from .enrich import Enricher
from .pools import NewPool, PoolInfo
from .rpc import Rpc
from .store import Store

log = logging.getLogger("dexwatch.scanner")


class Scanner:
    def __init__(
        self,
        chain: Chain,
        rpc: Rpc,
        enricher: Enricher,
        store: Store,
        dexs: list | None = None,
        workers: int = 6,
        retries: int = 2,
    ):
        self.chain = chain
        self.rpc = rpc
        self.enricher = enricher
        self.store = store
        self.dexs = dexs or list(chain.dexes)
        self.workers = max(1, workers)
        self.retries = retries
        self._timestamps = {}
        self._pending = {}  # pool -> (info, waktu_cek_berikutnya, percobaan)

    # ------------------------------------------------------------- utilitas --

    def head(self) -> int:
        return self.rpc.block_number()

    def timestamp(self, block: int) -> int:
        if block in self._timestamps:
            return self._timestamps[block]
        try:
            ts = self.rpc.block_timestamp(block)
        except Exception as exc:  # noqa: BLE001
            log.debug("timestamp block %s gagal: %s", block, exc)
            ts = 0
        self._timestamps[block] = ts
        return ts

    def _timestamps_for(self, blocks):
        todo = sorted({b for b in blocks if b not in self._timestamps})
        if not todo:
            return
        results = self.rpc.batch(
            [("eth_getBlockByNumber", [hex(b), False]) for b in todo]
        )
        for block, result in zip(todo, results):
            if isinstance(result, Exception) or not result:
                self._timestamps[block] = 0
            else:
                try:
                    self._timestamps[block] = int(result["timestamp"], 16)
                except (KeyError, ValueError):
                    self._timestamps[block] = 0
        # fallback per blok kalau batch tidak didukung
        for block in todo:
            if self._timestamps.get(block):
                continue
            self._timestamps[block] = self.timestamp(block)

    # ---------------------------------------------------------------- detect --

    def detect(self, from_block: int, to_block: int) -> list[NewPool]:
        found: dict = {}
        for dex in self.dexs:
            topic = pools.DECODERS[dex.kind][0]
            try:
                logs = self.rpc.get_logs(
                    dex.factory, [topic], from_block, to_block,
                    chunk=self.chain.log_chunk,
                )
            except Exception as exc:  # noqa: BLE001
                log.warning("getLogs %s gagal: %s", dex.key, exc)
                continue
            for raw in logs:
                new_pool = pools.decode_log(self.chain, dex, raw)
                if not new_pool:
                    continue
                if self.store.seen(self.chain.key, new_pool.pool):
                    continue
                found.setdefault(new_pool.pool.lower(), new_pool)
        return sorted(found.values(), key=lambda p: (p.block, p.log_index))

    # ---------------------------------------------------------------- enrich --

    def _enrich_one(self, new_pool: NewPool) -> PoolInfo:
        last_error = None
        for attempt in range(self.retries + 1):
            try:
                info = self.enricher.enrich(new_pool)
                if not info.created_ts:
                    info.created_ts = self.timestamp(new_pool.block)
                return info
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                log.debug("enrich %s gagal (percobaan %d): %s", new_pool.pool, attempt + 1, exc)
                time.sleep(0.3 * (attempt + 1))

        # tetap laporkan walau gagal, supaya tidak hilang
        base = PoolInfo(
            chain=self.chain.key,
            chain_name=self.chain.name,
            dex=new_pool.dex,
            dex_name=new_pool.dex_name,
            kind=new_pool.kind,
            pool=new_pool.pool,
            token0=new_pool.token0,
            token1=new_pool.token1,
            token=new_pool.token1 if self.chain.is_quote(new_pool.token0) else new_pool.token0,
            created_block=new_pool.block,
            created_tx=new_pool.tx,
            created_ts=self.timestamp(new_pool.block),
            first_seen=int(time.time()),
            notes=[f"gagal memperkaya: {str(last_error)[:70]}"],
        )
        return base

    def scan(self, from_block: int, to_block: int) -> list[PoolInfo]:
        new_pools = self.detect(from_block, to_block)
        if not new_pools:
            return []
        self._timestamps_for([p.block for p in new_pools])

        results = []
        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            futures = {pool.submit(self._enrich_one, np): np for np in new_pools}
            for future in as_completed(futures):
                new_pool = futures[future]
                try:
                    info = future.result()
                except Exception as exc:  # noqa: BLE001
                    log.warning("enrich fatal %s: %s", new_pool.pool, exc)
                    continue
                self.store.save(info)
                results.append(info)

        results.sort(key=lambda i: (i.created_ts, i.created_block))
        return results

    # ---------------------------------------------------------------- watch --

    def reenrich(self, info: PoolInfo) -> PoolInfo:
        new_pool = NewPool(
            chain=self.chain.key, dex=info.dex, dex_name=info.dex_name, kind=info.kind,
            pool=info.pool, token0=info.token0, token1=info.token1,
            fee_tier=info.fee_tier or 0, block=info.created_block,
            tx=info.created_tx, ts=info.created_ts,
        )
        fresh = self.enricher.enrich(new_pool)
        if not fresh.created_ts:
            fresh.created_ts = info.created_ts
        self.store.save(fresh)
        return fresh

    def _process_pending(self, recheck: float, max_attempts: int = 4):
        """Cek ulang pool yang saat dideteksi belum punya likuiditas."""
        now = time.time()
        updates = []
        for pool, (info, due, attempts) in list(self._pending.items()):
            if now < due:
                continue
            try:
                fresh = self.reenrich(info)
            except Exception as exc:  # noqa: BLE001
                log.debug("recheck %s gagal: %s", pool, exc)
                fresh = None
            if fresh and (fresh.liquidity_usd or 0) > 0:
                fresh.notes.append("likuiditas muncul setelah dideteksi")
                updates.append(fresh)
                del self._pending[pool]
            elif attempts + 1 >= max_attempts or now - (info.created_ts or now) > 900:
                del self._pending[pool]
            else:
                self._pending[pool] = (info, now + max(recheck, 20), attempts + 1)
        return updates

    def watch(self, interval: float = 5.0, max_catchup: int = 400, recheck: float = 45.0):
        head = self.head()
        last = head
        log.info("mulai memantau %s dari block %d", self.chain.name, head)
        while True:
            try:
                time.sleep(interval)
                updates = self._process_pending(recheck) if recheck else []
                infos = []
                head = self.head()
                if head > last:
                    from_block = last + 1
                    if head - from_block > max_catchup:
                        skipped = head - from_block - max_catchup
                        log.warning("tertinggal %d block, lompat ke %d block terakhir", skipped, max_catchup)
                        from_block = head - max_catchup + 1
                    infos = self.scan(from_block, head)
                    last = head
                    if recheck:
                        due = time.time() + recheck
                        for info in infos:
                            if (info.liquidity_usd or 0) <= 0:
                                self._pending[info.pool] = (info, due, 0)
                if infos or updates:
                    yield {"new": infos, "updated": updates}
            except KeyboardInterrupt:
                raise
            except Exception as exc:  # noqa: BLE001
                log.warning("loop error: %s", exc)
                time.sleep(min(interval * 2, 15))
