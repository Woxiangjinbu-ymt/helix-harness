#!/bin/sh
set -eu
cd "$(dirname "$0")/frontend"
exec npm run dev -- --host 127.0.0.1 --port "${RSI_UI_PORT:-5173}"
