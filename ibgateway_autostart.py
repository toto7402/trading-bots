"""
ibgateway_autostart.py — Auto-login IB Gateway sur VPS headless
===============================================================
Workflow :
  1. Démarre Xvfb (écran virtuel)
  2. Lance IB Gateway
  3. Attend la fenêtre de login
  4. Tape username + password automatiquement
  5. Envoie alerte Telegram pour le 2FA
  6. Attend confirmation 2FA (tu confirmes sur l'app IB depuis ton téléphone)
  7. Vérifie que le port 4001 est ouvert
  8. Relance automatiquement si déconnexion

Tourne en permanence — se reconnecte sans intervention manuelle
sauf pour le 2FA (1x par jour, depuis n'importe où via téléphone)
"""

import os, sys, time, subprocess, signal, logging, socket
from datetime import datetime, timedelta
import requests

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s  %(levelname)s  %(message)s',
    handlers=[
        logging.FileHandler('/root/bots/ibgateway_autostart.log'),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
IB_GATEWAY_PATH = '/root/ibgateway2/ibgateway'
IB_USERNAME     = os.environ.get('IB_USERNAME', '')
IB_PASSWORD     = os.environ.get('IB_PASSWORD', '')
TELEGRAM_TOKEN  = os.environ.get('TELEGRAM_TOKEN', '')
TELEGRAM_CHAT   = os.environ.get('TELEGRAM_CHAT_ID', '')
DISPLAY_NUM     = ':99'
IB_PORT         = 4001
JAVA_HOME       = '/root/zulu17.48.15-ca-jdk17.0.10-linux_x64'
CHECK_INTERVAL  = 60    # Vérifie connexion toutes les 60s
TWOFA_TIMEOUT   = 120   # Attend 2FA pendant 2 minutes

# ── Processus globaux ─────────────────────────────────────────────────────────
xvfb_proc   = None
gw_proc     = None

# ── Helpers ───────────────────────────────────────────────────────────────────

def send_telegram(msg: str):
    if not TELEGRAM_TOKEN:
        log.warning("Telegram non configuré")
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, json={
            'chat_id': TELEGRAM_CHAT,
            'text': msg,
            'parse_mode': 'Markdown'
        }, timeout=10)
    except Exception as e:
        log.warning(f"Telegram error: {e}")

def is_port_open(port: int, host: str = '127.0.0.1') -> bool:
    try:
        with socket.create_connection((host, port), timeout=3):
            return True
    except Exception:
        return False

def run_cmd(cmd: str, env=None) -> subprocess.Popen:
    return subprocess.Popen(
        cmd, shell=True, env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )

def kill_proc(proc):
    if proc and proc.poll() is None:
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            try: proc.kill()
            except Exception: pass

def xdotool(cmd: str):
    os.system(f"DISPLAY={DISPLAY_NUM} xdotool {cmd}")
    time.sleep(0.5)

def take_screenshot(path: str = '/tmp/ib_screen.png'):
    os.system(f"DISPLAY={DISPLAY_NUM} import -window root {path} 2>/dev/null")

def window_exists(title_fragment: str) -> bool:
    result = subprocess.run(
        f"DISPLAY={DISPLAY_NUM} xdotool search --name '{title_fragment}'",
        shell=True, capture_output=True, text=True
    )
    return bool(result.stdout.strip())

def get_window_id(title_fragment: str) -> str:
    result = subprocess.run(
        f"DISPLAY={DISPLAY_NUM} xdotool search --name '{title_fragment}'",
        shell=True, capture_output=True, text=True
    )
    ids = result.stdout.strip().split('\n')
    return ids[0] if ids else ''

# ── Démarrage Xvfb ────────────────────────────────────────────────────────────

def start_xvfb():
    global xvfb_proc
    log.info(f"Démarrage Xvfb sur {DISPLAY_NUM}")
    os.system(f"pkill -f 'Xvfb {DISPLAY_NUM}' 2>/dev/null")
    time.sleep(1)
    xvfb_proc = run_cmd(f"Xvfb {DISPLAY_NUM} -screen 0 1280x1024x24 -ac")
    time.sleep(2)
    log.info("Xvfb démarré")

# ── Démarrage IB Gateway ──────────────────────────────────────────────────────

def start_ibgateway():
    global gw_proc
    log.info("Démarrage IB Gateway...")

    env = os.environ.copy()
    env['DISPLAY']   = DISPLAY_NUM
    env['JAVA_HOME'] = JAVA_HOME
    env['PATH']      = f"{JAVA_HOME}/bin:" + env.get('PATH', '')

    gw_proc = subprocess.Popen(
        [IB_GATEWAY_PATH],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )
    log.info(f"IB Gateway lancé (PID {gw_proc.pid})")

# ── Auto-login ────────────────────────────────────────────────────────────────

