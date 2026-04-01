#!/bin/bash
# launchgateway.sh -- Lance IB Gateway via IBC 3.19 en mode headless

set -euo pipefail

GATEWAY_DIR=/opt/ibgateway
IBC_DIR=/opt/ibc
IB_SETTINGS_DIR=/root/Jts
TWS_PORT=4002
JAVA=/usr/lib/jvm/java-17-openjdk-amd64/bin/java
LOG_FILE=/root/bots/ibc.log
export DISPLAY="${DISPLAY:-:99}"

mkdir -p /root/bots "${IB_SETTINGS_DIR}"

# --- Debug pre-lancement ---
echo "=== IBC debug $(date '+%Y-%m-%d %H:%M:%S') ==="
echo "GATEWAY_DIR=${GATEWAY_DIR}"
echo "JAVA=${JAVA}"
echo "IBC.jar exists: $(ls ${IBC_DIR}/IBC.jar 2>/dev/null && echo YES || echo NO)"
echo "ibgateway binary: $(ls ${GATEWAY_DIR}/ibgateway 2>/dev/null && echo YES || echo NO)"
echo "jars: $(ls ${GATEWAY_DIR}/jars/*.jar 2>/dev/null | wc -l) fichiers"
echo "DISPLAY=${DISPLAY}"

# --- Verifications ---
if [ ! -f "${JAVA}" ]; then
    echo "ERREUR: Java 17 introuvable a ${JAVA}" >&2
    echo "Installe avec: apt-get install -y openjdk-17-jre-headless" >&2
    exit 1
fi

if [ ! -f "${IBC_DIR}/IBC.jar" ]; then
    echo "ERREUR: ${IBC_DIR}/IBC.jar introuvable -- lance setup_ibc.sh d'abord" >&2
    exit 1
fi

if [ ! -f "${IBC_DIR}/config.ini" ]; then
    echo "ERREUR: ${IBC_DIR}/config.ini introuvable" >&2
    exit 1
fi

if [ ! -f "${GATEWAY_DIR}/ibgateway" ]; then
    echo "ERREUR: ${GATEWAY_DIR}/ibgateway introuvable" >&2
    ls -la "${GATEWAY_DIR}" >&2
    exit 1
fi

echo "=== Lancement IBC ==="

# --- Lancement IBC 3.19 avec Java 17 ---
# --add-opens : requis par IB Gateway 10.x pour les classes AWT internes
# -Djava.awt.headless=false : IB Gateway a besoin d'un display (Xvfb)
# Classpath : IBC.jar + tous les JARs gateway (jts4launch, twslaunch, etc.)
# Args positionnels IBC : config.ini  GatewayDir  SettingsDir  ibgateway  port
exec "${JAVA}" \
    --add-opens java.desktop/sun.awt=ALL-UNNAMED \
    --add-opens java.desktop/sun.awt.X11=ALL-UNNAMED \
    -Djava.awt.headless=false \
    -cp "${IBC_DIR}/IBC.jar:${GATEWAY_DIR}/jars/*" \
    ibcalpha.ibc.IbcTws \
    "${IBC_DIR}/config.ini" \
    "${GATEWAY_DIR}" \
    "${IB_SETTINGS_DIR}" \
    "ibgateway" \
    "${TWS_PORT}" \
    >> "${LOG_FILE}" 2>&1
