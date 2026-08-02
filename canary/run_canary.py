"""Long-running S5a/S5b canary: sweep, log, sleep, repeat.

Detect and log only. This process never publishes an event, never sizes a
position, and never places an order. Its output is a JSONL file whose value is
*frequency over weeks* -- Session 29 checked S5a/S5b in a single snapshot, found
nothing, and correctly noted that a snapshot cannot rule out rare windows during
volatility or thin off-hours trading. This is the instrument that answers that.

Run:
    karbotrage_env/bin/python -m canary.run_canary --once
    karbotrage_env/bin/python -m canary.run_canary --interval-seconds 300

Every sweep writes a ``record: "sweep"`` line whether or not it found anything.
That line doubles as the heartbeat -- ``tail -1`` on the output file answers
"is this still alive and when did it last run", which is the one thing a
separate process loses by not living inside ``karbot_runner.py``'s supervision.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
import time
from typing import Optional

from canary import kalshi_rest
from canary.economics import assert_fee_model_agrees
from canary.qualify import ProfileStore
from canary.scan import sweep

DEFAULT_OUT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "logs",
    "basket_candidates.jsonl",
)

# A courtesy interval against Kalshi's public API, not a measured optimum. A
# full sweep of the open universe takes a few seconds; this is the gap between
# sweeps. Tune it from the observed 429 rate, not from a guess about how often
# arbitrage appears.
DEFAULT_INTERVAL_SECONDS = 300

log = logging.getLogger("canary")

_STOP = False


def _handle_signal(signum, _frame):
    global _STOP
    _STOP = True
    log.info("stop_requested signal=%s -- finishing current sweep", signum)


def _append(path: str, record: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, sort_keys=True) + "\n")
        fh.flush()


def run_once(
    out_path: str,
    store: ProfileStore,
    session,
    *,
    reconfirm_candidates: bool = True,
) -> dict:
    candidates, report = sweep(
        session=session, store=store, reconfirm_candidates=reconfirm_candidates
    )
    for candidate in candidates:
        record = candidate.to_dict()
        record["record"] = "candidate"
        _append(out_path, record)

    problem = report.check()
    if problem:
        # Loud, not fatal: the sweep still happened and its candidates are real,
        # but the totals not adding up is exactly the shape of Session 31's
        # silent-omission bug and must never scroll past unnoticed.
        log.error("sweep_reconciliation_failed %s", problem)
        report.errors.append(problem)

    summary = report.to_dict()
    _append(out_path, summary)
    store.save()
    log.info(
        "sweep events_seen=%s evaluated=%s series=%s candidates=%s confirmed=%s "
        "vanished=%s duration_s=%s",
        report.events_seen,
        report.events_evaluated,
        report.series_seen,
        report.candidates or {},
        report.confirmed or {},
        report.vanished or {},
        report.duration_s,
    )
    return summary


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description="S5a/S5b passive arbitrage canary")
    parser.add_argument("--once", action="store_true", help="one sweep, then exit")
    parser.add_argument(
        "--interval-seconds", type=int, default=DEFAULT_INTERVAL_SECONDS
    )
    parser.add_argument("--out", default=DEFAULT_OUT)
    parser.add_argument(
        "--max-sweeps", type=int, default=0, help="0 means run until stopped"
    )
    parser.add_argument(
        "--no-reconfirm",
        action="store_true",
        help="skip the order-book re-check (diagnostic only -- snapshot "
             "candidates are not trustworthy on their own)",
    )
    parser.add_argument(
        "--profile-max-age-days",
        type=float,
        default=7.0,
        help="rebuild a series profile older than this",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    mismatch = assert_fee_model_agrees()
    if mismatch:
        log.error("fee_model_disagrees_with_live_path %s", mismatch)
    else:
        log.info("fee_model_checked agrees_with_live_path_or_not_importable=True")

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    session = kalshi_rest.make_session()
    store = ProfileStore(max_age_days=args.profile_max_age_days)

    sweeps = 0
    while not _STOP:
        try:
            run_once(
                args.out,
                store,
                session,
                reconfirm_candidates=not args.no_reconfirm,
            )
        except Exception as exc:  # noqa: BLE001 - a canary must outlive its own bugs
            log.exception("sweep_failed %s", exc)
            _append(
                args.out,
                {"record": "sweep_error", "error": str(exc), "ts": time.time()},
            )
        sweeps += 1
        if args.once or (args.max_sweeps and sweeps >= args.max_sweeps):
            break
        for _ in range(args.interval_seconds):
            if _STOP:
                break
            time.sleep(1)

    log.info("canary_stopped sweeps=%s", sweeps)
    return 0


if __name__ == "__main__":
    sys.exit(main())