def wait_for_login_window(timeout: int = 60) -> bool:
    log.info("Attente fenêtre de login IB Gateway...")
    start = time.time()
    while time.time() - start < timeout:
        if window_exists('IB Gateway') or window_exists('Login') or window_exists('Interactive'):
            log.info("Fenêtre de login détectée")
            return True
        # Fallback : vérifier via wmctrl
        result = subprocess.run(
            f"DISPLAY={DISPLAY_NUM} wmctrl -l",
            shell=True, capture_output=True, text=True
        )
        if 'IB' in result.stdout or 'Login' in result.stdout or 'Gateway' in result.stdout:
            log.info("Fenêtre IB détectée via wmctrl")
            return True
        time.sleep(2)
    log.warning("Timeout — fenêtre login non détectée")
    return False

def do_login():
    """Tape les credentials dans la fenêtre de login."""
    log.info("Tentative de login automatique...")
    time.sleep(3)

    # Cliquer sur le champ username (position approximative)
    xdotool("mousemove 640 400")
    xdotool("click 1")
    time.sleep(0.5)

    # Sélectionner tout et effacer
    xdotool("key ctrl+a")
    xdotool(f"type --clearmodifiers '{IB_USERNAME}'")
    time.sleep(0.5)

    # Tab vers password
    xdotool("key Tab")
    time.sleep(0.3)
    xdotool(f"type --clearmodifiers '{IB_PASSWORD}'")
    time.sleep(0.5)

    # Chercher et cocher "Paper Trading" si présent
    # (clic sur position approximative du checkbox paper trading)
    xdotool("mousemove 640 480")
    xdotool("click 1")
    time.sleep(0.3)

    # Valider avec Enter
    xdotool("key Return")
    log.info("Credentials envoyés — attente 2FA")

def wait_for_2fa(timeout: int = TWOFA_TIMEOUT) -> bool:
    """Attend que le 2FA soit complété (port 4001 ouvert)."""
    send_telegram(
        f"🔐 *IB Gateway — 2FA requis*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📱 Ouvre l'app IBKR sur ton téléphone\n"
        f"✅ Confirme la notification de connexion\n"
        f"🕐 {datetime.now().strftime('%H:%M:%S')}\n"
        f"_Connexion automatique en attente..._"
    )
    log.info(f"Attente 2FA ({timeout}s)...")
    start = time.time()
    while time.time() - start < timeout:
        if is_port_open(IB_PORT):
            log.info("✅ Port 4001 ouvert — IB Gateway connecté !")
            send_telegram(
                f"✅ *IB Gateway connecté*\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"Port 4001 actif — bots opérationnels\n"
                f"🕐 {datetime.now().strftime('%H:%M:%S')}"
            )
            return True
        time.sleep(3)
    log.warning("Timeout 2FA")
    return False

# ── Boucle principale ─────────────────────────────────────────────────────────

def full_login_sequence() -> bool:
    """Séquence complète de démarrage et login."""
    # Arrêter les processus existants
    kill_proc(gw_proc)
    os.system("pkill -f ibgateway 2>/dev/null")
    time.sleep(3)

    # Démarrer IB Gateway
    start_ibgateway()

    # Attendre la fenêtre de login
    if not wait_for_login_window(timeout=90):
        log.error("Fenêtre login non détectée — retry")
        return False

    # Login
    do_login()

    # Attendre 2FA
    if wait_for_2fa(timeout=TWOFA_TIMEOUT):
        return True

    log.error("Login échoué")
    return False

def monitor_connection():
    """Surveille la connexion et relance si nécessaire."""
    consecutive_failures = 0

    while True:
        try:
            if is_port_open(IB_PORT):
                consecutive_failures = 0
                log.info(f"IB Gateway OK — port {IB_PORT} actif")
            else:
                consecutive_failures += 1
                log.warning(f"Port {IB_PORT} fermé ({consecutive_failures}/3)")

                if consecutive_failures >= 3:
                    log.warning("IB Gateway déconnecté — relance...")
                    send_telegram(
                        f"⚠️ *IB Gateway déconnecté*\n"
                        f"Reconnexion automatique en cours...\n"
                        f"🕐 {datetime.now().strftime('%H:%M:%S')}"
                    )
                    success = full_login_sequence()
                    if success:
                        consecutive_failures = 0
                    else:
                        log.error("Reconnexion échouée — retry dans 60s")

        except Exception as e:
            log.error(f"Monitor error: {e}")

        time.sleep(CHECK_INTERVAL)

def main():
    log.info("=" * 50)
    log.info("IB Gateway Auto-Start")
    log.info("=" * 50)

    if not IB_USERNAME or not IB_PASSWORD:
        log.error("IB_USERNAME ou IB_PASSWORD non configuré dans .env")
        sys.exit(1)

    # Démarrer Xvfb
    start_xvfb()

    # Premier login
    log.info("Premier démarrage...")
    send_telegram(
        f"🚀 *IB Gateway Auto-Start*\n"
        f"Démarrage sur VPS...\n"
        f"🕐 {datetime.now().strftime('%H:%M:%S')}"
    )

    success = full_login_sequence()
    if not success:
        log.error("Premier login échoué — relance dans 60s")
        time.sleep(60)
        success = full_login_sequence()
        if not success:
            log.error("Impossible de démarrer IB Gateway")
            sys.exit(1)

    # Surveiller en permanence
    monitor_connection()

if __name__ == '__main__':
    main()
