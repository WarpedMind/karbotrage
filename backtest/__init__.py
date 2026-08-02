"""Offline backtesting / calibration harness.

NOTHING in this package may be imported by the live trading path. It exists to
answer one question before any strategy code is written: is an external model,
converted to a probability, better calibrated than the Kalshi price itself?

Deliberately stdlib-only (plus ``requests``, already a project dependency).
Adding numpy/scipy/pandas/matplotlib to a live trading bot's requirements for
the sake of an offline report is dependency bloat that would land on the VPS;
the maths here is small enough to write out explicitly, and writing it out
makes the continuity corrections and the clustering assumptions visible rather
than buried in a library call.
"""
