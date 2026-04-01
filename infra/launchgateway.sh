#!/bin/bash
# launchgateway.sh -- Lance IB Gateway via IBC 3.19 en mode headless
# Fix exit 1107 : classpath IBC.jar + gateway/jars/*, 4eme arg "ibgateway" obligatoire

set -euo pipefail

GATEWAY_DIR=/opt/ibgateway           # repertoire installation IB Gateway (sans slash)
IBC_DIR=/opt/ibc                     # repertoire IBC
IB_SETTINGS_DIR=/root/Jts            # repertoire settings IB (= IbDir dans config.ini)
TWS_PORT=4001                         # port paper trading IB Gateway
CONFIG_FILE="${IBC_DIR}/config.ini"
LOG_FILE=/root/bots/ibc.log
export DISPLAY="${DISPLAY:-:99}"

# --- Verifications pre-lancement ---

if [ ! -d "${GATEWAY_DIR}/jars" ]; then
    echo "ERREUR: ${GATEWAY_DIR}/jars introuvable" >&2
    exit 1
fi

if [ ! -f "${GATEWAY_DIR}/ibgateway" ]; then
    echo "ERREUR: ${GATEWAY_DIR}/ibgateway introuvable" >&2
    echo "Contenu de ${GATEWAY_DIR}:" >&2
    ls -la "${GATEWAY_DIR}" >&2
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

mkdir -p /root/bots "${IB_SETTINGS_DIR}"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Demarrage IBC -- GATEWAY_DIR=${GATEWAY_DIR} PORT=${TWS_PORT}" \
    | tee -a "${LOG_FILE}"
echo "  Classpath: ${IBC_DIR}/IBC.jar + ${GATEWAY_DIR}/jars/*" | tee -a "${LOG_FILE}"
echo "  Args: config=${CONFIG_FILE} gwdir=${GATEWAY_DIR} settings=${IB_SETTINGS_DIR} mode=ibgateway port=${TWS_PORT}" \
    | tee -a "${LOG_FILE}"

# --- Lancement IBC 3.19 ---
#
# Syntaxe complete IBC 3.19 pour IB Gateway (pas TWS) :
#   IbcTws <config.ini> <GatewayDir> <IbSettingsDir> ibgateway <port>
#
# Classpath :
#   - IBC.jar        : la classe ibcalpha.ibc.IbcTws
#   - jars/*         : jts4launch-XXXX.jar, twslaunch-XXXX.jar, etc.
#                      Le wildcard * est interprete par Java (pas le shell)
#                      car il est entre guillemets -- ne pas enlever les quotes.
#
# -Djava.awt.headless=false est necessaire car IB Gateway affiche une fenetre
# (meme sous Xvfb) pour le login.

exec java \
    -Djava.awt.headless=false \
    -cp "${IBC_DIR}/IBC.jar:${GATEWAY_DIR}/jars/*" \
    ibcalpha.ibc.IbcTws \
    "${CONFIG_FILE}" \
    "${GATEWAY_DIR}" \
    "${IB_SETTINGS_DIR}" \
    "ibgateway" \
    "${TWS_PORT}" \
    >> "${LOG_FILE}" 2>&1
