"""Entry point CLI dexwatch."""

from __future__ import annotations

import argparse
import logging
import sys
import time

from . import abi, pools
from .chains import CHAINS, Dex, get_chain
from .enrich import DEXSCREENER_CHAIN, Enricher
from .pools import NewPool, PoolInfo
from .render import BOLD, DIM, GREEN, RED, Console, fmt_usd, to_csv, to_csv_rows, to_json
from .rpc import Rpc
from .scanner import Scanner
from .store import Store

log = logging.getLogger("dexwatch")

DEFAULT_DB = "dexwatch.db"


def _add_common(parser: argparse.ArgumentParser):
    parser.add_argument("--chain", default="bsc", help="chain: bsc, ethereum, base, arbitrum (default: bsc)")
    parser.add_argument("--dex", default="", help="daftar dex dipisah koma, mis. pancakeswap-v2,pancakeswap-v3")
    parser.add_argument("--rpc", action="append", default=[], help="URL RPC kustom (boleh berulang)")
    parser.add_argument("--db", default=DEFAULT_DB, help=f"path SQLite (default: {DEFAULT_DB})")
    parser.add_argument("--offline", action="store_true", help="hanya data on-chain, tanpa DexScreener/GoPlus")
    parser.add_argument("--no-security", action="store_true", help="matikan pemeriksaan GoPlus")
    parser.add_argument("--workers", type=int, default=4, help="paralelisme pengayaan (default 4)")
    parser.add_argument("--timeout", type=float, default=25.0, help="timeout RPC detik (default 25)")
    parser.add_argument("--width", type=int, default=0, help="lebar tabel (default: lebar terminal)")
    parser.add_argument("--no-color", action="store_true", help="matikan warna ANSI")
    parser.add_argument("-v", "--verbose", action="store_true", help="log debug")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dexwatch",
        description="Pantau koin/pool baru yang didaftarkan di DEX, lengkap dengan detail on-chain.",
    )
    sub = parser.add_subparsers(dest="command")

    p_watch = sub.add_parser("watch", help="pantau pool baru secara real-time")
    _add_common(p_watch)
    p_watch.add_argument("--interval", type=float, default=5.0, help="interval polling detik (default 5)")
    p_watch.add_argument("--min-liq", type=float, default=0.0, help="hanya tampilkan pool dengan likuiditas USD ≥ N (default 0 = semua)")
    p_watch.add_argument("--json", action="store_true", help="keluarkan JSON per baris")
    p_watch.add_argument("--csv", action="store_true", help="keluarkan CSV")
    p_watch.add_argument("--max-catchup", type=int, default=400, help="maksimal blok yang dikejar sekali jalan")
    p_watch.add_argument("--recheck", type=float, default=45.0,
                         help="cek ulang pool tanpa likuiditas setelah N detik (0 = mati)")

    p_new = sub.add_parser("new", help="lihat pool baru dalam N block terakhir (sekali jalan)")
    _add_common(p_new)
    p_new.add_argument("--blocks", type=int, default=300, help="jumlah block ke belakang (default 300)")
    p_new.add_argument("--min-liq", type=float, default=0.0, help="hanya tampilkan pool dengan likuiditas USD ≥ N (default 0 = semua)")
    p_new.add_argument("--json", action="store_true", help="keluarkan JSON per baris")
    p_new.add_argument("--csv", action="store_true", help="keluarkan CSV")
    p_new.add_argument("--sort", choices=["time", "liq"], default="time", help="urutan hasil (default time)")

    p_detail = sub.add_parser("detail", help="detail satu token / pool / pair")
    _add_common(p_detail)
    p_detail.add_argument("address", help="alamat token atau alamat pool")

    p_doctor = sub.add_parser("doctor", help="cek konfigurasi RPC/DEX/API")
    _add_common(p_doctor)

    p_export = sub.add_parser("export", help="ekspor isi database")
    _add_common(p_export)
    p_export.add_argument("--limit", type=int, default=200)
    p_export.add_argument("--all-chains", action="store_true", help="jangan filter chain")
    p_export.add_argument("--min-liq", type=float, default=0.0)
    p_export.add_argument("--json", action="store_true")
    p_export.add_argument("--csv", action="store_true")

    p_serve = sub.add_parser("serve", help="dashboard web lokal dari database")
    _add_common(p_serve)
    p_serve.add_argument("--port", type=int, default=8787)
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--scan", action="store_true", help="sekaligus pindai pool baru di background")
    p_serve.add_argument("--interval", type=float, default=5.0)
    p_serve.add_argument("--refresh", type=float, default=25.0,
                         help="interval segarkan data pasar (detik, default 25)")
    p_serve.add_argument("--no-sampler", action="store_true",
                         help="matikan sampler histori harga (sparkline)")
    return parser


