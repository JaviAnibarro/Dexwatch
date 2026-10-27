"""Tampilan terminal: tabel ringkas, detail lengkap, JSON/CSV."""

from __future__ import annotations

import csv
import io
import json
import re
import shutil
import sys
import time
import unicodedata

from .pools import PoolInfo

RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
BLUE = "\033[34m"
MAGENTA = "\033[35m"
CYAN = "\033[36m"
GREY = "\033[90m"

_ANSI = re.compile(r"\033\[[0-9;]*m")

EXPLORER_CHAIN = {
    "bsc": "bsc",
    "ethereum": "ethereum",
    "base": "base",
    "arbitrum": "arbitrum",
}


def strip_ansi(text: str) -> str:
    return _ANSI.sub("", text)


def width(text: str) -> int:
    total = 0
    for ch in strip_ansi(text):
        total += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return total


def truncate(text: str, limit: int) -> str:
    text = text or ""
    if width(text) <= limit:
        return text
    out = ""
    used = 0
    for ch in text:
        w = 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
        if used + w > limit - 1:
            break
        out += ch
        used += w
    return out + "…"


def pad(text: str, size: int, align: str = "left") -> str:
    text = truncate(text, size)
    gap = max(0, size - width(text))
    if align == "right":
        return " " * gap + text
    if align == "center":
        left = gap // 2
        return " " * left + text + " " * (gap - left)
    return text + " " * gap


# ------------------------------------------------------------------ numbers --

def fmt_num(value, digits: int = 2) -> str:
    if value is None:
        return "-"
    try:
        value = float(value)
    except (TypeError, ValueError):
        return "-"
    abs_v = abs(value)
    if abs_v >= 1e12:
        return f"{value / 1e12:.{digits}f}T"
    if abs_v >= 1e9:
        return f"{value / 1e9:.{digits}f}B"
    if abs_v >= 1e6:
        return f"{value / 1e6:.{digits}f}M"
    if abs_v >= 1e3:
        return f"{value / 1e3:.{digits}f}K"
    if abs_v >= 1:
        return f"{value:,.{digits}f}"
    if abs_v == 0:
        return "0"
    return f"{value:.6f}".rstrip("0").rstrip(".")


def fmt_usd(value, digits: int = 1) -> str:
    if value is None:
        return "-"
    try:
        value = float(value)
    except (TypeError, ValueError):
        return "-"
    if value >= 1:
        return "$" + fmt_num(value, digits)
    if value == 0:
        return "$0"
    text = f"{value:.8f}".rstrip("0").rstrip(".")
    return "$" + text


def fmt_price(value) -> str:
    if not value:
        return "-"
    if value >= 1:
        return f"${value:,.4f}"
    if value >= 0.0001:
        return f"${value:.6f}".rstrip("0").rstrip(".")
    return f"${value:.10f}".rstrip("0").rstrip(".")


def fmt_age(seconds) -> str:
    try:
        seconds = int(seconds or 0)
    except (TypeError, ValueError):
        return "-"
    if seconds < 0:
        return "-"
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}j"
    return f"{seconds // 86400}hr"


def fmt_local(value, decimals: int = 2) -> str:
    """Format angka gaya Indonesia: ribuan titik, desimal koma."""
    if value is None:
        return "-"
    try:
        text = f"{float(value):,.{decimals}f}"
    except (TypeError, ValueError):
        return "-"
    return text.replace(",", "\x00").replace(".", ",").replace("\x00", ".")


def fmt_token_amount(value) -> str:
    """Harga/jumlah token dengan presisi menyesuaikan besaran."""
    if value is None:
        return "-"
    try:
        value = float(value)
    except (TypeError, ValueError):
        return "-"
    if value == 0:
        return "0"
    if abs(value) >= 1000:
        return f"{value:,.2f}".replace(",", ".")
    if abs(value) >= 1:
        return f"{value:.6f}".rstrip("0").rstrip(".")
    if abs(value) >= 0.001:
        return f"{value:.8f}".rstrip("0").rstrip(".")
    return f"{value:.10f}".rstrip("0").rstrip(".")


