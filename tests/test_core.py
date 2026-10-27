import json
import os
import tempfile
import time
import unittest

from dexwatch import abi, mathx
from dexwatch.chains import get_chain
from dexwatch.keccak import keccak_hex
from dexwatch.pools import (
    NewPool, PoolInfo, decode_log, decode_pair_created,
    decode_pool_created_aero, decode_pool_created_v3,
)
from dexwatch.render import fmt_age, fmt_local, fmt_usd, to_csv
from dexwatch.store import Store


def word(value: int) -> str:
    return format(value, "064x")


def addr_word(address: str) -> str:
    return "0" * 24 + address.lower().replace("0x", "")


def make_log(topic, topics, data, block=100, index=0):
    return {
        "topics": [topic] + topics,
        "data": "0x" + data,
        "blockNumber": hex(block),
        "logIndex": hex(index),
        "transactionHash": "0x" + "ab" * 32,
        "address": "0x" + "cd" * 20,
    }


class TestKeccak(unittest.TestCase):
    def test_vectors(self):
        self.assertEqual(
            keccak_hex(b""),
            "0xc5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470",
        )
        self.assertEqual(
            keccak_hex("abc"),
            "0x4e03657aea45a94fc7d47ba826c8d667c0d1e6e33a64a036ec44f58fa12d6c45",
        )
        self.assertEqual(
            keccak_hex("Transfer(address,address,uint256)"),
            "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef",
        )

    def test_event_topics(self):
        self.assertEqual(
            abi.PAIR_CREATED,
            "0x0d3648bd0f6ba80134a33ba9275ac585d9d315f0ad8355cddefde31afa28d0e9",
        )
        self.assertEqual(abi.SEL_NAME, "0x06fdde03")
        self.assertEqual(abi.SEL_SYMBOL, "0x95d89b41")
        self.assertEqual(abi.SEL_DECIMALS, "0x313ce567")


class TestAbi(unittest.TestCase):
    def test_decode_string(self):
        text = "PancakeSwap Token"
        payload = word(32) + word(len(text)) + text.encode().hex().ljust(64, "0")
        self.assertEqual(abi.decode_string("0x" + payload), text)

    def test_decode_bytes32(self):
        payload = b"MKR".ljust(32, b"\x00").hex()
        self.assertEqual(abi.decode_string("0x" + payload), "MKR")

    def test_decode_numbers(self):
        data = "0x" + word(1234) + word(2 ** 256 - 1)
        self.assertEqual(abi.decode_uint(data, 0), 1234)
        self.assertEqual(abi.decode_int(data, 1, bits=24), -1)

    def test_checksum(self):
        self.assertEqual(
            abi.to_checksum("0x0e09fabb73bd3ade0a17ecc321fd13a19e81ce82"),
            "0x0E09FaBB73Bd3Ade0a17ECC321fD13a19e81cE82",
        )


class TestMath(unittest.TestCase):
    def test_v2(self):
        amount0, amount1, price = mathx.v2_amounts(1_000_000, 5 * 10**17, 6, 18)
        self.assertAlmostEqual(amount0, 1.0)
        self.assertAlmostEqual(amount1, 0.5)
        self.assertAlmostEqual(price, 0.5)

    def test_v3_price(self):
        self.assertAlmostEqual(mathx.v3_price0_in_1(2**96, 18, 18), 1.0)
        self.assertAlmostEqual(mathx.v3_price0_in_1(2**96 * 2, 18, 18), 4.0)
        self.assertAlmostEqual(mathx.v3_price0_in_1(2**96, 6, 18), 1e-12)

    def test_v3_amounts(self):
        a0, a1 = mathx.v3_amounts(10**18, 2**96, 18, 18)
        self.assertAlmostEqual(a0, 1.0)
        self.assertAlmostEqual(a1, 1.0)


