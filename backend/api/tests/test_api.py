from datetime import date, timedelta
from unittest import mock

import numpy as np
from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from api import views
from api.exceptions import UpstreamBusy
from api.models import CACHE_TTL, Analysis, DailyWeather

QUERY = {"lat": 28.61, "lon": 77.21, "start_year": 1990, "end_year": 2020}


def fake_weather(points, start, end):
    """Seasonal temperatures with more frequent heatwaves after 2000."""
    n = (end - start).days + 1
    time_axis = [(start + timedelta(days=i)).isoformat() for i in range(n)]
    out = []
    for k, _ in enumerate(points):
        rng = np.random.default_rng(k)
        doy = np.arange(n) % 365
        tmax = 30 + 8 * np.sin(2 * np.pi * (doy - 100) / 365) + rng.normal(0, 1.5, n)
        for i, day in enumerate(time_axis):
            year = int(day[:4])
            if day[5:] == "06-01" and (year >= 2000 or year % 4 == 0):
                tmax[i: i + 6] += 9
        precip = np.where(rng.random(n) < 0.3, rng.gamma(2, 6, n), 0)
        out.append({"lat": 28.5, "lon": 77.25, "elevation": 216.0, "time": time_axis,
                    "tmax": [round(float(x), 1) for x in tmax], "precip": [round(float(x), 1) for x in precip]})
    return out


@override_settings(ALLOWED_HOSTS=["testserver"])
class ApiTestCase(APITestCase):
    def setUp(self):
        cache.clear()
        patcher = mock.patch("api.providers.fetch_daily_weather", side_effect=fake_weather)
        self.fetch = patcher.start()
        self.addCleanup(patcher.stop)


class HealthAndMetaTests(ApiTestCase):
    def test_health(self):
        self.assertEqual(self.client.get(reverse("health")).json(), {"status": "ok"})

    def test_meta_lists_heatwave_as_default(self):
        data = self.client.get(reverse("meta")).json()
        self.assertEqual(data["default_hazard"], "heatwave")
        self.assertEqual(data["years"]["min"], 1950)


class AnalysisTests(ApiTestCase):
    def test_heatwave_analysis(self):
        r = self.client.get(reverse("analysis"), QUERY)
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(data["definition"]["hazard"], "heatwave")
        self.assertEqual(data["definition"]["baseline"], [1981, 2010])
        self.assertEqual(len(data["yearly"]), 31)
        self.assertTrue(data["trends"]["events"]["significant"])
        self.assertIn("increased", data["insights"]["headline"])
        self.assertEqual(data["query"]["region"], {"type": "point", "lat": 28.61, "lon": 77.21})
        self.assertIn("public", r["Cache-Control"])

    def test_weather_and_results_are_cached(self):
        first = self.client.get(reverse("analysis"), QUERY).json()
        # Nearby coordinates snap to the same grid cell -> same stored analysis.
        second = self.client.get(reverse("analysis"), {**QUERY, "lat": 28.55}).json()
        self.assertFalse(first["cached"])
        self.assertTrue(second["cached"])
        self.assertEqual(self.fetch.call_count, 1)
        # A different period reuses the cached weather years: no new download.
        r = self.client.get(reverse("analysis"), {**QUERY, "start_year": 1985, "hazard": "heavy_rain"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.fetch.call_count, 1)

    def test_stale_analyses_are_recomputed_from_cached_weather(self):
        self.client.get(reverse("analysis"), QUERY)
        Analysis.objects.update(created_at=timezone.now() - CACHE_TTL - timedelta(days=1))
        data = self.client.get(reverse("analysis"), QUERY).json()
        self.assertFalse(data["cached"])
        self.assertEqual(self.fetch.call_count, 1)  # weather rows never expire
        self.assertEqual(DailyWeather.objects.count(), 2020 - 1981 + 1)

    def test_only_missing_years_are_downloaded(self):
        self.client.get(reverse("analysis"), QUERY)
        self.client.get(reverse("analysis"), {**QUERY, "start_year": 1960})
        _, start, end = self.fetch.call_args.args
        self.assertEqual((start, end), (date(1960, 1, 1), date(1980, 12, 31)))

    def test_box_region_uses_several_grid_points(self):
        r = self.client.get(reverse("analysis"), {"south": 26, "north": 28, "west": 75, "east": 77,
                                                  "start_year": 1990, "end_year": 2020})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(self.fetch.call_args.args[0]), 4)
        self.assertEqual(len(r.json()["data_quality"]["grid_points"]), 4)

    def test_invalid_input_is_rejected(self):
        bad = [
            {**QUERY, "lat": "nan"},
            {**QUERY, "lat": 91},
            {**QUERY, "south": 1},
            {**QUERY, "start_year": 2015},
            {**QUERY, "end_year": 2999},
            {**QUERY, "hazard": "$where"},
            {**QUERY, "percentile": 50},
            {"south": 0, "north": 30, "west": 0, "east": 5, "start_year": 1990, "end_year": 2020},
            {"south": 5, "north": 1, "west": 0, "east": 5, "start_year": 1990, "end_year": 2020},
        ]
        for params in bad:
            with self.subTest(params=params):
                self.assertEqual(self.client.get(reverse("analysis"), params).status_code, 400)
        self.fetch.assert_not_called()

    def test_upstream_rate_limit_becomes_503_with_retry_after(self):
        self.fetch.side_effect = UpstreamBusy(retry_after=60)
        r = self.client.get(reverse("analysis"), QUERY)
        self.assertEqual(r.status_code, 503)
        self.assertEqual(r["Retry-After"], "60")


