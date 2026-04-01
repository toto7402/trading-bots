#!/bin/bash
# launchgateway.sh -- Lance IB Gateway via IBC 3.19 en mode headless

GATEWAY_DIR=/opt/ibgateway
IBC_DIR=/opt/ibc
IB_SETTINGS_DIR=/root/Jts
TWS_PORT=4002
JAVA_HOME=/usr/lib/jvm/java-17-openjdk-amd64
JAVA="${JAVA_HOME}/bin/java"
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

for f in "${JAVA}" "${IBC_DIR}/IBC.jar" "${IBC_DIR}/config.ini" "${GATEWAY_DIR}/ibgateway"; do
    [ ! -f "${f}" ] && echo "ERREUR: ${f} introuvable" && exit 1
done

# Classpath : IBC.jar + jars gateway + JARs JavaFX (requis par IB Gateway 10.37)
CP="${IBC_DIR}/IBC.jar"
for jar in "${GATEWAY_DIR}/jars/"*.jar; do
    CP="${CP}:${jar}"
done

# Localise les JARs OpenJFX (javafx.embed.swing.JFXPanel)
JAVAFX_LIB=""
for d in /usr/share/openjfx/lib /usr/lib/jvm/java-17-openjdk-amd64/lib/openjfx \
          /usr/lib/jvm/java-17-openjdk-amd64/jmods /usr/share/java; do
    if ls "${d}"/javafx*.jar 2>/dev/null | grep -q jar; then
        JAVAFX_LIB="${d}"
        break
    fi
done

if [ -n "${JAVAFX_LIB}" ]; then
    echo "JavaFX lib: ${JAVAFX_LIB}"
    for jar in "${JAVAFX_LIB}"/javafx*.jar; do
        CP="${CP}:${jar}"
    done
else
    echo "ATTENTION: OpenJFX introuvable -- installe openjfx"
fi

echo "classpath: $(echo "${CP}" | tr ':' '\n' | wc -l) entrees"
echo "JavaFX module-path: ${JAVAFX_LIB:-ABSENT}"
echo "=== Lancement ==="

exec xvfb-run \
    --auto-servernum \
    --server-args="-screen 0 1280x1024x24 -ac" \
    "${JAVA}" \
    ${JAVAFX_LIB:+--module-path "${JAVAFX_LIB}"} \
    ${JAVAFX_LIB:+--add-modules javafx.controls,javafx.swing,javafx.web,javafx.graphics,javafx.fxml} \
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
