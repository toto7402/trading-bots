#!/bin/bash
# launchgateway.sh -- Lance IB Gateway via IBC 3.19 en mode headless
# Fix exit code 1107 : chemin GATEWAY_DIR sans slash final, classpath explicite

set -euo pipefail

GATEWAY_DIR=/opt/ibgateway          # PAS de slash final -- requis par IBC 3.19
IBC_DIR=/opt/ibc
CONFIG_FILE="${IBC_DIR}/config.ini"
LOG_FILE=/root/bots/ibc.log
DISPLAY="${DISPLAY:-:99}"

# Verifications
if [ ! -d "${GATEWAY_DIR}/jars" ]; then
    echo "ERREUR: ${GATEWAY_DIR}/jars introuvable" >&2
    exit 1
fi

if [ ! -f "${IBC_DIR}/IBC.jar" ]; then
    echo "ERREUR: ${IBC_DIR}/IBC.jar introuvable -- lance setup_ibc.sh d'abord" >&2
    exit 1
fi

if [ ! -f "${CONFIG_FILE}" ]; then
    echo "ERREUR: ${CONFIG_FILE} introuvable" >&2
    exit 1
fi

# Construction du classpath : IBC.jar + tous les jars du gateway
CLASSPATH="${IBC_DIR}/IBC.jar"
for jar in "${GATEWAY_DIR}/jars/"*.jar; do
    CLASSPATH="${CLASSPATH}:${jar}"
done

mkdir -p /root/bots
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Demarrage IBC -- GATEWAY_DIR=${GATEWAY_DIR}" | tee -a "${LOG_FILE}"

# Lancement IBC 3.19
# Syntaxe: IbcTws <config.ini> <IbDir_sans_slash_final>
exec java \
    -Dswing.systemlaf=com.sun.java.swing.plaf.motif.MotifLookAndFeel \
    -Djava.awt.headless=false \
    -cp "${CLASSPATH}" \
    ibcalpha.ibc.IbcTws \
    "${CONFIG_FILE}" \
    "${GATEWAY_DIR}" \
    2>&1 | tee -a "${LOG_FILE}"
