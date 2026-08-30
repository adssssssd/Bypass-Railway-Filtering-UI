#!/usr/bin/env bash
# ============================================================
#  Xray VLESS+WS entrypoint — same as the proven manual setup
#  - generates UUID + WS_PATH at boot (random)
#  - renders xray config from template
#  - starts xray on 127.0.0.1:3128 (internal only)
#  - starts router on 0.0.0.0:8080 (Railway public port)
# ============================================================
set -euo pipefail

# --- 1. credentials (env overrides, else random) ---
UUID="${UUID:-$(cat /proc/sys/kernel/random/uuid)}"
WS_PATH="${WS_PATH:-/vpn-$(head -c6 /dev/urandom | od -An -tx1 | tr -d ' \n')}"

# trim leading slash (config template stores bare path)
WS_PATH_BARE="${WS_PATH#/}"
echo "[entrypoint] UUID=$UUID"
echo "[entrypoint] WS_PATH=$WS_PATH"

# --- export so router.py (subprocess) sees the REAL credentials ---
export UUID
export WS_PATH

mkdir -p /app/xray
sed -e "s|REPLACE_UUID_HERE|$UUID|g" \
    -e "s|REPLACE_WS_PATH|$WS_PATH_BARE|g" \
    /app/config.template.json > /app/xray/config.json

# --- 2. start xray (internal only, never public) ---
/usr/local/bin/xray run -c /app/xray/config.json > /app/xray.log 2>&1 &
XRAY_PID=$!
sleep 2
if ! kill -0 "$XRAY_PID" 2>/dev/null; then
    echo "[entrypoint] FATAL: xray failed to start" >&2
    cat /app/xray.log >&2
    exit 1
fi
echo "[entrypoint] xray running (pid $XRAY_PID)"

# --- 3. router in foreground (Railway maps :8080) ---
exec python3 router.py