"""NOAA National Blend of Models (NBM) station text bulletins.

WHY TEXT AND NOT GRIB2
----------------------
DECISIONS.md's Session 30 entry specced this data leg as byte-range fetches of
the ``core/`` GRIB2 files, pulling ``TMAX:2 m above ground:12-24 hour max fcst``
plus its ``ens std dev``, with the ``qmd/`` quantile suite as a later upgrade.
That works, but it needs a GRIB2 decoder (eccodes/cfgrib -- a binary
dependency), and every GRIB message is a full CONUS grid that then has to be
interpolated to a point.

The ``text/`` suite in the same bucket makes all of that unnecessary. The NBP
bulletin (``blend_nbptx.tCCz``) is plain ASCII, organised *by station*, and for
each station publishes, for max/min temperature:

    TXNMN   ensemble mean
    TXNSD   ensemble standard deviation
    TXNP1   10th percentile
    TXNP2   25th percentile
    TXNP5   50th percentile (median)
    TXNP7   75th percentile
    TXNP9   90th percentile

i.e. the mean, the spread AND the quantile suite, for exactly the airport
stations Kalshi settles on (KLAX, KNYC, KMIA, ... all present; 9,591 stations
in the 2026-07-01 12Z file), with no grid interpolation and no decoder.

CONFIRMED LIVE 2026-08-02: anonymous HTTPS GET against
``noaa-nbm-grib2-pds.s3.amazonaws.com`` returns 200 with no credentials; the
27.5 MB NBP bulletin downloads in ~1.6 s, so the byte-range trickery the
original spec called for is not needed either.

THE ONE REAL COST of the text route, recorded because it is a candidate
explanation for any overconfidence seen in the calibration report: every value
in these bulletins is an **integer degree F**, including TXNSD. A true forecast
spread of 1.4 F is published as ``1``. If the reliability curve shows the model
systematically overconfident at the extremes, integer truncation of the spread
is the first suspect and the GRIB2 route (float values) is the fix -- do not
conclude "the model is overconfident" without ruling that out first.
"""

from __future__ import annotations

import datetime as dt
import gzip
import os
import re
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence

import requests

BUCKET_URL = "https://noaa-nbm-grib2-pds.s3.amazonaws.com"

# api.weather.gov requires a descriptive User-Agent with contact info. The S3
# bucket does not, but sending one costs nothing and keeps a single convention
# for every outbound request this package makes.
USER_AGENT = "karbotrage-backtest/0.1 (research; contact: tomgrow@gmail.com)"

CACHE_DIR = os.path.join(os.path.dirname(__file__), "cache")

#: Bulletin products in the ``text/`` suite. NBP is the probabilistic one and
#: is the only product this module needs; the others are listed so a future
#: session does not have to re-derive the naming.
PRODUCTS = {
    "nbp": "blend_nbptx",  # probabilistic: mean, sd, quantiles. 6-hourly cycles.
    "nbs": "blend_nbstx",  # short range deterministic, 3-hourly out to ~72h
    "nbh": "blend_nbhtx",  # hourly out to ~25h
    "nbe": "blend_nbetx",  # extended
    "nbx": "blend_nbxtx",  # extended, further
}

_HEADER_RE = re.compile(
    r"^\s*(?P<station>\S+)\s+NBM\s+V\S+\s+(?P<product>\S+)\s+GUIDANCE\s+"
    r"(?P<month>\d{1,2})/(?P<day>\d{1,2})/(?P<year>\d{4})\s+(?P<cycle>\d{4})\s+UTC\s*$"
)


class NbmFetchError(RuntimeError):
    """Raised when a bulletin cannot be retrieved."""


