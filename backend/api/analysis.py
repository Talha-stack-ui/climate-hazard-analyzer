"""Hazard analysis pipeline.

  weather cache -> clean each grid point -> regional mean -> baseline threshold
  -> events -> yearly metrics -> trends -> plain-language insights

Results are stored in SQLite (`Analysis`). The key is derived from the grid
points actually sampled, not the raw coordinates, so nearby requests share it.
"""

import hashlib
import json
from datetime import date, datetime, timezone

import numpy as np

from . import trends, weather
from .cleaning import clean
from .hazards import BASELINE, HAZARDS, detect_events, threshold, yearly_metrics
from .models import Analysis

ALGORITHM_VERSION = 2  # bump when the logic changes; old cached analyses are then ignored
FIRST_YEAR = 1950
METRICS = ("events", "hazard_days", "longest_event", "mean_intensity", "annual_max")
TOP_EVENTS = 15


def last_complete_year(today=None):
    """ERA5 lags real time by about a week, so a year counts as complete from mid-January."""
    today = today or date.today()
    return today.year - 1 if today >= date(today.year, 1, 15) else today.year - 2


class Job:
    """A validated request reduced to what the computation actually depends on."""

    def __init__(self, query):
        self.hazard = HAZARDS[query["hazard"]]
        self.points = weather.sample_points(query["region"])
        self.start_year, self.end_year = query["start_year"], query["end_year"]
        self.percentile, self.min_duration = query["percentile"], query["min_duration"]
        # Load the baseline years too, even if they lie outside the analysis period.
        self.load_years = (min(self.start_year, BASELINE[0]), max(self.end_year, BASELINE[1]))
        key = json.dumps([ALGORITHM_VERSION, self.hazard.id, self.points, self.start_year,
                          self.end_year, self.percentile, self.min_duration])
        self.key = hashlib.sha256(key.encode()).hexdigest()

    def needs_download(self):
        return bool(weather.missing_cell_years(self.points, *self.load_years))


def get_cached(job):
    row = Analysis.objects.fresh().filter(key=job.key).first()
    return row.result if row else None


def run(job):
    result = compute(job)
    Analysis.objects.update_or_create(key=job.key, defaults={"result": result})
    return result


