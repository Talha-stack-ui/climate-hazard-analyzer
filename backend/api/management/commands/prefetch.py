"""Warm the weather cache for popular places so first visitors get instant answers.

    python manage.py prefetch                      # built-in list
    python manage.py prefetch 51.51,-0.13 40.42,-3.70 --start 1960

Waits and retries when Open-Meteo rate-limits (big downloads use a minute's quota).
"""

import time

from django.core.management.base import BaseCommand

from api import weather
from api.analysis import FIRST_YEAR, last_complete_year
from api.exceptions import UpstreamBusy

PLACES = {
    "New Delhi": (28.61, 77.21), "Mumbai": (19.08, 72.88), "Chennai": (13.08, 80.27),
    "London": (51.51, -0.13), "Madrid": (40.42, -3.70), "New York": (40.71, -74.01),
    "Phoenix": (33.45, -112.07), "Sydney": (-33.87, 151.21), "Lagos": (6.52, 3.38),
}


class Command(BaseCommand):
    help = "Download and cache daily weather for a list of points."

    def add_arguments(self, parser):
        parser.add_argument("points", nargs="*", help="lat,lon pairs (default: a built-in list of cities)")
        parser.add_argument("--start", type=int, default=FIRST_YEAR)

    def handle(self, *args, points, start, **options):
        targets = [tuple(map(float, p.split(","))) for p in points] or list(PLACES.values())
        end = last_complete_year()
        for lat, lon in targets:
            cell = [(weather.snap(lat), weather.snap(lon))]
            while True:
                try:
                    weather.load_daily(cell, start, end)
                    self.stdout.write(f"cached {cell[0]} {start}-{end}")
                    break
                except UpstreamBusy as exc:
                    self.stdout.write(f"rate-limited, waiting {exc.retry_after}s")
                    time.sleep(exc.retry_after + 1)
