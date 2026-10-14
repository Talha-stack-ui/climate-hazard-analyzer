import numpy as np
from django.test import SimpleTestCase

from api import trends
from api.cleaning import clean
from api.exceptions import InsufficientData
from api.hazards import HAZARDS, detect_events, threshold, yearly_metrics


def days(start, end):
    return np.arange(np.datetime64(start), np.datetime64(end) + 1)


class CleaningTests(SimpleTestCase):
    def setUp(self):
        self.dates = days("2000-01-01", "2009-12-31")
        rng = np.random.default_rng(0)
        doy = np.arange(self.dates.size) % 365
        self.tmax = 25 + 8 * np.sin(2 * np.pi * doy / 365) + rng.normal(0, 1.5, self.dates.size)

    def test_impossible_values_removed_and_short_gaps_filled(self):
        v = self.tmax.copy()
        v[100] = 75.0       # beyond world record -> removed, then interpolated (1-day gap)
        v[200:205] = np.nan  # 5-day gap -> stays missing
        out, stats = clean("tmax", self.dates, v)
        self.assertEqual(stats["implausible"], 1)
        self.assertTrue(np.isfinite(out[100]))
        self.assertTrue(np.isnan(out[200:205]).all())
        self.assertEqual(stats["interpolated"], 1)

    def test_isolated_spike_is_outlier_but_persistent_extreme_is_kept(self):
        v = self.tmax.copy()
        v[1000] += 25                 # one-day spike (e.g. unit slip)
        v[2000:2004] = v[2000] + 15   # four-day genuine-looking extreme
        out, stats = clean("tmax", self.dates, v)
        self.assertEqual(stats["outliers"], 1)
        self.assertNotAlmostEqual(out[1000], v[1000])
        np.testing.assert_allclose(out[2000:2004], v[2000:2004])

    def test_precipitation_is_never_interpolated_or_filtered(self):
        p = np.zeros(self.dates.size)
        p[50] = 400.0   # extreme but plausible
        p[60] = -3.0    # impossible
        p[70] = np.nan
        out, stats = clean("precip", self.dates, p)
        self.assertEqual(out[50], 400.0)
        self.assertTrue(np.isnan(out[60]) and np.isnan(out[70]))
        self.assertEqual((stats["outliers"], stats["interpolated"]), (0, 0))


class HazardTests(SimpleTestCase):
    def test_events_need_min_duration_and_gaps_break_them(self):
        d = days("2001-01-01", "2001-01-20")
        v = np.full(d.size, 30.0)
        v[2:4] = 40      # 2 days: too short
        v[6:10] = 41     # 4 days: event
        v[12:14] = 42
        v[14] = np.nan   # gap splits 12-17 into 2 + 3 days
        v[15:18] = 42
        events = detect_events(d, v, 35.0, 3)
        self.assertEqual([(e["start"], e["duration"]) for e in events], [("2001-01-07", 4), ("2001-01-16", 3)])
        self.assertEqual(events[0]["mean_excess"], 6.0)

    def test_yearly_metrics_and_low_coverage_years(self):
        d = days("2001-01-01", "2002-12-31")
        v = np.full(d.size, 30.0)
        v[100:105] = 40
        v[400:] = np.nan  # 2002 mostly missing
        events = detect_events(d, v, 35.0, 3)
        rows = yearly_metrics(d, v, events, [2001, 2002])
        self.assertEqual(rows[0]["events"], 1)
        self.assertEqual(rows[0]["hazard_days"], 5)
        self.assertEqual(rows[0]["mean_intensity"], 5.0)
        self.assertFalse(rows[1]["included"])
        self.assertIsNone(rows[1]["events"])

    def test_rain_threshold_uses_wet_days_only(self):
        p = np.zeros(2000)
        p[::4] = np.linspace(1, 100, 500)
        self.assertGreater(threshold(HAZARDS["heavy_rain"], p, 99), 90)

    def test_threshold_refuses_too_little_data(self):
        with self.assertRaises(InsufficientData):
            threshold(HAZARDS["heavy_rain"], np.zeros(5000), 99)


class TrendTests(SimpleTestCase):
    def test_clear_increase_is_significant(self):
        years = list(range(1980, 2020))
        rng = np.random.default_rng(1)
        values = [0.5 * (y - 1980) + rng.normal(0, 2) for y in years]
        t = trends.analyze(years, values)
        self.assertTrue(t["significant"])
        self.assertAlmostEqual(t["ols"]["slope_per_decade"], 5, delta=1)
        self.assertGreater(t["sen_slope_per_decade"], 3)

    def test_noise_is_not_significant_and_none_is_skipped(self):
        years = list(range(1980, 2020))
        rng = np.random.default_rng(2)
        values = [float(rng.poisson(3)) for _ in years]
        values[5] = None
        t = trends.analyze(years, values)
        self.assertFalse(t["significant"])
        self.assertEqual(t["n"], 39)

    def test_short_or_constant_series(self):
        self.assertFalse(trends.analyze(list(range(5)), [1, 2, 3, 4, 5])["sufficient"])
        flat = trends.analyze(list(range(20)), [0] * 20)
        self.assertEqual(flat["mann_kendall"]["p_value"], 1.0)
        self.assertFalse(flat["significant"])

    def test_rolling_mean(self):
        self.assertEqual(trends.rolling_mean([1, 2, 3, 4, 5], 3), [1.5, 2, 3, 4, 4.5])
        self.assertEqual(trends.rolling_mean([None, None, 3, None, None], 5)[2], None)
