# backtest/venues — offline venue-screen probes (Session 38)

Read-only probes of venues other than Kalshi's event contracts, written for the
Session 38 venue screen. Public endpoints only, no auth, no accounts. **Never
imported by the live path.** Stdlib plus `requests`, as elsewhere in `backtest/`.
Authoritative record: DECISIONS.md Session 38.

| module | what it answers | output |
|---|---|---|
| `polymarket_us_census` | Polymarket US universe by category, open vs closed; whether settled markets carry quote history | `reports/venue_polymarket_us_census.json` |
| `weather_cross_snapshot` | Live Kalshi vs Polymarket US daily-high ladders on the same NWS station; cheapest guaranteed-$1 basket | `reports/venue_weather_cross_snapshot.txt` |
| `kalshi_perps_funding` | Funding history summary for every Kalshi perp (mean, share positive/zero, last 30 days, spread, volume) | `reports/venue_kalshi_perps_funding.txt` |

Run any of them as `karbotrage_env/bin/python -m backtest.venues.<module>`.

## Endpoints (all public)
- Polymarket US: `https://gateway.polymarket.us` — `/v1/markets`
  (`categories`, `limit`, `offset`, `endDateMin`), `/v1/markets/{slug}/bbo`,
  `/v1/markets/{slug}/settlement`, `/v1/price-history` (`symbol`=slug;
  `fixedInterval` profiles, or a custom `timestamp.startTimestamp`/`endTimestamp`
  window of ≤24h at `fidelity=1`). Public limit 20 req/s/IP.
- Kalshi perps: `https://external-api.kalshi.com/trade-api/v2/margin` —
  `/markets`, `/markets/{ticker}/candlesticks` (`period_interval` 1/60/1440; bid
  and ask OHLC), `/funding_rates/historical` (`ticker`, `start_ts`, `end_ts`).
  Note the different host from event contracts (`api.elections.kalshi.com`).
- ForecastEx: `https://forecastex-public-data.s3.amazonaws.com` — `prices/`
  (daily OHLC, settlement, VWAP since 2024-08-01), `pairs/` (every matched pair),
  `daily_summary/`, `liquidity_rewards/`. Trades and closes only, **no quotes**.

## Traps
1. **Polymarket US weather slugs are off by one degree.** `gte60lt61f` is titled
   "60 to 61" and includes 61. Parse the `title`, never the slug
   (`weather_cross_snapshot.pm_interval`, pinned by `tests/test_backtest_venues.py`).
2. **A 1¢ placeholder stands in for a missing bid** in `/v1/price-history`:
   with no bid, `shortPrice` reads 0.99. When the book is two-sided, history
   equals live `/bbo` exactly (12 of 12 checked).
3. **`closed=true` does not partition the Polymarket US universe**; it returned
   only sports. Page by `categories` and read each market's `closed` field.
4. **Closed non-sports Polymarket US markets are mostly short-dated crypto**
   (130k+), which have no stored price history. Sample by category.
5. **The Polymarket US weather FAQ lists five stations; six cities trade** (PHL
   is missing from the table).
6. **Kalshi perps funding has a deadband** (1 bp per 8h on crypto, 0.2 bp on
   metals and indices); about half of all crypto periods pay exactly zero.
7. **Funding sign convention is inferred, not documented** in the API spec
   (premium = book above index ⇒ positive). Confirm it from Kalshi's perps
   rulebook before any P&L is computed.
8. **The perps fee schedule in `documentation/kalshi-fee-schedule.pdf` is dated
   2026-07-07**; `kalshi.com` returns 429 to scripts, so its currency is
   unconfirmed.
9. **Coinbase's public perp list with funding rates is Coinbase International**
   (`-INTX`), which US persons cannot use. The US perp-style products
   (`BIP-20DEC30-CDE` etc.) show a blank `funding_rate` in the public API.
