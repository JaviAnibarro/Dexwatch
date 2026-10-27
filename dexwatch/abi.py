"""Encoding/decoding ABI minimal untuk kebutuhan scanner.

Semua topic/selector diturunkan dari signature memakai keccak256,
jadi tidak ada hash hardcode yang bisa salah.
"""

from __future__ import annotations

from .keccak import keccak256

# ---------------------------------------------------------------- signature --

def selector(signature: str) -> str:
    """4-byte selector sebagai string 0x...."""
    return "0x" + keccak256(signature)[:4].hex()


def topic0(signature: str) -> str:
    """topic0 (32 byte) sebuah event sebagai string 0x...."""
    return "0x" + keccak256(signature).hex()


# Event yang dipakai scanner.
PAIR_CREATED = topic0("PairCreated(address,address,address,uint256)")
POOL_CREATED_V3 = topic0("PoolCreated(address,address,uint24,int24,address)")
POOL_CREATED_AERO = topic0("PoolCreated(address,address,bool,address,uint256)")

# Selector fungsi.
SEL_NAME = selector("name()")
SEL_SYMBOL = selector("symbol()")
SEL_DECIMALS = selector("decimals()")
SEL_TOTAL_SUPPLY = selector("totalSupply()")
SEL_GET_RESERVES = selector("getReserves()")
SEL_TOKEN0 = selector("token0()")
SEL_TOKEN1 = selector("token1()")
SEL_FACTORY = selector("factory()")
SEL_FEE = selector("fee()")
SEL_LIQUIDITY = selector("liquidity()")
SEL_SLOT0 = selector("slot0()")
SEL_OWNER = selector("owner()")
SEL_GET_PAIR = selector("getPair(address,address)")
SEL_BALANCE_OF = selector("balanceOf(address)")

_DEAD = "0x000000000000000000000000000000000000dead"


# ------------------------------------------------------------------ encode --

def enc_address(address: str) -> str:
    """Alamat -> 32 byte hex (tanpa 0x, lowercase)."""
    return address.lower().replace("0x", "").rjust(64, "0")[-64:]


def enc_uint(value: int) -> str:
    return format(value, "064x")


def calldata(signature_selector: str, *args: str) -> str:
    """Gabungkan selector + argumen yang sudah di-encode."""
    return signature_selector + "".join(args)


def balance_of(address: str) -> str:
    return calldata(SEL_BALANCE_OF, enc_address(address))


def get_pair(token_a: str, token_b: str) -> str:
    return calldata(SEL_GET_PAIR, enc_address(token_a), enc_address(token_b))


# ------------------------------------------------------------------ decode --

def _words(data_hex: str) -> list:
    raw = data_hex[2:] if data_hex.startswith("0x") else data_hex
    return [raw[i:i + 64] for i in range(0, len(raw) - 63, 64)]


def word(data_hex: str, index: int) -> str:
    words = _words(data_hex)
    if index >= len(words):
        return "0" * 64
    w = words[index]
    return w if len(w) == 64 else w.rjust(64, "0")


def decode_uint(data_hex: str, index: int = 0) -> int:
    return int(word(data_hex, index) or "0", 16)


def decode_int(data_hex: str, index: int = 0, bits: int = 256) -> int:
    """Decode integer bertanda (ABI menandatangani ke 256 bit)."""
    value = decode_uint(data_hex, index)
    if value >= (1 << 255):  # sign-extended negatif
        value -= 1 << 256
    if value >= (1 << (bits - 1)):
        value -= 1 << bits
    return value


def decode_address(data_hex: str, index: int = 0) -> str:
    return "0x" + word(data_hex, index)[-40:]


def decode_bool(data_hex: str, index: int = 0) -> bool:
    return decode_uint(data_hex, index) != 0


def decode_string(data_hex: str) -> str:
    """Decode string/bytes32 dari hasil eth_call `name()`/`symbol()`."""
    raw = data_hex[2:] if data_hex.startswith("0x") else data_hex
    if not raw:
        return ""
    data = bytes.fromhex(raw)
    if len(data) >= 64:
        offset = int.from_bytes(data[0:32], "big")
        # offset yang wajar: kelipatan 32 dan masih di dalam data
        if 0 < offset <= len(data) - 32 and offset % 32 == 0:
            length = int.from_bytes(data[offset:offset + 32], "big")
            if 0 <= length <= len(data) - offset - 32:
                text = data[offset + 32:offset + 32 + length]
                return text.decode("utf-8", "replace").strip("\x00").strip()
    first = data[:32].rstrip(b"\x00")
    try:
        return first.decode("utf-8", "replace").strip("\x00").strip()
    except Exception:
        return ""


# ---------------------------------------------------------------- addresses --

def to_checksum(address: str) -> str:
    """EIP-55 checksum, untuk tampilan yang enak diklik ke explorer."""
    addr = address.lower().replace("0x", "")
    if len(addr) != 40:
        return address
    digest = keccak256(addr).hex()
    out = "".join(
        ch.upper() if int(digest[i], 16) >= 8 else ch
        for i, ch in enumerate(addr)
    )
    return "0x" + out


def is_address(value: str) -> bool:
    if not isinstance(value, str):
        return False
    value = value.strip()
    if value.startswith("0x"):
        value = value[2:]
    return len(value) == 40 and all(c in "0123456789abcdefABCDEF" for c in value)


def norm_address(value: str) -> str:
    return "0x" + value.lower().replace("0x", "")


DEAD = "0x000000000000000000000000000000000000dEaD"

DEAD_ADDRESSES = {
    "0x0000000000000000000000000000000000000000",
    "0x000000000000000000000000000000000000dead",
    "0x0000000000000000000000000000000000000001",
}
