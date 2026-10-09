#!/bin/bash
# Daily SQLite backup (WAL-safe via the online backup API), local pruning, and
# optional weekly offsite push to Google Drive (--offsite).
#
# Fails loudly: any error exits non-zero and writes a "BACKUP ERROR" line to
# the log, so a broken backup can never again look like a successful one.
set -euo pipefail

# Must be the host path of the volume mounted at /app/data in the container.
HOST_BACKUP_DIR=/root/linauto/data/backups
KEEP_LOCAL=7
OFFSITE_REMOTE="gdrive:releasi-backups"
OFFSITE_KEEP_DAYS=90

DATE=$(date +%Y%m%d_%H%M%S)
HOST_DEST="$HOST_BACKUP_DIR/releasi_${DATE}.db"
CTR_DEST="/app/data/backups/releasi_${DATE}.db"

trap 'echo "$(date "+%Y-%m-%d %H:%M:%S") BACKUP ERROR: failed at line $LINENO" >&2' ERR

mkdir -p "$HOST_BACKUP_DIR"

# Python runs as root inside the container; the backup lands in the mounted dir.
# The copy is integrity-checked before anything is pruned.
docker exec -u root releasi python3 -c "
import sqlite3
src = sqlite3.connect('/app/data/releasi.db')
dst = sqlite3.connect('$CTR_DEST')
src.backup(dst)
result = dst.execute('PRAGMA integrity_check').fetchone()[0]
dst.close()
src.close()
if result != 'ok':
    raise SystemExit('integrity_check failed: ' + result)
"

if [ ! -s "$HOST_DEST" ]; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') BACKUP ERROR: $HOST_DEST missing or empty" >&2
    exit 1
fi

# Prune only after a verified backup exists: keep the newest $KEEP_LOCAL.
ls -t "$HOST_BACKUP_DIR"/releasi_*.db | tail -n +$((KEEP_LOCAL + 1)) | xargs -r rm --

echo "$(date '+%Y-%m-%d %H:%M:%S') backup complete: $HOST_DEST"

# Offsite push — used by the weekly Saturday cron.
if [ "${1:-}" = "--offsite" ]; then
    FILENAME=$(basename "$HOST_DEST")
    rclone copyto "$HOST_DEST" "$OFFSITE_REMOTE/$FILENAME"
    echo "$(date '+%Y-%m-%d %H:%M:%S') offsite upload complete: $OFFSITE_REMOTE/$FILENAME"
    # Offsite retention: only touches files this script created in its own folder.
    rclone delete "$OFFSITE_REMOTE" --include 'releasi_*.db' --min-age "${OFFSITE_KEEP_DAYS}d"
fi
