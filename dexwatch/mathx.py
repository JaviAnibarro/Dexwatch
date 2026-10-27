"""Rumus harga/likuiditas yang dipakai enricher (dipisah agar mudah diuji)."""

from __future__ import annotations

Q96 = 2 ** 96


def v2_amounts(reserve0: int, reserve1: int, decimals0: int, decimals1: int):
    """Reserve V2 dalam unit manusia -> (amount0, amount1, harga token0 dalam token1)."""
    amount0 = reserve0 / 10 ** decimals0
    amount1 = reserve1 / 10 ** decimals1
    price0_in_1 = (amount1 / amount0) if amount0 else None
    return amount0, amount1, price0_in_1


def v3_price0_in_1(sqrt_price_x96: int, decimals0: int, decimals1: int):
    """Harga token0 dinyatakan dalam token1 dari sqrtPriceX96."""
    if not sqrt_price_x96:
        return None
    price = (sqrt_price_x96 / Q96) ** 2
    return price * 10 ** (decimals0 - decimals1)


def v3_amounts(liquidity: int, sqrt_price_x96: int, decimals0: int, decimals1: int):
    """Perkiraan reserve dari likuiditas aktif (rentang tak terbatas)."""
    if not sqrt_price_x96:
        return 0.0, 0.0
    amount0 = (liquidity * Q96 / sqrt_price_x96) / 10 ** decimals0
    amount1 = (liquidity * sqrt_price_x96 / Q96) / 10 ** decimals1
    return amount0, amount1
