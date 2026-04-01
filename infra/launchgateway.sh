#!/bin/bash
# launchgateway.sh -- Lance IB Gateway via IBC 3.19 en mode headless

set -euo pipefail

GATEWAY_DIR=/opt/ibgateway           # repertoire installation IB Gateway (sans slash)
IBC_DIR=/opt/ibc                     # repertoire IBC
IB_SETTINGS_DIR=/root/Jts            # repertoire settings IB (= IbDir dans config.ini)
TWS_PORT=4002                         # port paper trading IB Gateway
LOG_FILE=/root/bots/ibc.log
export DISPLAY="${DISPLAY:-:99}"

mkdir -p /root/bots "${IB_SETTINGS_DIR}"

# --- Debug pre-lancement ---
echo "=== IBC debug $(date '+%Y-%m-%d %H:%M:%S') ==="
echo "GATEWAY_DIR=${GATEWAY_DIR}"
echo "IBC.jar exists: $(ls ${IBC_DIR}/IBC.jar 2>/dev/null && echo YES || echo NO)"
echo "ibgateway binary: $(ls ${GATEWAY_DIR}/ibgateway 2>/dev/null && echo YES || echo NO)"
echo "jars: $(ls ${GATEWAY_DIR}/jars/*.jar 2>/dev/null | wc -l) fichiers"

# --- Verifications ---
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

# --- Lancement IBC 3.19 ---
# Arguments positionnels obligatoires :
#   1) config.ini
#   2) GATEWAY_DIR  -- doit contenir l'executable ibgateway
#   3) IB_SETTINGS_DIR -- dossier Jts (IbDir dans config.ini)
#   4) "ibgateway"  -- chaine litterale (minuscule), distingue de TWS
#   5) TWS_PORT
# Classpath : uniquement IBC.jar (IBC charge lui-meme les jars gateway)
exec java \
    -Djava.awt.headless=false \
    -cp "${IBC_DIR}/IBC.jar" \
    ibcalpha.ibc.IbcTws \
    "${IBC_DIR}/config.ini" \
    "${GATEWAY_DIR}" \
    "${IB_SETTINGS_DIR}" \
    "ibgateway" \
    "${TWS_PORT}" \
    >> "${LOG_FILE}" 2>&1