@dataclass
class StationForecast:
    """One station's block from one bulletin cycle.

    ``rows`` maps a bulletin row label (``TXNMN``, ``TXNSD``, ``TXNP5``, ...) to
    a list of optional ints, positionally aligned with ``fhr`` and ``valid``.
    """

    station: str
    product: str
    cycle: dt.datetime  # tz-aware UTC, the model run time
    fhr: List[int] = field(default_factory=list)
    valid: List[dt.datetime] = field(default_factory=list)
    rows: Dict[str, List[Optional[int]]] = field(default_factory=dict)

    def value_at(self, label: str, valid_time: dt.datetime) -> Optional[int]:
        row = self.rows.get(label)
        if row is None:
            return None
        for i, v in enumerate(self.valid):
            if v == valid_time:
                return row[i]
        return None

    def lead_hours_to(self, valid_time: dt.datetime) -> Optional[int]:
        for i, v in enumerate(self.valid):
            if v == valid_time:
                return self.fhr[i]
        return None


def _cache_path(date: dt.date, cycle_hour: int, product: str) -> str:
    return os.path.join(
        CACHE_DIR,
        "nbm",
        f"{product}.{date:%Y%m%d}.t{cycle_hour:02d}z.txt.gz",
    )


def bulletin_key(date: dt.date, cycle_hour: int, product: str = "nbp") -> str:
    stem = PRODUCTS[product]
    return f"blend.{date:%Y%m%d}/{cycle_hour:02d}/text/{stem}.t{cycle_hour:02d}z"


def fetch_bulletin(
    date: dt.date,
    cycle_hour: int,
    product: str = "nbp",
    *,
    session: Optional[requests.Session] = None,
    use_cache: bool = True,
    timeout: int = 180,
) -> str:
    """Download (or read from cache) one NBM text bulletin.

    Bulletins are cached gzipped -- these files are ~27 MB of highly repetitive
    ASCII and compress to roughly a tenth of that, which matters when a backtest
    pulls a couple of hundred cycles.
    """
    path = _cache_path(date, cycle_hour, product)
    if use_cache and os.path.exists(path):
        with gzip.open(path, "rt", encoding="ascii", errors="replace") as fh:
            return fh.read()

    url = f"{BUCKET_URL}/{bulletin_key(date, cycle_hour, product)}"
    http = session or requests
    resp = http.get(url, headers={"User-Agent": USER_AGENT}, timeout=timeout)
    if resp.status_code == 404:
        raise NbmFetchError(f"no bulletin at {url}")
    if resp.status_code != 200:
        raise NbmFetchError(f"HTTP {resp.status_code} for {url}")
    text = resp.text

    if use_cache:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".part"
        with gzip.open(tmp, "wt", encoding="ascii", errors="replace") as fh:
            fh.write(text)
        os.replace(tmp, path)
    return text


def _column_spans(fhr_line: str, label_width: int) -> List[tuple]:
    """Derive the fixed-width column grid from the FHR row.

    Bulletin values are right-aligned on a common grid, and the ``|`` day
    separators sit between columns. Anchoring on the *right edge* of each FHR
    token and taking everything back to the previous column's right edge yields
    a slice that tolerates 2-digit, 3-digit and negative values alike, without
    hardcoding a field width that differs between NBP, NBS and NBH.
    """
    spans = []
    prev_end = label_width
    for m in re.finditer(r"[^\s|]+", fhr_line[label_width:]):
        end = label_width + m.end()
        spans.append((prev_end, end))
        prev_end = end
    return spans


def _slice_int(
    line: str, start: int, end: int, *, label_end: Optional[int] = None
) -> Optional[int]:
    """Read one column. ``label_end`` enables the leading-column bleed repair.

    Rows whose values are 3 characters wide and packed with no separator (VIS,
    SLV, CIG, LCB in NBS) push their FIRST value one character to the left of
    the FHR-derived grid, into what is otherwise label padding. Slicing strictly
    on the grid silently drops that character -- turning ``-88`` into ``88``
    (a sign flip) and ``100`` into ``00``.

    The repair reclaims digits and minus signs found in the padding, but must
    stop at the end of the label itself: NBP labels such as ``TXNP1`` and
    ``Q24P5`` END IN A DIGIT, and an unbounded repair swallows it and destroys
    the whole row. ``label_end`` is the first column after the label, so only
    genuine padding is ever reclaimed. Later columns need no repair because the
    grid spans are contiguous.
    """
    if label_end is not None:
        while start > label_end and (
            line[start - 1].isdigit() or line[start - 1] == "-"
        ):
            start -= 1
    chunk = line[start:end].replace("|", " ").strip()
    if not chunk:
        return None
    try:
        return int(chunk)
    except ValueError:
        return None


