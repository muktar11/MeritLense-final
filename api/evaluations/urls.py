from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import EvaluationViewSet, ScoringRuleSetViewSet
from api.reports.urls import urlpatterns as report_urlpatterns

router = DefaultRouter(trailing_slash=False)
router.register(r'evaluations', EvaluationViewSet, basename='evaluation')
router.register(r'rule-sets', ScoringRuleSetViewSet, basename='scoring-rule-set')

urlpatterns = [
    path('', include(router.urls)),
    path('', include(report_urlpatterns)),
]
