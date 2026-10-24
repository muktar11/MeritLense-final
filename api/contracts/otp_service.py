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

    def _html(self, code, language):
        from api.core.email_i18n import ARABIC
        from api.core.email_template import code_security_notice, render_email

        minutes = settings.AGREEMENT_OTP_VALIDITY_MINUTES
        if language == ARABIC:
            title, use, label = ("رمز التوقيع الخاص بك", "استخدم الرمز أدناه لتوقيع اتفاقيتك في MeritLense.",
                                 "رمز التوقيع الخاص بك")
            expiry = f"تنتهي صلاحية هذا الرمز خلال {minutes} دقيقة، ولا يمكن استخدامه إلا مرة واحدة."
            ignore, eyebrow = "إذا لم تطلب هذا الرمز، يمكنك تجاهل هذه الرسالة بأمان.", "توقيع الاتفاقية"
        else:
            title, use, label = ("Your signing code", "Use the code below to sign your MeritLense agreement.",
                                 "Your signing code")
            expiry = f"Expires in {minutes} minutes and can only be used once."
            ignore, eyebrow = "If you didn't request this code, you can safely ignore this email.", "Agreement signing"
        return render_email(language=language, title=title, eyebrow=eyebrow, preheader=use, blocks=[
            ("p", use),
            ("code", {"code": code, "label": label, "expiry": expiry}),
            code_security_notice(language),
            ("p", ignore),
        ])

    def send(self, email, code, language="en"):
        from api.accounts.utils import safe_send_mail
        from api.core.email_i18n import ARABIC, SIGNATURE

        if language == ARABIC:
            minutes = settings.AGREEMENT_OTP_VALIDITY_MINUTES
            try:
                sent_count = safe_send_mail("رمز التوقيع الخاص بك في MeritLense", f"""رمز التحقق لتوقيع اتفاقيتك في MeritLense هو: {code}

تنتهي صلاحية هذا الرمز خلال {minutes} دقيقة، ولا يمكن استخدامه إلا مرة واحدة.

إذا لم تطلب هذا الرمز، يمكنك تجاهل هذه الرسالة بأمان.

{SIGNATURE[ARABIC]}
""", [email], html_message=self._html(code, language))
                return sent_count > 0
            except Exception:
                logger.exception("Failed to send OTP email to %s", email)
                return False

        subject = "Your MeritLense signing code"
        message = f"""
        Your verification code to sign your MeritLense agreement is: {code}

        This code expires in {settings.AGREEMENT_OTP_VALIDITY_MINUTES} minutes and can only be used once.

        If you didn't request this code, you can safely ignore this email.

        Best regards,
        MeritLense Team
        """
        try:
            sent_count = safe_send_mail(subject, message, [email], html_message=self._html(code, language))
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