def parse_bulletin(
    text: str,
    stations: Optional[Iterable[str]] = None,
) -> Dict[str, StationForecast]:
    """Parse a bulletin into ``{station: StationForecast}``.

    ``stations`` restricts parsing to a set of station IDs. A backtest wants a
    dozen airports out of ~9,600, and skipping the rest is most of the runtime.
    """
    wanted = set(stations) if stations is not None else None
    out: Dict[str, StationForecast] = {}

    lines = text.splitlines()
    i = 0
    n = len(lines)
    while i < n:
        m = _HEADER_RE.match(lines[i])
        if not m:
            i += 1
            continue

        station = m.group("station")
        if wanted is not None and station not in wanted:
            i += 1
            continue

        cycle = dt.datetime(
            int(m.group("year")),
            int(m.group("month")),
            int(m.group("day")),
            int(m.group("cycle")[:2]),
            int(m.group("cycle")[2:]),
            tzinfo=dt.timezone.utc,
        )

        # Find the FHR row; it defines the column grid for every row below it.
        j = i + 1
        fhr_idx = None
        while j < n and j < i + 8:
            if lines[j][1:6].strip() == "FHR":
                fhr_idx = j
                break
            j += 1
        if fhr_idx is None:
            i += 1
            continue

        fhr_line = lines[fhr_idx]
        label_width = 6
        spans = _column_spans(fhr_line, label_width)
        fhr = [_slice_int(fhr_line, s, e) for s, e in spans]
        if any(f is None for f in fhr):
            i = fhr_idx + 1
            continue

        fc = StationForecast(
            station=station,
            product=m.group("product"),
            cycle=cycle,
            fhr=[int(f) for f in fhr],
            valid=[cycle + dt.timedelta(hours=int(f)) for f in fhr],
        )

        k = fhr_idx + 1
        while k < n:
            line = lines[k]
            if _HEADER_RE.match(line):
                break
            # Some NBS rows (VIS, SLV, CIG, LCB) pack 3-digit values with no
            # separator, and their first value bleeds one character left of the
            # column grid -- so a naive fixed-width slice yields labels like
            # "VIS 1". Cut at the first space to recover the real label.
            label = line[1:label_width].strip().split(" ")[0]
            if not label:
                k += 1
                continue
            label_end = 1 + len(label)
            fc.rows[label] = [
                _slice_int(line, s, e, label_end=label_end if i == 0 else None)
                for i, (s, e) in enumerate(spans)
            ]
            k += 1

        out[station] = fc
        i = k
        if wanted is not None and len(out) == len(wanted):
            break

    return out


def available_cycles(date: dt.date, product: str = "nbp") -> List[int]:
    """Which cycle hours actually published ``product`` on ``date``.

    Cheap HEAD probes. NBP is nominally 6-hourly but this asks the bucket
    rather than assuming -- the archive has gaps, and a missing cycle silently
    turning into a wrong lead time is exactly the class of bug that this
    project keeps paying for.
    """
    found = []
    for hour in range(24):
        url = f"{BUCKET_URL}/{bulletin_key(date, hour, product)}"
        resp = requests.head(url, headers={"User-Agent": USER_AGENT}, timeout=30)
        if resp.status_code == 200:
            found.append(hour)
    return found


def daytime_max_valid_time(local_day: dt.date) -> dt.datetime:
    """UTC valid time of the NBM max-temperature forecast for ``local_day``.

    NBM publishes max/min temperature on alternating 12-hour windows: the value
    valid at 00Z covers the preceding 12Z-00Z window (the daytime max), and the
    value valid at 12Z covers the preceding 00Z-12Z window (the overnight min).
    So the daytime max for US local day D is the value valid at 00Z on D+1.

    THIS OFF-BY-ONE-DAY IS ASSUMED, NOT CONFIRMED, at the point this docstring
    was written. ``backtest/verify_alignment.py`` tests it empirically against
    Kalshi's own settled ladders, which bracket the observed high. Do not build
    on this function's answer without looking at that check's output -- a
    one-day misalignment would show up as a plausible-looking but useless
    model, which is precisely how S1 survived for three months.
    """
    return dt.datetime(
        local_day.year, local_day.month, local_day.day, 0, 0, tzinfo=dt.timezone.utc
    ) + dt.timedelta(days=1)


