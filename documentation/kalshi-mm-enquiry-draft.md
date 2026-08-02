# Draft enquiry to Kalshi — market-maker programme

**Status: DRAFT, not sent.** Written Session 32 (2026-08-02) so the
market-making decision can be made with real information instead of inference.

## Why ask before building

Market-making (S8) is the largest remaining candidate and the largest new
subsystem this project would have built — a full live order-management layer
(place / cancel / amend / reconcile, order state machine, cancel-on-disconnect,
rate limits), all of it up front, and **unlike divergence it cannot be falsified
offline at all.** Every other strategy this project has considered could be
killed cheaply by a measurement first; this one cannot.

So the cheapest possible de-risking step is to ask the exchange what the terms
actually are, *before* committing to the build. Three of the four questions
below could change the answer materially, and all of them are free to ask.

The measured basis for the interest, from this project's own live data
(2026-08-02), so the enquiry is concrete rather than speculative:
- Kalshi's published maker multiplier defaults to **0**, so maker fees are $0
  outside the ~76 series enumerated in the fee schedule's Non-Standard Fees
  table. (Primary source: fee schedule effective 2026-07-07, in
  `documentation/kalshi-fee-schedule.pdf`.)
- **3,651 of 3,858** tradeable two-sided markets carry no maker fee, at a **2¢
  median spread**; **489** of those show ≥2¢ spread with ≥100 contracts resting
  on both sides.
- The fee-charging series (KXPGATOUR, KXMLBGAME) are both the highest-volume
  *and* the tightest at 1¢ — already professionally made. The zero-fee
  opportunity, if any, is in mid-volume series.

## The questions

1. **Is there a formal market-maker programme, and what are its terms?**
   Specifically: are there rebates, fee-tier reductions, or reduced-fee status
   beyond the published schedule's default multipliers? What are the
   obligations — minimum quote size, maximum spread, uptime//quoting-time
   requirements, per-series commitments?

2. **What are the eligibility requirements?** Is it open to individual
   participants and small accounts, or does it require an institutional entity,
   a minimum capital commitment, or registration as a professional participant?
   *(This is the question most likely to end the discussion, which is why it is
   worth asking first rather than last.)*

3. **What are the API rate limits for order placement, cancellation and
   amendment**, and do they differ for participants in the programme? Passive
   quoting means a high cancel/replace rate, and a limit that is comfortable for
   a taker can be binding for a maker. Are there separate limits for orders
   versus market-data reads?

4. **Is there a documented cancel-on-disconnect or similar protection?** If the
   WebSocket drops while quotes are resting, what happens to them? This project
   has had a confirmed multi-hour feed outage (Session 19) and a
   crash-loop-to-permanent-stop (Session 23), so "what happens to my resting
   orders when my process dies" is not hypothetical here.

## Secondary, only if the above is encouraging

5. Are there series where Kalshi actively wants more liquidity — i.e. is there
   a published or informal list of under-served markets?
6. Does the maker multiplier table change often, and is there notice? A series
   moving from multiplier 0 to 1 would invert the economics of quoting it.

## Notes for whoever sends this

- Send as a straightforward participant enquiry. There is no need to describe
  the system in detail, and no reason to.
- **Do not send API keys, account identifiers, or private key material** in any
  correspondence. Nothing in these questions requires them.
- Answers should be filed in `documentation/` and summarised in DECISIONS.md —
  and treated as **primary source**, unlike the secondary sources that produced
  the retracted fee correction in Session 30. Standing lesson: agreement among
  secondary sources is not confirmation.

## What each answer would change

| answer | consequence |
|---|---|
| Programme is institution-only | Market-making effectively closed; the 489-market surface stays theoretical. Direction question narrows to the other candidates. |
| Open, with quoting obligations | Obligations become hard requirements on the order layer's design — uptime and cancel-on-disconnect stop being nice-to-haves. |
| Rebates on top of $0 maker fees | Materially improves the case, and would justify the order-layer build on its own. |
| Order rate limits are tight | Constrains quoting frequency, which constrains inventory management, which is the whole risk model. Needs to be known **before** the design, not after. |
