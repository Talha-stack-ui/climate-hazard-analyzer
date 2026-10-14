import logging

from django.db import DatabaseError
from rest_framework import status
from rest_framework.exceptions import APIException
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

logger = logging.getLogger(__name__)


class UpstreamError(APIException):
    """A public data provider failed or returned something unusable."""

    status_code = status.HTTP_502_BAD_GATEWAY
    default_detail = "The weather data provider returned an invalid response. Try again later."
    default_code = "upstream_error"


class InsufficientData(APIException):
    """The data exists but cannot support the requested analysis."""

    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    default_detail = "Not enough data for this analysis."
    default_code = "insufficient_data"


class UpstreamBusy(APIException):
    """A provider rate-limited us, or another request is already fetching this region."""

    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    default_detail = "The weather data provider is busy. Try again in a minute."
    default_code = "upstream_busy"

    def __init__(self, detail=None, retry_after=60):
        super().__init__(detail)
        self.retry_after = retry_after


def exception_handler(exc, context):
    """DRF errors as usual; database and unexpected errors as JSON without internals."""
    response = drf_exception_handler(exc, context)
    if response is not None:
        if isinstance(exc, UpstreamBusy):
            response["Retry-After"] = str(exc.retry_after)
        return response

    if isinstance(exc, DatabaseError):
        logger.exception("Database error")
        return Response(
            {"detail": "Database unavailable. Try again later."},
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    logger.exception("Unhandled error")
    return Response({"detail": "Internal server error."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
