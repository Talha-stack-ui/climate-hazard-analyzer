"""Validation of query parameters. Nothing user-supplied reaches the database or an
upstream API without passing through here (typed, bounded, normalised)."""

import math
import re

from rest_framework import serializers

from .analysis import FIRST_YEAR, last_complete_year
from .hazards import HAZARDS
from .trends import MIN_YEARS

MAX_BOX_DEG = 15.0
PERCENTILE_RANGE = (80.0, 99.9)
DURATION_RANGE = (1, 10)
# Letters (any script), digits, spaces and common place-name punctuation.
PLACE_QUERY = re.compile(r"^[\w\s.,'()\-]+$")


class FiniteFloat(serializers.FloatField):
    def to_internal_value(self, data):
        value = super().to_internal_value(data)
        if not math.isfinite(value):
            raise serializers.ValidationError("Must be a finite number.")
        return value


class AnalysisQuerySerializer(serializers.Serializer):
    hazard = serializers.ChoiceField(choices=list(HAZARDS), default="heatwave")
    # Either a point (lat, lon) or a box (south, north, west, east).
    lat = FiniteFloat(required=False, min_value=-90, max_value=90)
    lon = FiniteFloat(required=False, min_value=-180, max_value=180)
    south = FiniteFloat(required=False, min_value=-90, max_value=90)
    north = FiniteFloat(required=False, min_value=-90, max_value=90)
    west = FiniteFloat(required=False, min_value=-180, max_value=180)
    east = FiniteFloat(required=False, min_value=-180, max_value=180)
    start_year = serializers.IntegerField()
    end_year = serializers.IntegerField()
    percentile = FiniteFloat(required=False, min_value=PERCENTILE_RANGE[0], max_value=PERCENTILE_RANGE[1])
    min_duration = serializers.IntegerField(required=False, min_value=DURATION_RANGE[0], max_value=DURATION_RANGE[1])

    def validate(self, attrs):
        point = [k for k in ("lat", "lon") if k in attrs]
        box = [k for k in ("south", "north", "west", "east") if k in attrs]
        if point and box or not (len(point) == 2 or len(box) == 4):
            raise serializers.ValidationError("Give either lat and lon, or south, north, west and east.")

        if point:
            region = {"type": "point", "lat": round(attrs["lat"], 4), "lon": round(attrs["lon"], 4)}
        else:
            s, n, w, e = (round(attrs[k], 4) for k in ("south", "north", "west", "east"))
            if s >= n or w >= e:
                raise serializers.ValidationError("Box needs south < north and west < east "
                                                  "(boxes crossing the 180° meridian are not supported).")
            if n - s > MAX_BOX_DEG or e - w > MAX_BOX_DEG:
                raise serializers.ValidationError(f"Box sides must be at most {MAX_BOX_DEG:g}°.")
            region = {"type": "box", "south": s, "north": n, "west": w, "east": e}

        start, end, last = attrs["start_year"], attrs["end_year"], last_complete_year()
        if not FIRST_YEAR <= start <= end <= last:
            raise serializers.ValidationError(f"Years must satisfy {FIRST_YEAR} <= start <= end <= {last}.")
        if end - start + 1 < MIN_YEARS:
            raise serializers.ValidationError(f"Choose at least {MIN_YEARS} years.")

        hazard = HAZARDS[attrs["hazard"]]
        return {
            "hazard": hazard.id,
            "region": region,
            "start_year": start,
            "end_year": end,
            "percentile": round(attrs.get("percentile", hazard.default_percentile), 1),
            "min_duration": attrs.get("min_duration", hazard.default_min_duration),
        }


class GeocodeQuerySerializer(serializers.Serializer):
    q = serializers.CharField(min_length=2, max_length=100)

    def validate_q(self, value):
        if not PLACE_QUERY.match(value):
            raise serializers.ValidationError("Use letters, numbers, spaces and basic punctuation only.")
        return " ".join(value.split()).lower()
