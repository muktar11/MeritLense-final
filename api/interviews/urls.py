from rest_framework.routers import DefaultRouter

from .views import InterviewConfigurationViewSet, InterviewSessionViewSet


router = DefaultRouter()
router.register(r"configs", InterviewConfigurationViewSet, basename="interview-config")
router.register(r"", InterviewSessionViewSet, basename="interview-session")

urlpatterns = router.urls
