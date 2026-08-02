# Candidate Signal Register

Standing list of data sources that *might* carry predictive signal for markets
this system trades, plus the methodology any of them must pass before it is
allowed to influence a position.

Created Session 30 (2026-08-02) at the operator's direction. Nothing here is
endorsed, and nothing here is dismissed. The register exists so that ideas are
written down, prioritised, and tested rather than argued about.

---

## The governing principle

From the World Intelligence vision doc, and worth keeping verbatim in spirit:

> The system never decides in advance that information is irrelevant.
> Everything is potentially signal. The job is to discover which signals
> actually predict outcomes.

The operator's framing, recorded because it is the right one: *use any tangible
data, sans politics and bias, like a real objective trader should. Awareness and
an open mind keep one able to make connections not anticipated.*

Agreed, with one hard condition attached below.

---

## THE METHODOLOGY GATE — read this before adding or testing anything

The danger with a large candidate-signal list is not that any individual idea
is silly. It is **multiple comparisons**. Testing a few hundred exotic signals
against a small sample will produce "statistically significant" relationships
**by construction**, even if every single signal is pure noise. At p < 0.05,
one test in twenty passes by chance alone.

**The budget is much smaller than it looks — MEASURED, Session 31, correcting
this section's original estimate.** This document first put the sample at
"roughly 800–5,000 settled market outcomes." The market count is real (7,565
settled daily-high markets as of 2026-08-02), but **almost none of it is
independent information**:

- A city-day is a ladder of ~6 markets forming an *exhaustive partition* —
  confirmed on all 1,261 city-days, exactly one YES each. Six outcomes, one
  temperature. Not six observations.
- Every city on a given calendar date shares one synoptic weather pattern, so
  even city-days are correlated across cities within a date.

So the honest independent unit is the **date**, and there are **71 of them**
(2026-05-22 → 2026-08-01) — not thousands. Treating markets as independent
shrinks a confidence interval by roughly a factor of nine and converts noise
into a publishable edge. Every number in `backtest/` bootstraps whole dates for
exactly this reason, and any future test on this register must do the same.

**Practical consequence: at n≈71 independent observations, the register can
support very few hypotheses, not hundreds.** Spend the budget accordingly, and
count every hypothesis tested including the failures.

This is not a hypothetical failure mode for this project. It is the same shape
as the failure that cost it three months: S1 produced a consistent, plausible,
hand-verifiable stream of "profitable" signals for a year, and every one was an
artifact. **A machine that generates confident findings from noise is worse
than no machine**, because it is expensive to disbelieve.

So every candidate here must clear the guardrails the vision doc itself
specified (Correlation Engine section) — these are the load-bearing part of
that design, not the ambition:

1. **Bonferroni (or FDR) correction** for the total number of signals tested.
   Count every hypothesis tested, including the ones that failed.
2. **Minimum 20 independent market resolutions** before a relationship is even
   provisionally considered.
3. **Replication across 3 separate time periods.** A relationship that holds in
   one window and not the others is noise.
4. **Minimum 2-hour lead** between signal and outcome, to rule out reverse
   causation.
5. **Out-of-sample by default.** Fit on one period, test on another, and report
   the out-of-sample number — never the in-sample one.
6. **The baseline is always the market price, never a coin flip.** "Better than
   chance" is worthless. The only question that pays is "better calibrated than
   what Kalshi already prices," measured by Brier score.

A signal that passes all six gets a Source Credibility Score and a small
weight. A signal that fails goes on the "tested, no edge" list below — which is
as valuable as the pass list, because it stops the same idea being re-litigated
every six months.

**Prioritisation rule:** the multiple-comparisons budget is finite, so spend it
where the prior is highest — signals with a *plausible physical path* to the
settlement quantity get tested first. This is not a judgment about which ideas
are respectable; it is arithmetic about how many tests the sample can support.

---

## Tier A — official, high-reliability, directly causal (test first)

These have a real mechanism connecting them to what Kalshi weather markets
actually settle on, and they come from official registries.

