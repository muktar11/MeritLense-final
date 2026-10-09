from django.conf import settings

from api.core.emails import Email, send_email

# Mirrors api/evaluations/utils.py's email pattern (branded api.core.emails, sent directly, no
# queue/async - this codebase has no Celery or other background-job
# infrastructure) for the two "material events" the Slot Reservation
# Lifecycle spec calls out (Section 3, #3): a scheduling attempt that
# failed for lack of a Slot, and an account's Slot balance crossing a low
# threshold. Both go to the Scheduler and the Account/Billing Owner -
# these can be the same person (B2C, or a B2B admin scheduling directly),
# in which case they simply get one email instead of two duplicates.


def _owner_email_and_name(*, created_by, company):
    """The account owner - the company's admin_user for B2B, or the
    scheduler themselves for B2C, where there's no separate owner."""
    if company and company.admin_user_id:
        return company.admin_user.email, company.admin_user.get_full_name()
    return created_by.email, created_by.get_full_name()


def _recipients(*, created_by, company):
    owner_email, owner_name = _owner_email_and_name(created_by=created_by, company=company)
    recipients = {owner_email: owner_name}
    recipients.setdefault(created_by.email, created_by.get_full_name())
    return recipients


def send_reservation_failed_email(*, candidate, created_by, company, role_name, reason):
    """A scheduling attempt was blocked because reserve_slot found no
    Assessment Slot available - sent to whoever tried to schedule and the
    account/billing owner, since the owner is the one who can actually
    fix it (buy more Slots) and may not otherwise ever hear that someone
    on their team hit this wall."""
    recipients = _recipients(created_by=created_by, company=company)
    for email, name in recipients.items():
        send_email(Email(
            subject="Interview scheduling blocked: no Assessment Slots available",
            eyebrow="Action needed", title="Add Assessment Slots to keep scheduling",
            greeting=f"Hello {name},",
            intro=["An interview couldn't be scheduled because your account has no Assessment Slots available. "
                   "No interview was created and nothing was charged."],
            details=[("Candidate", candidate.get_full_name()), ("Role", role_name),
                     ("Attempted by", f"{created_by.get_full_name()} ({created_by.email})"), ("Reason", reason)],
            button=("Add Assessment Slots", _packages_url()) if _packages_url() else None,
            after_button=["Add Slots from the Packages & Subscription page, then schedule the interview again."],
            notes=["We'll only send this once. You won't get another reminder for repeated attempts."],
            tone="warning",
        ), [email])


def _packages_url():
    return f"{settings.FRONTEND_URL}/en/auth/login" if settings.FRONTEND_URL else ""


def _owner_key(*, created_by, company):
    return f"COMPANY:{company.id}" if company else f"USER:{created_by.id}"


def send_reservation_failed_email_once(*, candidate, created_by, company, role_name, reason):
    """send_reservation_failed_email, but only once per account until it
    can schedule again. Repeated attempts before the account pays for Slots
    used to send the same email every time. Must be called outside the
    failed session-creation transaction, or the marker is rolled back with
    it."""
    from .models import OwnerNotificationMarker

    _, created = OwnerNotificationMarker.objects.get_or_create(
        kind=OwnerNotificationMarker.NO_SLOTS_AVAILABLE,
        owner_key=_owner_key(created_by=created_by, company=company),
    )
    if created:
        send_reservation_failed_email(candidate=candidate, created_by=created_by, company=company,
                                      role_name=role_name, reason=reason)
    return created


def clear_reservation_failed_marker(*, created_by, company):
    """Called after a successful reservation: the account has Slots again,
    so a future shortage should notify (once) again."""
    from .models import OwnerNotificationMarker

    OwnerNotificationMarker.objects.filter(
        kind=OwnerNotificationMarker.NO_SLOTS_AVAILABLE,
        owner_key=_owner_key(created_by=created_by, company=company),
    ).delete()


