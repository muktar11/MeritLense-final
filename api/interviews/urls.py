from rest_framework.routers import DefaultRouter

from .views import (
    InterviewConfigurationViewSet,
    InterviewRubricViewSet,
    InterviewSessionViewSet,
    PackageSessionConfigViewSet,
    QuestionTemplateViewSet,
    ResponseAIProcessingViewSet,
    RolePackageCoverageViewSet,
)


router = DefaultRouter()
router.register(r"configs", InterviewConfigurationViewSet, basename="interview-config")
router.register(r"rubrics", InterviewRubricViewSet, basename="interview-rubric")
router.register(r"package-configs", PackageSessionConfigViewSet, basename="package-session-config")
router.register(r"role-coverage", RolePackageCoverageViewSet, basename="role-package-coverage")
router.register(r"question-templates", QuestionTemplateViewSet, basename="question-template")
router.register(r"responses", ResponseAIProcessingViewSet, basename="interview-response")
router.register(r"", InterviewSessionViewSet, basename="interview-session")

urlpatterns = router.urls
