#!/usr/bin/env bash
# Jalankan dashboard dexwatch di GitHub Codespaces.
# Port 8787 di-bind ke 0.0.0.0 supaya bisa diteruskan Codespaces.
set -euo pipefail
cd "$(dirname "$0")"
PORT="${PORT:-8787}"
exec python3 -u -m dexwatch serve --scan --host 0.0.0.0 --port "$PORT" "$@"
