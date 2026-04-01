#!/bin/bash
# deploy.sh -- Applique la derniere version de launchgateway + service sur le VPS
set -e

INFRA="$(cd "$(dirname "$0")" && pwd)"

# --- Dependances : Java complet (pas headless) + libs X11 AWT ---
# openjdk-17-jre-headless n'inclut pas libawt_xawt.so (AWT natif)
# IB Gateway a besoin du JRE complet pour java.awt.Toolkit
echo "[deploy] Installation dependances Java + X11..."
apt-get install -y --no-install-recommends \
    openjdk-17-jre \
    libx11-6 libxext6 libxi6 libxrender1 libxtst6 \
    xvfb x11-utils 2>&1 | tail -5

echo "[deploy] Copie launchgateway.sh -> /opt/ibc/"
cp "${INFRA}/launchgateway.sh" /opt/ibc/launchgateway.sh
chmod +x /opt/ibc/launchgateway.sh

echo "[deploy] Copie ib-gateway.service -> /etc/systemd/system/"
cp "${INFRA}/ib-gateway.service" /etc/systemd/system/ib-gateway.service

echo "[deploy] daemon-reload"
systemctl daemon-reload

echo "[deploy] (Re)start ib-gateway"
systemctl stop ib-gateway 2>/dev/null || true
pkill Xvfb 2>/dev/null || true
rm -f /tmp/.X99-lock 2>/dev/null || true
sleep 2
systemctl start ib-gateway

echo "[deploy] Attente 30s (demarrage IBC + login)..."
sleep 30

echo "=== systemctl status ==="
systemctl status ib-gateway --no-pager || true

echo "=== gateway_stdout.log (50 dernieres lignes) ==="
tail -50 /opt/ibc/gateway_stdout.log 2>/dev/null || echo "(vide)"

echo "=== gateway_stderr.log (20 dernieres lignes) ==="
tail -20 /opt/ibc/gateway_stderr.log 2>/dev/null || echo "(vide)"
