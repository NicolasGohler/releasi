#!/bin/bash
# Deploy code changes to the releasi server.
# Usage: ssh root@REDACTED 'bash -s' < scripts/deploy.sh
#    or: ssh root@REDACTED 'cd /root/linauto && bash scripts/deploy.sh'

set -e

DB=/app/data/releasi.db
RESUME_FILE=/tmp/releasi_deploy_resume_ids

_db() { docker exec releasi python3 -c "import sqlite3; c=sqlite3.connect('$DB'); $1; c.commit(); c.close()"; }

cd /root/linauto
echo "==> Pulling latest code ..."
git pull

echo "==> Pausing active accounts before restart ..."
# Save active account IDs so we can restore exactly those after restart
_db "rows=c.execute(\"SELECT id FROM accounts WHERE status='active'\").fetchall(); print(' '.join(str(r[0]) for r in rows))" \
  > "$RESUME_FILE" 2>/dev/null || true
ACTIVE_IDS=$(cat "$RESUME_FILE" 2>/dev/null | tr -d '[:space:]')

if [ -n "$ACTIVE_IDS" ]; then
  _db "c.execute(\"UPDATE accounts SET status='paused' WHERE status='active'\")"
  COUNT=$(echo "$ACTIVE_IDS" | wc -w | tr -d ' ')
  echo "    Paused $COUNT active account(s) (IDs: $ACTIVE_IDS)."
else
  echo "    No active accounts to pause."
fi

echo "==> Restarting container to reload Python modules ..."
docker restart releasi

echo "==> Waiting for container to be healthy ..."
sleep 5
docker logs releasi --since 5s 2>&1 | tail -5

echo "==> Resuming accounts that were active before deploy ..."
if [ -n "$ACTIVE_IDS" ]; then
  IDS_CSV=$(echo "$ACTIVE_IDS" | tr ' ' ',')
  _db "c.execute(\"UPDATE accounts SET status='active' WHERE id IN ($IDS_CSV)\")"
  echo "    Resumed account IDs: $IDS_CSV"
else
  echo "    Nothing to resume."
fi

rm -f "$RESUME_FILE"
echo "==> Deploy complete."
