# Draft enquiry to Kalshi — market-maker programme

**Status: SENT and ANSWERED (Session 35, 2026-09-28) — see
`kalshi-market-maker-inquiry-reply.md` and DECISIONS.md Session 35. The answer
was AI-composed and silent on eligibility, rate limits and cancel-on-disconnect.**
(Previously: final email text and recipient appended Session 34.) Written Session 32
(2026-08-02) so the market-making decision can be made with real information
instead of inference.

## Update, Session 34: a designated Market Maker Program already exists

Checked Kalshi's own Help Center (`help.kalshi.com`, article "How to Become a
Market Maker on Kalshi," dated 2026-04-28 — **before** this project's Session
30 measurement) while finalizing this draft. Kalshi already runs a formal,
named Market Maker Program: designated MMs agree to quote both sides at
defined size/uptime (published as "98% of each 1h increment" per covered
series) in exchange for reduced fees and adjusted position limits, subject to
review of "financial resources, trading experience, and business reputation."

**This matters for the 489-market opportunity estimate**: the covered-products
list already includes several series this project's Session 30 measurement
was counting as opportunity surface — `KXATPMATCH`, `KXWTAMATCH`, `KXMLB`,
`KXNBA`/`KXNBAGAME`, `KXNFLGAME`, `KXPGATOUR`, and others. If a designated MM
is already actively quoting 98%+ of the time on a series, that series'
measured spread/depth numbers from Session 30 may already reflect that MM's
presence rather than an open gap this project could fill — the 489-market
figure has not been re-cut against this list and should be before treating it
as the size of the opportunity. No public "how to apply" link or dedicated
program email was found on that page or elsewhere on Kalshi's site; the
question below asks Kalshi to route the enquiry, since there's no confirmed
better address than general support.

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

---

## Final email — ready to send, Session 34 (2026-09-27)

**To:** `support@kalshi.com`
*(Confirmed from Kalshi's own published AsyncAPI spec —
`https://docs.kalshi.com/asyncapi.yaml`, `info.contact.email` — as of this
session, the only Kalshi-published contact address found anywhere in their
docs, help center, or site. No dedicated market-maker program email or
application link exists publicly; the email below asks to be routed if a
different team owns this.)*

**Subject:** Market Maker Program — eligibility and terms enquiry

**Body:**

> Hi,
>
> I'm an individual trader running my own automated system on Kalshi
> (Phase 1, Kalshi-only, currently paper trading) and I'm evaluating whether
> to build toward market-making. Before investing in that build, I'd like to
> understand the actual Market Maker Program terms — I found the overview at
> help.kalshi.com ("How to Become a Market Maker on Kalshi") but it doesn't
> cover the specifics below. If this isn't the right inbox for these
> questions, I'd appreciate being pointed to the right team.
>
> 1. Is there a formal application process for the Market Maker Program, and
>    is it open to individual traders/small accounts, or does it require an
>    institutional entity or a minimum capital commitment? The help article
>    mentions review of "financial resources, trading experience, and
>    business reputation" — what does that review actually involve in
>    practice for a small applicant?
> 2. For a designated market maker, what are the specific quoting
>    obligations (minimum size, maximum spread, uptime/quoting-time
>    percentage) and what fee reductions or position-limit adjustments come
>    with meeting them?
> 3. What are the API rate limits for order placement, cancellation, and
>    amendment for a market maker specifically — are they different from a
>    standard participant's limits? Passive quoting means a high
>    cancel/replace rate, so this matters a lot to the design.
> 4. Is there a documented cancel-on-disconnect protection, or something
>    equivalent, for resting orders if a market maker's connection drops
>    unexpectedly?
> 5. Are there specific series where Kalshi is looking for more
>    market-making coverage right now, as opposed to series that already
>    have an active designated market maker?
>
> Thanks very much for your time — happy to provide any account details you
> need to route this properly.
>
> [Your name]

**Before sending:**
- Fill in a sign-off name/account identifier if Kalshi's team would need one
  to look up the account — do not include API keys, private key material, or
  any credential in the email itself (per the Notes section above).
- Consider sending from the email address tied to the Kalshi trading account,
  since a support inbox is likely to want to match the sender to an account
  on file.
- File Kalshi's reply in `documentation/` and summarize it in DECISIONS.md as
  primary source, per the existing convention in this file.
