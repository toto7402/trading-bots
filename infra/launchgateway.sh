#!/bin/bash
# launchgateway.sh -- Lance IB Gateway via IBC 3.19 en mode headless
# xvfb-run gere Xvfb proprement (start, DISPLAY, cleanup)

GATEWAY_DIR=/opt/ibgateway
IBC_DIR=/opt/ibc
IB_SETTINGS_DIR=/root/Jts
TWS_PORT=4002
JAVA_HOME=/usr/lib/jvm/java-17-openjdk-amd64
JAVA="${JAVA_HOME}/bin/java"
# libjvm.so est dans lib/server/ -- requis par libawt_xawt.so au chargement
export LD_LIBRARY_PATH="${JAVA_HOME}/lib/server:${JAVA_HOME}/lib${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}"
LOG=/opt/ibc/gateway_stdout.log
ELOG=/opt/ibc/gateway_stderr.log

mkdir -p "${IB_SETTINGS_DIR}" "$(dirname "${LOG}")"

exec >> "${LOG}" 2>> "${ELOG}"

echo "=== IBC $(date '+%Y-%m-%d %H:%M:%S') ==="
echo "JAVA=$("${JAVA}" -version 2>&1 | head -1)"
echo "IBC.jar: $(test -f "${IBC_DIR}/IBC.jar" && echo YES || echo NO)"
echo "ibgateway: $(test -f "${GATEWAY_DIR}/ibgateway" && echo YES || echo NO)"
echo "jars: $(ls "${GATEWAY_DIR}/jars/"*.jar 2>/dev/null | wc -l) fichiers"
echo "xvfb-run: $(command -v xvfb-run || echo ABSENT)"

for f in "${JAVA}" "${IBC_DIR}/IBC.jar" "${IBC_DIR}/config.ini" "${GATEWAY_DIR}/ibgateway"; do
    if [ ! -f "${f}" ]; then
        echo "ERREUR: ${f} introuvable"
        exit 1
    fi
done

# Classpath explicite jar par jar
CP="${IBC_DIR}/IBC.jar"
for jar in "${GATEWAY_DIR}/jars/"*.jar; do
    CP="${CP}:${jar}"
done
echo "classpath: $(echo "${CP}" | tr ':' '\n' | wc -l) entrees"
echo "=== Lancement ==="

# xvfb-run demarre Xvfb, set DISPLAY, puis exec java -- pas de gestion manuelle
exec xvfb-run \
    --auto-servernum \
    --server-args="-screen 0 1280x1024x24 -ac" \
    "${JAVA}" \
    --add-opens java.desktop/javax.swing=ALL-UNNAMED \
    --add-opens java.desktop/javax.swing.plaf.basic=ALL-UNNAMED \
    --add-opens java.desktop/sun.awt=ALL-UNNAMED \
    --add-opens java.desktop/sun.awt.X11=ALL-UNNAMED \
    --add-opens java.base/java.lang.reflect=ALL-UNNAMED \
    --add-opens java.base/java.lang=ALL-UNNAMED \
    -Djava.awt.headless=false \
    -cp "${CP}" \
    ibcalpha.ibc.IbcTws \
    "${IBC_DIR}/config.ini" \
    "${GATEWAY_DIR}" \
    "${IB_SETTINGS_DIR}" \
    "ibgateway" \
    "${TWS_PORT}"
