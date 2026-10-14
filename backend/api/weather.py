"""Region sampling and the cached daily-weather store.

Raw data is cached in SQLite as one row per (grid cell, year). ERA5 has
a ~0.25° grid, so coordinates are snapped to 0.25° and nearby requests share a
cache entry. Historical years never change, so a cell-year is fetched once,
ever. Only missing years are requested, in one multi-location call.
"""

import hashlib
import logging
import time
from datetime import date, timedelta

import numpy as np
from django.db import IntegrityError, transaction
from django.utils import timezone

from . import providers
from .exceptions import UpstreamBusy, UpstreamError
from .models import DailyWeather, FetchLock

logger = logging.getLogger(__name__)

GRID_DEG = 0.25
BOX_SPLIT_DEG = 1.0  # a box side at least this long is sampled at 2 points instead of 1
LOCK_SECONDS = 90
WAIT_SECONDS = 45


def snap(value):
    return round(round(value / GRID_DEG) * GRID_DEG, 2)


def cell_id(lat, lon):
    return f"{lat:.2f},{lon:.2f}"


def sample_points(region):
    """Grid points that represent a region: the nearest cell for a point,
    up to a 2×2 grid for a box (see README, "Assumptions")."""
    if region["type"] == "point":
        return [(snap(region["lat"]), snap(region["lon"]))]

    def axis(lo, hi):
        n = 2 if hi - lo >= BOX_SPLIT_DEG else 1
        return [lo + (hi - lo) * (i + 0.5) / n for i in range(n)]

    points = {
        (snap(lat), snap(lon))
        for lat in axis(region["south"], region["north"])
        for lon in axis(region["west"], region["east"])
    }
    return sorted(points)


def _cached_rows(points, start_year, end_year):
    return DailyWeather.objects.filter(
        cell__in=[cell_id(*p) for p in points], year__range=(start_year, end_year),
    )


def missing_cell_years(points, start_year, end_year):
    found = set(_cached_rows(points, start_year, end_year).values_list("cell", "year"))
    return [(p, y) for p in points for y in range(start_year, end_year + 1) if (cell_id(*p), y) not in found]


def load_daily(points, start_year, end_year):
    """Daily series for every point, fetching whatever is not cached yet.

    Returns (dates, tmax, precip, grid): dates is datetime64[D] of length D;
    tmax/precip are arrays of shape (n_points, D) with NaN for missing values.
    """
    missing = missing_cell_years(points, start_year, end_year)
    if missing:
        _fetch(sorted({p for p, _ in missing}), min(y for _, y in missing), max(y for _, y in missing))
        if missing_cell_years(points, start_year, end_year):
            raise UpstreamError("The weather provider has no complete data for this region and period.")

    rows = {(r.cell, r.year): r for r in _cached_rows(points, start_year, end_year)}

    dates = np.arange(np.datetime64(f"{start_year}-01-01"), np.datetime64(f"{end_year + 1}-01-01"))
    tmax, precip, grid = [], [], []
    for p in points:
        years = [rows[(cell_id(*p), y)] for y in range(start_year, end_year + 1)]
        tmax.append(np.concatenate([np.array(r.tmax, dtype=float) for r in years]))
        precip.append(np.concatenate([np.array(r.precip, dtype=float) for r in years]))
        grid.append({"lat": years[0].grid_lat, "lon": years[0].grid_lon, "elevation": years[0].elevation})
    return dates, np.vstack(tmax), np.vstack(precip), grid


def _fetch(points, start_year, end_year):
    key = "fetch:" + hashlib.sha1(f"{points}|{start_year}|{end_year}".encode()).hexdigest()
    if not _acquire(key):
        _wait_until_cached(points, start_year, end_year)
        return
    try:
        logger.info("Fetching %d point(s) for %d-%d from Open-Meteo", len(points), start_year, end_year)
        results = providers.fetch_daily_weather(points, date(start_year, 1, 1), date(end_year, 12, 31))
        rows = [
            DailyWeather(
                cell=cell_id(lat, lon), year=year, tmax=tmax, precip=precip,
                grid_lat=res["lat"], grid_lon=res["lon"], elevation=res["elevation"],
            )
            for (lat, lon), res in zip(points, results)
            for year, (tmax, precip) in _split_by_year(res).items()
        ]
        # Years already cached (the span can overlap) are immutable: skip them.
        DailyWeather.objects.bulk_create(rows, ignore_conflicts=True)
    finally:
        FetchLock.objects.filter(key=key).delete()


def _split_by_year(res):
    """Group a provider series by calendar year; keep only complete years."""
    by_year = {}
    for day, t, p in zip(res["time"], res["tmax"], res["precip"]):
        tmax, precip = by_year.setdefault(int(day[:4]), ([], []))
        tmax.append(t)
        precip.append(p)
    return {
        year: values for year, values in by_year.items()
        if len(values[0]) == (date(year + 1, 1, 1) - date(year, 1, 1)).days
    }


def _acquire(key):
    """Database lock so concurrent requests (any thread or process) don't fetch the same data twice."""
    now = timezone.now()
    # Take over a lock whose owner crashed.
    FetchLock.objects.filter(key=key, expires_at__lt=now).delete()
    try:
        with transaction.atomic():
            FetchLock.objects.create(key=key, expires_at=now + timedelta(seconds=LOCK_SECONDS))
        return True
    except IntegrityError:
        return False


def _wait_until_cached(points, start_year, end_year):
    deadline = time.monotonic() + WAIT_SECONDS
    while time.monotonic() < deadline:
        if not missing_cell_years(points, start_year, end_year):
            return
        time.sleep(1)
    raise UpstreamBusy("This region is still being downloaded. Try again shortly.", retry_after=15)
