from django.contrib import admin
from django.urls import path, include
from rest_framework_simplejwt.views import TokenRefreshView
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView
from api.accounts.serializers import CustomTokenObtainPairSerializer
from rest_framework_simplejwt.views import TokenObtainPairView
from api.core.views import health_check

class CustomTokenObtainPairView(TokenObtainPairView):
    serializer_class = CustomTokenObtainPairSerializer

api_v1_patterns = [
    path('health', health_check, name='api-v1-health'),
    path('auth/login', CustomTokenObtainPairView.as_view(), name='api-v1-token-obtain-pair'),
    path('auth/refresh', TokenRefreshView.as_view(), name='api-v1-token-refresh'),
    path('auth/', include('api.accounts.urls')),
    path('interviews/', include('api.interviews.urls')),
    path('candidates/', include('api.candidates.urls')),
    path('evaluations/', include('api.evaluations.urls')),
    path('scores/', include('api.scores.urls')),
    path('payments/', include('api.payments.urls')),
    path('dashboard/', include('api.dashboard.urls')),
    path('audit/', include('api.audit.urls')),
    path('agreements/', include('api.contracts.urls')),
    path('live-calls/', include('api.live_calls.urls')),
    path('schema', SpectacularAPIView.as_view(), name='api-v1-schema'),
    path('docs', SpectacularSwaggerView.as_view(url_name='api-v1-schema'), name='api-v1-swagger-ui'),
]

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/v1/', include(api_v1_patterns)),
]
