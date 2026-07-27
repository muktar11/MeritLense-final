from django.urls import path
from rest_framework.routers import DefaultRouter

from api.reports.views import EvaluationReportViewSet, verify_report

router = DefaultRouter(trailing_slash=False)
router.register(r"reports", EvaluationReportViewSet, basename="evaluation-report")

urlpatterns = [
    *router.urls,
    path("reports/verify/<str:report_number>", verify_report, name="evaluation-report-verify"),
]
