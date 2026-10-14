"""Quality control for daily series.

Order matters: physically impossible values -> NaN, then statistical outliers
(temperature only) -> NaN, then short temperature gaps are interpolated.
Precipitation is never interpolated or outlier-filtered: extremes are the
signal we are looking for, and inventing rain is worse than a gap.
"""

import numpy as np

PLAUSIBLE = {"tmax": (-90.0, 60.0), "precip": (0.0, 2000.0)}  # beyond world records
OUTLIER_Z = 6.0       # robust z-score vs. the day-of-year climatology
SPIKE_C = 10.0        # ...and this far from the mean of both neighbouring days
DOY_WINDOW = 7        # ± days pooled for that climatology
MAX_GAP_DAYS = 2      # longer gaps stay missing (and break hazard events)


def clean(variable, dates, values):
    """Return (cleaned copy, counts of what was changed)."""
    v = np.array(values, dtype=float)
    lo, hi = PLAUSIBLE[variable]
    implausible = ~np.isnan(v) & ((v < lo) | (v > hi))
    v[implausible] = np.nan

    outliers = np.zeros_like(implausible)
    interpolated = 0
    if variable == "tmax":
        outliers = _climatology_outliers(dates, v)
        v[outliers] = np.nan
        v, interpolated = _fill_short_gaps(v, MAX_GAP_DAYS)

    return v, {
        "implausible": int(implausible.sum()),
        "outliers": int(outliers.sum()),
        "interpolated": interpolated,
    }


def day_of_year(dates):
    years = dates.astype("datetime64[Y]")
    return (dates - years).astype(int)  # 0..365


def _climatology_outliers(dates, v):
    """Flag isolated spikes: more than OUTLIER_Z robust SDs from the median of
    the same time of year (±DOY_WINDOW days, all years pooled) AND more than
    SPIKE_C from the mean of the day before and after. Real extremes persist
    over several days (e.g. Delhi's record-cold 19 May 2021 is kept); sensor
    glitches and unit slips usually do not."""
    doy = day_of_year(dates)
    neighbours = (np.roll(v, 1) + np.roll(v, -1)) / 2
    neighbours[[0, -1]] = np.nan
    with np.errstate(invalid="ignore"):
        spike = np.abs(v - neighbours) > SPIKE_C
    flags = np.zeros(v.shape, dtype=bool)
    for d in range(366):
        dist = np.abs(doy - d)
        pool = v[np.minimum(dist, 366 - dist) <= DOY_WINDOW]
        pool = pool[~np.isnan(pool)]
        if pool.size < 30:
            continue
        median = np.median(pool)
        mad = 1.4826 * np.median(np.abs(pool - median))
        if mad == 0:
            continue
        today = (doy == d) & ~np.isnan(v)
        flags[today] = (np.abs(v[today] - median) / mad > OUTLIER_Z) & spike[today]
    return flags


def _fill_short_gaps(v, max_gap):
    """Linearly interpolate NaN runs of length <= max_gap that have data on both sides."""
    missing = np.isnan(v)
    if not missing.any():
        return v, 0
    edges = np.diff(np.concatenate(([0], missing.astype(int), [0])))
    starts, ends = np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)
    filled = 0
    for s, e in zip(starts, ends):
        if e - s <= max_gap and s > 0 and e < v.size:
            v[s:e] = np.interp(np.arange(s, e), [s - 1, e], [v[s - 1], v[e]])
            filled += int(e - s)
    return v, filled
