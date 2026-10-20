"""Service incidents for platform failures (rulebook EXC-04, EXC-06).

An incident is opened where an assessment outcome could otherwise be lost
without anyone noticing. Administrators are emailed once per incident and
work it through the admin API. Valid unaffected outputs stay available;
an incident never changes a result (EXC-07).
"""
import logging
from datetime import timedelta

from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction
from django.utils import timezone

from api.core.constants import Roles

from .models import ServiceIncident

logger = logging.getLogger(__name__)

# Default next action per cause; the owner can change it.
NEXT_ACTIONS = {
    ServiceIncident.CAUSE_SCORING_FAILED: "Check the scoring error, fix the cause and re-run scoring for the evaluation.",
    ServiceIncident.CAUSE_REPORT_FAILED: "Check the report error, fix the cause and regenerate the report.",
    ServiceIncident.CAUSE_CERTIFICATE_FAILED: "Check the certificate error and re-run certificate generation if the candidate is eligible.",
    ServiceIncident.CAUSE_AI_PROCESSING_FAILED: (
        "Re-run AI processing for the failed answers. If the evidence cannot be recovered, the assessment "
        "stays Incomplete; decide on a recapture or points restoration."
    ),
    ServiceIncident.CAUSE_QUEUE_POISONED: (
        "Inspect the poison-queue message, fix the cause and re-run AI processing for the answer; "
        "the session's outputs are not generated until it is processed."
    ),
}


class ServiceIncidentService:
    @classmethod
    def report(cls, *, cause, summary, details="", session=None, evaluation=None, affected_output=""):
        """Open an incident, or count a repeat of an active one for the same
        session and cause. Never raises: incident recording must not break
        the flow that reported it."""
        try:
            return cls._report(
                cause=cause, summary=summary, details=details, session=session,
                evaluation=evaluation, affected_output=affected_output,
            )
        except Exception:
            logger.exception("Could not record service incident: %s", summary)
            return None

    @classmethod
    def _report(cls, *, cause, summary, details, session, evaluation, affected_output):
        now = timezone.now()
        if evaluation is None and session is not None:
            evaluation = getattr(session, "linked_evaluation", None)
        with transaction.atomic():
            existing = None
            if session is not None:
                existing = (
                    ServiceIncident.objects.select_for_update()
                    .filter(session=session, cause=cause, status__in=ServiceIncident.ACTIVE_STATUSES)
                    .first()
                )
            if existing is not None:
                existing.occurrence_count += 1
                existing.last_seen_at = now
                if details:
                    existing.details = f"{existing.details}\n[{now.isoformat()}] {details}".strip()[-20000:]
                existing.save(update_fields=["occurrence_count", "last_seen_at", "details", "updated_at"])
                return existing
            incident = ServiceIncident.objects.create(
                cause=cause,
                summary=summary[:255],
                details=details,
                affected_output=affected_output,
                session=session,
                evaluation=evaluation,
                company=getattr(session, "organization", None) or getattr(evaluation, "company", None),
                account_user=getattr(evaluation, "created_by", None) or getattr(session, "created_by", None),
                next_action=NEXT_ACTIONS.get(cause, ""),
                owner=cls._default_owner(),
                deadline=now + timedelta(hours=settings.SERVICE_INCIDENT_RESPONSE_HOURS),
                last_seen_at=now,
            )
        from api.audit.services import AuditLogService
        from api.core.constants import AuditLogAction, AuditLogCategory, AuditLogSeverity

        AuditLogService.log_system(
            action=AuditLogAction.SERVICE_INCIDENT_OPENED,
            category=AuditLogCategory.SYSTEM,
            description=f"Service incident opened: {incident.get_cause_display()} - {incident.summary}",
            resource=incident,
            severity=AuditLogSeverity.ERROR,
            data={"incident_id": str(incident.public_id), "cause": cause, "affected_output": affected_output},
        )
        cls._notify(incident)
        return incident

    @staticmethod
    def _default_owner():
        from api.accounts.models import User

        email = getattr(settings, "SERVICE_INCIDENT_DEFAULT_OWNER_EMAIL", "")
        if not email:
            return None
        return User.objects.filter(email__iexact=email, is_active=True).first()

    @staticmethod
    def _recipients():
        from api.accounts.models import User

        extra = [email for email in getattr(settings, "SERVICE_INCIDENT_NOTIFY_EMAILS", []) if email]
        admins = list(
            User.objects.filter(role__in=[Roles.ADMIN, Roles.SUPERADMIN], is_active=True)
            .exclude(email="")
            .values_list("email", flat=True)
        )
        return sorted(set(admins + extra))

    @classmethod
    def _notify(cls, incident):
        """One email per incident, so a failure that repeats does not cause
        a notification storm (EXC-06)."""
        recipients = cls._recipients()
        if not recipients:
            logger.error("Service incident %s has no recipients to notify", incident.public_id)
            return
        session = incident.session
        lines = [
            f"Cause: {incident.get_cause_display()}",
            f"Summary: {incident.summary}",
            f"Affected output: {incident.affected_output or '-'}",
            f"Session: {session.public_id if session else '-'}",
            f"Evaluation: {incident.evaluation.public_id if incident.evaluation else '-'}",
            f"Account: {incident.company or incident.account_user or '-'}",
            f"Owner: {incident.owner.email if incident.owner else 'unassigned'}",
            f"Deadline: {incident.deadline.isoformat() if incident.deadline else '-'}",
            f"Next action: {incident.next_action}",
            "",
            f"Incident: {incident.public_id}",
            "Manage it with GET/PATCH /api/v1/monitoring/incidents.",
        ]
        try:
            send_mail(
                f"[MeritLense] Service incident: {incident.get_cause_display()}",
                "\n".join(lines),
                settings.DEFAULT_FROM_EMAIL,
                recipients,
                fail_silently=False,
            )
        except Exception:
            logger.exception("Service incident email failed for %s", incident.public_id)
            return
        incident.notified_at = timezone.now()
        incident.save(update_fields=["notified_at", "updated_at"])
