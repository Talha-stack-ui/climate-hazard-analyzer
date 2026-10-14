"""Clients for the public APIs: Open-Meteo (weather) and Nominatim (geocoding).

Every outbound call has a timeout, retries transient 5xx errors with backoff,
turns 429 into UpstreamBusy (503 + Retry-After to our client) and validates
the payload before anything is stored.
"""

import logging
import threading
import time

import requests
from django.conf import settings
from django.core.cache import cache
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .exceptions import UpstreamBusy, UpstreamError

logger = logging.getLogger(__name__)

OPEN_METEO_ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
NOMINATIM_SEARCH_URL = "https://nominatim.openstreetmap.org/search"
TIMEOUT = (5, 60)  # connect, read (seconds)

# Converters to the units we store; anything else is rejected.
TO_CELSIUS = {"°C": lambda v: v, "°F": lambda v: (v - 32) * 5 / 9, "K": lambda v: v - 273.15}
TO_MM = {"mm": lambda v: v, "inch": lambda v: v * 25.4}


def _session():
    retry = Retry(
        total=2,
        backoff_factor=1.0,
        status_forcelist=(500, 502, 503, 504),
        allowed_methods={"GET"},
        raise_on_status=False,
    )
    session = requests.Session()
    session.mount("https://", HTTPAdapter(max_retries=retry))
    session.headers["User-Agent"] = settings.GEOCODER_USER_AGENT
    return session


_http = _session()


def _get_json(url, params, provider):
    # Circuit breaker: after a 429, don't call the provider again until the window has passed.
    blocked_key = f"blocked:{provider}"
    blocked_until = cache.get(blocked_key)
    if blocked_until and blocked_until > time.time():
        raise UpstreamBusy(f"{provider} rate limit reached. Try again later.", int(blocked_until - time.time()) + 1)

    try:
        resp = _http.get(url, params=params, timeout=TIMEOUT)
    except requests.RequestException as exc:
        logger.warning("%s request failed: %s", provider, exc)
        raise UpstreamError(f"{provider} is unreachable. Try again later.") from exc

    if resp.status_code == 429:
        retry_after = _retry_after(resp)
        cache.set(blocked_key, time.time() + retry_after, retry_after)
        logger.warning("%s rate-limited us for %ss", provider, retry_after)
        raise UpstreamBusy(f"{provider} rate limit reached. Try again later.", retry_after)
    if resp.status_code != 200:
        logger.warning("%s returned %s: %s", provider, resp.status_code, resp.text[:300])
        raise UpstreamError(f"{provider} returned an error ({resp.status_code}).")
    try:
        return resp.json()
    except ValueError as exc:
        raise UpstreamError(f"{provider} returned invalid JSON.") from exc


def _retry_after(resp):
    """Retry-After header if present, else infer from Open-Meteo's message
    ("Minutely/Hourly/Daily API request limit exceeded")."""
    header = resp.headers.get("Retry-After", "")
    if header.isdigit():
        return int(header)
    reason = resp.text.lower()
    if "daily" in reason:
        return 3600 * 6
    if "hourly" in reason:
        return 3600
    return 60


# --- Open-Meteo historical weather (ERA5 reanalysis) -------------------------

def fetch_daily_weather(points, start_date, end_date):
    """Daily max temperature (°C) and precipitation (mm) for several points in one call.

    points: list of (lat, lon). Returns one dict per point, in order:
    {"lat", "lon", "elevation", "time": [iso dates], "tmax": [...], "precip": [...]}
    with None where the provider has no value.
    """
    payload = _get_json(
        OPEN_METEO_ARCHIVE_URL,
        {
            "latitude": ",".join(f"{lat:.4f}" for lat, _ in points),
            "longitude": ",".join(f"{lon:.4f}" for _, lon in points),
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "daily": "temperature_2m_max,precipitation_sum",
            "temperature_unit": "celsius",
            "precipitation_unit": "mm",
            "timezone": "UTC",
        },
        "Open-Meteo",
    )
    locations = payload if isinstance(payload, list) else [payload]
    if len(locations) != len(points):
        raise UpstreamError("Open-Meteo returned the wrong number of locations.")
    return [_parse_location(loc) for loc in locations]


def _parse_location(loc):
    try:
        daily, units = loc["daily"], loc["daily_units"]
        time_axis = daily["time"]
        tmax_raw, precip_raw = daily["temperature_2m_max"], daily["precipitation_sum"]
        to_c = TO_CELSIUS[units["temperature_2m_max"]]
        to_mm = TO_MM[units["precipitation_sum"]]
    except (KeyError, TypeError) as exc:
        raise UpstreamError("Open-Meteo response is missing fields or uses unknown units.") from exc
    if not (len(time_axis) == len(tmax_raw) == len(precip_raw)):
        raise UpstreamError("Open-Meteo returned series of different lengths.")

    def convert(values, fn):
        return [None if v is None else round(fn(float(v)), 2) for v in values]

    return {
        "lat": loc.get("latitude"),
        "lon": loc.get("longitude"),
        "elevation": loc.get("elevation"),
        "time": time_axis,
        "tmax": convert(tmax_raw, to_c),
        "precip": convert(precip_raw, to_mm),
    }


# --- Nominatim geocoding -----------------------------------------------------

class _MinInterval:
    """Allow at most one call per `seconds` across threads (Nominatim policy: 1 req/s)."""

    def __init__(self, seconds):
        self.seconds = seconds
        self._lock = threading.Lock()
        self._next = 0.0

    def wait(self):
        with self._lock:
            now = time.monotonic()
            delay = self._next - now
            self._next = max(now, self._next) + self.seconds
        if delay > 0:
            time.sleep(delay)


_nominatim_gate = _MinInterval(1.1)


def search_places(query, limit=6):
    _nominatim_gate.wait()
    rows = _get_json(
        NOMINATIM_SEARCH_URL,
        {"q": query, "format": "jsonv2", "limit": limit, "accept-language": "en"},
        "Nominatim",
    )
    if not isinstance(rows, list):
        raise UpstreamError("Nominatim returned an unexpected response.")

    places = []
    for row in rows:
        try:
            south, north, west, east = (float(v) for v in row["boundingbox"])
            places.append({
                "name": row.get("name") or row["display_name"].split(",")[0],
                "label": row["display_name"],
                "kind": row.get("addresstype") or row.get("type") or "place",
                "lat": float(row["lat"]),
                "lon": float(row["lon"]),
                "bbox": {"south": south, "north": north, "west": west, "east": east},
            })
        except (KeyError, TypeError, ValueError):
            continue  # skip malformed rows rather than failing the whole search
    return places
