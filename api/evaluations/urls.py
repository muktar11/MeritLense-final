from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import CandidateScoreSummaryView, CertificateVerifyView, EvaluationViewSet, ScoringRuleSetViewSet
from api.reports.urls import urlpatterns as report_urlpatterns

router = DefaultRouter(trailing_slash=False)
router.register(r'evaluations', EvaluationViewSet, basename='evaluation')
router.register(r'rule-sets', ScoringRuleSetViewSet, basename='scoring-rule-set')

urlpatterns = [
    path('candidate-scores', CandidateScoreSummaryView.as_view(), name='candidate-score-summaries'),
    path('', include(router.urls)),
    path('', include(report_urlpatterns)),
    path('certificates/verify/<str:certificate_id>', CertificateVerifyView.as_view(), name='certificate-verify'),
]
