from django.core import signing
from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.authentication import JWTAuthentication


TICKET_SALT = "meritlense.live-call.v1"


class OptionalJWTAuthentication(JWTAuthentication):
    """JWTAuthentication, but a present-and-invalid token degrades to
    anonymous instead of a hard 401.

    These endpoints are AllowAny and meant to serve both an authenticated
    evaluator and an anonymous candidate (identified by a session token in
    the request body/query, not a header) - but AllowAny only controls the
    permission check, which runs *after* authentication. The candidate's
    browser is often the same one an evaluator has since logged into on
    this origin (localStorage persists a stale/expired accessToken across
    tabs), so plain JWTAuthentication raises AuthenticationFailed before
    the view ever gets a chance to fall through to the token-based
    candidate path. An absent header still behaves exactly as before.
    """

    def authenticate(self, request):
        try:
            return super().authenticate(request)
        except AuthenticationFailed:
            return None


def issue_socket_ticket(call, role):
    return signing.dumps({"call": str(call.public_id), "role": role}, salt=TICKET_SALT, compress=True)


def read_socket_ticket(ticket, max_age):
    return signing.loads(ticket, salt=TICKET_SALT, max_age=max_age)

