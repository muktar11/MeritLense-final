from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import ServiceIncidentViewSet

router = DefaultRouter(trailing_slash=False)
router.register(r"incidents", ServiceIncidentViewSet, basename="service-incident")

urlpatterns = [
    path("", include(router.urls)),
]
