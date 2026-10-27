#!/usr/bin/env bash
# Wrapper: jalankan dexwatch dari folder mana pun.
set -e
cd "$(dirname "$0")"
exec python3 -u -m dexwatch "$@"