# ------------------------------------------------------------------ helpers --

def _dex_list(chain, raw: str):
    if not raw:
        return list(chain.dexes)
    keys = [k.strip() for k in raw.split(",") if k.strip()]
    return [chain.dex(key) for key in keys]


def _console(args) -> Console:
    return Console(color=not args.no_color, selected_width=args.width or None)


def _filter(infos, min_liq: float):
    """min_liq > 0 menyembunyikan pool yang likuiditas USD-nya belum diketahui."""
    if not min_liq:
        return infos
    return [i for i in infos if (i.liquidity_usd or 0) >= min_liq]


def _print_rows(console: Console, infos, args):
    if args.json:
        for info in infos:
            print(to_json(info))
        return
    if args.csv:
        print(to_csv(infos), end="")
        return
    console.table(infos)


# -------------------------------------------------------------------- detail --

def _kind_from_chain(chain, factory: str, reserves: bool, slot0: bool) -> tuple:
    factory = abi.norm_address(factory) if factory else ""
    for dex in chain.dexes:
        if dex.factory.lower() == factory:
            return dex, dex.kind
    kind = "v3" if slot0 and not reserves else "v2"
    return Dex("custom", "DEX lain", kind, factory), kind


def detect_pool(chain, rpc, address: str) -> NewPool | None:
    """Kenali sebuah alamat: pool? kalau ya kembalikan NewPool."""
    address = abi.norm_address(address)
    try:
        code = rpc.get_code(address)
    except Exception:  # noqa: BLE001
        return None
    if not code or code == "0x":
        return None
    results = rpc.batch([
        ("eth_call", [{"to": address, "data": abi.SEL_TOKEN0}, "latest"]),
        ("eth_call", [{"to": address, "data": abi.SEL_TOKEN1}, "latest"]),
        ("eth_call", [{"to": address, "data": abi.SEL_FACTORY}, "latest"]),
        ("eth_call", [{"to": address, "data": abi.SEL_GET_RESERVES}, "latest"]),
        ("eth_call", [{"to": address, "data": abi.SEL_SLOT0}, "latest"]),
        ("eth_call", [{"to": address, "data": abi.SEL_LIQUIDITY}, "latest"]),
    ])
    token0 = token1 = factory = None
    reserves = slot0 = liquidity = None
    if not isinstance(results[0], Exception) and results[0] not in (None, "0x"):
        token0 = abi.decode_address(results[0])
    if not isinstance(results[1], Exception) and results[1] not in (None, "0x"):
        token1 = abi.decode_address(results[1])
    if not isinstance(results[2], Exception) and results[2] not in (None, "0x"):
        factory = abi.decode_address(results[2])
    if not isinstance(results[3], Exception) and results[3] not in (None, "0x"):
        reserves = results[3]
    if not isinstance(results[4], Exception) and results[4] not in (None, "0x"):
        slot0 = results[4]
    if not isinstance(results[5], Exception) and results[5] not in (None, "0x"):
        liquidity = results[5]

    if not token0 or not token1:
        return None

    dex, kind = _kind_from_chain(chain, factory, reserves is not None, slot0 is not None)
    fee_tier = 0.0
    if kind == "v3":
        try:
            fee_raw = rpc.eth_call(address, abi.SEL_FEE)
            if fee_raw and fee_raw != "0x":
                fee_tier = abi.decode_uint(fee_raw) / 1_000_000 * 100
        except Exception:  # noqa: BLE001
            pass
    return NewPool(
        chain=chain.key,
        dex=dex.key,
        dex_name=dex.name,
        kind=kind,
        pool=address,
        token0=token0,
        token1=token1,
        fee_tier=fee_tier,
    )


