"""SQLite tables.

- DailyWeather : raw daily series, one row per grid cell and year (kept forever)
- GeocodeCache : place search results (treated as stale after CACHE_TTL)
- Analysis     : computed hazard analyses (treated as stale after CACHE_TTL)
- FetchLock    : short-lived locks so one region is downloaded only once at a time
"""

from datetime import timedelta

from django.db import models
from django.utils import timezone

CACHE_TTL = timedelta(days=30)


class FreshQuerySet(models.QuerySet):
    def fresh(self):
        return self.filter(created_at__gte=timezone.now() - CACHE_TTL)


class DailyWeather(models.Model):
    cell = models.CharField(max_length=16)  # "28.50,77.25" (0.25° grid)
    year = models.PositiveSmallIntegerField()
    tmax = models.JSONField()    # °C per day, null = missing
    precip = models.JSONField()  # mm per day, null = missing
    grid_lat = models.FloatField()
    grid_lon = models.FloatField()
    elevation = models.FloatField(null=True)
    source = models.CharField(max_length=32, default="open-meteo/era5")
    fetched_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["cell", "year"], name="unique_cell_year")]


class GeocodeCache(models.Model):
    query = models.CharField(max_length=100, primary_key=True)
    places = models.JSONField()
    created_at = models.DateTimeField(auto_now=True)

    objects = FreshQuerySet.as_manager()


class Analysis(models.Model):
    key = models.CharField(max_length=64, primary_key=True)  # sha256 of the normalised job
    result = models.JSONField()
    created_at = models.DateTimeField(auto_now=True)

    objects = FreshQuerySet.as_manager()


class FetchLock(models.Model):
    key = models.CharField(max_length=64, primary_key=True)
    expires_at = models.DateTimeField()
