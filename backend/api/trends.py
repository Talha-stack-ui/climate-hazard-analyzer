"""Trend statistics for yearly hazard series.

- OLS linear regression: slope, 95% CI, R², p-value (drawn on the chart)
- Mann-Kendall test: non-parametric significance, robust to the skewed,
  zero-heavy counts typical of hazard data (used for the headline)
- Sen's slope: robust trend magnitude
- Centred rolling mean for smoothing plots
"""

import math

import numpy as np
from scipy import stats

MIN_YEARS = 10
ALPHA = 0.05


def analyze(years, values):
    """Trend summary for one metric. None values (excluded years) are dropped."""
    pairs = [(y, v) for y, v in zip(years, values) if v is not None]
    if len(pairs) < MIN_YEARS:
        return {"n": len(pairs), "sufficient": False}
    x = np.array([p[0] for p in pairs], dtype=float)
    y = np.array([p[1] for p in pairs], dtype=float)

    if np.ptp(y) == 0:  # constant series (e.g. never any events): no trend to fit
        ols = {"slope_per_decade": 0.0, "intercept": float(y[0]), "ci95_per_decade": [0.0, 0.0],
               "r2": 0.0, "p_value": 1.0}
        mk = {"tau": 0.0, "z": 0.0, "p_value": 1.0}
        sen = 0.0
    else:
        fit = stats.linregress(x, y)
        half_ci = stats.t.ppf(1 - ALPHA / 2, len(x) - 2) * fit.stderr
        ols = {
            "slope_per_decade": _r(fit.slope * 10),
            "intercept": _r(fit.intercept),
            "ci95_per_decade": [_r((fit.slope - half_ci) * 10), _r((fit.slope + half_ci) * 10)],
            "r2": _r(fit.rvalue ** 2),
            "p_value": _r(fit.pvalue),
        }
        mk = mann_kendall(y)
        sen = _r(stats.theilslopes(y, x).slope * 10)

    return {
        "n": len(pairs),
        "sufficient": True,
        "ols": ols,
        "mann_kendall": mk,
        "sen_slope_per_decade": sen,
        "significant": mk["p_value"] < ALPHA,
    }


def mann_kendall(y):
    """Two-sided Mann-Kendall test with tie correction (normal approximation)."""
    n = len(y)
    s = sum(np.sign(y[j] - y[i]) for i in range(n - 1) for j in range(i + 1, n))
    _, counts = np.unique(y, return_counts=True)
    var_s = (n * (n - 1) * (2 * n + 5) - sum(t * (t - 1) * (2 * t + 5) for t in counts)) / 18
    if var_s <= 0:
        return {"tau": 0.0, "z": 0.0, "p_value": 1.0}
    z = (s - np.sign(s)) / math.sqrt(var_s)  # continuity correction
    return {
        "tau": _r(s / (n * (n - 1) / 2)),
        "z": _r(z),
        "p_value": _r(math.erfc(abs(z) / math.sqrt(2))),
    }


def rolling_mean(values, window=5):
    """Centred moving average; None where fewer than half the window has data."""
    out = []
    half = window // 2
    for i in range(len(values)):
        chunk = [v for v in values[max(0, i - half): i + half + 1] if v is not None]
        out.append(_r(sum(chunk) / len(chunk)) if len(chunk) > window // 2 else None)
    return out


def _r(x, digits=4):
    return round(float(x), digits)