class TestDecode(unittest.TestCase):
    def setUp(self):
        self.chain = get_chain("bsc")
        self.v2 = self.chain.dex("pancakeswap-v2")
        self.v3 = self.chain.dex("pancakeswap-v3")

    def test_pair_created(self):
        t0 = "0x" + "11" * 20
        t1 = "0x" + "22" * 20
        pair = "0x" + "33" * 20
        log = make_log(abi.PAIR_CREATED, [addr_word(t0), addr_word(t1)],
                       addr_word(pair) + word(0), block=1234)
        np = decode_log(self.chain, self.v2, log)
        self.assertEqual(np.pool, pair)
        self.assertEqual(np.token0, t0)
        self.assertEqual(np.token1, t1)
        self.assertEqual(np.block, 1234)

    def test_pool_created_v3_indexed_fee(self):
        t0 = "0x" + "44" * 20
        t1 = "0x" + "55" * 20
        pool = "0x" + "66" * 20
        log = make_log(abi.POOL_CREATED_V3,
                       [addr_word(t0), addr_word(t1), word(500)],
                       word(10) + addr_word(pool))
        np = decode_log(self.chain, self.v3, log)
        self.assertEqual(np.pool, pool)
        self.assertAlmostEqual(np.fee_tier, 0.05)

    def test_pool_created_aero(self):
        t0 = "0x" + "77" * 20
        t1 = "0x" + "88" * 20
        pool = "0x" + "99" * 20
        log = make_log(abi.POOL_CREATED_AERO,
                       [addr_word(t0), addr_word(t1), word(1)],
                       addr_word(pool) + word(0))
        chain = get_chain("base")
        np = decode_log(chain, chain.dex("aerodrome"), log)
        self.assertEqual(np.pool, pool)
        self.assertTrue(np.stable)


