"""Keccak-256 murni Python (tanpa dependensi).

Dipakai untuk menurunkan topic event dan selector fungsi secara
programatik, supaya tidak ada konstanta hash yang salah ketik.
"""

_MASK = (1 << 64) - 1

_ROUND_CONSTANTS = [
    0x0000000000000001, 0x0000000000008082, 0x800000000000808A,
    0x8000000080008000, 0x000000000000808B, 0x0000000080000001,
    0x8000000080008081, 0x8000000000008009, 0x000000000000008A,
    0x0000000000000088, 0x0000000080008009, 0x000000008000000A,
    0x000000008000808B, 0x800000000000008B, 0x8000000000008089,
    0x8000000000008003, 0x8000000000008002, 0x8000000000000080,
    0x000000000000800A, 0x800000008000000A, 0x8000000080008081,
    0x8000000000008080, 0x0000000080000001, 0x8000000080008008,
]

_ROTATIONS = [
    [0, 36, 3, 41, 18],
    [1, 44, 10, 45, 2],
    [62, 6, 43, 15, 61],
    [28, 55, 25, 21, 56],
    [27, 20, 39, 8, 14],
]

_RATE = 136  # 1088 bit


def _rotl(value: int, shift: int) -> int:
    shift %= 64
    if shift == 0:
        return value & _MASK
    return ((value << shift) | (value >> (64 - shift))) & _MASK


def _keccak_f(state: list) -> None:
    for rnd in range(24):
        # theta
        c = [state[x] ^ state[x + 5] ^ state[x + 10] ^ state[x + 15] ^ state[x + 20]
             for x in range(5)]
        d = [c[(x - 1) % 5] ^ _rotl(c[(x + 1) % 5], 1) for x in range(5)]
        for x in range(5):
            for y in range(5):
                state[x + 5 * y] ^= d[x]
        # rho + pi
        b = [0] * 25
        for x in range(5):
            for y in range(5):
                b[y + 5 * ((2 * x + 3 * y) % 5)] = _rotl(
                    state[x + 5 * y], _ROTATIONS[x][y]
                )
        # chi
        for x in range(5):
            for y in range(5):
                state[x + 5 * y] = b[x + 5 * y] ^ (
                    (~b[(x + 1) % 5 + 5 * y]) & b[(x + 2) % 5 + 5 * y]
                )
        # iota
        state[0] ^= _ROUND_CONSTANTS[rnd]


def keccak256(data: bytes) -> bytes:
    """Keccak-256 versi asli (padding 0x01), seperti yang dipakai EVM."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    padded = bytearray(data)
    padded.append(0x01)
    while len(padded) % _RATE != _RATE - 1:
        padded.append(0x00)
    padded.append(0x80)

    state = [0] * 25
    for offset in range(0, len(padded), _RATE):
        block = padded[offset:offset + _RATE]
        for i in range(_RATE // 8):
            state[i] ^= int.from_bytes(block[i * 8:(i + 1) * 8], "little")
        _keccak_f(state)

    out = b"".join(state[i].to_bytes(8, "little") for i in range(4))
    return out


def keccak_hex(data) -> str:
    return "0x" + keccak256(data).hex()
