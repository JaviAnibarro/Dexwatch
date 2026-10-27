"""Konfigurasi chain & DEX yang dipantau.

Menambah DEX baru = tambah satu entri Dex(...) dengan alamat factory.
Menambah chain baru = tambah entri CHAINS.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Dex:
    key: str
    name: str
    kind: str          # "v2" | "v3" | "aerodrome"
    factory: str
    fee_pct: float = 0.30   # fee pool v2 (informasi saja)


@dataclass(frozen=True)
class Chain:
    key: str
    name: str
    chain_id: int
    native: str
    wrapped: str
    stables: tuple
    rpc: tuple
    explorer: str
    dexes: tuple
    blockscout: str = ""
    block_time: float = 3.0
    log_chunk: int = 400
    extra: dict = field(default_factory=dict)

    def dex(self, key: str) -> Dex:
        for dex in self.dexes:
            if dex.key == key:
                return dex
        raise KeyError(f"DEX '{key}' tidak ada di chain {self.key}")

    def is_quote(self, address: str) -> bool:
        addr = address.lower()
        return addr == self.wrapped.lower() or addr in {s.lower() for s in self.stables}


WBNB = "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c"
WETH = "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"
BASE_WETH = "0x4200000000000000000000000000000000000006"
ARB_WETH = "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1"

USDT_BSC = "0x55d398326f99059fF775485246999027B3197955"
USDC_BSC = "0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d"
BUSD_BSC = "0xe9e7CEA3DedcA5984780Bafc599bD69ADd087D56"

USDC_ETH = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
USDT_ETH = "0xdAC17F958D2ee523a2206206994597C13D831ec7"
DAI_ETH = "0x6B175474E89094C44Da98b954EedeAC495271d0F"

USDC_BASE = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
USDB_C_BASE = "0xd9aAEc86B65D86f6A7B5B1b0c42FFA531710b6CA"

USDC_ARB = "0xaf88d065e77c8cC2239327C5EDb3A432268e5831"
USDT_ARB = "0xFd086bC7CD5C481DCC9C85ebE478A1C0b69FCbb9"

CHAINS = {
    "bsc": Chain(
        key="bsc",
        name="BNB Smart Chain",
        chain_id=56,
        native="BNB",
        wrapped=WBNB,
        stables=(USDT_BSC, USDC_BSC, BUSD_BSC),
        rpc=(
            "https://bsc-rpc.publicnode.com",
            "https://rpc.48.club",
            "https://bsc.drpc.org",
            "https://1rpc.io/bnb",
            "https://bsc-dataseed.binance.org",
        ),
        explorer="https://bscscan.com",
        blockscout="https://api.bscscan.com",
        block_time=0.75,
        log_chunk=400,
        dexes=(
            Dex("pancakeswap-v2", "PancakeSwap V2", "v2",
                "0xcA143Ce32Fe78f1f7019d7d551a6402fC5350c73", 0.25),
            Dex("pancakeswap-v3", "PancakeSwap V3", "v3",
                "0x0BFbCF9fa4f9C56B0F40a671Ad40E0805A091865", 0.25),
            Dex("biswap-v2", "Biswap V2", "v2",
                "0x858E3312ed3A876947EA49d572A7C42DE08af7EE", 0.10),
            Dex("apeswap-v2", "ApeSwap V2", "v2",
                "0x0841BD0B734E4F5853f0dD8d7Ea041c241fb0Da6", 0.20),
        ),
    ),
    "ethereum": Chain(
        key="ethereum",
        name="Ethereum",
        chain_id=1,
        native="ETH",
        wrapped=WETH,
        stables=(USDC_ETH, USDT_ETH, DAI_ETH),
        rpc=(
            "https://ethereum-rpc.publicnode.com",
            "https://eth.drpc.org",
            "https://eth.merkle.io",
            "https://rpc.ankr.com/eth",
        ),
        explorer="https://etherscan.io",
        block_time=12.0,
        log_chunk=800,
        dexes=(
            Dex("uniswap-v2", "Uniswap V2", "v2",
                "0x5C69bEe701ef814a2B6a3EDD4B1652CB9cc5aA6f", 0.30),
            Dex("uniswap-v3", "Uniswap V3", "v3",
                "0x1F98431c8aD98523631AE4a59f267346ea31F984", 0.30),
            Dex("sushiswap-v2", "SushiSwap V2", "v2",
                "0xC0AEe478e3658e2610c5F7A4A2E1777cE9e4f2Ac", 0.30),
        ),
    ),
    "base": Chain(
        key="base",
        name="Base",
        chain_id=8453,
        native="ETH",
        wrapped=BASE_WETH,
        stables=(USDC_BASE, USDB_C_BASE),
        rpc=(
            "https://base-rpc.publicnode.com",
            "https://base.drpc.org",
            "https://mainnet.base.org",
        ),
        explorer="https://basescan.org",
        block_time=2.0,
        log_chunk=600,
        dexes=(
            Dex("uniswap-v2", "Uniswap V2", "v2",
                "0x8909Dc15e40173Ff4699343b6eB8132c65e18eC6", 0.30),
            Dex("uniswap-v3", "Uniswap V3", "v3",
                "0x33128a8fC17869897dcE68Ed026d694621f6FDfD", 0.30),
            Dex("aerodrome", "Aerodrome", "aerodrome",
                "0x420DD381b31aEf6683db6B902084cB0FFECe40Da", 0.30),
        ),
    ),
    "arbitrum": Chain(
        key="arbitrum",
        name="Arbitrum One",
        chain_id=42161,
        native="ETH",
        wrapped=ARB_WETH,
        stables=(USDC_ARB, USDT_ARB),
        rpc=(
            "https://arbitrum-one-rpc.publicnode.com",
            "https://arbitrum.drpc.org",
            "https://arb1.arbitrum.io/rpc",
        ),
        explorer="https://arbiscan.io",
        block_time=0.25,
        log_chunk=800,
        dexes=(
            Dex("uniswap-v3", "Uniswap V3", "v3",
                "0x1F98431c8aD98523631AE4a59f267346ea31F984", 0.30),
            Dex("sushiswap-v2", "SushiSwap V2", "v2",
                "0xc35DADB65012eC5796536bD9864eD8773aBc74C4", 0.30),
        ),
    ),
}

DEFAULT_CHAIN = "bsc"


def get_chain(key: str) -> Chain:
    key = (key or DEFAULT_CHAIN).lower()
    if key not in CHAINS:
        raise KeyError(
            f"chain '{key}' belum didukung. Pilihan: {', '.join(sorted(CHAINS))}"
        )
    return CHAINS[key]
