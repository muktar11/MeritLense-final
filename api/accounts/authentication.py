from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import AuthenticationFailed


class PasswordChangeAwareJWTAuthentication(JWTAuthentication):
    """Rejects an access token issued before the user's most recent password
    change/reset.

    Without this, changing or resetting a password only stopped a *new*
    refresh from succeeding (via the token blacklist) - any access token
    already issued kept working for the rest of its lifetime regardless of
    the password change, contradicting the "please log in again" messaging
    shown to the user.
    """

    def get_user(self, validated_token):
        user = super().get_user(validated_token)

        password_changed_at = getattr(user, "password_changed_at", None)
        issued_at = validated_token.get("iat")
        if password_changed_at and issued_at is not None:
            # "iat" is a whole-second Unix timestamp while password_changed_at
            # has microsecond precision - floor the threshold to whole
            # seconds so a token minted in the same wall-clock second as the
            # password change (e.g. an immediate re-login) isn't rejected.
            changed_at_epoch = int(password_changed_at.timestamp())
            if issued_at < changed_at_epoch:
                raise AuthenticationFailed(
                    "This session is no longer valid because the password was changed. Please log in again.",
                    code="password_changed",
                )

        return user
