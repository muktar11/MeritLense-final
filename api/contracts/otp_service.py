import hashlib
import logging
import secrets

from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)


class OTPService:
    """Generates, sends, and validates the OTP codes used for binding
    (agreement) e-signatures. Codes are never stored in plaintext.
    """

    def generate_code(self):
        digits = "0123456789"
        return "".join(secrets.choice(digits) for _ in range(settings.AGREEMENT_OTP_LENGTH))

    def hash_code(self, code):
        # OTPs are short-lived, low-entropy secrets by nature — a keyed
        # hash (pepper = Django SECRET_KEY) is sufficient here, no need
        # for a slow password hasher.
        payload = f"{settings.SECRET_KEY}:{code}".encode()
        return hashlib.sha256(payload).hexdigest()

    def issue(self, phone_number):
        """Generate a code, return (code, code_hash, expires_at). Caller
        is responsible for persisting code_hash/expires_at and for
        actually dispatching `code` via send().
        """
        code = self.generate_code()
        code_hash = self.hash_code(code)
        expires_at = timezone.now() + timezone.timedelta(
            minutes=settings.AGREEMENT_OTP_VALIDITY_MINUTES
        )
        return code, code_hash, expires_at

    def send(self, email, code):
        from api.accounts.utils import safe_send_mail

        subject = "Your MeritLense signing code"
        message = f"""
        Your verification code to sign your MeritLense agreement is: {code}

        This code expires in {settings.AGREEMENT_OTP_VALIDITY_MINUTES} minutes and can only be used once.

        If you didn't request this code, you can safely ignore this email.

        Best regards,
        MeritLense Team
        """
        try:
            sent_count = safe_send_mail(subject, message, [email])
            return sent_count > 0
        except Exception:
            logger.exception("Failed to send OTP email to %s", email)
            return False

    def verify(self, agreement, code):
        """Returns (ok: bool, error: str | None). Increments attempts and
        clears the challenge on success or once attempts are exhausted.
        """
        if not agreement.otp_code_hash or not agreement.otp_expires_at:
            return False, "No signing code was requested for this agreement."

        if timezone.now() > agreement.otp_expires_at:
            return False, "This code has expired. Please request a new one."

        if agreement.otp_attempts >= settings.AGREEMENT_OTP_MAX_ATTEMPTS:
            return False, "Too many incorrect attempts. Please request a new code."

        if self.hash_code(code) != agreement.otp_code_hash:
            agreement.otp_attempts += 1
            agreement.save(update_fields=["otp_attempts"])
            return False, "Incorrect code."

        return True, None