def _best_pair(chain, enricher: Enricher, token: str):
    pairs = enricher.dexscreener_token(token)
    best = None
    for pair in pairs:
        try:
            liq = float((pair.get("liquidity") or {}).get("usd") or 0)
        except (TypeError, ValueError):
            liq = 0
        if best is None or liq > best[0]:
            best = (liq, pair)
    return best[1] if best else None


def cmd_detail(args) -> int:
    console = _console(args)
    chain = get_chain(args.chain)
    rpc = Rpc(args.rpc or chain.rpc, timeout=args.timeout)
    enricher = Enricher(chain, rpc, security=not args.no_security, offline=args.offline)
    store = Store(args.db)
    value = args.address.strip()

    if not abi.is_address(value):
        print(f"Bukan alamat valid: {value}")
        return 2

    info = None
    other_pairs = []

    # 1) sudah ada di database -> segarkan
    stored = store.find(chain.key, value)
    if stored:
        new_pool = NewPool(
            chain=chain.key, dex=stored.dex, dex_name=stored.dex_name, kind=stored.kind,
            pool=stored.pool, token0=stored.token0, token1=stored.token1,
            fee_tier=stored.fee_tier or 0, block=stored.created_block,
            tx=stored.created_tx, ts=stored.created_ts,
        )
        try:
            info = enricher.enrich(new_pool)
        except Exception as exc:  # noqa: BLE001
            log.warning("gagal menyegarkan, pakai data tersimpan: %s", exc)
            info = stored
        store.save(info)

    # 2) alamat pool
    if info is None:
        new_pool = detect_pool(chain, rpc, value)
        if new_pool:
            info = enricher.enrich(new_pool)

    # 3) alamat token -> cari pool terbaik
    if info is None:
        pair = _best_pair(chain, enricher, value)
        if pair:
            other_pairs = enricher.dexscreener_token(value)
            new_pool = detect_pool(chain, rpc, pair.get("pairAddress", ""))
            if new_pool:
                info = enricher.enrich(new_pool)
            else:
                info = PoolInfo(
                    chain=chain.key, chain_name=chain.name,
                    dex=pair.get("dexId", "?"), dex_name=pair.get("dexId", "?").title(),
                    kind="v3" if "v3" in (pair.get("labels") or []) else "v2",
                    pool=pair.get("pairAddress", ""),
                    token=value,
                    token0=(pair.get("baseToken") or {}).get("address", ""),
                    token1=(pair.get("quoteToken") or {}).get("address", ""),
                    symbol=(pair.get("baseToken") or {}).get("symbol", ""),
                    name=(pair.get("baseToken") or {}).get("name", ""),
                    price_usd=float(pair.get("priceUsd") or 0) or None,
                    liquidity_usd=float((pair.get("liquidity") or {}).get("usd") or 0) or None,
                    fdv_usd=float(pair.get("fdv") or 0) or None,
                    mcap_usd=float(pair.get("marketCap") or 0) or None,
                    sources=["dexscreener"],
                )
            store.save(info)

    # 4) on-chain saja (belum ada pool terdeteksi)
    if info is None:
        try:
            code = rpc.get_code(value)
        except Exception:  # noqa: BLE001
            code = "0x"
        if not code or code == "0x":
            print(f"Alamat {value} bukan kontrak di {chain.name}.")
            store.close()
            return 1
        meta = enricher.token_meta(value)
        if not meta or (not meta.get("symbol") and not meta.get("name")):
            print(f"Tidak menemukan token/pool untuk {value} di {chain.name}.")
            store.close()
            return 1
        info = PoolInfo(
            chain=chain.key, chain_name=chain.name, dex="-", dex_name="-", kind="-",
            pool="", token=value,
            name=meta["name"], symbol=meta["symbol"], decimals=meta["decimals"],
            total_supply=(meta["total_supply_raw"] / 10 ** meta["decimals"]) if meta["total_supply_raw"] else 0,
            sources=["onchain"],
            notes=["tidak ada pool yang terdeteksi"],
        )

    console.detail(info)

    if other_pairs:
        console.title(f"Pool lain untuk {info.symbol or 'token ini'}")
        for pair in other_pairs[:12]:
            try:
                liq = float((pair.get("liquidity") or {}).get("usd") or 0)
                price = float(pair.get("priceUsd") or 0)
            except (TypeError, ValueError):
                liq, price = 0, 0
            print(
                f"  {pair.get('dexId', '?'):<16} {pair.get('pairAddress', ''):<44} "
                f"{fmt_usd(liq):>10}  {('$' + format(price, '.8f')).rstrip('0'):>14}  "
                f"{'/'.join(pair.get('labels') or [])}"
            )
    if not args.offline:
        dex_link = f"https://dexscreener.com/{DEXSCREENER_CHAIN.get(chain.key, chain.key)}/{info.pool}"
        print(console.c(f"  {dex_link}", DIM))
    store.close()
    return 0


