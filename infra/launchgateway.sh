#!/bin/bash
# launchgateway.sh -- Lance IB Gateway via IBC 3.19 en mode headless
# Xvfb demarre ici (pas dans ExecStartPre) pour eviter le kill systemd du process group

GATEWAY_DIR=/opt/ibgateway
IBC_DIR=/opt/ibc
IB_SETTINGS_DIR=/root/Jts
TWS_PORT=4002
JAVA=/usr/lib/jvm/java-17-openjdk-amd64/bin/java
LOG=/opt/ibc/gateway_stdout.log
ELOG=/opt/ibc/gateway_stderr.log

mkdir -p /root/bots "${IB_SETTINGS_DIR}" "$(dirname "${LOG}")"

exec >> "${LOG}" 2>> "${ELOG}"   # tout ce qui suit va dans les logs

echo "=== IBC debug $(date '+%Y-%m-%d %H:%M:%S') ==="
echo "GATEWAY_DIR=${GATEWAY_DIR}"
echo "JAVA=$(${JAVA} -version 2>&1 | head -1)"
echo "IBC.jar: $(ls ${IBC_DIR}/IBC.jar 2>/dev/null && echo YES || echo NO)"
echo "ibgateway: $(ls ${GATEWAY_DIR}/ibgateway 2>/dev/null && echo YES || echo NO)"
echo "jars: $(ls ${GATEWAY_DIR}/jars/*.jar 2>/dev/null | wc -l) fichiers"

# --- Verifications ---
for f in "${JAVA}" "${IBC_DIR}/IBC.jar" "${IBC_DIR}/config.ini" "${GATEWAY_DIR}/ibgateway"; do
    if [ ! -f "${f}" ]; then
        echo "ERREUR: ${f} introuvable" >&2
        exit 1
    fi
done

# --- Xvfb : demarrage dans le script (pas dans ExecStartPre) ---
pkill Xvfb 2>/dev/null || true
rm -f /tmp/.X99-lock 2>/dev/null || true
Xvfb :99 -screen 0 1280x1024x24 -ac 2>/dev/null &
XVFB_PID=$!
echo "Xvfb PID=${XVFB_PID}"
sleep 3

if ! kill -0 "${XVFB_PID}" 2>/dev/null; then
    echo "ERREUR: Xvfb n'a pas demarre" >&2
    exit 1
fi
export DISPLAY=:99
echo "DISPLAY=${DISPLAY} OK"

# --- Classpath explicite (wildcard non resolu via exec bash) ---
CP="${IBC_DIR}/IBC.jar"
for jar in "${GATEWAY_DIR}/jars/"*.jar; do
    CP="${CP}:${jar}"
done
echo "classpath jars count: $(echo "${CP}" | tr ':' '\n' | wc -l)"

echo "=== Lancement IBC ==="

# Args positionnels IBC 3.19 : config.ini  GatewayDir  SettingsDir  ibgateway  port
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
    "${TWS_PORT}"