class TestStore(unittest.TestCase):
    def test_save_dedupe(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            store = Store(path)
            info = PoolInfo(chain="bsc", pool="0x" + "aa" * 20, token="0x" + "bb" * 20,
                            symbol="TEST", name="Test", dex="pancakeswap-v2",
                            created_ts=1_700_000_000, created_block=10, liquidity_usd=1234.0)
            self.assertFalse(store.seen("bsc", info.pool))
            store.save(info)
            self.assertTrue(store.seen("bsc", info.pool))
            self.assertEqual(store.count(), 1)
            found = store.find("bsc", info.token)
            self.assertEqual(found.symbol, "TEST")
            self.assertEqual(len(store.recent(10)), 1)
            store.close()
        finally:
            for suffix in ("", "-wal", "-shm"):
                try:
                    os.unlink(path + suffix)
                except OSError:
                    pass


class TestPendingRecheck(unittest.TestCase):
    def test_update_when_liquidity_appears(self):
        from types import SimpleNamespace
        from dexwatch.scanner import Scanner

        class FakeEnricher:
            def __init__(self):
                self.calls = 0

            def enrich(self, new_pool):
                self.calls += 1
                return PoolInfo(chain="bsc", pool=new_pool.pool, token="0x" + "cc" * 20,
                                symbol="NEW", liquidity_usd=5000.0, reserve_quote=2.5)

        saved = []

        class FakeStore:
            def save(self, info):
                saved.append(info)

        chain = get_chain("bsc")
        scanner = Scanner(chain, rpc=None, enricher=FakeEnricher(), store=FakeStore())
        info = PoolInfo(chain="bsc", pool="0x" + "dd" * 20, token="0x" + "cc" * 20,
                        symbol="NEW", created_ts=int(__import__("time").time()) - 5,
                        token0="0x" + "cc" * 20, token1=chain.wrapped)
        scanner._pending[info.pool] = (info, 0, 0)
        updates = scanner._process_pending(recheck=30)
        self.assertEqual(len(updates), 1)
        self.assertEqual(updates[0].liquidity_usd, 5000.0)
        self.assertEqual(len(saved), 1)
        self.assertNotIn(info.pool, scanner._pending)


class TestRender(unittest.TestCase):
    def test_formats(self):
        self.assertEqual(fmt_usd(12345), "$12.3K")
        self.assertEqual(fmt_usd(2_000_000), "$2.0M")
        self.assertEqual(fmt_age(65), "1m")
        self.assertEqual(fmt_age(3700), "1j")
        self.assertEqual(fmt_local(1234.5, 2), "1.234,50")

    def test_csv(self):
        info = PoolInfo(chain="bsc", pool="0xabc", token="0xdef", symbol="X")
        text = to_csv([info])
        self.assertTrue(text.startswith("created_ts,chain,dex"))


if __name__ == "__main__":
    unittest.main()


class TestSnapshots(unittest.TestCase):
    def test_history_and_prune(self):
        import tempfile
        from dexwatch.store import Store

        with tempfile.TemporaryDirectory() as tmp:
            store = Store(os.path.join(tmp, "s.db"))
            for ts in (100, 200, 300):
                store.add_snapshot("bsc", "0xAbC", 1.0 + ts, 5000, ts=ts)
            store.add_snapshot("bsc", "0xAbC", 0, 0, ts=400)  # harga 0 diabaikan
            hist = store.history("bsc", "0xabc", limit=10)
            self.assertEqual([p["ts"] for p in hist], [100, 200, 300])
            grouped = store.history_map("bsc", since=0)
            self.assertEqual(len(grouped["0xabc"]), 3)
            store.prune_snapshots("bsc", "0xabc", keep=2)
            self.assertEqual(len(store.history("bsc", "0xabc")), 2)
            self.assertEqual(store.last_snapshot_ts("bsc", "0xABC"), 300)
            store.close()


class TestMarket(unittest.TestCase):
    def test_pair_payload(self):
        from dexwatch.market import _pair_payload

        payload = _pair_payload({
            "priceUsd": "0.0012",
            "priceNative": "0.0000016",
            "liquidity": {"usd": 42000},
            "fdv": 1200000,
            "marketCap": 900000,
            "priceChange": {"m5": 1.5, "h24": -12.5},
            "volume": {"h24": 55000},
            "txns": {"h24": {"buys": 120, "sells": 80}},
            "pairCreatedAt": 1700000000000,
            "labels": ["v2"],
            "info": {
                "imageUrl": "https://cdn.example/x.png",
                "websites": [{"url": "https://a.example"}],
                "socials": [{"type": "twitter", "url": "https://x.com/a"}],
            },
        })
        self.assertAlmostEqual(payload["price_usd"], 0.0012)
        self.assertEqual(payload["change"]["h24"], -12.5)
        self.assertEqual(payload["volume"]["h24"], 55000)
        self.assertEqual(payload["txns"]["h24"]["buys"], 120)
        self.assertEqual(payload["image"], "https://cdn.example/x.png")
        self.assertEqual(payload["socials"][0]["type"], "twitter")
        self.assertEqual(payload["liquidity_usd"], 42000)


class TestWebMerge(unittest.TestCase):
    def _info(self):
        from dexwatch.pools import PoolInfo

        return PoolInfo(
            chain="bsc", pool="0xP", token="0xT", symbol="CAKE", name="Pancake",
            dex="pancakeswap-v2", liquidity_usd=1000, price_usd=1.0,
            created_ts=int(time.time()) - 120, holders=10,
        )

    def test_merge_prefers_market(self):
        from dexwatch.web import _merge

        item = _merge(self._info(), {
            "price_usd": 2.5, "liquidity_usd": 9000, "mcap_usd": 50000,
            "change": {"h24": 12.0}, "volume": {"h24": 800},
            "image": "https://img", "socials": [], "websites": [],
        }, [{"ts": 1, "price": 2.0}])
        self.assertEqual(item["price_usd"], 2.5)
        self.assertEqual(item["liquidity_usd"], 9000)
        self.assertEqual(item["mcap_usd"], 50000)
        self.assertEqual(item["change"]["h24"], 12.0)
        self.assertEqual(item["spark"], [{"ts": 1, "price": 2.0}])
        self.assertTrue(100 <= item["age_seconds"] <= 180)

    def test_filters(self):
        from dexwatch.web import _matches

        item = {"symbol": "CAKE", "name": "Pancake", "token": "0xT", "pool": "0xP",
                "dex": "pancakeswap-v2", "liquidity_usd": 5000, "honeypot": True,
                "verified": False}
        self.assertTrue(_matches(item, "cake", 1000, False, False))
        self.assertFalse(_matches(item, "cake", 10000, False, False))
        self.assertFalse(_matches(item, "cake", 0, True, False))
        self.assertFalse(_matches(item, "", 0, False, True))
        self.assertTrue(_matches(item, "0xp", 0, False, False))
