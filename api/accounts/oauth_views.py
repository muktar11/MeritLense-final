import logging

from django.conf import settings
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from api.audit.services import AuditLogService
from api.core.constants import AuditLogAction, AuditLogCategory

from .models import User
from .serializers import CustomTokenObtainPairSerializer

logger = logging.getLogger(__name__)


def build_oauth_login_response(user):
    """Same response shape as CustomTokenObtainPairSerializer.validate()
    (api/accounts/serializers.py) for a normal email/password login, so the
    frontend can reuse identical post-login handling regardless of how the
    user actually signed in. Mints tokens directly since there's no
    password to run through the parent serializer's validate()."""
    refresh = CustomTokenObtainPairSerializer.get_token(user)
    return {
        'refresh': str(refresh),
        'access': str(refresh.access_token),
        'user_id': str(user.public_id),
        'role': user.role,
        'is_superuser': user.is_superuser,
        'is_staff': user.is_staff,
        'is_verified': user.is_verified,
        'documents_verified': user.documents_verified,
        'full_name': user.get_full_name(),
    }


class GoogleLoginView(APIView):
    """POST /auth/oauth/google - body: {"id_token": "<Google ID token>"}.

    Logs an EXISTING account in after verifying the Google ID token
    server-side. Deliberately does not create new accounts - sign-up still
    goes through the normal registration flow, matching how the Google
    button is only shown on the login page, never on register.
    """
    permission_classes = [AllowAny]

    def post(self, request):
        token = request.data.get('id_token')
        if not token:
            return Response({'error': 'id_token is required'}, status=status.HTTP_400_BAD_REQUEST)

        client_id = getattr(settings, 'GOOGLE_OAUTH_CLIENT_ID', '')
        if not client_id:
            logger.error("GOOGLE_OAUTH_CLIENT_ID is not configured")
            return Response(
                {'error': 'Google sign-in is not configured.'},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        try:
            from google.auth.transport import requests as google_requests
            from google.oauth2 import id_token as google_id_token

            payload = google_id_token.verify_oauth2_token(token, google_requests.Request(), client_id)
        except Exception:
            logger.warning("Google ID token verification failed", exc_info=True)
            return Response(
                {'error': 'Could not verify Google sign-in. Please try again.'},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        if not payload.get('email_verified', False):
            return Response(
                {'error': 'Your Google account email is not verified.'},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        email = (payload.get('email') or '').lower()
        try:
            user = User.objects.get(email__iexact=email)
        except User.DoesNotExist:
            return Response(
                {'error': 'No MeritLense account found for this email. Please sign up first.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        if not user.is_active:
            return Response({'error': 'This account has been disabled.'}, status=status.HTTP_403_FORBIDDEN)

        # Google has already independently verified ownership of this
        # mailbox - the same proof our own 5-digit email code exists to
        # establish - so honor it instead of leaving the account stuck
        # asking for a code the user has no way to see from this flow.
        if not user.is_verified:
            user.is_verified = True
            user.save(update_fields=['is_verified'])
            AuditLogService.log(
                user=user,
                action=AuditLogAction.USER_VERIFIED,
                category=AuditLogCategory.USER,
                description=f"Email auto-verified via Google sign-in: {user.email}",
                resource=user,
                request=request,
            )

        return Response(build_oauth_login_response(user), status=status.HTTP_200_OK)
