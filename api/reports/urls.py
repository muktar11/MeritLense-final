from rest_framework.routers import DefaultRouter

from api.reports.views import EvaluationReportViewSet

router = DefaultRouter(trailing_slash=False)
router.register(r"reports", EvaluationReportViewSet, basename="evaluation-report")

urlpatterns = router.urls