def fmt_ts(ts) -> str:
    if not ts:
        return "-"
    return time.strftime("%d/%m/%Y %H:%M:%S", time.localtime(int(ts)))


def short_addr(address: str) -> str:
    if not address:
        return "-"
    return address[:6] + "…" + address[-4:]


# ------------------------------------------------------------------ console --

class Console:
    def __init__(self, color: bool | None = None, selected_width: int | None = None):
        if color is None:
            color = sys.stdout.isatty()
        self.color = color
        self.width = selected_width or shutil.get_terminal_size((0, 0)).columns or 0

    # --------------------------------------------------------------- helpers --

    def c(self, text: str, *codes: str) -> str:
        """Warnai teks bila mode warna aktif."""
        return self._c(text, *codes)

    def _c(self, text: str, *codes: str) -> str:
        if not self.color or not codes:
            return text
        return "".join(codes) + text + RESET

    def _w(self) -> int:
        return self.width if self.width and self.width > 20 else 200

    def line(self, text: str = ""):
        print(text)

    def rule(self, char: str = "─"):
        print(self._c(char * min(self._w() - 2, 100), GREY))

    def title(self, text: str):
        print()
        print(self._c(text, BOLD))

    # ----------------------------------------------------------------- table --

    def table(self, infos: list, empty_text: str = "(belum ada pool baru)"):
        if not infos:
            print(self._c(f"  {empty_text}", DIM))
            return
        columns = self._columns()
        header = "  ".join(
            self._c(pad(title.upper(), size, align), DIM, BOLD)
            for title, size, align, _get in columns
        )
        print(header)
        print(self._c("  ".join("─" * size for _t, size, _a, _g in columns), GREY))
        for info in infos:
            cells = []
            for title, size, align, getter in columns:
                text, codes = getter(info)
                text = pad(text, size, align)
                cells.append(self._c(text, *codes) if codes else text)
            print("  ".join(cells))

    def _columns(self):
        all_columns = [
            ("umur", 6, "left", lambda i: (fmt_age(i.age_seconds), [DIM])),
            ("dex", 16, "left", lambda i: (i.dex, [BLUE])),
            ("token", 24, "left", lambda i: (
                (truncate(i.symbol or "?", 12) + " " + truncate(i.name or "", 11)).strip(),
                [CYAN, BOLD])),
            ("contract", 13, "left", lambda i: (short_addr(i.token), [GREY])),
            ("likuiditas", 11, "right", lambda i: (
                fmt_usd(i.liquidity_usd), self._liq_color(i.liquidity_usd))),
            ("harga", 12, "right", lambda i: (fmt_price(i.price_usd), [])),
            ("mcap", 10, "right", lambda i: (fmt_usd(i.mcap_usd), [])),
            ("holder", 8, "right", lambda i: (
                fmt_num(i.holders, 0) if i.holders is not None else "-", [])),
            ("pajak", 10, "right", lambda i: (_tax(i), [])),
            ("lp burn", 8, "right", lambda i: (
                f"{i.lp_burned_pct:.0f}%" if i.lp_burned_pct is not None else "-", [])),
            ("catatan", 24, "left", lambda i: (_flags(i), [YELLOW])),
        ]
        width_left = self._w()
        columns = list(all_columns)
        total = sum(c[1] for c in columns) + 2 * (len(columns) - 1)
        # buang kolom tambahan kalau layar sempit
        for droppable in ("catatan", "lp burn", "pajak", "holder", "mcap"):
            if total <= width_left:
                break
            for col in list(columns):
                if col[0] == droppable:
                    columns.remove(col)
                    total -= col[1] + 2
                    break
        if total > width_left and len(columns) > 4:
            over = total - width_left
            name_col = columns[2]
            new_size = max(12, name_col[1] - over)
            columns[2] = (name_col[0], new_size, name_col[2], name_col[3])
            total = sum(c[1] for c in columns) + 2 * (len(columns) - 1)
        return columns

    @staticmethod
    def _liq_color(liq):
        if liq is None:
            return [DIM]
        if liq >= 50_000:
            return [GREEN]
        if liq >= 5_000:
            return [YELLOW]
        return [RED]

    # ---------------------------------------------------------------- detail --

    def detail(self, info: PoolInfo):
        name = info.name or "(tanpa nama)"
        symbol = info.symbol or "?"
        chain = info.chain_name or info.chain
        self.rule("═")
        print(self._c(f"{symbol}", BOLD, CYAN) + self._c(f"  {name}", BOLD))
        print(self._c(f"{info.dex_name} · {chain} · {info.kind.upper()}", DIM))
        self.rule("═")

        rows = [
            ("Contract token", self._value(info.token, addr=True)),
            ("Pool / pair", self._value(info.pool, addr=True)),
            ("Quote token", f"{info.quote_symbol}  {self._c(short_addr(info.quote), GREY)}"),
            ("Dibuat", f"{fmt_ts(info.created_ts)}  ({fmt_age(info.age_seconds)} lalu)"),
            ("Block / Tx", (f"{info.created_block:,}".replace(",", ".")
                            + (f"  {short_addr(info.created_tx)}" if info.created_tx else ""))
                           if info.created_block else "-"),
            ("Total supply", fmt_local(info.total_supply, 0) if info.total_supply else "-"),
            ("Harga", self._price(info)),
            ("Likuiditas", self._liquidity(info)),
            ("Reserve token", fmt_local(info.reserve_token, 2) if info.reserve_token is not None else "-"),
            ("Reserve quote", (f"{fmt_local(info.reserve_quote, 4).rstrip('0').rstrip(',')}"
                               f" {info.quote_symbol}") if info.reserve_quote is not None else "-"),
            ("LP supply", fmt_local(info.lp_supply, 2) if info.lp_supply else "-"),
            ("LP burned", f"{info.lp_burned_pct:.2f}%" if info.lp_burned_pct is not None else "-"),
            ("Fee pool", f"{info.fee_tier}%" if info.fee_tier else "-"),
            ("Holder", self._holders(info)),
            ("Pajak beli/jual", _tax(info) if _tax(info) != "-" else "-"),
            ("Honeypot", self._bool(info.honeypot, dangerous=True)),
            ("Verified source", self._bool(info.verified)),
            ("Owner", info.owner or "-"),
            ("Creator", info.creator or "-"),
            ("Web", "\n".join(info.websites) if info.websites else "-"),
            ("Social", "\n".join(f"{s.get('type', '')}: {s.get('url')}" for s in info.socials) if info.socials else "-"),
            ("Sumber data", ", ".join(info.sources) or "-"),
            ("Catatan", " | ".join(info.notes) if info.notes else "-"),
        ]
        printable = [(label, value) for label, value in rows if value not in (None, "", "-")]
        for label, value in printable:
            print(f"{self._c(pad(label, 17), DIM)}: {value}")

        explorer = self._explorer(info)
        token_link = f"{explorer}/token/{info.token}" if explorer else ""
        pool_link = f"{explorer}/address/{info.pool}" if explorer else ""
        dex_link = f"https://dexscreener.com/{EXPLORER_CHAIN.get(info.chain, info.chain)}/{info.pool}"
        links = [link for link in (token_link, pool_link, dex_link) if link]
        if links:
            print(self._c("Link".ljust(17), DIM) + ": " + "  ".join(links))
        self.rule("═")

    def _explorer(self, info):
        from .chains import CHAINS
        chain = CHAINS.get(info.chain)
        return chain.explorer if chain else ""

    @staticmethod
    def _value(text, addr=False):
        return text or "-"

    @staticmethod
    def _price(info):
        parts = []
        if info.price_native and info.quote_symbol:
            parts.append(f"{fmt_token_amount(info.price_native)} {info.quote_symbol}")
        if info.price_usd:
            parts.append(fmt_price(info.price_usd))
        return "  ≈  ".join(parts) if parts else "-"

    @staticmethod
    def _liquidity(info):
        parts = []
        if info.liquidity_usd is not None:
            parts.append(f"{fmt_usd(info.liquidity_usd)} (2 sisi)")
        if info.liquidity_native:
            native = {"bsc": "BNB", "ethereum": "ETH", "base": "ETH", "arbitrum": "ETH"}.get(info.chain, "")
            tag = " (perkiraan)" if info.kind == "v3" else ""
            parts.append(f"{fmt_token_amount(info.liquidity_native)} {native}{tag}".rstrip())
        return " · ".join(parts) if parts else "-"

    @staticmethod
    def _holders(info):
        if info.holders is None:
            return "-"
        text = f"{info.holders:,}".replace(",", ".")
        if info.top10_pct is not None:
            text += f"  (top10 {info.top10_pct:.1f}%)"
        return text

    def _bool(self, value, dangerous=False):
        if value is None:
            return "-"
        if value and dangerous:
            return self._c("YA (hati-hati)", RED, BOLD)
        return self._c("ya", GREEN) if value else self._c("tidak", YELLOW)

    # ------------------------------------------------------------- ringkasan --

    def summary(self, infos: list, chain: str, db_path: str):
        with_liq = [i for i in infos if (i.liquidity_usd or 0) > 0]
        print(
            self._c(
                f"  {len(infos)} pool baru · {len(with_liq)} punya likuiditas · "
                f"chain {chain} · db {db_path}",
                DIM,
            )
        )


