# backtest/venues — offline venue-screen probes (Session 38)

Read-only probes of venues other than Kalshi's event contracts, written for the
Session 38 venue screen. Public endpoints only, no auth, no accounts. **Never
imported by the live path.** Stdlib plus `requests`, as elsewhere in `backtest/`.
Authoritative record: DECISIONS.md Session 38 (screen) and Session 39 (H11 and H10 results).

| module | what it answers | output |
|---|---|---|
| `polymarket_us_census` | Polymarket US universe by category, open vs closed; whether settled markets carry quote history | `reports/venue_polymarket_us_census.json` |
| `weather_cross_snapshot` | Live Kalshi vs Polymarket US daily-high ladders on the same NWS station; cheapest guaranteed-$1 basket | `reports/venue_weather_cross_snapshot.txt` |
| `kalshi_perps_funding` | Funding history summary for every Kalshi perp (mean, share positive/zero, last 30 days, spread, volume) | `reports/venue_kalshi_perps_funding.txt` |
| `h11_fetch`, `h11_rule`, `h11_analyze` | **H11 (Session 39):** Kalshi ↔ Polymarket US same-station weather crossings over 2026-04-22 → 10-17, as pre-registered | `reports/venue_h11_crossings.txt` |
| `h10_rule`, `h10_carry` | **H10 (Session 39):** BTC/ETH perp funding carry vs T-bills; in-sample half only (contaminated) | `reports/venue_h10_carry_insample.txt` |

Choices declared before any Session 39 result: `reports/session39_declared_before_results.txt`.
Rule tests: `tests/test_backtest_h11_rule.py`, `tests/test_backtest_h10_rule.py`.

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
7. **Funding sign convention is not documented in the API spec** (premium =
   book above index ⇒ positive). *Confirmed in Session 39 from Kalshi's help
   center; see trap 16.*
8. **The perps fee schedule in `documentation/kalshi-fee-schedule.pdf` is dated
   2026-07-07**; `kalshi.com` returns 429 to scripts, so its currency is
   unconfirmed.
9. **Coinbase's public perp list with funding rates is Coinbase International**
   (`-INTX`), which US persons cannot use. The US perp-style products
   (`BIP-20DEC30-CDE` etc.) show a blank `funding_rate` in the public API.
10. **The 0.5 listing seed outlives the first quote** (Session 39). A new
    Polymarket US market starts at `longPrice` 0.5 / `shortPrice` 0.5. When
    the first ask arrives `longPrice` moves, but `shortPrice` can sit at exactly
    0.5 for hours until someone bids, so it reads as a 50¢ bid that never
    existed. Requiring bid < ask removes only the locked case. In H11 this
    seed produced 54% of the binding locked profit. Treat a `shortPrice` that
    has been exactly 0.5 since listing as "no bid".
11. **`1 − 0.99` is 0.01000000000000001 in floating point**, so a naive
    `bid > 0.01` lets the 1¢ placeholder through. Round the derived bid
    (`h11_rule.pm_quote`).
12. **Some Polymarket US titles carry a degree sign** ("56° to 57°"); 40 of
    the climate rungs. The title parser strips it.
13. **The same NWS CLI can be issued twice with different highs, and the two
    venues can settle on different issuances.** KMIA 2026-08-29: the 4:24 AM
    EDT report said 90 °F, a 5:10 AM re-issue said 85 °F (both headed "final",
    neither marked corrected). Kalshi settled at 90, Polymarket US on 85. A
    cross-venue "guaranteed" basket on those rungs could lose on both legs.
    List every issuance with IEM's `api/1/nws/afos/list.json?pil=CLIMIA`.
14. **Polymarket US's gateway bans unpaced scripts** with Cloudflare error 1015
    (a temporary IP ban), not a 429. Keep every caller on a shared pacer
    (`h11_fetch.pm_get`, ≤14 req/s).
15. **Kalshi perp candles skip one hour every Thursday** (08:00 → 10:00 UTC,
    maintenance). Monday decision and entry hours are unaffected.
16. **The funding sign is confirmed** (Session 39) from Kalshi's help center
    article "How Funding Works" (dated 2026-06-03, saved in
    `backtest/cache/venues/primary_sources/`): a positive rate means longs pay
    shorts. That supersedes trap 7. The rulebook PDF itself was not read
    (kalshi.com returns 429 to scripts).
