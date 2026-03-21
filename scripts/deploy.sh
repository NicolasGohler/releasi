#!/bin/bash
# Deploy code changes to the linauto server.
# Usage: ssh root@REDACTED 'bash -s' < scripts/deploy.sh
#    or: ssh root@REDACTED 'cd /root/linauto && bash scripts/deploy.sh'

set -e

cd /root/linauto
echo "==> Pulling latest code ..."
git pull

echo "==> Restarting container to reload Python modules ..."
docker restart linauto

echo "==> Waiting for container to be healthy ..."
sleep 5
docker logs linauto --since 5s 2>&1 | tail -5

echo "==> Deploy complete."