def _tax(info: PoolInfo) -> str:
    if info.buy_tax is None and info.sell_tax is None:
        return "-"
    buy = f"{info.buy_tax:.1f}".rstrip("0").rstrip(".") if info.buy_tax is not None else "?"
    sell = f"{info.sell_tax:.1f}".rstrip("0").rstrip(".") if info.sell_tax is not None else "?"
    return f"{buy}/{sell}%"


def _flags(info: PoolInfo) -> str:
    flags = []
    if (info.reserve_quote or 0) == 0 and (info.reserve_token or 0) == 0:
        flags.append("belum ada likuiditas")
    if info.honeypot:
        flags.append("HONEYPOT")
    if (info.buy_tax or 0) >= 10 or (info.sell_tax or 0) >= 10:
        flags.append("pajak tinggi")
    if info.verified is False:
        flags.append("belum verify")
    if (info.lp_burned_pct is not None and info.lp_burned_pct < 50
            and (info.reserve_quote or 0) > 0):
        flags.append("LP belum burn")
    if info.notes:
        flags.extend(info.notes)
    return " · ".join(flags)


# --------------------------------------------------------------- export ------

CSV_FIELDS = [
    "created_ts", "chain", "dex", "kind", "name", "symbol", "token", "pool",
    "quote", "quote_symbol", "decimals", "total_supply", "price_native",
    "price_usd", "reserve_token", "reserve_quote", "liquidity_native",
    "liquidity_usd", "fdv_usd", "mcap_usd", "lp_supply", "lp_burned_pct",
    "fee_tier", "holders", "top10_pct", "buy_tax", "sell_tax", "honeypot",
    "verified", "owner", "creator", "created_block", "created_tx",
]


def to_json(info: PoolInfo) -> str:
    return json.dumps(info.to_dict(), ensure_ascii=False)


def to_csv_rows(infos: list) -> str:
    """CSV tanpa baris header (untuk mode streaming)."""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=CSV_FIELDS, extrasaction="ignore")
    for info in infos:
        writer.writerow(info.to_dict())
    return buf.getvalue()


def to_csv(infos: list) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=CSV_FIELDS, extrasaction="ignore")
    writer.writeheader()
    for info in infos:
        writer.writerow(info.to_dict())
    return buf.getvalue()
