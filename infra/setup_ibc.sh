#!/bin/bash
# setup_ibc.sh -- Installe IBC 3.19 et configure le lancement headless
# Cible: VPS Ubuntu, IB Gateway dans /opt/ibgateway

set -euo pipefail

IBC_VERSION=3.19.0
IBC_DIR=/opt/ibc
INFRA_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR=/root/bots

echo "=== Setup IBC ${IBC_VERSION} ==="

# 1. Dependances
apt-get install -y --no-install-recommends \
    openjdk-17-jre-headless \
    xvfb \
    x11-utils \
    wmctrl \
    2>/dev/null || true

# 2. Telechargement IBC si absent
mkdir -p "${IBC_DIR}"
if [ ! -f "${IBC_DIR}/IBC.jar" ]; then
    echo "Telechargement IBC ${IBC_VERSION}..."
    TMP=$(mktemp -d)
    curl -fsSL \
        "https://github.com/IbcAlpha/IBC/releases/download/${IBC_VERSION}/IBCLinux-${IBC_VERSION}.zip" \
        -o "${TMP}/ibc.zip"
    unzip -q "${TMP}/ibc.zip" -d "${TMP}/ibc"
    cp "${TMP}/ibc/"*.jar "${IBC_DIR}/" 2>/dev/null || \
        find "${TMP}/ibc" -name "*.jar" -exec cp {} "${IBC_DIR}/" \;
    rm -rf "${TMP}"
    echo "IBC installe dans ${IBC_DIR}"
else
    echo "IBC.jar deja present dans ${IBC_DIR}"
fi

# 3. Verification IB Gateway
if [ ! -d /opt/ibgateway/jars ]; then
    echo "ERREUR: /opt/ibgateway/jars absent -- verifier l'installation IB Gateway" >&2
    exit 1
fi
echo "IB Gateway OK: /opt/ibgateway/jars/"

# 4. Generation config.ini depuis template + variables d'environnement
IB_USER="${IB_USERNAME:-toto74000}"
IB_PASS="${IB_PASSWORD:-}"

if [ -z "${IB_PASS}" ]; then
    echo "ERREUR: IB_PASSWORD non defini. Exporter la variable avant de lancer ce script." >&2
    echo "  export IB_PASSWORD='votre_mot_de_passe'" >&2
    exit 1
fi

sed \
    -e "s/YOUR_IB_USERNAME/${IB_USER}/" \
    -e "s/YOUR_IB_PASSWORD/${IB_PASS}/" \
    "${INFRA_DIR}/config.ini.template" > "${IBC_DIR}/config.ini"
chmod 600 "${IBC_DIR}/config.ini"
echo "config.ini genere dans ${IBC_DIR}/config.ini"

# 5. Copie launchgateway.sh et permissions
cp "${INFRA_DIR}/launchgateway.sh" "${IBC_DIR}/launchgateway.sh"
chmod +x "${IBC_DIR}/launchgateway.sh"

# 6. Service systemd
mkdir -p "${LOG_DIR}"
cat > /etc/systemd/system/ibgateway.service << 'EOF'
[Unit]
Description=IB Gateway via IBC (headless)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
Environment="DISPLAY=:99"
ExecStartPre=/bin/sh -c 'Xvfb :99 -screen 0 1280x1024x24 -ac &'
ExecStartPre=/bin/sleep 2
ExecStart=/opt/ibc/launchgateway.sh
Restart=on-failure
RestartSec=30
StandardOutput=append:/root/bots/ibgateway.log
StandardError=append:/root/bots/ibgateway.log

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable ibgateway.service
echo "Service ibgateway.service installe et active"

echo ""
echo "=== Setup termine ==="
echo "Pour demarrer: systemctl start ibgateway"
echo "Pour les logs:  journalctl -u ibgateway -f"
echo "              tail -f /root/bots/ibc.log"