| Source | What it gives | Access | Why it could matter |
|---|---|---|---|
| **NOAA Weather Modification Project Reports** | Legally-required filings for all non-federal US weather-modification activity | Public repository, updated quarterly | **The strongest item on this list.** Federal law requires operators to file **at least 10 days BEFORE** activity commences. That is genuine, official, public *advance notice of planned weather modification* — cloud seeding is a real, legal, funded industry, and a seeding programme in a catchment area has a direct physical path to precipitation outcomes. No conspiracy framing needed; this is a government registry. |
| **State cloud-seeding programme pages** (e.g. Idaho Dept. of Water Resources) | Monthly summaries, season reports, operational windows | Public | Operational detail the federal registry summarises. Directly relevant to KXRAIN/KXSNOW-type markets. |
| **NOAA NBM archive** — **TESTED, NO EDGE (Session 31)** | Forecast mean + spread + quantiles, per station, in plain ASCII (`text/` suite, no GRIB decoder) | AWS S3, anonymous | Still the correct baseline model and the cheapest NOAA point-forecast route. But as a *tradeable divergence signal* against Kalshi it FAILED — see "Tested — no edge found". |
| **NWS climatological reports** | The actual settlement values | Not needed — Kalshi's own `expiration_value` **is** the settled observation. For cross-checks use IEM `json/cli.py`, **not** `cgi-bin/request/daily.py`, which disagrees by a degree. | Ground truth for scoring. |

**Note on the timing asymmetry**: the federal registry updates *quarterly*,
which is far too slow to trade on directly. The 10-day advance-filing
requirement is the interesting part, but only if filings are visible when
submitted rather than at quarterly publication. **Unverified — check this
before building anything on it.** State-level pages may be timelier.

## Tier B — physical / astronomical / geophysical

Free, official, machine-readable. Weak or absent priors for daily surface
temperature, but cheap to test and some have real meteorological literature.

- **Solar / geomagnetic activity** (NOAA SWPC) — solar flares, Kp index,
  aurora. Real effects on the ionosphere and HF radio; **no established
  mechanism for next-day surface temperature.** Low prior, near-zero cost.
- **Lunar phase, tides, syzygy** — tidal effects on the atmosphere are real but
  tiny; there is a scattered academic literature on lunar-cycle market
  volatility (mostly weak and poorly replicated). Free to compute, no API
  needed.
- **USGS seismic activity**, geyser behaviour, volcanic eruptions — volcanic
  aerosol loading has a *genuine, documented* climate effect at large scale
  (Pinatubo), though on a seasonal-to-annual timescale, not a next-day one.
  Worth including specifically for large-eruption regime shifts.
- **Meteor showers / atmospheric entry**, rocket launches and re-entries
  (SpaceX and global) — launch schedules are public and precise. Plausible
  local, short-lived effects (launch plumes, noctilucent clouds); no credible
  path to a city's daily high temperature. Cheap.
- **EIA energy data, power-grid load** — this one has the arrow *reversed*
  (weather drives load), which makes grid load a potential **nowcast** of
  observed conditions rather than a forecast. Possibly useful for
  intraday markets where settlement is partly determined but not yet public.

## Tier C — crowd-sourced claim data

The sources the operator listed: ChemTrail.app, ChemTracker, Chemtrail Tracker,
Chemtrail Watch, GeoEngineeringWatch.org.

Handled without editorialising in either direction, because the honest
technical description is enough to determine how to use them:

- These are **claim data, not measurement data.** A record says "someone
  reported seeing X at this place and time." That is a real, timestamped,
  geolocated observation *of a claim*, and claim data is legitimately testable.
- They are therefore **attention/sentiment-class signals**, which the vision
  doc already anticipates (Tier 2 Behavioural & Attention Intelligence) and
  already has the right machinery for: the **Source Credibility Score** system,
  which weights every source by measured per-topic accuracy and gives new
  sources a 30-day shadow-mode period at zero weight.
- The correct treatment is exactly the SCS treatment: log them, score them
  against outcomes, keep whatever predicts, drop whatever doesn't. Same rule
  applied to a wire service.
- **Practical caveat**: several are commercial apps with Pro tiers and unclear
  terms; check licensing and rate limits before automated scraping, and prefer
  ADS-B Exchange / adsb.fi directly for the underlying flight telemetry, which
  is raw, unmediated, and already in the vision doc's Tier 3.
