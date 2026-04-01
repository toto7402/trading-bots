#!/bin/bash
# deploy.sh -- Applique launchgateway + service sur le VPS
# set -e retire pour continuer et afficher les logs meme si le service crash

INFRA="$(cd "$(dirname "$0")" && pwd)"

echo "[deploy] Installation xvfb + libs X11 + fonts + openjfx..."
apt-get install -y --no-install-recommends \
    xvfb x11-utils \
    libx11-6 libxext6 libxi6 libxrender1 libxtst6 \
    openjdk-17-jre \
    openjfx \
    fontconfig \
    fonts-dejavu-core \
    fonts-liberation 2>&1 | grep -E "^(Get:|Setting up|font|openjfx|Unpacking)" || true
fc-cache -f 2>/dev/null || true
echo "[deploy] Fonts: $(fc-list 2>/dev/null | wc -l)"
echo "[deploy] JavaFX: $(ls /usr/share/openjfx/lib/javafx*.jar 2>/dev/null | wc -l) jars dans /usr/share/openjfx/lib/"

echo "[deploy] Verification xvfb-run..."
command -v xvfb-run || { echo "ERREUR: xvfb-run introuvable"; exit 1; }

# libjvm.so est dans lib/server/ -- dlopen() ne le trouve pas sans ldconfig
# L'enregistrer dans le cache systeme resout UnsatisfiedLinkError: libawt_xawt.so
echo "[deploy] Enregistrement libjvm.so dans ldconfig..."
echo "/usr/lib/jvm/java-17-openjdk-amd64/lib/server" > /etc/ld.so.conf.d/java17-server.conf
echo "/usr/lib/jvm/java-17-openjdk-amd64/lib"        > /etc/ld.so.conf.d/java17-lib.conf
ldconfig
echo "[deploy] ldconfig OK -- verification libjvm.so:"
ldconfig -p | grep libjvm || echo "ATTENTION: libjvm.so absent du cache"

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
sleep 2
# Vider les logs pour avoir une sortie propre
> /opt/ibc/gateway_stdout.log
> /opt/ibc/gateway_stderr.log
systemctl start ib-gateway || true

echo "[deploy] Attente 30s..."
sleep 30

echo ""
echo "=== systemctl status ==="
systemctl status ib-gateway --no-pager || true

echo ""
echo "=== gateway_stdout.log ==="
cat /opt/ibc/gateway_stdout.log 2>/dev/null || echo "(vide)"

echo ""
echo "=== gateway_stderr.log ==="
cat /opt/ibc/gateway_stderr.log 2>/dev/null || echo "(vide)"

echo ""
echo "=== ldd libawt_xawt (deps manquantes) ==="
ldd /usr/lib/jvm/java-17-openjdk-amd64/lib/libawt_xawt.so 2>/dev/null | grep "not found" || echo "toutes les deps OK"
