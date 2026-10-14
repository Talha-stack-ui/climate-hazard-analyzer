from django.db import connection
from rest_framework import status
from rest_framework.response import Response
from rest_framework.throttling import SimpleRateThrottle
from rest_framework.views import APIView

from . import analysis, providers
from .hazards import BASELINE, HAZARDS
from .models import GeocodeCache
from .serializers import (
    DURATION_RANGE, MAX_BOX_DEG, PERCENTILE_RANGE, AnalysisQuerySerializer, GeocodeQuerySerializer,
)
from .trends import MIN_YEARS

# Place types shown as an area (box) rather than a single point.
AREA_KINDS = {"state", "province", "region", "state_district", "county", "district", "country"}


class UpstreamThrottle(SimpleRateThrottle):
    """Per-client budget for requests that trigger new downloads from Open-Meteo.
    Applied by hand only on a cache miss; cached regions are never charged."""

    scope = "upstream"

    def get_cache_key(self, request, view):
        return self.cache_format % {"scope": self.scope, "ident": self.get_ident(request)}


class HealthView(APIView):
    """GET /api/health/ - API and database are up."""

    def get(self, request):
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        return Response({"status": "ok"})


class MetaView(APIView):
    """GET /api/meta/ - hazards, defaults and limits, so the frontend never hard-codes them."""

    def get(self, request):
        hazards = [
            {"id": h.id, "label": h.label, "units": h.units,
             "default_percentile": h.default_percentile, "default_min_duration": h.default_min_duration}
            for h in HAZARDS.values()
        ]
        response = Response({
            "hazards": hazards,
            "default_hazard": "heatwave",
            "years": {"min": analysis.FIRST_YEAR, "max": analysis.last_complete_year(), "min_span": MIN_YEARS},
            "baseline": list(BASELINE),
            "limits": {"max_box_deg": MAX_BOX_DEG, "percentile": PERCENTILE_RANGE, "min_duration": DURATION_RANGE},
        })
        response["Cache-Control"] = "public, max-age=3600"
        return response


class GeocodeView(APIView):
    """GET /api/geocode/?q=<place> - search a city, state or region (cached 30 days)."""

    throttle_scope = "geocode"

    def get(self, request):
        params = GeocodeQuerySerializer(data=request.query_params)
        params.is_valid(raise_exception=True)
        query = params.validated_data["q"]

        hit = GeocodeCache.objects.fresh().filter(query=query).first()
        if hit:
            places = hit.places
        else:
            places = [_with_region(p) for p in providers.search_places(query)]
            GeocodeCache.objects.update_or_create(query=query, defaults={"places": places})
        return Response({"query": query, "results": places})


class AnalysisView(APIView):
    """GET /api/analysis/?hazard=heatwave&lat=..&lon=..&start_year=..&end_year=..
    (or south/north/west/east for a box). Optional: percentile, min_duration."""

    throttle_scope = "analysis"

    def get(self, request):
        params = AnalysisQuerySerializer(data=request.query_params)
        params.is_valid(raise_exception=True)
        query = params.validated_data

        job = analysis.Job(query)
        result = analysis.get_cached(job)
        cached = result is not None
        if not cached:
            if job.needs_download():
                throttle = UpstreamThrottle()
                if not throttle.allow_request(request, self):
                    self.throttled(request, throttle.wait())
            result = analysis.run(job)

        response = Response({"query": query, "cached": cached, **result}, status=status.HTTP_200_OK)
        response["Cache-Control"] = "public, max-age=3600"
        return response


def _with_region(place):
    """Suggest how to analyse a search hit: states etc. as a box, places as a point."""
    b = place["bbox"]
    fits = b["north"] - b["south"] <= MAX_BOX_DEG and b["east"] - b["west"] <= MAX_BOX_DEG
    if place["kind"] in AREA_KINDS and fits and b["south"] < b["north"] and b["west"] < b["east"]:
        region = {"type": "box", **b}
    else:
        region = {"type": "point", "lat": place["lat"], "lon": place["lon"]}
    return {**place, "region": region}
