from django.db import models

from api.core.models import TimeStampedModel


class KnowledgeEntry(TimeStampedModel):
    """One Q&A record from the MeritLense Master Q&A knowledge base.

    This is the controlled backend source described in the Knowledge
    Layer Security, Privacy & IP Protection spec (v1.1) - it replaces
    hand-curating "safe" content directly into frontend translation
    files. A row is never edited in place: KnowledgeContentService
    always writes an update as a brand-new row (see `supersedes`/
    `is_current`), so historical versions are retained rather than
    overwritten, per the spec's Section 7.7 change-control requirement.

    Defaults are deliberately maximally-restrictive (INTERNAL visibility,
    HIGHLY_CONFIDENTIAL classification, AI retrieval off) so a record
    created without explicit classification never accidentally becomes
    public.
    """

    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Draft"
        APPROVED = "APPROVED", "Approved"
        ARCHIVED = "ARCHIVED", "Archived"

    class Visibility(models.TextChoices):
        PUBLIC = "PUBLIC", "Public"
        AUTHENTICATED = "AUTHENTICATED", "Authenticated"
        PARTNER = "PARTNER", "Partner"
        INTERNAL = "INTERNAL", "Internal"

    class ContentClassification(models.TextChoices):
        PUBLIC = "PUBLIC", "Public"
        BUSINESS_CONFIDENTIAL = "BUSINESS_CONFIDENTIAL", "Business Confidential"
        PROPRIETARY = "PROPRIETARY", "Proprietary"
        HIGHLY_CONFIDENTIAL = "HIGHLY_CONFIDENTIAL", "Highly Confidential"

    # Stable identifier shared by every version of the same question
    # (e.g. "ML-QA-001") - NOT unique by itself, since multiple rows
    # (versions) can share it; exactly one has is_current=True at a time
    # (enforced by the partial unique constraint below).
    question_id = models.CharField(max_length=20, db_index=True)

    category = models.CharField(max_length=100)
    category_ar = models.CharField(max_length=100, blank=True)
    category_id = models.SlugField(max_length=100, help_text="Stable slug for grouping/ordering on the public page.")
    category_order = models.PositiveIntegerField(default=0)
    sequence = models.PositiveIntegerField(default=0, help_text="Display order within the category.")

    question_en = models.TextField()
    question_ar = models.TextField(blank=True)
    short_answer_en = models.TextField()
    short_answer_ar = models.TextField(blank=True)
    detailed_answer_en = models.TextField(blank=True)
    detailed_answer_ar = models.TextField(blank=True)

    audience = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    version = models.CharField(max_length=20, default="v1.0")
    owner = models.CharField(max_length=150, blank=True)

    visibility = models.CharField(max_length=20, choices=Visibility.choices, default=Visibility.INTERNAL)
    content_classification = models.CharField(
        max_length=30, choices=ContentClassification.choices, default=ContentClassification.HIGHLY_CONFIDENTIAL
    )
    sensitive_content = models.BooleanField(default=False)
    ai_retrieval_allowed = models.BooleanField(default=False)
    last_security_review = models.DateField(null=True, blank=True)

    is_current = models.BooleanField(
        default=True,
        db_index=True,
        help_text="Only one row per question_id may be current at a time - this is what every read query filters on.",
    )
    supersedes = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL, related_name="superseded_by_entries"
    )

    class Meta:
        verbose_name = "Knowledge entry"
        verbose_name_plural = "Knowledge entries"
        ordering = ["category_order", "sequence", "question_id"]
        constraints = [
            models.UniqueConstraint(
                fields=["question_id"],
                condition=models.Q(is_current=True),
                name="unique_current_knowledge_entry_per_question_id",
            )
        ]
        indexes = [
            models.Index(fields=["is_current", "status", "visibility"]),
        ]

    def __str__(self):
        return f"{self.question_id} ({self.version}) - {self.visibility}"

    @property
    def is_publicly_visible(self):
        return self.is_current and self.status == self.Status.APPROVED and self.visibility == self.Visibility.PUBLIC
