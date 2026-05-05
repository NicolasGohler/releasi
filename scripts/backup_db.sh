#!/bin/bash
# Backup linauto SQLite database using the online backup API (safe against live writes).
# Keeps 7 daily snapshots in /root/linauto/data/backups/.
# Add an rclone/scp line at the bottom to push offsite.

set -e

BACKUP_DIR=/root/linauto/data/backups
DATE=$(date +%Y%m%d_%H%M%S)
DEST="$BACKUP_DIR/linauto_${DATE}.db"

mkdir -p "$BACKUP_DIR"

# sqlite3.backup() is WAL-safe — no need to stop the container
docker exec linauto python3 - << PYEOF
import sqlite3
src = sqlite3.connect('/app/data/linauto.db')
dst = sqlite3.connect('/app/data/backups/linauto_${DATE}.db')
src.backup(dst)
dst.close()
src.close()
print("backup ok: /app/data/backups/linauto_${DATE}.db")
PYEOF

# Prune: keep only the 7 most recent backups
ls -t "$BACKUP_DIR"/linauto_*.db 2>/dev/null | tail -n +8 | xargs -r rm --

echo "$(date '+%Y-%m-%d %H:%M:%S') backup complete: $DEST"

# --- Offsite push (uncomment and configure one) ---
# rclone copy "$DEST" remote:linauto-backups/
# scp "$DEST" user@backup-server:/backups/linauto/
