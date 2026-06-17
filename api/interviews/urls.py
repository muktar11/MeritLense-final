from rest_framework.routers import DefaultRouter

from .views import (
    InterviewConfigurationViewSet,
    InterviewRubricViewSet,
    InterviewSessionViewSet,
    QuestionTemplateViewSet,
)


router = DefaultRouter()
router.register(r"configs", InterviewConfigurationViewSet, basename="interview-config")
router.register(r"rubrics", InterviewRubricViewSet, basename="interview-rubric")
router.register(r"question-templates", QuestionTemplateViewSet, basename="question-template")
router.register(r"", InterviewSessionViewSet, basename="interview-session")

urlpatterns = router.urls
