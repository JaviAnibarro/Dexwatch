"""Model data dan decoding event pembuatan pool."""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field

from . import abi


@dataclass
class NewPool:
    """Pool yang baru terdeteksi dari log factory (belum diperkaya)."""

    chain: str
    dex: str
    dex_name: str
    kind: str
    pool: str
    token0: str
    token1: str
    fee_tier: float = 0.0
    stable: bool = False
    block: int = 0
    tx: str = ""
    log_index: int = 0
    ts: int = 0

    @property
    def key(self) -> str:
        return f"{self.chain}:{self.pool.lower()}"


@dataclass
class PoolInfo:
    # identitas
    chain: str = ""
    chain_name: str = ""
    dex: str = ""
    dex_name: str = ""
    kind: str = ""
    pool: str = ""
    token: str = ""
    token0: str = ""
    token1: str = ""
    quote: str = ""
    quote_symbol: str = ""
    # metadata token
    name: str = ""
    symbol: str = ""
    decimals: int = 18
    total_supply: float = 0.0
    # harga & likuiditas
    price_native: float | None = None
    price_usd: float | None = None
    reserve_token: float | None = None
    reserve_quote: float | None = None
    liquidity_native: float | None = None
    liquidity_usd: float | None = None
    fdv_usd: float | None = None
    mcap_usd: float | None = None
    lp_supply: float | None = None
    lp_burned_pct: float | None = None
    fee_tier: float | None = None
    # keamanan (butuh API eksternal, opsional)
    holders: int | None = None
    top10_pct: float | None = None
    buy_tax: float | None = None
    sell_tax: float | None = None
    honeypot: bool | None = None
    verified: bool | None = None
    owner: str | None = None
    creator: str | None = None
    # informasi tambahan
    websites: list = field(default_factory=list)
    socials: list = field(default_factory=list)
    image: str | None = None
    created_block: int = 0
    created_tx: str = ""
    created_ts: int = 0
    first_seen: int = 0
    sources: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    raw: dict = field(default_factory=dict)

    # ------------------------------------------------------------ turunan --

    @property
    def age_seconds(self) -> int:
        if not self.created_ts:
            return 0
        return max(0, int(time.time()) - self.created_ts)

    def to_dict(self) -> dict:
        data = asdict(self)
        data["age_seconds"] = self.age_seconds
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "PoolInfo":
        data = dict(data)
        data.pop("age_seconds", None)
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known})


# ---------------------------------------------------------------- decoding --

def _addr(topic: str) -> str:
    return "0x" + topic[-40:].lower()


def decode_pair_created(chain, dex, log) -> NewPool | None:
    """PairCreated(address indexed token0, address indexed token1, address pair, uint256)."""
    topics = log.get("topics") or []
    data = log.get("data") or "0x"
    if len(topics) < 3:
        return None
    return NewPool(
        chain=chain.key,
        dex=dex.key,
        dex_name=dex.name,
        kind=dex.kind,
        pool=abi.decode_address(data, 0),
        token0=_addr(topics[1]),
        token1=_addr(topics[2]),
        block=int(log["blockNumber"], 16),
        tx=log.get("transactionHash", ""),
        log_index=int(log.get("logIndex", "0x0"), 16),
    )


def decode_pool_created_v3(chain, dex, log) -> NewPool | None:
    """PoolCreated(address indexed token0, address indexed token1, uint24 indexed fee,
    int24 tickSpacing, address pool).

    Sebagian fork tidak meng-index fee, jadi layout data dideteksi dari jumlah topic.
    """
    topics = log.get("topics") or []
    data = log.get("data") or "0x"
    if len(topics) < 3:
        return None
    if len(topics) >= 4:
        fee = int(topics[3], 16)
        pool = abi.decode_address(data, 1)
    else:
        fee = abi.decode_uint(data, 0)
        pool = abi.decode_address(data, 2)
    if not pool or pool == "0x" + "0" * 40:
        return None
    return NewPool(
        chain=chain.key,
        dex=dex.key,
        dex_name=dex.name,
        kind=dex.kind,
        pool=pool,
        token0=_addr(topics[1]),
        token1=_addr(topics[2]),
        fee_tier=fee / 1_000_000 * 100 if fee else 0.0,
        block=int(log["blockNumber"], 16),
        tx=log.get("transactionHash", ""),
        log_index=int(log.get("logIndex", "0x0"), 16),
    )


def decode_pool_created_aero(chain, dex, log) -> NewPool | None:
    """PoolCreated(address indexed token0, address indexed token1, bool indexed stable, address pool, uint256)."""
    topics = log.get("topics") or []
    data = log.get("data") or "0x"
    if len(topics) < 4:
        return None
    return NewPool(
        chain=chain.key,
        dex=dex.key,
        dex_name=dex.name,
        kind=dex.kind,
        pool=abi.decode_address(data, 0),
        token0=_addr(topics[1]),
        token1=_addr(topics[2]),
        stable=bool(int(topics[3], 16)),
        block=int(log["blockNumber"], 16),
        tx=log.get("transactionHash", ""),
        log_index=int(log.get("logIndex", "0x0"), 16),
    )


DECODERS = {
    "v2": (abi.PAIR_CREATED, decode_pair_created),
    "v3": (abi.POOL_CREATED_V3, decode_pool_created_v3),
    "aerodrome": (abi.POOL_CREATED_AERO, decode_pool_created_aero),
}


def decode_log(chain, dex, log) -> NewPool | None:
    expected, decoder = DECODERS[dex.kind]
    topics = log.get("topics") or []
    if not topics or topics[0].lower() != expected:
        return None
    try:
        return decoder(chain, dex, log)
    except (ValueError, KeyError, IndexError):
        return None
