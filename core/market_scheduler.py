"""
market_scheduler.py
-------------------
Manages which markets are open and schedules trading sessions.
"""

from datetime import time, datetime, timezone, timedelta

from data.asset_universe import ASSET_CLASSES

# ---------------------------------------------------------------------------
# Session hours (all times in UTC)
# Overnight sessions have open_utc > close_utc (they span midnight).
# ---------------------------------------------------------------------------

SESSION_HOURS: dict[str, tuple[time, time]] = {
    'sydney':       (time(22, 0),  time(5, 30)),   # overnight
    'tokyo':        (time(0, 0),   time(6, 30)),
    'hong_kong':    (time(1, 30),  time(8, 0)),
    'china':        (time(1, 30),  time(7, 0)),
    'eu':           (time(8, 0),   time(16, 30)),
    'us':           (time(13, 0),  time(21, 0)),
    'futures':      (time(13, 0),  time(21, 30)),
    'crypto':       (time(0, 0),   time(23, 59)),
}


class MarketScheduler:
    """Determines open sessions and asset classes for a given UTC time."""

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _current_utc_time() -> time:
        """Return the current UTC wall-clock time (no date)."""
        return datetime.now(timezone.utc).time()

    @staticmethod
    def _is_session_open(open_utc: time, close_utc: time, t: time) -> bool:
        """
        Return True when *t* falls inside [open_utc, close_utc].

        Overnight sessions (open_utc > close_utc) span midnight, so the
        window is: t >= open_utc  OR  t <= close_utc.
        """
        if open_utc <= close_utc:
            # Normal intra-day window
            return open_utc <= t <= close_utc
        else:
            # Overnight window (e.g. 22:00 → 05:30)
            return t >= open_utc or t <= close_utc

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def active_sessions(self, t: time = None) -> list[str]:
        """
        Return the names of all sessions that are currently open.

        Parameters
        ----------
        t:
            UTC time to evaluate.  Defaults to *now* when omitted or None.
        """
        if t is None:
            t = self._current_utc_time()

        return [
            name
            for name, (open_utc, close_utc) in SESSION_HOURS.items()
            if self._is_session_open(open_utc, close_utc, t)
        ]

    def active_asset_classes(self, t: time = None) -> list[str]:
        """
        Return the names of all asset classes whose ``is_open_at`` method
        returns True for the given UTC time.

        Parameters
        ----------
        t:
            UTC time to evaluate.  Defaults to *now* when omitted or None.
        """
        if t is None:
            t = self._current_utc_time()

        return [
            name
            for name, asset_class in ASSET_CLASSES.items()
            if asset_class.is_open_at(t)
        ]

    def next_open(self, session: str) -> datetime:
        """
        Return the next UTC datetime at which *session* opens.

        The returned datetime is always timezone-aware (UTC) and is strictly
        in the future relative to the current moment.

        Parameters
        ----------
        session:
            A key from SESSION_HOURS (e.g. ``'us'``, ``'tokyo'``).

        Raises
        ------
        KeyError
            If *session* is not found in SESSION_HOURS.
        """
        if session not in SESSION_HOURS:
            raise KeyError(f"Unknown session {session!r}. "
                           f"Valid sessions: {list(SESSION_HOURS)}")

        open_utc, close_utc = SESSION_HOURS[session]
        now = datetime.now(timezone.utc)
        today = now.date()

        # Build a candidate datetime for today's open
        candidate = datetime(
            today.year, today.month, today.day,
            open_utc.hour, open_utc.minute, open_utc.second,
            tzinfo=timezone.utc,
        )

        # If the candidate is in the past, advance by one day
        if candidate <= now:
            candidate += timedelta(days=1)

        return candidate

    def is_market_open(self, asset_class: str = 'us_stocks') -> bool:
        """
        Convenience method: return True when *asset_class* is currently open.

        Parameters
        ----------
        asset_class:
            A key from ASSET_CLASSES.  Defaults to ``'us_stocks'``.
        """
        t = self._current_utc_time()
        ac = ASSET_CLASSES.get(asset_class)
        if ac is None:
            raise KeyError(f"Unknown asset class {asset_class!r}. "
                           f"Valid classes: {list(ASSET_CLASSES)}")
        return ac.is_open_at(t)


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

scheduler = MarketScheduler()
