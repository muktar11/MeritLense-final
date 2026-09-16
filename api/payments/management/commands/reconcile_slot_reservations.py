from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from api.audit.services import AuditLogService
from api.core.constants import AuditLogAction, AuditLogCategory, AuditLogSeverity, InterviewSessionStatus
from api.payments.models import SlotReservation
from api.sessions.models import InterviewSession

ACTIVE_SESSION_STATUSES = [
    InterviewSessionStatus.CREATED,
    InterviewSessionStatus.VERIFICATION_PENDING,
    InterviewSessionStatus.READY,
    InterviewSessionStatus.PAUSED,
    InterviewSessionStatus.IN_PROGRESS,
]


class Command(BaseCommand):
    help = (
        "Safety-net scan for Slot Reservation states that should never be "
        "reachable if the Reserve/Consume/Release lifecycle is implemented "
        "correctly (Slot Reservation Lifecycle spec, Section 11). Never "
        "auto-corrects anything - every finding is only logged (as a "
        "RESERVATION_ANOMALY_DETECTED audit entry) for Support/Ops to "
        "investigate."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--since",
            type=str,
            default=None,
            help="Only scan sessions created after this rollout (ISO datetime). "
                 "Defaults to the timestamp of the earliest SlotReservation ever "
                 "created, so pre-rollout sessions (which legitimately have none) "
                 "are never flagged.",
        )

    def handle(self, *args, **options):
        # Sessions created before this rollout have no reservation row at
        # all - they're expected, not an anomaly. Default cutoff is the
        # timestamp of the earliest SlotReservation ever created, so
        # pre-rollout sessions are never flagged for finding #1 below.
        since = None
        if options["since"]:
            since = parse_datetime(options["since"])
            if since is None:
                raise CommandError(f"--since must be an ISO datetime, got {options['since']!r}")
            if timezone.is_naive(since):
                since = timezone.make_aware(since)
        else:
            since = SlotReservation.objects.order_by("created_at").values_list("created_at", flat=True).first()
        findings = []

        if since is not None:
            # 1. An active (not yet terminal) session created after the
            # rollout with no reservation at all - the Hard Rule says this
            # should be structurally impossible, so this would mean a bug
            # bypassed create_session's reserve_slot call somewhere.
            missing = InterviewSession.objects.filter(
                status__in=ACTIVE_SESSION_STATUSES,
                created_at__gte=since,
                slot_reservation__isnull=True,
            )
            for session in missing.iterator():
                findings.append({
                    "type": "SESSION_MISSING_RESERVATION",
                    "session_id": str(session.public_id),
                    "session_status": session.status,
                    "detail": "Active session created after rollout has no SlotReservation at all.",
                })

        # 2. A reservation still RESERVED for a session that's CANCELLED or
        # EXPIRED - cancel_session/expire_session should always have
        # released it; still RESERVED means a release was missed.
        orphaned_reserved = SlotReservation.objects.filter(
            status=SlotReservation.RESERVED,
            session__status__in=[InterviewSessionStatus.CANCELLED, InterviewSessionStatus.EXPIRED],
        ).select_related("session")
        for reservation in orphaned_reserved.iterator():
            findings.append({
                "type": "RESERVED_ON_CLOSED_SESSION",
                "reservation_id": str(reservation.public_id),
                "session_id": str(reservation.session.public_id),
                "session_status": reservation.session.status,
                "detail": "Reservation is still RESERVED but its session is already closed.",
            })

        # 3. A reservation CONSUMED with its session never actually
        # started (no started_at) - Consume should only ever happen inside
        # start_session, right after session.start() sets started_at.
        consumed_without_start = SlotReservation.objects.filter(
            status=SlotReservation.CONSUMED,
            session__started_at__isnull=True,
        ).select_related("session")
        for reservation in consumed_without_start.iterator():
            findings.append({
                "type": "CONSUMED_WITHOUT_START",
                "reservation_id": str(reservation.public_id),
                "session_id": str(reservation.session.public_id),
                "session_status": reservation.session.status,
                "detail": "Reservation is CONSUMED but its session has no started_at.",
            })

        for finding in findings:
            self.stdout.write(self.style.WARNING(f"{finding['type']}: {finding['detail']} ({finding})"))
            AuditLogService.log_system(
                action=AuditLogAction.RESERVATION_ANOMALY_DETECTED,
                category=AuditLogCategory.SUBSCRIPTION,
                description=f"Slot reservation reconciliation: {finding['type']} - {finding['detail']}",
                data=finding,
                severity=AuditLogSeverity.WARNING,
            )

        if findings:
            self.stdout.write(self.style.WARNING(f"Found {len(findings)} anomaly(ies) - logged for Support/Ops review."))
        else:
            self.stdout.write(self.style.SUCCESS(f"No anomalies found as of {timezone.now().isoformat()}."))
