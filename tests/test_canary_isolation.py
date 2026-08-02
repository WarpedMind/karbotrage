"""
tests/test_canary_isolation.py

The live trading path must never import ``canary`` or ``backtest``.

Both are offline/research code with different constraints from the trading
process. ``canary`` uses blocking ``requests`` deliberately -- correct in its own
process, and the exact shape of the Session 23 outage if it ever ran inside
``karbot_runner.py``'s event loop (a blocking call there stalled the loop past
the WebSocket's 10s ping deadline, Kalshi tore down the transport, and
PriceWatcher crashed three times in eight minutes until its restart budget was
exhausted). ``backtest`` exists so that adding an analysis dependency never
lands on the VPS.

The dependency direction that IS allowed: ``canary`` imports ``backtest`` (for
the fee model and settled-market fetching). Neither is on the trading path, so
that costs nothing.

This is a static source check rather than an import-graph check on purpose -- it
catches the mistake at the point it is written, without importing the live
agents.
"""

import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

LIVE_PATH_ROOTS = ("agents", "karbot", "core", "execution", "data", "strategies",
                   "trading", "monitoring", "intelligence")
LIVE_PATH_FILES = ("karbot_runner.py", "main.py")

FORBIDDEN = re.compile(r"^\s*(?:from|import)\s+(canary|backtest)\b", re.MULTILINE)


def _live_path_sources():
    for name in LIVE_PATH_FILES:
        path = PROJECT_ROOT / name
        if path.exists():
            yield path
    for root in LIVE_PATH_ROOTS:
        base = PROJECT_ROOT / root
        if base.is_dir():
            yield from base.rglob("*.py")


def test_live_path_never_imports_research_packages():
    offenders = []
    checked = 0
    for path in _live_path_sources():
        checked += 1
        text = path.read_text(encoding="utf-8", errors="replace")
        for match in FORBIDDEN.finditer(text):
            offenders.append(f"{path.relative_to(PROJECT_ROOT)}: {match.group(0).strip()}")
    assert checked > 0, "found no live-path sources to check -- the guard is vacuous"
    assert not offenders, "live trading path imports research code: " + "; ".join(offenders)


def test_canary_may_import_backtest():
    """The allowed direction, asserted so nobody 'fixes' it the wrong way."""
    text = (PROJECT_ROOT / "canary" / "economics.py").read_text(encoding="utf-8")
    assert "from backtest.costs import" in text
