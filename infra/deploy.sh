#!/bin/bash
# deploy.sh -- Applique la derniere version de launchgateway + service sur le VPS
set -e

INFRA="$(cd "$(dirname "$0")" && pwd)"

echo "[deploy] Copie launchgateway.sh -> /opt/ibc/"
cp "${INFRA}/launchgateway.sh" /opt/ibc/launchgateway.sh
chmod +x /opt/ibc/launchgateway.sh

echo "[deploy] Copie ib-gateway.service -> /etc/systemd/system/"
cp "${INFRA}/ib-gateway.service" /etc/systemd/system/ib-gateway.service

echo "[deploy] daemon-reload"
systemctl daemon-reload

echo "[deploy] (Re)start ib-gateway"
systemctl stop ib-gateway 2>/dev/null || true
sleep 2
systemctl start ib-gateway

echo "[deploy] Attente 25s..."
sleep 25

echo "=== systemctl status ==="
systemctl status ib-gateway --no-pager || true

echo "=== gateway_stdout.log (40 dernieres lignes) ==="
tail -40 /opt/ibc/gateway_stdout.log 2>/dev/null || echo "(vide)"

echo "=== gateway_stderr.log (20 dernieres lignes) ==="
tail -20 /opt/ibc/gateway_stderr.log 2>/dev/null || echo "(vide)"
