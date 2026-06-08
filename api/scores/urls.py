from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    ScoreCategoryViewSet,
    ScoreSetViewSet,
    CandidateScoreViewSet,
    JobRoleScoreAreasView
)

router = DefaultRouter(trailing_slash=False)
router.register(r'categories', ScoreCategoryViewSet, basename='score-category')
router.register(r'sets', ScoreSetViewSet, basename='score-set')
router.register(r'scores', CandidateScoreViewSet, basename='candidate-score')

urlpatterns = [
    path('', include(router.urls)),
    path('job-role-areas', JobRoleScoreAreasView.as_view({'get': 'list'}), name='job-role-areas'),
    path('job-role-areas/by-role/<str:role_code>', 
         JobRoleScoreAreasView.as_view({'get': 'by_role'}), 
         name='job-role-areas-by-role'),
]
