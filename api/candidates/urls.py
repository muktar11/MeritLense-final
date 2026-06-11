from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import CandidateViewSet

router = DefaultRouter(trailing_slash=False)
router.register(r'candidates', CandidateViewSet, basename='candidate')

urlpatterns = [
    path('', include(router.urls)),
]
