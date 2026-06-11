from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import AuditLogViewSet

router = DefaultRouter(trailing_slash=False)
router.register(r'logs', AuditLogViewSet, basename='audit-log')

urlpatterns = [
    path('', include(router.urls)),
]
