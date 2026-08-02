"""
tests/test_backtest_nbm_parser.py

Covers backtest/nbm_text.py -- the NBM station-bulletin parser added in Session
31 as the forecast leg of the S6 calibration harness.

Why a fixture and not a live fetch: these tests must fail when the PARSER
breaks, not when NOAA is slow or the archive rotates. The fixture below is a
verbatim excerpt of real bulletins pulled from
noaa-nbm-grib2-pds.s3.amazonaws.com on 2026-08-02 (blend.20260701/12/text/),
trimmed to two stations and truncated in width -- the column geometry, the
row labels and the value alignment are unmodified.

The specific hazards under test, each of which was a real defect during the
build:

* Values are right-aligned on a grid derived from the FHR row, and the day
  separators are "|" characters sitting between columns. A naive fixed-width
  split gets the 3-digit and negative values wrong.
* Some NBS rows (VIS, SLV, CIG, LCB) pack 3-digit values with no separator and
  bleed one character to the left of the grid, so a fixed-width label slice
  produced the label "VIS 1" instead of "VIS".
* TXN/TXNMN appear only on alternating columns (00Z max, 12Z min) with blanks
  in between; the blanks must parse as None, not 0.
"""

import datetime as dt
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backtest import nbm_text  # noqa: E402


NBS_FIXTURE = """
1

 KLAX    NBM V5.0 NBS GUIDANCE    7/01/2026  1200 UTC
 DT /JUL   1/JUL   2                /JUL   3
 UTC  18 21 00 03 06 09 12 15 18 21 00
 FHR  06 09 12 15 18 21 24 27 30 33 36
 TXN        69          59          70
 XND         1           1           1
 TMP  68 68 67 65 63 61 60 62 66 68 67
 CIG -88-88-88-88-88-88-88 35 43-88-88
 VIS 100100100100100100100 90 90100 90
 SLV 130140130130130130130130140140140

 KNYC    NBM V5.0 NBS GUIDANCE    7/01/2026  1200 UTC
 DT /JUL   1/JUL   2                /JUL   3
 UTC  18 21 00 03 06 09 12 15 18 21 00
 FHR  06 09 12 15 18 21 24 27 30 33 36
 TXN        88          72          91
 XND         2           1           2
 TMP  86 87 84 79 75 73 72 78 85 89 87
 CIG -88-88-88-88-88-88-88-88-88-88-88
 VIS 100100100100100100100100100100100
 SLV 130140130130130130130130140140140
"""

NBP_FIXTURE = """
 KLAX    NBM V5.0 NBP GUIDANCE    7/01/2026  1200 UTC
    THU 02| FRI 03| SAT 04
 UTC    12| 00  12| 00  12
 FHR    24| 36  48| 60  72
 TXNMN  59| 70  60| 72  61
 TXNSD   1|  1   1|  1   1
 TXNP1  58| 69  59| 70  59
 TXNP2  59| 69  59| 71  60
 TXNP5  59| 70  60| 72  61
 TXNP7  60| 71  61| 72  62
 TXNP9  61| 71  61| 73  63
"""


