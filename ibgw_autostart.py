#!/usr/bin/env python3
"""
ibgw_autostart.py -- Auto-login IB Gateway sur VPS headless
============================================================
1. Demarre Xvfb + Openbox
2. Lance IB Gateway
3. Detecte la fenetre de login
4. Selectionne IB API + Paper Trading
5. Tape les credentials dans Market Data
6. Clique Log In
7. Attend le 2FA sur telephone (notification IBKR)
8. Verifie port 4001
9. Surveille et reconnecte si deconnexion
"""

import os, sys, time, subprocess, socket, logging, signal
from datetime import datetime
import requests

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    handlers=[
        logging.FileHandler('/root/bots/ibgw_autostart.log'),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)

#        Config                                                                                                                                                                                                             
IB_GW_PATH     = '/root/ibgateway2/ibgateway'
IB_USERNAME    = os.environ.get('IB_USERNAME', 'toto74000')
IB_PASSWORD    = os.environ.get('IB_PASSWORD', 'W@2@fuzovire')
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN', '')
TELEGRAM_CHAT  = os.environ.get('TELEGRAM_CHAT_ID', '')
DISPLAY        = ':99'
IB_PORT        = 4001
TWOFA_TIMEOUT  = 180   # 3 min pour confirmer 2FA
CHECK_INTERVAL = 60

#        Helpers                                                                                                                                                                                                          

def send_telegram(msg):
    if not TELEGRAM_TOKEN:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={'chat_id': TELEGRAM_CHAT, 'text': msg, 'parse_mode': 'Markdown'},
            timeout=10
        )
    except Exception as e:
        log.warning(f"Telegram: {e}")

def is_port_open(port=4001, host='127.0.0.1'):
    try:
        with socket.create_connection((host, port), timeout=3):
            return True
    except Exception:
        return False

def run(cmd, env=None):
    return subprocess.Popen(cmd, shell=True, env=env,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)

def xdo(cmd):
    os.system(f"DISPLAY={DISPLAY} xdotool {cmd}")
    time.sleep(0.4)

def scrot(path='/tmp/ib_screen.png'):
    os.system(f"DISPLAY={DISPLAY} scrot {path} 2>/dev/null")

def get_window_pos():
    """Retourne (x, y, w, h) de la fenetre IBKR Gateway."""
    result = subprocess.run(
        f"DISPLAY={DISPLAY} xdotool search --name 'IBKR Gateway' getwindowgeometry",
        shell=True, capture_output=True, text=True
    )
    out = result.stdout
    try:
        import re
        pos = re.search(r'Position: (\d+),(\d+)', out)
        size = re.search(r'Geometry: (\d+)x(\d+)', out)
        if pos and size:
            return (int(pos.group(1)), int(pos.group(2)),
                    int(size.group(1)), int(size.group(2)))
    except Exception:
        pass
    return None

def click(x, y):
    xdo(f"mousemove {x} {y} click 1")

def type_text(text):
    # Utiliser xdotool type avec delay pour eviter les caracteres manques
    os.system(f"DISPLAY={DISPLAY} xdotool type --clearmodifiers --delay 50 '{text}'")
    time.sleep(0.3)

def wait_for_window(timeout=60):
    log.info("Attente fenetre IBKR Gateway...")
    start = time.time()
    while time.time() - start < timeout:
        result = subprocess.run(
            f"DISPLAY={DISPLAY} wmctrl -l",
            shell=True, capture_output=True, text=True
        )
        if 'IBKR Gateway' in result.stdout or 'IB Gateway' in result.stdout:
            log.info("Fenetre detectee")
            time.sleep(3)  # laisser la fenetre charger completement
            return True
        time.sleep(2)
    log.warning("Fenetre non detectee apres timeout")
    return False

#        Login                                                                                                                                                                                                                

def do_login():
    """Login complet avec detection automatique des coordonnees."""
    log.info("Tentative de login...")

    # Obtenir la position de la fenetre
    geo = get_window_pos()
    if geo:
        wx, wy, ww, wh = geo
        log.info(f"Fenetre: pos=({wx},{wy}) size=({ww}x{wh})")
    else:
        # Valeurs par defaut si detection echoue
        wx, wy, ww, wh = 240, 0, 750, 780
        log.warning("Geometrie fenetre non detectee, utilisation valeurs defaut")

    # Calculer les coordonnees relatives a la fenetre
    # Basees sur les screenshots precendents (fenetre ~750x580 centree)
    cx = wx + ww // 2   # centre horizontal

    # Bouton IB API (toggle droite) -- y ~ 401 dans la fenetre

    # Coordonnees fixes basees sur screenshots
    ib_api_x = 727
    ib_api_y = 401
    paper_x  = 727
    paper_y  = 441
    mdu_x    = 632
    mdu_y    = 566
    mdp_x    = 632
    mdp_y    = 602
    login_x  = 632
    login_y  = 651
    log.info(f"Clic IB API: ({ib_api_x}, {ib_api_y})")
    click(ib_api_x, ib_api_y)
    time.sleep(1)

    log.info(f"Clic Paper Trading: ({paper_x}, {paper_y})")
    click(paper_x, paper_y)
    time.sleep(1)

    # Screenshot pour verifier les selections
    scrot('/tmp/ib_after_toggle.png')

    log.info(f"Saisie username Market Data: ({mdu_x}, {mdu_y})")
    click(mdu_x, mdu_y)
    time.sleep(0.5)
    xdo("key ctrl+a")
    xdo("key Delete")
    type_text(IB_USERNAME)
    time.sleep(0.3)

    log.info(f"Saisie password Market Data: ({mdp_x}, {mdp_y})")
    click(mdp_x, mdp_y)
    time.sleep(0.5)
    xdo("key ctrl+a")
    xdo("key Delete")
    type_text(IB_PASSWORD)
    time.sleep(0.3)

    # Screenshot avant login
    scrot('/tmp/ib_before_login.png')

    log.info(f"Clic Log In: ({login_x}, {login_y})")
    click(login_x, login_y)

    log.info("Credentials envoyes -- attente 2FA")

