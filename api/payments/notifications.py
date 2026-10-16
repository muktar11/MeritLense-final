from django.core.mail import send_mail
from django.conf import settings

from api.core.email_i18n import ARABIC, SIGNATURE, user_language

# Mirrors api/evaluations/utils.py's email pattern (plain send_mail, no
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


def _languages(*, created_by, company):
    """Each recipient's email language (owner and scheduler may differ)."""
    owner = company.admin_user if company and company.admin_user_id else created_by
    languages = {owner.email: user_language(owner)}
    languages.setdefault(created_by.email, user_language(created_by))
    return languages


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
    languages = _languages(created_by=created_by, company=company)
    subject = f"Interview scheduling blocked: no Assessment Slots available"

    for email, name in recipients.items():
        if languages.get(email) == ARABIC:
            send_mail("تعذّر تحديد موعد المقابلة: لا توجد خانات تقييم متاحة", f"""مرحبًا {name}،

تعذّر إكمال محاولة لتحديد موعد مقابلة على MeritLense:

- المرشح: {candidate.get_full_name()}
- الدور: {role_name}
- المحاولة من: {created_by.get_full_name()} ({created_by.email})
- السبب: {reason}

لم يُحدَّد أي موعد ولم تُستخدم أي خانة. لحل ذلك، أضف خانات تقييم إلى حسابك من صفحة الباقات والاشتراك.

{SIGNATURE[ARABIC]}
""", settings.DEFAULT_FROM_EMAIL, [email], fail_silently=False)
            continue
        message = f"""
Hello {name},

An attempt to schedule an interview on MeritLense could not be completed:

- Candidate: {candidate.get_full_name()}
- Role: {role_name}
- Attempted by: {created_by.get_full_name()} ({created_by.email})
- Reason: {reason}

No interview was scheduled and no Slot was used. To resolve this, add more
Assessment Slots to your account from the Packages & Subscription page.

Best regards,
MeritLense Team
"""
        send_mail(subject, message, settings.DEFAULT_FROM_EMAIL, [email], fail_silently=False)


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
    languages = _languages(created_by=created_by, company=company)
    subject = f"Interview cancelled: underlying purchase was refunded"

    for email, name in recipients.items():
        if languages.get(email) == ARABIC:
            send_mail("أُلغيت المقابلة: تم استرداد قيمة الشراء", f"""مرحبًا {name}،

أُلغيت تلقائيًا مقابلة مجدولة على MeritLense:

- المرشح: {candidate.get_full_name()}

حدث ذلك لأن عملية شراء الباقة التي حُجزت منها خانة التقييم قد استُردّت أو أُلغيت، فلم يعد بالإمكان الإبقاء على الحجز. لم تُحتسب أي خانة لهذه المقابلة الملغاة.

إذا كان ذلك غير متوقع، يرجى التواصل مع دعم MeritLense.

{SIGNATURE[ARABIC]}
""", settings.DEFAULT_FROM_EMAIL, [email], fail_silently=False)
            continue
        message = f"""
Hello {name},

A scheduled interview on MeritLense has been automatically cancelled:

- Candidate: {candidate.get_full_name()}

This happened because the package purchase that reserved its Assessment
Slot was refunded or reversed, so the reservation could no longer be
honored. No Slot was charged for this cancelled interview.

If this was unexpected, please contact MeritLense support.

Best regards,
MeritLense Team
"""
        send_mail(subject, message, settings.DEFAULT_FROM_EMAIL, [email], fail_silently=False)


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
    languages = _languages(created_by=requested_by, company=company)
    subject = "Assessment Slot used: existing certificate reused"

    for email, name in recipients.items():
        if languages.get(email) == ARABIC:
            send_mail("استُخدمت خانة تقييم: إعادة استخدام شهادة قائمة", f"""مرحبًا {name}،

أُعيد استخدام شهادة قائمة بدلًا من إجراء مقابلة جديدة على MeritLense:

- المرشح: {candidate.get_full_name()}
- بطلب من: {requested_by.get_full_name()} ({requested_by.email})

خُصمت خانة تقييم واحدة من باقتك لذلك (المتبقي: {remaining})، كما هو الحال في المقابلة الجديدة.

{SIGNATURE[ARABIC]}
""", settings.DEFAULT_FROM_EMAIL, [email], fail_silently=False)
            continue
        message = f"""
Hello {name},

An existing certificate was reused instead of running a new interview on MeritLense:

- Candidate: {candidate.get_full_name()}
- Requested by: {requested_by.get_full_name()} ({requested_by.email})

One Assessment Slot has been deducted from your package for this ({remaining} remaining), the same as it would be for a new interview.

Best regards,
MeritLense Team
"""
        send_mail(subject, message, settings.DEFAULT_FROM_EMAIL, [email], fail_silently=False)


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
    languages = _languages(created_by=created_by, company=company)
    subject = "Low Assessment Slot balance on your MeritLense account"

    for email, name in recipients.items():
        if languages.get(email) == ARABIC:
            send_mail("رصيد خانات التقييم منخفض في حسابك على MeritLense", f"""مرحبًا {name}،

أوشكت خانات التقييم في حسابك على MeritLense على النفاد:

- المتاح: {remaining} من {limit}

عندما يصل المتاح إلى 0، سيتوقف تحديد مواعيد مقابلات جديدة حتى تُضاف خانات أخرى. تفضّل بزيارة صفحة الباقات والاشتراك لإعادة الشحن.

{SIGNATURE[ARABIC]}
""", settings.DEFAULT_FROM_EMAIL, [email], fail_silently=False)
            continue
        message = f"""
Hello {name},

Your MeritLense account is running low on Assessment Slots:

- Available: {remaining} of {limit}

Once Available reaches 0, scheduling new interviews will be blocked until
more Slots are added. Visit the Packages & Subscription page to top up.

Best regards,
MeritLense Team
"""
        send_mail(subject, message, settings.DEFAULT_FROM_EMAIL, [email], fail_silently=False)
