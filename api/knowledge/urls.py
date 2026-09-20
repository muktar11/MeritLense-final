from django.urls import path, include
from rest_framework.routers import DefaultRouter

from .views import KnowledgeEntryViewSet, PublicKnowledgeBaseView

router = DefaultRouter(trailing_slash=False)
router.register(r'entries', KnowledgeEntryViewSet, basename='knowledge-entry')

urlpatterns = [
    path('faq', PublicKnowledgeBaseView.as_view(), name='knowledge-public-faq'),
    path('', include(router.urls)),
]
