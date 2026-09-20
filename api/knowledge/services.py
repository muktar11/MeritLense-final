from api.audit.services import AuditLogService
from api.core.constants import AuditLogAction, AuditLogCategory

from .models import KnowledgeEntry

# Fields copied forward from the previous version of a KnowledgeEntry when
# creating a new one - everything except identity/bookkeeping columns that
# must never be inherited (pk, public_id, timestamps, the is_current/
# supersedes pointers themselves).
_VERSIONED_FIELD_NAMES = [
    field.name
    for field in KnowledgeEntry._meta.fields
    if field.name not in {"id", "public_id", "created_at", "updated_at", "is_current", "supersedes"}
]


class KnowledgeContentService:
    """Every content change is a new row, never an in-place edit - this is
    what satisfies the spec's versioning requirement ("future content
    updates do not unexpectedly overwrite historical versions"). The
    previous row is kept forever with is_current=False; only the single
    current row per question_id is ever eligible to be served."""

    @classmethod
    def create_new_version(cls, *, actor, previous=None, question_id=None, **overrides):
        if previous is not None:
            question_id = previous.question_id
            base_fields = {name: getattr(previous, name) for name in _VERSIONED_FIELD_NAMES}
        else:
            if not question_id:
                raise ValueError("question_id is required when there is no previous version")
            base_fields = {}

        base_fields.update(overrides)
        base_fields["question_id"] = question_id

        # Demote the previous row to not-current BEFORE inserting the new
        # one - the reverse order would momentarily have two rows sharing
        # the same question_id with is_current=True, tripping the partial
        # unique constraint that exists precisely to prevent that.
        if previous is not None:
            previous.is_current = False
            previous.save(update_fields=["is_current", "updated_at"])

        entry = KnowledgeEntry.objects.create(is_current=True, supersedes=previous, **base_fields)

        AuditLogService.log(
            user=actor,
            action=AuditLogAction.KNOWLEDGE_ENTRY_UPDATED if previous else AuditLogAction.KNOWLEDGE_ENTRY_CREATED,
            category=AuditLogCategory.KNOWLEDGE_BASE,
            description=(
                f"Knowledge entry {question_id} "
                f"{'updated to' if previous else 'created as'} version {entry.version}"
            ),
            resource=entry,
            data={
                "question_id": question_id,
                "version": entry.version,
                "status": entry.status,
                "visibility": entry.visibility,
                "content_classification": entry.content_classification,
                "previous_version_id": previous.id if previous else None,
            },
        )
        return entry

    @classmethod
    def archive(cls, entry, *, actor):
        """Soft-removal only - the row (and its full version history)
        stays in the database for the administrative record; it just stops
        being current/servable. A hard delete would destroy the audit
        trail the spec requires, so ModelViewSet's default destroy() is
        never used here."""
        if entry.status == KnowledgeEntry.Status.ARCHIVED and not entry.is_current:
            return entry

        entry.status = KnowledgeEntry.Status.ARCHIVED
        entry.is_current = False
        entry.save(update_fields=["status", "is_current", "updated_at"])

        AuditLogService.log(
            user=actor,
            action=AuditLogAction.KNOWLEDGE_ENTRY_ARCHIVED,
            category=AuditLogCategory.KNOWLEDGE_BASE,
            description=f"Knowledge entry {entry.question_id} (version {entry.version}) archived",
            resource=entry,
            data={"question_id": entry.question_id, "version": entry.version},
        )
        return entry