# ----------------------------------------------------------------- commands --

def cmd_watch(args) -> int:
    console = _console(args)
    chain = get_chain(args.chain)
    dexs = _dex_list(chain, args.dex)
    rpc = Rpc(args.rpc or chain.rpc, timeout=args.timeout)
    enricher = Enricher(chain, rpc, security=not args.no_security and not args.offline, offline=args.offline)
    store = Store(args.db)
    scanner = Scanner(chain, rpc, enricher, store, dexs=dexs, workers=args.workers)

    if not (args.json or args.csv):
        console.title(f"DEXWATCH · {chain.name}")
        mode = "on-chain saja" if args.offline else "+ DexScreener/GoPlus"
        print(console.c(f"  DEX   : {', '.join(d.name for d in dexs)}", DIM))
        print(console.c(f"  Mode  : pantau event pembuatan pool tiap {args.interval:g}s ({mode})", DIM))
        print(console.c("  Ctrl+C untuk berhenti", DIM))

    csv_header_done = False
    if args.csv:
        sys.stdout.write(to_csv([]))
        csv_header_done = True
    try:
        for batch in scanner.watch(interval=args.interval, max_catchup=args.max_catchup,
                                   recheck=args.recheck):
            infos = _filter(batch.get("new") or [], args.min_liq)
            updates = _filter(batch.get("updated") or [], args.min_liq)
            if updates and not (args.json or args.csv):
                print(console.c(f"\n[{time.strftime('%H:%M:%S')}] {len(updates)} pembaruan likuiditas", BOLD))
                console.table(updates)
            if args.json:
                for info in updates:
                    print(to_json(info))
            elif args.csv and updates:
                sys.stdout.write(to_csv_rows(updates))
            if not infos:
                continue
            if not (args.json or args.csv):
                stamp = time.strftime("%H:%M:%S")
                print(console.c(f"\n[{stamp}] {len(infos)} pool baru", BOLD))
            if args.csv:
                sys.stdout.write(to_csv_rows(infos))
            elif args.json:
                for info in infos:
                    print(to_json(info))
            else:
                console.table(infos)
            sys.stdout.flush()
    except KeyboardInterrupt:
        print("\nberhenti.")
    finally:
        store.close()
    return 0


def cmd_new(args) -> int:
    console = _console(args)
    chain = get_chain(args.chain)
    dexs = _dex_list(chain, args.dex)
    rpc = Rpc(args.rpc or chain.rpc, timeout=args.timeout)
    enricher = Enricher(chain, rpc, security=not args.no_security and not args.offline, offline=args.offline)
    store = Store(args.db)
    scanner = Scanner(chain, rpc, enricher, store, dexs=dexs, workers=args.workers)

    head = scanner.head()
    start = max(1, head - args.blocks)
    message = f"memindai block {start}..{head} ({args.blocks} block) di {chain.name}…"
    print(console.c(message, DIM), file=sys.stderr if (args.json or args.csv) else sys.stdout)
    infos = scanner.scan(start, head)
    infos = _filter(infos, args.min_liq)
    if args.sort == "liq":
        infos.sort(key=lambda i: i.liquidity_usd or 0, reverse=True)
    else:
        infos.sort(key=lambda i: (i.created_ts, i.created_block), reverse=True)

    if not (args.json or args.csv):
        console.title(f"POOL BARU · {chain.name}")
    if not infos and not (args.json or args.csv) and args.min_liq:
        console.table([], empty_text=f"(tidak ada pool dengan likuiditas ≥ ${args.min_liq:,.0f})")
    else:
        _print_rows(console, infos, args)
    if not (args.json or args.csv):
        console.summary(infos, chain.name, args.db)
    store.close()
    return 0