class TestNbsParsing:
    def test_parses_both_stations(self):
        out = nbm_text.parse_bulletin(NBS_FIXTURE)
        assert set(out) == {"KLAX", "KNYC"}

    def test_station_filter_skips_unwanted_blocks(self):
        out = nbm_text.parse_bulletin(NBS_FIXTURE, ["KNYC"])
        assert set(out) == {"KNYC"}

    def test_cycle_time_is_utc_aware(self):
        fc = nbm_text.parse_bulletin(NBS_FIXTURE, ["KLAX"])["KLAX"]
        assert fc.cycle == dt.datetime(2026, 7, 1, 12, tzinfo=dt.timezone.utc)
        assert fc.product == "NBS"

    def test_forecast_hours_and_valid_times_line_up(self):
        fc = nbm_text.parse_bulletin(NBS_FIXTURE, ["KLAX"])["KLAX"]
        assert fc.fhr == [6, 9, 12, 15, 18, 21, 24, 27, 30, 33, 36]
        assert fc.valid[0] == dt.datetime(2026, 7, 1, 18, tzinfo=dt.timezone.utc)
        assert fc.valid[2] == dt.datetime(2026, 7, 2, 0, tzinfo=dt.timezone.utc)
        assert fc.valid[-1] == dt.datetime(2026, 7, 3, 0, tzinfo=dt.timezone.utc)

    def test_txn_populated_only_on_alternating_columns(self):
        """Blanks between max/min columns must be None, never 0 -- a 0 F
        forecast high would silently poison every probability downstream."""
        fc = nbm_text.parse_bulletin(NBS_FIXTURE, ["KLAX"])["KLAX"]
        txn = fc.rows["TXN"]
        assert txn[2] == 69   # 00Z 2 Jul -> daytime max for local 1 Jul
        assert txn[6] == 59   # 12Z 2 Jul -> overnight min
        assert txn[10] == 70  # 00Z 3 Jul -> daytime max for local 2 Jul
        assert txn[0] is None and txn[1] is None
        assert all(v is None for i, v in enumerate(txn) if i not in (2, 6, 10))

    def test_negative_values_parse(self):
        """CIG uses -88 as a missing-value sentinel packed 3 wide; a grid that
        clips to 2 characters would read it as 88 or -8."""
        fc = nbm_text.parse_bulletin(NBS_FIXTURE, ["KLAX"])["KLAX"]
        assert fc.rows["CIG"][0] == -88
        assert fc.rows["CIG"][7] == 35

    def test_packed_three_digit_first_column_is_reclaimed(self):
        """VIS values are 100 packed 3 wide; the strict grid read '00'."""
        fc = nbm_text.parse_bulletin(NBS_FIXTURE, ["KLAX"])["KLAX"]
        assert fc.rows["VIS"][0] == 100
        assert fc.rows["SLV"][0] == 130

    def test_packed_three_digit_rows_keep_a_clean_label(self):
        """Regression: VIS/SLV values bleed left of the column grid, which made
        a fixed-width label slice produce 'VIS 1'."""
        fc = nbm_text.parse_bulletin(NBS_FIXTURE, ["KLAX"])["KLAX"]
        assert "VIS" in fc.rows
        assert "SLV" in fc.rows
        assert not any(" " in label for label in fc.rows)

    def test_value_at_and_lead_hours(self):
        fc = nbm_text.parse_bulletin(NBS_FIXTURE, ["KNYC"])["KNYC"]
        valid = dt.datetime(2026, 7, 2, 0, tzinfo=dt.timezone.utc)
        assert fc.value_at("TXN", valid) == 88
        assert fc.lead_hours_to(valid) == 12
        assert fc.value_at("TXN", dt.datetime(2030, 1, 1, tzinfo=dt.timezone.utc)) is None


class TestNbpParsing:
    def test_quantile_rows_present(self):
        fc = nbm_text.parse_bulletin(NBP_FIXTURE, ["KLAX"])["KLAX"]
        for label in ("TXNMN", "TXNSD", "TXNP1", "TXNP2", "TXNP5", "TXNP7", "TXNP9"):
            assert label in fc.rows, label

    def test_pipe_separators_do_not_corrupt_values(self):
        fc = nbm_text.parse_bulletin(NBP_FIXTURE, ["KLAX"])["KLAX"]
        assert fc.fhr == [24, 36, 48, 60, 72]
        assert fc.rows["TXNMN"] == [59, 70, 60, 72, 61]
        assert fc.rows["TXNSD"] == [1, 1, 1, 1, 1]

    def test_quantiles_are_monotone_non_decreasing(self):
        fc = nbm_text.parse_bulletin(NBP_FIXTURE, ["KLAX"])["KLAX"]
        for i in range(len(fc.fhr)):
            ladder = [
                fc.rows[label][i]
                for label in ("TXNP1", "TXNP2", "TXNP5", "TXNP7", "TXNP9")
            ]
            assert ladder == sorted(ladder), ladder


class TestValidTimeMapping:
    def test_daytime_max_for_local_day_is_00z_next_day(self):
        """CONFIRMED EMPIRICALLY, not assumed: backtest/verify_alignment.py
        scores both readings against 1,261 real Kalshi settlements and this one
        wins at MAE 1.85 F vs 3.46 F."""
        valid = nbm_text.daytime_max_valid_time(dt.date(2026, 7, 1))
        assert valid == dt.datetime(2026, 7, 2, 0, tzinfo=dt.timezone.utc)

    def test_bulletin_key_shape(self):
        key = nbm_text.bulletin_key(dt.date(2026, 7, 1), 12, "nbp")
        assert key == "blend.20260701/12/text/blend_nbptx.t12z"


class TestColumnGrid:
    def test_spans_are_contiguous_and_ordered(self):
        fhr_line = " FHR    24| 36  48| 60  72"
        spans = nbm_text._column_spans(fhr_line, 6)
        assert len(spans) == 5
        for (s0, e0), (s1, e1) in zip(spans, spans[1:]):
            assert e0 <= s1
            assert s0 < e0

    def test_blank_slice_is_none_not_zero(self):
        assert nbm_text._slice_int("          ", 0, 5) is None
        assert nbm_text._slice_int("    |     ", 0, 5) is None
        assert nbm_text._slice_int("   -88    ", 0, 6) == -88

    def test_bleed_repair_never_eats_a_digit_ending_label(self):
        """Regression: NBP labels like TXNP1/Q24P5 end in a digit. An unbounded
        left-extension swallowed it and destroyed the entire row."""
        fc = nbm_text.parse_bulletin(NBP_FIXTURE, ["KLAX"])["KLAX"]
        assert fc.rows["TXNP1"] == [58, 69, 59, 70, 59]
        assert fc.rows["TXNP9"] == [61, 71, 61, 73, 63]
