#!/usr/bin/env bash
# Local runner for Linauto — runs the scheduler on your laptop using your
# local IP (no proxy). Syncs DB from/to the server before and after.
#
# Usage:
#   releasi local-login --account "Nicolas Goehler"   # First time or after cookie expires
#   ./scripts/local_run.sh start                       # Sync DB from server, start scheduler
#   ./scripts/local_run.sh stop                        # Stop scheduler, sync DB back to server
#   ./scripts/local_run.sh sync                        # Just sync DB back to server (if you ctrl-C'd)
#
# NOTE: Run local-login BEFORE start. The start script preserves your local
# cookie even after syncing the DB from the server.

set -euo pipefail
cd "$(dirname "$0")/.."

SERVER="root@${RELEASI_SERVER:?Set RELEASI_SERVER to your server hostname/IP}"
REMOTE_DB="/root/linauto/data/releasi.db"
LOCAL_DB="data/releasi.db"
REMOTE_BROWSER_DATA="/root/linauto/data/browser_data"
LOCAL_BROWSER_DATA="data/browser_data"
PID_FILE=".local_run.pid"
CONTAINER="releasi"
ACCOUNT_ID="${RELEASI_ACCOUNT_ID:?Set RELEASI_ACCOUNT_ID to your account UUID}"
PROXY_COUNTRY="ca"  # Restore this after local run

sync_from_server() {
    echo "==> Stopping server container..."
    ssh "$SERVER" "docker stop $CONTAINER 2>/dev/null || true; docker rm $CONTAINER 2>/dev/null || true"

    # Preserve local cookie before overwriting with server DB (e.g. after local-login)
    LOCAL_COOKIE_FILE=$(mktemp)
    python3 -c "
import sqlite3, sys
try:
    c = sqlite3.connect('$LOCAL_DB')
    row = c.execute(\"SELECT li_at_cookie, status FROM accounts WHERE id='$ACCOUNT_ID'\").fetchone()
    print(row[0] if row and row[0] else '', end='')
except: print('', end='')
" > "$LOCAL_COOKIE_FILE" 2>/dev/null || true
    SAVED_COOKIE=$(cat "$LOCAL_COOKIE_FILE")
    rm -f "$LOCAL_COOKIE_FILE"

    echo "==> Copying DB from server..."
    mkdir -p data
    scp "$SERVER:$REMOTE_DB" "$LOCAL_DB"

    # Restore local cookie if local-login was run (i.e. local cookie differs from server's)
    if [ -n "$SAVED_COOKIE" ]; then
        python3 -c "
import sqlite3
c = sqlite3.connect('$LOCAL_DB')
c.execute(\"UPDATE accounts SET li_at_cookie=?, status='ACTIVE' WHERE id='$ACCOUNT_ID'\", ('$SAVED_COOKIE',))
c.commit()
c.close()
print('  Local cookie preserved (from local-login)')
"
    fi

    # Only sync browser profile from server if no local profile exists yet.
    # If local-login already created a profile, keep it — don't overwrite.
    mkdir -p "$LOCAL_BROWSER_DATA"
    if [ ! -d "$LOCAL_BROWSER_DATA/$ACCOUNT_ID" ] || [ -z "$(ls -A "$LOCAL_BROWSER_DATA/$ACCOUNT_ID" 2>/dev/null)" ]; then
        echo "==> Syncing browser profile from server..."
        rsync -az "$SERVER:$REMOTE_BROWSER_DATA/$ACCOUNT_ID/" \
            "$LOCAL_BROWSER_DATA/$ACCOUNT_ID/"
    else
        echo "==> Using existing local browser profile (from local-login)."
    fi

    echo "==> Clearing proxy_country in local DB (use local IP)..."
    python3 -c "
import sqlite3
c = sqlite3.connect('$LOCAL_DB')
c.execute(\"UPDATE accounts SET proxy_country = NULL WHERE id = '$ACCOUNT_ID'\")
c.commit()
c.close()
print('  proxy_country set to NULL for local run')
"
    echo "==> Ready."
}

sync_to_server() {
    echo "==> Restoring proxy_country in local DB before upload..."
    python3 -c "
import sqlite3
c = sqlite3.connect('$LOCAL_DB')
c.execute(\"UPDATE accounts SET proxy_country = '$PROXY_COUNTRY' WHERE id = '$ACCOUNT_ID'\")
c.commit()
c.close()
print('  proxy_country restored to $PROXY_COUNTRY')
"
    echo "==> Copying DB back to server..."
    scp "$LOCAL_DB" "$SERVER:$REMOTE_DB"

    echo "==> Syncing browser profile back to server..."
    rsync -az "$LOCAL_BROWSER_DATA/$ACCOUNT_ID/" \
        "$SERVER:$REMOTE_BROWSER_DATA/$ACCOUNT_ID/"

    echo "==> Starting server container..."
    # Use docker run if container was removed, docker start if it still exists
    ssh "$SERVER" "docker start $CONTAINER 2>/dev/null || \
        (cd /root/releasi && docker run -d --name $CONTAINER --restart unless-stopped \
        -v /root/releasi/data:/app/data \
        -v /root/releasi/config/settings.yaml:/app/config/settings.yaml:ro \
        -v /root/releasi/src:/app/src \
        -p 8000:8000 -p 6080:6080 \
        -e RELEASI_API_ENABLED=true \
        -e RELEASI_API_KEY="${RELEASI_API_KEY:?Set RELEASI_API_KEY}" \
        -e 'RELEASI_CORS_ORIGINS=[\"*\"]' \
        -e RELEASI_LOG_LEVEL=INFO -e TZ=Europe/Berlin \
        --memory=3g --cpus=1.5 \
        releasi_releasi:latest)"
    echo "==> Done. Server is running with updated DB."
}

start_local() {
    if [ -f "$PID_FILE" ]; then
        echo "Already running (PID $(cat $PID_FILE)). Run '$0 stop' first."
        exit 1
    fi

    sync_from_server

    echo "==> Starting local scheduler..."
    echo "    Logs: data/logs/releasi.log"
    echo "    Press Ctrl-C or run '$0 stop' to stop."
    echo ""

    # Activate venv if present
    if [ -f ".venv/bin/activate" ]; then
        source .venv/bin/activate
    fi

    # Run in foreground so Ctrl-C works
    releasi run
}

stop_local() {
    if [ -f "$PID_FILE" ]; then
        kill "$(cat $PID_FILE)" 2>/dev/null || true
        rm -f "$PID_FILE"
    fi
    sync_to_server
}

case "${1:-help}" in
    start) start_local ;;
    stop)  stop_local ;;
    sync)  sync_to_server ;;
    *)
        echo "Usage: $0 {start|stop|sync}"
        echo "  start  — sync DB from server, start local scheduler"
        echo "  stop   — stop local scheduler, sync DB back to server"
        echo "  sync   — just sync DB back to server"
        exit 1
        ;;
esac
