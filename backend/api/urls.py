from django.urls import path

from .views import AnalysisView, GeocodeView, HealthView, MetaView

urlpatterns = [
    path("health/", HealthView.as_view(), name="health"),
    path("meta/", MetaView.as_view(), name="meta"),
    path("geocode/", GeocodeView.as_view(), name="geocode"),
    path("analysis/", AnalysisView.as_view(), name="analysis"),
]