def send_reservation_invalidated_email(*, candidate, created_by, company):
    """A scheduled interview's Slot reservation lost its entitlement
    backing (Slot Reservation Lifecycle spec, Section 7) because the
    package purchase it was drawn from got refunded/reversed, so the
    session was cancelled automatically. Sent to the scheduler and the
    account/billing owner - not the candidate, who has no visibility into
    the employer's billing and doesn't need to."""
    recipients = _recipients(created_by=created_by, company=company)
    for email, name in recipients.items():
        send_email(Email(
            subject="Interview cancelled: underlying purchase was refunded",
            eyebrow="Interview cancelled", title="A scheduled interview was cancelled",
            greeting=f"Hello {name},",
            intro=["A scheduled interview was cancelled automatically because the package purchase that reserved "
                   "its Assessment Slot was refunded or reversed. No Slot was charged for it."],
            details=[("Candidate", candidate.get_full_name())],
            notes=["If this was unexpected, contact us at info@meritlense.com."],
            tone="danger",
        ), [email])


# A grant this small already reads as "nearly out" in absolute terms
# (matches the Slot Reservation Lifecycle spec's own example: "1 available
# slot, 3 reserved, 3 pending sessions"), so the threshold is whichever is
# larger of a flat floor and 10% of the total grant - a 200-Slot Business
# plan warns under 20, a 5-Slot Basic package warns under 1.
LOW_BALANCE_FLOOR = 1
LOW_BALANCE_FRACTION = 0.1


def low_balance_threshold(limit):
    if not limit:
        return 0
    return max(LOW_BALANCE_FLOOR, round(limit * LOW_BALANCE_FRACTION))


def send_certificate_reused_email(*, candidate, requested_by, company, remaining):
    """A business added a candidate who already has an existing, issued
    certificate from a different account (passport ID match) and chose to
    reuse it rather than run a new interview - one Slot was charged for
    that (EntitlementService.consume_slot_for_certificate_reuse). Sent to
    whoever requested it and the account/billing owner, same recipients
    and reasoning as send_reservation_failed_email - the owner is the one
    who notices the Slot count and should know why it moved without a new
    interview being scheduled."""
    recipients = _recipients(created_by=requested_by, company=company)
    for email, name in recipients.items():
        send_email(Email(
            subject="Assessment Slot used: existing certificate reused",
            eyebrow="Certificate reused", title="An existing certificate was reused",
            greeting=f"Hello {name},",
            intro=["An existing certificate was reused instead of running a new interview. One Assessment Slot "
                   "was used, the same as for a new interview."],
            details=[("Candidate", candidate.get_full_name()),
                     ("Requested by", f"{requested_by.get_full_name()} ({requested_by.email})"),
                     ("Slots remaining", remaining)],
        ), [email])


def maybe_send_low_balance_warning(*, created_by, company, remaining, limit, role_name=None):
    """Fires exactly once per crossing - only when this specific
    reservation is the one that pushed Available at-or-below the
    threshold, not on every reservation made while already low (which
    would just spam the same warning on every subsequent scheduling
    attempt)."""
    if limit is None or remaining is None:
        return
    threshold = low_balance_threshold(limit)
    if remaining > threshold or remaining + 1 <= threshold:
        return

    recipients = _recipients(created_by=created_by, company=company)
    for email, name in recipients.items():
        send_email(Email(
            subject="Low Assessment Slot balance on your MeritLense account",
            eyebrow="Balance running low", title=f"Only {remaining} Assessment Slot{'' if remaining == 1 else 's'} left",
            greeting=f"Hello {name},",
            intro=["Your account is running low on Assessment Slots. When it reaches 0, new interviews "
                   "can't be scheduled until more Slots are added."],
            details=[("Available", f"{remaining} of {limit}")],
            button=("Top up Slots", _packages_url()) if _packages_url() else None,
            tone="warning",
        ), [email])
