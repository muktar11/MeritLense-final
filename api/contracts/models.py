from django.db import models

from api.core.models import TimeStampedModel
from api.core.constants import AgreementType, AgreementMethod, AgreementStatus


class Agreement(TimeStampedModel):
    user = models.ForeignKey(
        'accounts.User',
        on_delete=models.CASCADE,
        related_name='agreements',
    )
    company = models.ForeignKey(
        'accounts.Company',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='agreements',
        help_text="Set for company-scoped agreements (B2B Agreement, DPA) so the whole "
                   "company is gated, not just the signing user.",
    )

    agreement_type = models.CharField(max_length=30, choices=AgreementType.CHOICES)
    version = models.CharField(max_length=20)
    method = models.CharField(max_length=20, choices=AgreementMethod.CHOICES)
    status = models.CharField(
        max_length=20, choices=AgreementStatus.CHOICES, default=AgreementStatus.PENDING
    )

    # Typed full legal name at signing time (OTP method only).
    signatory_name = models.CharField(max_length=255, blank=True)
    accepted_at = models.DateTimeField(null=True, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)

    # OTP challenge state — cleared once confirmed or expired.
    otp_code_hash = models.CharField(max_length=128, blank=True)
    otp_expires_at = models.DateTimeField(null=True, blank=True)
    otp_attempts = models.PositiveSmallIntegerField(default=0)
    otp_resend_count = models.PositiveSmallIntegerField(default=0)
    otp_last_sent_at = models.DateTimeField(null=True, blank=True)
    # Shared across a bundle of agreements signed together with one OTP
    # (e.g. B2B Agreement + DPA are signed with a single code).
    otp_reference = models.CharField(max_length=100, blank=True, db_index=True)

    # B2B only: signatory declared authorization to bind the organization,
    # confirmed before the OTP was dispatched.
    auth_checkbox_confirmed = models.BooleanField(default=False)

    contract_id = models.CharField(max_length=50, blank=True)
    signed_pdf = models.FileField(upload_to='agreements/', null=True, blank=True)
    pdf_hash = models.CharField(max_length=64, blank=True, help_text="SHA-256 hex digest of the signed PDF binary.")

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['user', 'agreement_type', 'status']),
            models.Index(fields=['company', 'agreement_type', 'status']),
        ]

    def __str__(self):
        return f"{self.agreement_type} v{self.version} - user {self.user_id} ({self.status})"
