"""
Django settings for the Climate Hazard Trend Analyzer API.

The API is read-only for clients: no sessions, no auth. SQLite (api/models.py)
stores the weather cache, geocoding cache and computed analyses. Values that differ per
environment are read from backend/.env (see .env.example).
"""

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BASE_DIR / ".env")


def env_list(name, default=""):
    return [item.strip() for item in os.getenv(name, default).split(",") if item.strip()]


SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "")
if not SECRET_KEY:
    raise RuntimeError("DJANGO_SECRET_KEY is not set. Copy .env.example to .env and fill it in.")

DEBUG = os.getenv("DJANGO_DEBUG", "False").lower() in ("1", "true", "yes")

ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1")


INSTALLED_APPS = [
    "rest_framework",
    "corsheaders",
    "api",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.common.CommonMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": os.getenv("SQLITE_PATH", str(BASE_DIR / "db.sqlite3")),
        "OPTIONS": {
            # WAL lets readers work while one thread writes; IMMEDIATE + timeout makes
            # concurrent writers wait for the lock instead of failing with "database is locked".
            "init_command": "PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;",
            "transaction_mode": "IMMEDIATE",
            "timeout": 20,
        },
    }
}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = False
USE_TZ = True


# Sent to Nominatim, whose usage policy requires an identifying User-Agent.
GEOCODER_USER_AGENT = os.getenv("GEOCODER_USER_AGENT", "climate-hazard-analyzer/1.0")


# Throttle counters live in this cache. LocMem is per process, so run one
# process with threads (see render.yaml) or point this at Redis when scaling out.
CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}

REST_FRAMEWORK = {
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_PARSER_CLASSES": [],
    "DEFAULT_AUTHENTICATION_CLASSES": [],
    "DEFAULT_PERMISSION_CLASSES": [],
    "UNAUTHENTICATED_USER": None,
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.AnonRateThrottle",    # global ceiling per client IP
        "rest_framework.throttling.ScopedRateThrottle",  # per-endpoint limits (throttle_scope)
    ],
    "DEFAULT_THROTTLE_RATES": {
        "anon": "120/min",
        "analysis": "30/min",
        "geocode": "20/min",
        # Only charged when a request needs data that is not cached yet,
        # so one client cannot burn the shared Open-Meteo quota.
        "upstream": "10/hour",
    },
    # Number of reverse proxies in front of the app; used to read the real
    # client IP from X-Forwarded-For. 0 = trust only REMOTE_ADDR.
    "NUM_PROXIES": int(os.getenv("NUM_PROXIES", "0")),
    "EXCEPTION_HANDLER": "api.exceptions.exception_handler",
}


# CORS: the frontend only ever reads.
CORS_ALLOWED_ORIGINS = env_list("CORS_ALLOWED_ORIGINS", "http://localhost:5173")
CORS_ALLOW_METHODS = ["GET", "OPTIONS"]
CORS_EXPOSE_HEADERS = ["Retry-After", "Content-Disposition"]


if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_SSL_REDIRECT = True
    SECURE_HSTS_SECONDS = 60 * 60 * 24 * 30
    SECURE_REFERRER_POLICY = "same-origin"


LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "loggers": {"api": {"handlers": ["console"], "level": "INFO"}},
}
