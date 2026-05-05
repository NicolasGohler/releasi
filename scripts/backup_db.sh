#!/bin/bash
set -e

HOST_BACKUP_DIR=/root/linauto/data/backups
DATE=$(date +%Y%m%d_%H%M%S)
HOST_DEST="$HOST_BACKUP_DIR/linauto_${DATE}.db"
CTR_DEST="/app/data/backups/linauto_${DATE}.db"

mkdir -p "$HOST_BACKUP_DIR"

# Python runs as root inside the container; /app/data is volume-mounted from /root/linauto/data
docker exec -u root linauto python3 -c "
import sqlite3
src = sqlite3.connect('/app/data/linauto.db')
dst = sqlite3.connect('$CTR_DEST')
src.backup(dst)
dst.close()
src.close()
"

# Prune: keep only the 7 most recent backups
ls -t "$HOST_BACKUP_DIR"/linauto_*.db 2>/dev/null | tail -n +8 | xargs -r rm --

echo "$(date '+%Y-%m-%d %H:%M:%S') backup complete: $HOST_DEST"

# Offsite push to Google Drive — used by weekly Saturday cron
if [ "${1}" = "--offsite" ]; then
    FILENAME=$(basename "$HOST_DEST")
    rclone copyto "$HOST_DEST" "gdrive:linauto-backups/$FILENAME"
    echo "$(date '+%Y-%m-%d %H:%M:%S') offsite upload complete: gdrive:linauto-backups/$FILENAME"
fi