def compute(job):
    hz = job.hazard
    dates, tmax, precip, grid = weather.load_daily(job.points, *job.load_years)
    raw = tmax if hz.variable == "tmax" else precip

    cleaned, quality = [], {"implausible": 0, "outliers": 0, "interpolated": 0}
    for series in raw:
        values, counts = clean(hz.variable, dates, series)
        cleaned.append(values)
        for k in quality:
            quality[k] += counts[k]
    values = _regional_mean(np.vstack(cleaned))

    years_of = dates.astype("datetime64[Y]").astype(int) + 1970
    in_baseline = (years_of >= BASELINE[0]) & (years_of <= BASELINE[1])
    limit = threshold(hz, values[in_baseline], job.percentile)

    in_period = (years_of >= job.start_year) & (years_of <= job.end_year)
    p_dates, p_values = dates[in_period], values[in_period]
    events = detect_events(p_dates, p_values, limit, job.min_duration)
    years = list(range(job.start_year, job.end_year + 1))
    yearly = yearly_metrics(p_dates, p_values, events, years)

    for metric in ("events", "hazard_days"):
        for row, avg in zip(yearly, trends.rolling_mean([r[metric] for r in yearly])):
            row[f"{metric}_5yr"] = avg
    trend = {m: trends.analyze(years, [r[m] for r in yearly]) for m in METRICS}

    quality.update({
        "source": "Open-Meteo Historical Weather API (ECMWF ERA5 reanalysis, ~25 km grid)",
        "grid_points": grid,
        "days": int(p_values.size),
        "days_missing": int(np.isnan(p_values).sum()),
        "years_excluded": [r["year"] for r in yearly if not r["included"]],
    })
    definition = {
        "hazard": hz.id, "label": hz.label, "variable": hz.variable, "units": hz.units,
        "percentile": job.percentile, "min_duration": job.min_duration,
        "baseline": list(BASELINE), "threshold": round(limit, 2),
        "text": hz.description.format(min_duration=job.min_duration, threshold=limit,
                                      percentile=job.percentile,
                                      baseline=f"{BASELINE[0]}-{BASELINE[1]}"),
    }
    return {
        "definition": definition,
        "data_quality": quality,
        "yearly": yearly,
        "trends": trend,
        "events": sorted(events, key=lambda e: (-e["duration"], -e["peak"]))[:TOP_EVENTS],
        "event_count": len(events),
        "insights": summarize(hz, yearly, trend, len(job.points), quality),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def _regional_mean(stack):
    """Mean over grid points; a day is missing if fewer than half the points have data."""
    valid = (~np.isnan(stack)).sum(axis=0)
    with np.errstate(invalid="ignore"):
        mean = np.nansum(stack, axis=0) / np.maximum(valid, 1)
    mean[valid * 2 < stack.shape[0]] = np.nan
    return mean


# --- Insights ---------------------------------------------------------------

def _p(p):
    return "p < 0.001" if p < 0.001 else f"p = {p:.3f}"


def summarize(hz, yearly, trend, n_points, quality):
    """Plain-language findings. A change is only called an increase/decrease
    when the Mann-Kendall test is significant; otherwise we say so explicitly."""
    label = hz.label.lower()
    days_trend = trend["hazard_days"]
    valid = [r for r in yearly if r["included"]]

    if not days_trend["sufficient"]:
        return {
            "headline": f"Too few complete years ({days_trend['n']}) to assess a trend. Select at least {trends.MIN_YEARS} years.",
            "details": [], "caveats": [],
        }

    k = min(10, len(valid) // 3)
    first, last = valid[:k], valid[-k:]
    first_mean = sum(r["hazard_days"] for r in first) / k
    last_mean = sum(r["hazard_days"] for r in last) / k
    first_span = f"{first[0]['year']}-{first[-1]['year']}"
    last_span = f"{last[0]['year']}-{last[-1]['year']}"
    if first_mean > 0:
        change = f"{(last_mean - first_mean) / first_mean * 100:+.0f}%"
    else:
        change = f"from none to {last_mean:.1f} days/yr"
    comparison = (f"{last_mean:.1f} {label} days/yr in {last_span} vs {first_mean:.1f} in {first_span}")
    mk_p = days_trend["mann_kendall"]["p_value"]
    slope = days_trend["ols"]["slope_per_decade"]

    if days_trend["significant"]:
        direction = "increased" if slope > 0 else "decreased"
        headline = (f"{hz.label} days have {direction} in this region since {valid[0]['year']} "
                    f"({change}): {comparison}.")
        significance = f"The trend is statistically significant (Mann-Kendall {_p(mk_p)})."
    else:
        headline = (f"No statistically significant trend in {label} days since {valid[0]['year']} "
                    f"(Mann-Kendall {_p(mk_p)}).")
        significance = (f"The difference between the first and last {k} years ({change}: {comparison}) "
                        f"is within normal year-to-year variability.")

    details = [
        significance,
        f"Linear trend: {slope:+.2f} {label} days per decade "
        f"(95% CI {days_trend['ols']['ci95_per_decade'][0]:+.2f} to {days_trend['ols']['ci95_per_decade'][1]:+.2f}).",
    ]
    ev = trend["events"]
    if ev["sufficient"]:
        details.append(f"Number of events: {ev['ols']['slope_per_decade']:+.2f} per decade"
                       f"{' (significant)' if ev['significant'] else ' (not significant)'}.")
    peak = trend["annual_max"]
    if peak["sufficient"]:
        name = "hottest day of the year" if hz.variable == "tmax" else "wettest day of the year"
        details.append(f"The {name} changed by {peak['ols']['slope_per_decade']:+.2f} {hz.units} per decade"
                       f"{' (significant)' if peak['significant'] else ' (not significant)'}.")

    caveats = []
    if len(valid) < 30:
        caveats.append("Fewer than 30 years: the trend is sensitive to a few extreme years.")
    if quality["years_excluded"]:
        caveats.append(f"{len(quality['years_excluded'])} year(s) with under 90% valid data were left out.")
    if n_points > 1:
        caveats.append(f"Regional series is the mean of {n_points} grid points; averaging damps local extremes.")
    caveats.append("ERA5 is a ~25 km reanalysis, not station data: it smooths local extremes such as urban heat islands.")
    caveats.append("Trend tests assume independent years; year-to-year persistence can overstate significance.")

    return {"headline": headline, "details": details, "caveats": caveats}
