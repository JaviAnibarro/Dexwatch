"""Klien JSON-RPC kecil: failover endpoint, throttle, dan getLogs adaptif.

RPC publik punya batas berbeda-beda (rentang eth_getLogs, rate limit), jadi:
  * endpoint dirotasi dan yang sedang kena limit diberi cooldown,
  * rentang getLogs dipotong otomatis dan diperkecil saat ditolak,
  * request diberi jarak minimal supaya tidak menabrak rate limit.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import urllib.error
import urllib.request

log = logging.getLogger("dexwatch.rpc")

_UA = "dexwatch/1.0 (+python-urllib)"

_THROTTLE_WORDS = (
    "rate limit", "too many", "limit exceeded", "usage limit", "upgrade",
    "capacity", "busy", "429", "exceeded", "quota",
)
_RANGE_WORDS = (
    "range", "block range", "query returned", "response size", "too large",
    "max results", "invalid params", "limit",
)


class RpcError(Exception):
    """Error dari endpoint RPC (bukan error jaringan)."""

    def __init__(self, message: str, code=None, endpoint: str = ""):
        super().__init__(message)
        self.message = message
        self.code = code
        self.endpoint = endpoint


class TransportError(Exception):
    """Gagal menghubungi endpoint (timeout/HTTP/DNS)."""


def _is_throttle(message: str) -> bool:
    message = (message or "").lower()
    return any(word in message for word in _THROTTLE_WORDS)


def _is_range_error(message: str) -> bool:
    message = (message or "").lower()
    return any(word in message for word in _RANGE_WORDS)


def _post(url: str, payload, timeout: float):
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": _UA,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        raw = exc.read() if hasattr(exc, "read") else b""
        if raw:
            try:
                return json.loads(raw.decode("utf-8", "replace"))
            except ValueError:
                pass
        raise TransportError(f"HTTP {exc.code} dari {url}") from exc
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
        raise TransportError(f"gagal menghubungi {url}: {exc}") from exc

    try:
        return json.loads(raw.decode("utf-8", "replace"))
    except ValueError as exc:
        raise TransportError(f"balasan bukan JSON dari {url}") from exc


class Rpc:
    def __init__(self, urls, timeout: float = 25.0, retries: int = 2, rate: float = 12.0):
        self.urls = [u for u in urls if u]
        if not self.urls:
            raise ValueError("butuh minimal satu URL RPC")
        self.timeout = timeout
        self.retries = retries
        self.rate = rate
        self._bad_until = {}
        self._nolog = set()
        self._lock = threading.Lock()
        self._rr = 0
        self._next_slot = 0.0

    # ------------------------------------------------------------ endpoints --

    def _order(self, for_logs: bool = False):
        now = time.time()
        with self._lock:
            start = self._rr
            self._rr = (self._rr + 1) % len(self.urls)
        ordered = [self.urls[(start + i) % len(self.urls)] for i in range(len(self.urls))]
        if for_logs:
            ordered = [u for u in ordered if u not in self._nolog] or ordered
        healthy = [u for u in ordered if self._bad_until.get(u, 0) <= now]
        return healthy or ordered

    def _mark_bad(self, url, seconds=30):
        with self._lock:
            self._bad_until[url] = time.time() + seconds

    def _mark_good(self, url):
        with self._lock:
            self._bad_until.pop(url, None)

    def _throttle(self):
        if self.rate <= 0:
            return
        with self._lock:
            now = time.time()
            slot = max(now, self._next_slot)
            self._next_slot = slot + 1.0 / self.rate
        delay = slot - time.time()
        if delay > 0:
            time.sleep(delay)

    # ---------------------------------------------------------------- calls --

    def call(self, method: str, params=None, retries=None):
        params = params or []
        attempts = self.retries if retries is None else retries
        last_error = None
        for attempt in range(attempts + 1):
            for url in self._order():
                self._throttle()
                try:
                    data = _post(
                        url,
                        {"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
                        self.timeout,
                    )
                except TransportError as exc:
                    last_error = exc
                    self._mark_bad(url, 20)
                    log.debug("transport %s: %s", url, exc)
                    continue
                self._mark_good(url)
                if isinstance(data, dict) and "error" in data:
                    err = data["error"]
                    message = str(err.get("message", err))
                    last_error = RpcError(message, err.get("code"), url)
                    if _is_throttle(message):
                        self._mark_bad(url, 25)
                        log.debug("throttle %s: %s", url, message[:60])
                        continue
                    raise last_error
                return data.get("result")
            time.sleep(0.25 + attempt * 0.75)
        raise last_error if isinstance(last_error, RpcError) else TransportError(
            str(last_error) if last_error else "semua endpoint gagal"
        )

    def batch(self, calls):
        """calls: list of (method, params). Kembalikan list result/exception."""
        results = [None] * len(calls)
        remaining = list(range(len(calls)))
        for attempt in range(self.retries + 1):
            if not remaining:
                break
            payload = [
                {"jsonrpc": "2.0", "id": i, "method": calls[i][0], "params": calls[i][1]}
                for i in remaining
            ]
            progressed = False
            for url in self._order():
                self._throttle()
                try:
                    data = _post(url, payload, max(self.timeout, 40))
                except TransportError as exc:
                    self._mark_bad(url, 25)
                    log.debug("batch transport %s: %s", url, exc)
                    continue
                if not isinstance(data, list):
                    continue
                progressed = True
                by_id = {}
                for item in data:
                    if isinstance(item, dict):
                        by_id[item.get("id")] = item
                for i in remaining:
                    item = by_id.get(i)
                    if not item:
                        continue
                    if "error" in item:
                        message = str(item["error"].get("message", ""))
                        if _is_throttle(message):
                            results[i] = None  # coba lagi di endpoint lain
                            continue
                        results[i] = RpcError(message, item["error"].get("code"), url)
                    else:
                        results[i] = item.get("result")
                remaining = [i for i in remaining if isinstance(results[i], Exception) or results[i] is None]
                self._mark_good(url)
                break
            if not progressed:
                time.sleep(0.4 + attempt * 0.8)
        for i in remaining:
            if results[i] is None:
                results[i] = TransportError("batch gagal")
        return results

    # ------------------------------------------------------------- shortcuts --

    def block_number(self) -> int:
        return int(self.call("eth_blockNumber"), 16)

    def get_block(self, number: int):
        return self.call("eth_getBlockByNumber", [hex(number), False])

    def block_timestamp(self, number: int) -> int:
        block = self.get_block(number)
        return int(block["timestamp"], 16) if block else 0

    def eth_call(self, to: str, data: str, block: str = "latest"):
        return self.call("eth_call", [{"to": to, "data": data}, block])

    def get_code(self, address: str) -> str:
        return self.call("eth_getCode", [address, "latest"])

    # --------------------------------------------------------------- getLogs --

    def get_logs(self, address, topics, from_block: int, to_block: int,
                 chunk: int = 400, min_chunk: int = 10, max_logs: int = 20000):
        """Ambil log dengan rentang adaptif + failover endpoint."""
        if from_block > to_block:
            return []
        address = address if isinstance(address, (list, tuple)) else [address]
        errors = []
        for url in self._order(for_logs=True):
            current = max(1, chunk)
            cursor = from_block
            out = []
            failed = False
            range_fail = False
            while cursor <= to_block:
                end = min(to_block, cursor + current - 1)
                params = [{
                    "fromBlock": hex(cursor),
                    "toBlock": hex(end),
                    "address": list(address),
                }]
                if topics:
                    params[0]["topics"] = topics
                self._throttle()
                try:
                    data = _post(
                        url,
                        {"jsonrpc": "2.0", "id": 1, "method": "eth_getLogs", "params": params},
                        max(self.timeout, 30),
                    )
                except TransportError as exc:
                    errors.append(str(exc))
                    self._mark_bad(url, 25)
                    failed = True
                    break
                if isinstance(data, dict) and "error" in data:
                    message = str(data["error"].get("message", ""))
                    low = message.lower()
                    errors.append(f"{url}: {message}")
                    if any(w in low for w in ("rate", "too many", "busy", "capacity", "quota", "429")):
                        self._mark_bad(url, 30)
                        failed = True
                        break
                    if _is_range_error(message):
                        if current // 2 >= min_chunk:
                            current = max(min_chunk, current // 2)
                            log.debug("perkecil chunk jadi %d (%s)", current, message[:60])
                            continue
                        range_fail = True
                        failed = True
                        break
                    self._mark_bad(url, 60)
                    failed = True
                    break
                logs = data.get("result") if isinstance(data, dict) else None
                if logs is None:
                    failed = True
                    break
                out.extend(logs)
                if len(out) > max_logs:
                    out = out[:max_logs]
                    cursor = to_block + 1
                    break
                cursor = end + 1
            if not failed and cursor > to_block:
                self._mark_good(url)
                return out
            if range_fail:
                with self._lock:
                    self._nolog.add(url)
        raise RpcError("semua endpoint gagal untuk eth_getLogs: " + "; ".join(errors[:3]))
