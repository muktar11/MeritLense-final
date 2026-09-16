from django.core.management.base import BaseCommand
from django.utils import timezone

from api.core.constants import InterviewSessionStatus
from api.sessions.models import InterviewSession
from api.sessions.services import InterviewSessionService

# Only pre-start statuses are eligible - a PAUSED session has already
# started (see InterviewSession.pause_for_integrity) and its Slot is
# already Consumed, not Reserved, so it must never be touched here (Slot
# Reservation Lifecycle spec, Section 6.1: expiry must never fire, and
# must never release a Slot, once a session has actually started).
EXPIRY_ELIGIBLE_STATUSES = [
    InterviewSessionStatus.CREATED,
    InterviewSessionStatus.VERIFICATION_PENDING,
    InterviewSessionStatus.READY,
]


class Command(BaseCommand):
    help = (
        "Proactively expires interview sessions whose invite window has "
        "passed but were never revisited (so the lazy check-on-access path "
        "in start_session never got a chance to catch them) - flips each to "
        "EXPIRED and releases its Slot reservation, if one is still held. "
        "Without this, an invite link a candidate never clicks again would "
        "leave its Slot Reserved forever. Intended to run on an external "
        "schedule (no in-repo scheduler exists in this codebase)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would be expired without changing anything.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        now = timezone.now()

        stale_sessions = InterviewSession.objects.filter(
            status__in=EXPIRY_ELIGIBLE_STATUSES,
            expires_at__lte=now,
        ).select_related("slot_reservation")

        expired_count = 0
        released_count = 0

        for session in stale_sessions.iterator():
            reservation = InterviewSessionService.get_slot_reservation(session)
            would_release = reservation is not None and reservation.status == reservation.RESERVED

            if dry_run:
                self.stdout.write(
                    f"[dry-run] session {session.public_id} (status {session.status}, "
                    f"expired {session.expires_at.isoformat()}): would expire"
                    + (", would release its Slot reservation" if would_release else "")
                )
                expired_count += 1
                if would_release:
                    released_count += 1
                continue

            InterviewSessionService.expire_session(session)
            expired_count += 1
            if would_release:
                released_count += 1

        verb = "Would expire" if dry_run else "Expired"
        self.stdout.write(
            self.style.SUCCESS(
                f"{verb} {expired_count} stale session(s), releasing {released_count} "
                f"Slot reservation(s)."
            )
        )
