"""Hazard definitions, event detection and yearly metrics.

Heatwave (default): at least `min_duration` consecutive days with daily
maximum temperature above the `percentile` of all daily maxima in the
1981-2010 baseline at that location.

Heavy rainfall: days with precipitation above the `percentile` of wet days
(>= 1 mm) in the baseline, in the spirit of the ETCCDI R95p/R99p indices.

A missing day ends an event: we never assume a gap was hot or wet.
"""

from dataclasses import dataclass

import numpy as np

from .exceptions import InsufficientData

BASELINE = (1981, 2010)  # WMO climate normal period: fixes the threshold so periods are comparable
MIN_YEAR_COVERAGE = 0.9  # years with fewer valid days are left out of trends


@dataclass(frozen=True)
class Hazard:
    id: str
    label: str
    variable: str
    units: str
    default_percentile: float
    default_min_duration: int
    wet_day_mm: float | None
    description: str


HAZARDS = {
    h.id: h for h in (
        Hazard(
            id="heatwave", label="Heatwave", variable="tmax", units="°C",
            default_percentile=95, default_min_duration=3, wet_day_mm=None,
            description="{min_duration}+ consecutive days with daily maximum temperature above "
                        "{threshold:.1f} °C, the {percentile:g}th percentile of {baseline} daily maxima.",
        ),
        Hazard(
            id="heavy_rain", label="Heavy rainfall", variable="precip", units="mm",
            default_percentile=99, default_min_duration=1, wet_day_mm=1.0,
            description="{min_duration}+ consecutive days with precipitation above {threshold:.1f} mm/day, "
                        "the {percentile:g}th percentile of {baseline} wet days (>= 1 mm).",
        ),
    )
}


def threshold(hazard, baseline_values, percentile):
    values = baseline_values[~np.isnan(baseline_values)]
    if hazard.wet_day_mm is not None:
        values = values[values >= hazard.wet_day_mm]
    if values.size < 100:
        raise InsufficientData(f"Only {values.size} qualifying baseline days here; too few for a "
                               f"{percentile:g}th-percentile threshold.")
    return float(np.percentile(values, percentile))


def detect_events(dates, values, limit, min_duration):
    """Runs of consecutive days with value > limit lasting >= min_duration days."""
    above = np.nan_to_num(values, nan=-np.inf) > limit
    edges = np.diff(np.concatenate(([0], above.astype(int), [0])))
    events = []
    for s, e in zip(np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)):
        if e - s < min_duration:
            continue
        run = values[s:e]
        events.append({
            "start": str(dates[s]),
            "end": str(dates[e - 1]),
            "duration": int(e - s),
            "peak": round(float(run.max()), 2),
            "mean_excess": round(float((run - limit).mean()), 2),
        })
    return events


def yearly_metrics(dates, values, events, years):
    """One row per year. Events count toward the year they start in.
    Years with < MIN_YEAR_COVERAGE valid days get metrics = None."""
    year_of_day = dates.astype("datetime64[Y]").astype(int) + 1970
    by_year = {y: [] for y in years}
    for ev in events:
        by_year.setdefault(int(ev["start"][:4]), []).append(ev)

    rows = []
    for y in years:
        day_values = values[year_of_day == y]
        coverage = float(np.mean(~np.isnan(day_values))) if day_values.size else 0.0
        row = {"year": y, "coverage": round(coverage, 3), "included": coverage >= MIN_YEAR_COVERAGE}
        evs = by_year[y]
        days = sum(e["duration"] for e in evs)
        if row["included"]:
            row.update({
                "events": len(evs),
                "hazard_days": days,
                "longest_event": max((e["duration"] for e in evs), default=0),
                # Day-weighted mean exceedance; undefined (None) in years without events.
                "mean_intensity": round(sum(e["mean_excess"] * e["duration"] for e in evs) / days, 2) if days else None,
                "annual_max": round(float(np.nanmax(day_values)), 2),
            })
        else:
            row.update(dict.fromkeys(("events", "hazard_days", "longest_event", "mean_intensity", "annual_max")))
        rows.append(row)
    return rows
