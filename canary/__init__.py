"""S5a/S5b passive arbitrage canary -- detect and log only, never trade.

This package is a **research telemetry process**. It polls Kalshi's public REST
API, does arithmetic, and appends candidates to a JSONL file. It publishes
nothing to the event bus, places no orders, and is never imported by
``karbot_runner.py`` or any agent.

Why it is a separate process rather than an agent (decided Session 32):
``karbot_runner.py`` hosts ``PriceWatcher``, whose Kalshi WebSocket runs with
``ping_timeout=10s``. Session 23's confirmed outage was caused by adding a
blocking call inside a REST helper in that event loop -- it stalled the loop
past the ping deadline, Kalshi tore down the transport, and the agent crashed
three times in eight minutes until it exhausted its restart budget. This
package uses blocking ``requests`` deliberately, which makes it correct here
and unsafe there. Keeping it out of the trading process also means a bug in
research telemetry can never take down the price feed.

It imports ``backtest`` (offline analysis) for the fee model and settled-market
fetching. That direction is fine -- neither package is on the live trading path.
The rule that must not be broken is the other direction: the live path must
never import ``backtest`` or ``canary``. ``tests/test_canary_isolation.py``
enforces it.
"""

__all__ = ["kalshi_rest", "strikes", "economics", "qualify", "scan"]