- **Confounder to control for explicitly**: persistent-contrail formation is
  itself driven by upper-atmosphere humidity and temperature. So contrail
  reports will correlate with weather *because both respond to the same
  atmospheric state* — this is a textbook common-cause confound, and any
  apparent predictive relationship must be tested against an NBM-conditioned
  baseline before it means anything. Without that control, this signal will
  look predictive and won't be.

## Tier D — traditional / folk methods

- **Farmer's Almanac** — genuinely testable and worth including precisely
  because it makes *specific, dated, public forecasts well in advance*. Score
  it exactly like any other source: Brier against outcome, versus the market
  baseline. Published studies have generally found accuracy near chance, but
  this project's own rule applies: measure rather than assume. Cheap to test.
- Regional folk indicators (persimmon seeds, woolly-bear caterpillars, etc.) —
  not machine-readable, no collection path, skip unless someone finds a
  structured dataset.

## Tier E — non-weather, for later strategies

Carried from the vision docs so they aren't lost, not relevant until a
non-weather strategy exists: congressional STOCK Act filings, SEC EDGAR,
FOIA/MuckRock releases, ADS-B corporate-jet movements, Google Trends,
Wikipedia edit velocity and page traffic, Metaculus/Manifold cross-market
divergence, options-implied probability, COT reports, ACLED conflict data.

---

## Tested — no edge found

### NOAA/NBM temperature forecast → Kalshi daily-high markets — FAILED, Session 31 (2026-08-02)

**Do not re-propose without new information about why this would now differ.**
Full detail: DECISIONS.md Session 31; raw output in `backtest/reports/`.

| Gate | Result |
|---|---|
| 1. Multiple-comparisons correction | Applied — 18 cities, uncorrected per-city table published with the Bonferroni threshold stated. Not needed in the end: 17 of 18 lose. |
| 2. ≥20 independent resolutions | **1,261 city-days over 71 dates.** Far exceeded. |
| 3. Replication across periods | Consistent across 3 lead times (12/24/30h) and both halves of the sample. |
| 4. ≥2h lead | 12h minimum — and 12h is the shortest lead NOAA publishes a daytime max for at all. |
| 5. Out-of-sample | Yes. Zero-parameter model and a 3-parameter model fitted on the first 35 dates, scored on the last 36. Both lose. |
| 6. **Baseline is the market price** | **This is where it fails.** Brier 0.2013 (model) vs **0.1757 (market)** at 12h; skill −0.146; P(model no better) = 1.000. |

**The reason, measured rather than inferred:** the market's *implied point
forecast* is ~20% more accurate than NBM's (MAE 1.27 °F vs 1.59 °F at 12h
lead), while NBM's published uncertainty is close to correct (published SD /
realised RMSE = 0.93). So the failure is in the forecast, not in the
probability conversion — a better error model cannot recover it.

**The generalisable lesson, and the reason this entry matters beyond weather:**
a divergence signal needs a source with a *plausible informational advantage
over the market before it is measured*. A free, public, official forecast that
every participant can read is the weakest possible candidate — and Kalshi
weather markets were chosen in Session 30 precisely because the forecast source
and the settlement source are the same agency, which is the very property that
guarantees every other participant is reading it too. **Add "is there a reason
the market does not already know this?" as a screening question before any
future candidate on this register consumes multiple-comparisons budget.**

One diagnostic worth reusing: the model claimed +$0.11 to +$0.17 net EV per
contract and realised −$0.01 to −$0.04, and **tightening the divergence
threshold made it worse**. Demanding a larger disagreement with the market
selected harder for the model being wrong. That inverted relationship is a
cheap, general test of which side is the informed one.

**Not invalidated by this**: the `FairValueProvider` abstraction, the divergence
strategy shape, or any other source on this register. One provider on one market
family failed, for a legible reason.

## Tested — edge confirmed

*(Empty. Nothing has passed the gate above, because nothing has been tested
yet. Do not add anything here without the out-of-sample number, sample size,
and replication periods attached.)*

---

## Standing note

The operator's instinct here is right, and worth stating plainly: an
unconventional data source is not disqualified by being unconventional, and a
respectable one is not qualified by being respectable. Track record decides.

The only discipline that has to be non-negotiable is the statistics — because
the failure mode of an open mind, applied to a large signal space and a small
sample, is not "wasting time on odd ideas." It is **confidently trading noise**,
which is exactly what this project spent a year doing with S1. Open mind, hard
gate.
