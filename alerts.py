import os
"""
alerts.py — Module d'alertes Telegram pour le système de trading
================================================================
Deux déclencheurs :
  1. Drawdown portefeuille > seuil configurable
  2. Bot déconnecté / crash détecté

Setup (5 min) :
  1. Ouvre Telegram → cherche @BotFather → /newbot → copie le TOKEN
  2. Cherche @userinfobot → envoie n'importe quoi → copie ton CHAT_ID
  3. Remplis TELEGRAM_TOKEN et TELEGRAM_CHAT_ID ci-dessous

Usage depuis n'importe quel bot :
    from alerts import alert_manager
    alert_manager.check_drawdown(nav_current, nav_peak)
    alert_manager.notify_crash("ib_paper_trading", "ConnectionError: TWS not responding")
"""

import requests
import logging
import time
from datetime import datetime
from typing import Optional

log = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════════════════════════════
#  CONFIG — À REMPLIR (5 min, voir Setup ci-dessus)
# ══════════════════════════════════════════════════════════════════════════════

TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN", "")       # ex: 7412638905:AAFx...
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")         # ex: 123456789

DRAWDOWN_THRESHOLD = 0.05    # -5% → alerte
CAPITAL            = 1_090_000  # référence pour calcul drawdown en $

# Anti-spam : délai minimum entre deux alertes du même type (secondes)
COOLDOWN_DRAWDOWN = 3_600    # 1h entre deux alertes drawdown
COOLDOWN_CRASH    = 300      # 5min entre deux alertes crash (par bot)

# ══════════════════════════════════════════════════════════════════════════════


class AlertManager:
    """
    Gestionnaire d'alertes Telegram.
    Thread-safe, avec cooldown anti-spam par type d'alerte.
    """

    def __init__(self):
        self._last_sent: dict[str, float] = {}
        self._nav_peak: float = CAPITAL

    # ── Vérification drawdown ──────────────────────────────────────────────────

    def check_drawdown(self, nav_current: float, nav_peak: Optional[float] = None):
        """
        Appeler à chaque refresh du dashboard.
        nav_current : NAV actuelle en $
        nav_peak    : NAV maximale atteinte (optionnel, sinon géré en interne)
        """
        if nav_peak is not None:
            self._nav_peak = max(self._nav_peak, nav_peak)
        self._nav_peak = max(self._nav_peak, nav_current)

        drawdown = (nav_current - self._nav_peak) / self._nav_peak

        if drawdown <= -DRAWDOWN_THRESHOLD:
            key = 'drawdown'
            if self._cooldown_ok(key, COOLDOWN_DRAWDOWN):
                dd_pct = abs(drawdown) * 100
                dd_usd = abs(nav_current - self._nav_peak)
                msg = (
                    f"🚨 *ALERTE DRAWDOWN*\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"📉 Drawdown : *-{dd_pct:.2f}%* (−${dd_usd:,.0f})\n"
                    f"💰 NAV actuelle : ${nav_current:,.2f}\n"
                    f"🏔 NAV peak : ${self._nav_peak:,.2f}\n"
                    f"⚠️ Seuil : -{DRAWDOWN_THRESHOLD*100:.0f}%\n"
                    f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"_Vérifier dashboard — stop global à -15%_"
                )
                self._send(msg)
                self._last_sent[key] = time.time()
                log.warning(f"Alerte drawdown envoyée : {dd_pct:.1f}%")

        return drawdown

    # ── Notification crash / déconnexion ──────────────────────────────────────

    def notify_crash(self, bot_name: str, error_msg: str):
        """
        Appeler dans le except d'une boucle bot quand la connexion IB est perdue
        ou qu'une exception non gérée survient.
        """
        key = f"crash_{bot_name}"
        if self._cooldown_ok(key, COOLDOWN_CRASH):
            msg = (
                f"⚠️ *BOT CRASH / DÉCONNEXION*\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"🤖 Bot : *{bot_name}*\n"
                f"❌ Erreur : `{error_msg[:200]}`\n"
                f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"_Relancer le bot ou vérifier TWS/IB Gateway_"
            )
            self._send(msg)
            self._last_sent[key] = time.time()
            log.error(f"Alerte crash envoyée : {bot_name} — {error_msg}")

    # ── Notification fill (optionnel) ─────────────────────────────────────────

    def notify_fill(self, bot_name: str, ticker: str, action: str,
                    shares: int, price: float):
        """Décommenter l'appel dans les bots si tu veux les confirmations de fills."""
        msg = (
            f"✅ *ORDRE EXÉCUTÉ*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🤖 Bot : {bot_name}\n"
            f"📌 {action} {shares}× *{ticker}* @ ${price:.3f}\n"
            f"🕐 {datetime.now().strftime('%H:%M:%S')}"
        )
        self._send(msg)

    # ── Test de connexion ─────────────────────────────────────────────────────

    def test(self):
        """Envoie un message de test pour vérifier la config."""
        msg = (
            f"✅ *Alertes Trading — Connexion OK*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"Compte paper : DUP091760\n"
            f"Capital : ${CAPITAL:,}\n"
            f"Seuil drawdown : -{DRAWDOWN_THRESHOLD*100:.0f}%\n"
            f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"_Système d'alertes opérationnel._"
        )
        self._send(msg)

    # ── Envoi Telegram ────────────────────────────────────────────────────────

    def _send(self, text: str):
        if TELEGRAM_TOKEN.startswith("VOTRE"):
            log.warning("AlertManager : TELEGRAM_TOKEN non configuré, alerte ignorée.")
            print(f"  [ALERTE NON ENVOYÉE — Telegram non configuré]\n  {text}")
            return
        try:
            url  = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
            resp = requests.post(url, json={
                "chat_id":    TELEGRAM_CHAT_ID,
                "text":       text,
                "parse_mode": "Markdown",
            }, timeout=10)
            if resp.status_code == 200:
                print(f"  📲 Alerte Telegram envoyée")
            else:
                log.error(f"Telegram API error {resp.status_code}: {resp.text}")
                print(f"  ❌ Telegram error {resp.status_code}: {resp.text}")
        except requests.exceptions.ConnectionError:
            log.error("Telegram : pas de connexion réseau")
            print("  ❌ Telegram : pas de connexion réseau")
        except Exception as e:
            log.error(f"Erreur envoi Telegram : {e}")
            print(f"  ❌ Telegram : {e}")

    # ── Cooldown anti-spam ────────────────────────────────────────────────────

    def _cooldown_ok(self, key: str, delay_s: float) -> bool:
        last = self._last_sent.get(key, 0)
        return (time.time() - last) >= delay_s


# ── Instance globale partageable ──────────────────────────────────────────────
alert_manager = AlertManager()


# ── Test rapide si lancé directement ─────────────────────────────────────────
if __name__ == '__main__':
    print("Test d'envoi Telegram...")
    alert_manager.test()
