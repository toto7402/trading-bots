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
    exit 1
fi
if [ ! -f "${IBC_DIR}/IBC.jar" ]; then
    echo "ERREUR: ${IBC_DIR}/IBC.jar introuvable" >&2
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

# --- Construction explicite du classpath ---
# Le wildcard Java (jars/*) n'est pas résolu quand il est passé via exec bash.
# On construit la chaine CP jar par jar pour que chaque .jar soit explicite.
CP="${IBC_DIR}/IBC.jar"
for jar in "${GATEWAY_DIR}/jars/"*.jar; do
    CP="${CP}:${jar}"
done
echo "classpath: ${CP}"

echo "=== Lancement IBC ==="

# Args positionnels IBC 3.19 :
#   1) config.ini
#   2) GATEWAY_DIR  (contient l'executable ibgateway)
#   3) IB_SETTINGS_DIR  (dossier Jts, = IbDir dans config.ini)
#   4) "ibgateway"  (litteral minuscule, distingue de tws)
#   5) TWS_PORT
exec "${JAVA}" \
    --add-opens java.desktop/sun.awt=ALL-UNNAMED \
    --add-opens java.desktop/sun.awt.X11=ALL-UNNAMED \
    -Djava.awt.headless=false \
    -cp "${CP}" \
    ibcalpha.ibc.IbcTws \
    "${IBC_DIR}/config.ini" \
    "${GATEWAY_DIR}" \
    "${IB_SETTINGS_DIR}" \
    "ibgateway" \
    "${TWS_PORT}" \
    >> "${LOG_FILE}" 2>&1