def cmd_export(args) -> int:
    console = _console(args)
    store = Store(args.db)
    infos = store.recent(
        limit=args.limit,
        chain=None if args.all_chains else args.chain,
        min_liq=args.min_liq,
    )
    if not (args.json or args.csv):
        console.title(f"DATABASE · {args.db} · {len(infos)} baris")
    _print_rows(console, infos, args)
    store.close()
    return 0


def cmd_doctor(args) -> int:
    console = _console(args)
    chain = get_chain(args.chain)
    rpc = Rpc(args.rpc or chain.rpc, timeout=args.timeout)
    enricher = Enricher(chain, rpc, security=not args.no_security, offline=args.offline)

    checks = []

    def add(name, ok, detail=""):
        checks.append((name, ok, detail))

    try:
        head = rpc.block_number()
        add("RPC block number", True, f"head={head} ({time.strftime('%H:%M:%S')})")
    except Exception as exc:  # noqa: BLE001
        add("RPC block number", False, str(exc)[:80])
        head = 0

    for dex in chain.dexes:
        try:
            code = rpc.get_code(dex.factory)
            ok = bool(code and code != "0x")
            add(f"Factory {dex.key}", ok, "kontrak ada" if ok else "alamat factory kosong/salah")
        except Exception as exc:  # noqa: BLE001
            add(f"Factory {dex.key}", False, str(exc)[:80])
            continue
        if head and ok:
            topic = pools.DECODERS[dex.kind][0]
            window = 300 if chain.block_time < 5 else 40
            try:
                logs = rpc.get_logs(dex.factory, [topic], head - window, head, chunk=window // 5 or 25)
                add(f"Event {dex.key}", True, f"{len(logs)} pool baru / {window} block")
            except Exception as exc:  # noqa: BLE001
                add(f"Event {dex.key}", False, f"getLogs gagal: {str(exc)[:60]}")

    if not args.offline:
        try:
            price = enricher.native_usd()
            add("Harga native (USD)", bool(price), f"1 {chain.native} = ${price:,.2f}" if price else "tidak terbaca")
        except Exception as exc:  # noqa: BLE001
            add("Harga native (USD)", False, str(exc)[:60])
        try:
            pairs = enricher.dexscreener_token(chain.wrapped, ttl=5)
            add("DexScreener API", bool(pairs), f"{len(pairs)} pair untuk {chain.wrapped[:10]}…")
        except Exception as exc:  # noqa: BLE001
            add("DexScreener API", False, str(exc)[:60])
        try:
            data = enricher.goplus(chain.wrapped)
            add("GoPlus API", bool(data), "respons diterima" if data else "kosong")
        except Exception as exc:  # noqa: BLE001
            add("GoPlus API", False, str(exc)[:60])

    console.title(f"DOCTOR · {chain.name}")
    for name, ok, detail in checks:
        mark = "✓" if ok else "✗"
        color = GREEN if ok else RED
        text = f"  {mark} {name:<28} {detail}"
        print(text if not console.color else f"\033[{color}m{text}\033[0m")
    failed = [c for c in checks if not c[1]]
    print()
    print("semua siap." if not failed else f"{len(failed)} pemeriksaan gagal.")
    return 0 if not failed else 1


def cmd_serve(args) -> int:
    from .web import serve

    return serve(args)


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 0
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    if args.chain not in CHAINS:
        print(f"chain '{args.chain}' tidak dikenal. Pilihan: {', '.join(CHAINS)}")
        return 2
    handlers = {
        "watch": cmd_watch,
        "new": cmd_new,
        "detail": cmd_detail,
        "doctor": cmd_doctor,
        "export": cmd_export,
        "serve": cmd_serve,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
