"""C10 decision rule, frozen in DECISIONS.md Session 37 before any outcome was seen.

Pure functions only (no I/O) so the rule can be pinned by unit tests. Every
temperature here is degrees F, every timestamp a UTC epoch second.

The rule, in one paragraph: at each hourly candle, take the station's running
maximum for the climate day (local *standard* time) from METARs timestamped at
least 10 minutes before the candle closes. A ladder rung whose outcome that
running max already settles is "decided"; buy the decided side at its ask if
the book is two-sided. One entry per market, at the first qualifying candle.
"""

from __future__ import annotations

import re
from bisect import bisect_right
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

OBS_LAG_SECONDS = 600  # an observation must be >= 10 min older than the candle close
H8_LOCAL_HOUR = 19     # H8 only looks at candles ending at or after 19:00 LST
H8_MARGIN_F = 3.0      # H8: YES region must start >= running max + 3 F

# Local STANDARD time offsets (hours from UTC). The NWS CLI climate day ignores
# daylight saving, and each market's close_time is 23:59 LST, which agrees.
STATION_STD_OFFSET = {
    "KAUS": -6, "KMDW": -6, "KDEN": -7, "KLAX": -8, "KMIA": -5, "KNYC": -5,
    "KPHL": -5, "KATL": -5, "KBOS": -5, "KDFW": -6, "KDCA": -5, "KHOU": -6,
    "KLAS": -8, "KMSP": -6, "KMSY": -6, "KOKC": -6, "KSAT": -6, "KSFO": -8,
}

_TGROUP = re.compile(r"\bT([01])(\d{3})([01])(\d{3})\b")
_BODY = re.compile(r"\s(M?\d{2})/(M?\d{2})?\s")


def c_to_f(c: float) -> float:
    return c * 9.0 / 5.0 + 32.0


def metar_temp_f(metar: str) -> Optional[float]:
    """Temperature from one METAR, in F, unrounded.

    Uses the remark T group (tenths of a degree C) when present. Otherwise the
    body temperature in whole C minus 0.5 C, which is a lower bound on the true
    reading -- the rule must only ever under-state the running max, never
    over-state it.
    """
    m = _TGROUP.search(metar)
    if m:
        sign = -1.0 if m.group(1) == "1" else 1.0
        return c_to_f(sign * int(m.group(2)) / 10.0)
    b = _BODY.search(" " + metar + " ")
    if b:
        t = b.group(1)
        c = -int(t[1:]) if t.startswith("M") else int(t)
        return c_to_f(c - 0.5)
    return None


def climate_day_start_utc(local_day_ordinal_epoch: int, std_offset_hours: int) -> int:
    """UTC epoch of local-standard midnight, given the epoch of that date at 00:00 UTC."""
    return local_day_ordinal_epoch - std_offset_hours * 3600


@dataclass(frozen=True)
class Rung:
    strike_type: str              # greater | less | between
    floor_strike: Optional[float]
    cap_strike: Optional[float]

    def yes_lower(self) -> Optional[float]:
        """Lowest settled high that makes YES true; None if unbounded below."""
        if self.strike_type == "greater":
            return self.floor_strike + 1
        if self.strike_type == "between":
            return self.floor_strike
        return None

    def decided_side(self, running_max: Optional[float]) -> Optional[str]:
        """'yes' / 'no' if the running max already settles this rung, else None.

        Kalshi conventions (Session 31, replayed against every settlement):
        ``greater`` f -> YES iff high >= f+1; ``less`` c (threshold in
        cap_strike!) -> YES iff high <= c-1; ``between`` f..c -> YES iff
        f <= high <= c. The settled high is a whole degree and can only be at
        or above the running max, so these are the only sides it can decide.
        """
        if running_max is None:
            return None
        if self.strike_type == "greater" and running_max >= self.floor_strike + 1:
            return "yes"
        if self.strike_type == "between" and running_max >= self.cap_strike + 1:
            return "no"
        if self.strike_type == "less" and running_max >= self.cap_strike:
            return "no"
        return None


def two_sided(yes_bid: Optional[float], yes_ask: Optional[float]) -> bool:
    return yes_bid is not None and yes_ask is not None and 0 < yes_bid < yes_ask < 1


def ask_for(side: str, yes_bid: float, yes_ask: float) -> float:
    """Price to BUY ``side``: the YES ask, or 1 - YES bid for NO."""
    return yes_ask if side == "yes" else 1.0 - yes_bid


def taker_fee(p: float) -> float:
    return 0.07 * p * (1.0 - p)


def net_return(p: float, won: bool, fee: Optional[float] = None) -> float:
    f = taker_fee(p) if fee is None else fee
    return ((1.0 if won else 0.0) - p - f) / (p + f)


class RunningMax:
    """Running max of observations, queried as of a time, with the lag rule."""

    def __init__(self, obs: Sequence[Tuple[int, float]], day_start: int):
        pts = sorted((t, v) for t, v in obs if t >= day_start)
        self._t: List[int] = []
        self._m: List[float] = []
        cur = None
        for t, v in pts:
            cur = v if cur is None else max(cur, v)
            self._t.append(t)
            self._m.append(cur)

    def as_of(self, candle_end: int) -> Optional[float]:
        i = bisect_right(self._t, candle_end - OBS_LAG_SECONDS)
        return self._m[i - 1] if i else None


@dataclass
class Entry:
    ts: int
    side: str
    price: float


def first_entry_h7(rung: Rung, candles, rmax: RunningMax, close_ts: int) -> Optional[Entry]:
    """``candles``: iterable of (end_ts, yes_bid, yes_ask), any order."""
    for ts, yb, ya in sorted(candles):
        if ts > close_ts:
            break
        if not two_sided(yb, ya):
            continue
        side = rung.decided_side(rmax.as_of(ts))
        if side:
            return Entry(ts, side, ask_for(side, yb, ya))
    return None


def first_entry_h8(rung: Rung, candles, rmax: RunningMax, close_ts: int,
                   day_start: int) -> Optional[Entry]:
    lo = rung.yes_lower()
    if lo is None:
        return None
    for ts, yb, ya in sorted(candles):
        if ts > close_ts:
            break
        if ts < day_start + H8_LOCAL_HOUR * 3600 or not two_sided(yb, ya):
            continue
        m = rmax.as_of(ts)
        if m is not None and lo >= m + H8_MARGIN_F:
            return Entry(ts, "no", ask_for("no", yb, ya))
    return None
