import uuid

from django.conf import settings
from django.db import models

from api.candidates.models import Candidate
from api.core.models import TimeStampedModel


class AgreementType:
    PRIVACY_TERMS = "privacy_terms"
    AI_DISCLOSURE = "ai_disclosure"
    B2B_AGREEMENT = "b2b_agreement"
    DPA = "dpa"
    B2C_AGREEMENT = "b2c_agreement"
    CANDIDATE_CONSENT = "candidate_consent"

    CHOICES = [
        (PRIVACY_TERMS, "Privacy Policy & Terms of Use"),
        (AI_DISCLOSURE, "AI Transparency & Disclosure"),
        (B2B_AGREEMENT, "B2B Agreement"),
        (DPA, "Data Processing Agreement"),
        (B2C_AGREEMENT, "B2C Agreement"),
        (CANDIDATE_CONSENT, "Candidate Consent"),
    ]


class AgreementMethod:
    CHECKBOX = "checkbox"
    OTP_SIGNATURE = "otp_signature"

    CHOICES = [
        (CHECKBOX, "Checkbox"),
        (OTP_SIGNATURE, "OTP Signature"),
    ]


class AgreementStatus:
    DRAFT = "draft"
    PENDING_REVIEW = "pending_review"
    PENDING_SIGNATURE = "pending_signature"
    SIGNED = "signed"
    EXPIRED = "expired"
    SUPERSEDED = "superseded"

    CHOICES = [
        (DRAFT, "Draft"),
        (PENDING_REVIEW, "Pending Review"),
        (PENDING_SIGNATURE, "Pending Signature"),
        (SIGNED, "Signed"),
        (EXPIRED, "Expired"),
        (SUPERSEDED, "Superseded"),
    ]


class AgreementEventType:
    CREATED = "created"
    REVIEWED = "reviewed"
    CHECKBOX_ACCEPTED = "checkbox_accepted"
    OTP_SENT = "otp_sent"
    OTP_RESENT = "otp_resent"
    OTP_CONFIRMED = "otp_confirmed"
    SIGNED = "signed"
    DOWNLOAD = "download"
    VERSION_MISMATCH = "version_mismatch"
    PRIVACY_NOTICE_ACKNOWLEDGED = "privacy_notice_acknowledged"
    VERBAL_CONFIRMATION_RECORDED = "verbal_confirmation_recorded"

    CHOICES = [
        (CREATED, "Created"),
        (REVIEWED, "Reviewed"),
        (CHECKBOX_ACCEPTED, "Checkbox Accepted"),
        (OTP_SENT, "OTP Sent"),
        (OTP_RESENT, "OTP Resent"),
        (OTP_CONFIRMED, "OTP Confirmed"),
        (SIGNED, "Signed"),
        (DOWNLOAD, "Downloaded"),
        (VERSION_MISMATCH, "Version Mismatch"),
        (PRIVACY_NOTICE_ACKNOWLEDGED, "Privacy Notice Acknowledged"),
        (VERBAL_CONFIRMATION_RECORDED, "Verbal Confirmation Recorded"),
    ]


def company_stamp_upload_to(instance, filename):
    return f"documents/agreements/company-stamps/{instance.user_id}/{filename}"


class Agreement(TimeStampedModel):
    agreement_id = models.CharField(max_length=32, unique=True, editable=False, db_index=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="agreements",
        null=True,
        blank=True,
    )
    candidate = models.ForeignKey(
        Candidate,
        on_delete=models.CASCADE,
        related_name="agreements",
        null=True,
        blank=True,
    )
    session = models.ForeignKey(
        "interview_sessions.InterviewSession",
        on_delete=models.CASCADE,
        related_name="agreements",
        null=True,
        blank=True,
    )
    subscription = models.ForeignKey(
        "payments.Subscription",
        on_delete=models.SET_NULL,
        related_name="agreements",
        null=True,
        blank=True,
    )
    agreement_type = models.CharField(max_length=40, choices=AgreementType.CHOICES, db_index=True)
    version = models.CharField(max_length=20)
    status = models.CharField(
        max_length=20,
        choices=AgreementStatus.CHOICES,
        default=AgreementStatus.DRAFT,
        db_index=True,
    )
    method = models.CharField(max_length=20, choices=AgreementMethod.CHOICES)
    signatory_name = models.CharField(max_length=255, blank=True)
    signed_at = models.DateTimeField(null=True, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)
    otp_reference = models.CharField(max_length=64, blank=True)
    otp_code = models.CharField(max_length=10, blank=True)
    otp_attempts = models.PositiveIntegerField(default=0)
    otp_expires_at = models.DateTimeField(null=True, blank=True)
    otp_last_sent_at = models.DateTimeField(null=True, blank=True)
    auth_checkbox_confirmed = models.BooleanField(default=False)
    pdf_path = models.TextField(blank=True)
    pdf_hash = models.CharField(max_length=64, blank=True)
    verification_url = models.URLField(blank=True)
    verbal_audio_path = models.TextField(blank=True)
    previous_version = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        related_name="superseding_versions",
        null=True,
        blank=True,
    )
    company_stamp = models.FileField(upload_to=company_stamp_upload_to, null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        verbose_name = "Agreement"
        verbose_name_plural = "Agreements"
        indexes = [
            models.Index(fields=["user", "agreement_type", "-created_at"]),
            models.Index(fields=["candidate", "agreement_type", "-created_at"]),
            models.Index(fields=["session", "agreement_type"]),
            models.Index(fields=["status", "agreement_type"]),
            models.Index(fields=["agreement_id"]),
        ]
        ordering = ["-created_at"]

    def __str__(self):
        owner = self.user.email if self.user_id else (self.candidate.email if self.candidate_id else "unknown")
        return f"{self.agreement_id} - {self.agreement_type} - {owner}"

    def save(self, *args, **kwargs):
        if not self.agreement_id:
            self.agreement_id = self.build_agreement_id()
        super().save(*args, **kwargs)

    @staticmethod
    def build_agreement_id():
        return f"ML-AGR-{uuid.uuid4().hex[:12].upper()}"


class AgreementEvent(TimeStampedModel):
    agreement = models.ForeignKey(
        Agreement,
        on_delete=models.CASCADE,
        related_name="events",
    )
    event_type = models.CharField(max_length=50, choices=AgreementEventType.CHOICES)
    description = models.TextField(blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)
    data = models.JSONField(default=dict, blank=True)

    class Meta:
        verbose_name = "Agreement Event"
        verbose_name_plural = "Agreement Events"
        indexes = [
            models.Index(fields=["agreement", "-created_at"]),
            models.Index(fields=["event_type", "-created_at"]),
        ]
        ordering = ["created_at"]

    def __str__(self):
        return f"{self.agreement.agreement_id} - {self.event_type}"


class CookieConsent(TimeStampedModel):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="cookie_consents",
        null=True,
        blank=True,
    )
    visitor_key = models.CharField(max_length=64, blank=True, db_index=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)
    categories_accepted = models.JSONField(default=dict, blank=True)
    expires_at = models.DateTimeField()

    class Meta:
        verbose_name = "Cookie Consent"
        verbose_name_plural = "Cookie Consents"
        indexes = [
            models.Index(fields=["user", "-created_at"]),
            models.Index(fields=["visitor_key", "-created_at"]),
            models.Index(fields=["expires_at"]),
        ]
        ordering = ["-created_at"]

    def __str__(self):
        return self.user.email if self.user_id else self.visitor_key or "anonymous-consent"
