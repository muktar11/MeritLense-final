from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import EvaluationViewSet

router = DefaultRouter(trailing_slash=False)
router.register(r'evaluations', EvaluationViewSet, basename='evaluation')

urlpatterns = [
    path('', include(router.urls)),
]
