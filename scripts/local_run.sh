#!/usr/bin/env bash
# Local runner for Linauto — runs the scheduler on your laptop using your
# local IP (no proxy). Syncs DB from/to the server before and after.
#
# Usage:
#   ./scripts/local_run.sh start   # Sync DB from server, start scheduler
#   ./scripts/local_run.sh stop    # Stop scheduler, sync DB back to server
#   ./scripts/local_run.sh sync    # Just sync DB back to server (if you ctrl-C'd)

set -euo pipefail
cd "$(dirname "$0")/.."

SERVER="root@REDACTED"
REMOTE_DB="/root/linauto/data/linauto.db"
LOCAL_DB="data/linauto.db"
REMOTE_BROWSER_DATA="/root/linauto/data/browser_data"
LOCAL_BROWSER_DATA="data/browser_data"
PID_FILE=".local_run.pid"
CONTAINER="linauto"
ACCOUNT_ID="REDACTED"  # Nicolas Goehler
PROXY_COUNTRY="ca"  # Restore this after local run

sync_from_server() {
    echo "==> Stopping server container..."
    ssh "$SERVER" "docker stop $CONTAINER 2>/dev/null || true"

    echo "==> Copying DB from server..."
    mkdir -p data
    scp "$SERVER:$REMOTE_DB" "$LOCAL_DB"

    echo "==> Syncing browser profile (rsync)..."
    mkdir -p "$LOCAL_BROWSER_DATA"
    rsync -az "$SERVER:$REMOTE_BROWSER_DATA/$ACCOUNT_ID/" \
        "$LOCAL_BROWSER_DATA/$ACCOUNT_ID/"

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
    ssh "$SERVER" "docker start $CONTAINER"
    echo "==> Done. Server is running with updated DB."
}

start_local() {
    if [ -f "$PID_FILE" ]; then
        echo "Already running (PID $(cat $PID_FILE)). Run '$0 stop' first."
        exit 1
    fi

    sync_from_server

    echo "==> Starting local scheduler..."
    echo "    Logs: data/logs/linauto.log"
    echo "    Press Ctrl-C or run '$0 stop' to stop."
    echo ""

    # Activate venv if present
    if [ -f ".venv/bin/activate" ]; then
        source .venv/bin/activate
    fi

    # Run in foreground so Ctrl-C works
    linauto run
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