def object_last_modified(
    date: dt.date, cycle_hour: int, product: str = "nbp"
) -> Optional[dt.datetime]:
    """When the bucket actually published this bulletin.

    Used to measure the publication lag rather than assume it. A backtest that
    lets a strategy see a forecast before it existed is the single most common
    way to manufacture edge, and "cycle time plus a guessed two hours" is an
    assumption, not a measurement.
    """
    url = f"{BUCKET_URL}/{bulletin_key(date, cycle_hour, product)}"
    resp = requests.head(url, headers={"User-Agent": USER_AGENT}, timeout=30)
    if resp.status_code != 200:
        return None
    stamp = resp.headers.get("Last-Modified")
    if not stamp:
        return None
    parsed = dt.datetime.strptime(stamp, "%a, %d %b %Y %H:%M:%S %Z")
    return parsed.replace(tzinfo=dt.timezone.utc)


def fetch_stations(
    date: dt.date,
    cycle_hour: int,
    stations: Sequence[str],
    product: str = "nbp",
    *,
    session: Optional[requests.Session] = None,
) -> Dict[str, StationForecast]:
    """Parsed forecasts for ``stations`` only, cached as a small JSON subset.

    The raw bulletins are ~30 MB each and a full backtest touches a couple of
    hundred cycles; keeping only the dozen-odd stations that matter turns a
    multi-gigabyte cache into a few megabytes, at the cost of a re-download if
    the station list ever changes.
    """
    import hashlib
    import json

    # hashlib, not hash() -- str hashing is salted per interpreter run, so
    # hash() would silently miss the cache on every restart.
    tag = "-".join(sorted(stations))
    digest = hashlib.md5(tag.encode()).hexdigest()[:10]
    path = os.path.join(
        CACHE_DIR, "nbm_subset", f"{product}.{date:%Y%m%d}.t{cycle_hour:02d}z.{digest}.json"
    )
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as fh:
            blob = json.load(fh)
        out = {}
        for station, rec in blob.items():
            out[station] = StationForecast(
                station=station,
                product=rec["product"],
                cycle=dt.datetime.fromisoformat(rec["cycle"]),
                fhr=rec["fhr"],
                valid=[dt.datetime.fromisoformat(v) for v in rec["valid"]],
                rows=rec["rows"],
            )
        return out

    text = fetch_bulletin(date, cycle_hour, product, session=session, use_cache=False)
    parsed = parse_bulletin(text, stations)
    blob = {
        s: {
            "product": fc.product,
            "cycle": fc.cycle.isoformat(),
            "fhr": fc.fhr,
            "valid": [v.isoformat() for v in fc.valid],
            "rows": fc.rows,
        }
        for s, fc in parsed.items()
    }
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".part"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(blob, fh)
    os.replace(tmp, path)
    return parsed


def load_station_forecasts(
    dates_and_cycles: Sequence[tuple],
    stations: Sequence[str],
    product: str = "nbp",
    *,
    session: Optional[requests.Session] = None,
    on_error: str = "skip",
) -> Dict[tuple, Dict[str, StationForecast]]:
    """Fetch+parse many cycles, returning ``{(date, cycle_hour): {station: fc}}``."""
    out: Dict[tuple, Dict[str, StationForecast]] = {}
    sess = session or requests.Session()
    for date, hour in dates_and_cycles:
        try:
            text = fetch_bulletin(date, hour, product, session=sess)
        except (NbmFetchError, requests.RequestException) as exc:
            if on_error == "raise":
                raise
            out[(date, hour)] = {}
            print(f"  [nbm] MISS {date} {hour:02d}Z {product}: {exc}")
            continue
        out[(date, hour)] = parse_bulletin(text, stations)
    return out