class RateLimitTests(ApiTestCase):
    def test_new_downloads_have_a_per_client_budget_but_cache_hits_do_not(self):
        with mock.patch.object(views.UpstreamThrottle, "THROTTLE_RATES", {"upstream": "1/hour"}):
            self.assertEqual(self.client.get(reverse("analysis"), QUERY).status_code, 200)
            self.assertEqual(self.client.get(reverse("analysis"), QUERY).status_code, 200)  # cached
            other = {**QUERY, "lat": -33.9, "lon": 18.4}
            r = self.client.get(reverse("analysis"), other)
            self.assertEqual(r.status_code, 429)
            self.assertIn("Retry-After", r)

    def test_endpoint_throttle(self):
        with mock.patch("rest_framework.throttling.ScopedRateThrottle.THROTTLE_RATES", {"geocode": "2/min"}), \
             mock.patch("api.providers.search_places", return_value=[]):
            codes = [self.client.get(reverse("geocode"), {"q": "delhi"}).status_code for _ in range(3)]
        self.assertEqual(codes, [200, 200, 429])


class GeocodeTests(ApiTestCase):
    PLACES = [
        {"name": "Rajasthan", "label": "Rajasthan, India", "kind": "state", "lat": 26.8, "lon": 73.8,
         "bbox": {"south": 23.0, "north": 30.2, "west": 69.5, "east": 78.3}},
        {"name": "Jaipur", "label": "Jaipur, India", "kind": "city", "lat": 26.9, "lon": 75.8,
         "bbox": {"south": 26.7, "north": 27.0, "west": 75.6, "east": 76.0}},
    ]

    def test_results_suggest_region_and_are_cached(self):
        with mock.patch("api.providers.search_places", return_value=self.PLACES) as search:
            first = self.client.get(reverse("geocode"), {"q": "  Rajasthan "}).json()
            self.client.get(reverse("geocode"), {"q": "rajasthan"})
        self.assertEqual(search.call_count, 1)
        self.assertEqual(first["results"][0]["region"]["type"], "box")
        self.assertEqual(first["results"][1]["region"], {"type": "point", "lat": 26.9, "lon": 75.8})

    def test_rejects_bad_queries(self):
        for q in ["a", "<script>", "x" * 101, '{"$gt": ""}']:
            with self.subTest(q=q):
                self.assertEqual(self.client.get(reverse("geocode"), {"q": q}).status_code, 400)


class ProviderCircuitBreakerTests(ApiTestCase):
    def test_429_blocks_further_calls_until_window_passes(self):
        from api import providers

        self.fetch.side_effect = None
        mock.patch.stopall()  # use the real fetch_daily_weather, with a fake HTTP layer
        limited = mock.Mock(status_code=429, headers={}, text='{"reason":"Hourly API request limit exceeded"}')
        with mock.patch.object(providers._http, "get", return_value=limited) as get:
            for _ in range(2):
                with self.assertRaises(UpstreamBusy) as ctx:
                    providers.fetch_daily_weather([(1.0, 2.0)], date(2000, 1, 1), date(2000, 12, 31))
        self.assertEqual(get.call_count, 1)  # second attempt never left the server
        self.assertGreater(ctx.exception.retry_after, 3500)