def wait_for_2fa():
    """Attend confirmation 2FA (port 4001 ouvert)."""
    send_telegram(
        f"*IB Gateway -- 2FA requis*\n"
        f"Ouvre l'app IBKR sur ton telephone\n"
        f"Confirme la notification de connexion\n"
        f"_{datetime.now().strftime('%H:%M:%S')}_"
    )
    log.info(f"Attente 2FA ({TWOFA_TIMEOUT}s)...")
    start = time.time()
    while time.time() - start < TWOFA_TIMEOUT:
        if is_port_open(IB_PORT):
            log.info(f"Port {IB_PORT} ouvert -- connecte!")
            send_telegram(
                f"*IB Gateway connecte*\n"
                f"Port {IB_PORT} actif -- bots operationnels\n"
                f"_{datetime.now().strftime('%H:%M:%S')}_"
            )
            return True
        # Verifier aussi port 7497 (paper trading alternatif)
        if is_port_open(7497):
            log.info("Port 7497 ouvert -- connecte via port alternatif!")
            return True
        time.sleep(3)
    log.warning("Timeout 2FA")
    scrot('/tmp/ib_2fa_timeout.png')
    return False

#        Demarrage                                                                                                                                                                                                    

def start_xvfb():
    os.system(f"pkill -f 'Xvfb {DISPLAY}' 2>/dev/null")
    time.sleep(1)
    os.system(f"rm -f /tmp/.X{DISPLAY[1:]}-lock 2>/dev/null")
    run(f"Xvfb {DISPLAY} -screen 0 1280x1024x24 -ac")
    time.sleep(2)
    run(f"DISPLAY={DISPLAY} openbox")
    time.sleep(2)
    log.info("Xvfb + Openbox demarres")

def start_ibgw():
    os.system("pkill -f ibgateway 2>/dev/null")
    time.sleep(3)
    env = os.environ.copy()
    env['DISPLAY'] = DISPLAY
    subprocess.Popen([IB_GW_PATH], env=env,
                     stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL)
    log.info("IB Gateway lance")

def full_login_sequence():
    """Sequence complete: start -> login -> 2FA -> verify."""
    start_ibgw()

    if not wait_for_window(timeout=90):
        log.error("Fenetre login non apparue")
        scrot('/tmp/ib_nowin.png')
        return False

    do_login()

    if wait_for_2fa():
        log.info("IB Gateway connecte avec succes!")
        # Redemarrer les bots trading
        os.system("systemctl restart momentum-bot pead-bot spinoff-bot index-rebal-bot convertibles-bot options-bot etf-options-bot futures-bot news-bot news-trading-bot mr-intraday-bot etf-intraday-bot pairs-intraday-bot breakout-intraday-bot 2>/dev/null")
        log.info("Bots trading redemarres")
        return True

    log.error("Login echoue")
    return False

def monitor():
    """Surveille la connexion et reconnecte si necessaire."""
    failures = 0
    while True:
        try:
            port_ok = is_port_open(IB_PORT) or is_port_open(7497)
            if port_ok:
                failures = 0
                log.info(f"IB Gateway OK")
            else:
                failures += 1
                log.warning(f"IB Gateway deconnecte ({failures}/3)")
                if failures >= 3:
                    log.warning("Reconnexion...")
                    send_telegram(
                        f"*IB Gateway deconnecte*\n"
                        f"Reconnexion en cours...\n"
                        f"_{datetime.now().strftime('%H:%M:%S')}_"
                    )
                    if full_login_sequence():
                        failures = 0
        except Exception as e:
            log.error(f"Monitor error: {e}")
        time.sleep(CHECK_INTERVAL)

#        Main                                                                                                                                                                                                                   

def main():
    log.info("=" * 50)
    log.info("IB Gateway Auto-Start v2")
    log.info("=" * 50)

    send_telegram(
        f"*IB Gateway Auto-Start*\n"
        f"Demarrage sur VPS...\n"
        f"_{datetime.now().strftime('%H:%M:%S')}_"
    )

    start_xvfb()

    # Premier login
    success = full_login_sequence()
    if not success:
        log.warning("Premier essai echoue, retry dans 30s...")
        time.sleep(30)
        success = full_login_sequence()
        if not success:
            log.error("Impossible de demarrer IB Gateway")
            send_telegram("IB Gateway: echec du demarrage apres 2 tentatives")
            sys.exit(1)

    # Surveillance continue
    monitor()

if __name__ == '__main__':
    main()
