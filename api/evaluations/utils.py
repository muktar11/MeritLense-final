import logging
from zoneinfo import ZoneInfo

from django.core.mail import send_mail
from django.conf import settings
from django.utils import timezone

from api.core.email_i18n import (
    ARABIC,
    EVALUATION_TYPES_AR,
    SIGNATURE,
    candidate_language,
    format_date,
    format_time,
    user_language,
)

# All scheduled_date/completed_at values are stored as UTC instants
# (TIME_ZONE="UTC" in settings) - emails were previously formatting them
# with raw strftime(), which silently printed UTC as if it were the
# recipient's local time. Display everything in Arabian Standard Time
# (UTC+3, no DST) instead, matching the GCC market this platform serves,
# and label it explicitly so recipients never have to guess which zone
# a time is in.
DISPLAY_TZ = ZoneInfo("Asia/Riyadh")
DISPLAY_TZ_LABEL = "AST"

logger = logging.getLogger(__name__)


def _send(subject, message, recipients):
    """These notifications go out after the evaluation (and its interview
    session) has already been saved. Letting an SMTP error propagate turned
    a successful scheduling into a 500 the scheduler saw as "failed", even
    though the evaluation existed. Log it instead."""
    try:
        return send_mail(subject, message, settings.DEFAULT_FROM_EMAIL, recipients, fail_silently=False)
    except Exception:
        logger.exception("Evaluation email failed. subject=%s recipients=%s", subject, recipients)
        return 0


def _format_date(value):
    return timezone.localtime(value, DISPLAY_TZ).strftime("%A, %B %d, %Y")


def _format_time(value):
    return f"{timezone.localtime(value, DISPLAY_TZ).strftime('%I:%M %p')} {DISPLAY_TZ_LABEL}"


def _format_datetime(value):
    return f"{timezone.localtime(value, DISPLAY_TZ).strftime('%A, %B %d, %Y at %I:%M %p')} {DISPLAY_TZ_LABEL}"


def _local(value):
    return timezone.localtime(value, DISPLAY_TZ)


def _date_ar(value):
    return format_date(_local(value), ARABIC)


def _time_ar(value):
    return f"{format_time(_local(value), ARABIC)} بتوقيت السعودية"


def _datetime_ar(value):
    return f"{_date_ar(value)}، الساعة {_time_ar(value)}"


def _type_ar(evaluation):
    return EVALUATION_TYPES_AR.get(evaluation.evaluation_type, evaluation.get_evaluation_type_display())


def _candidate_lang(evaluation):
    return candidate_language(getattr(evaluation, "candidate", None))


def _account_owner(evaluation):
    if evaluation.company_id and evaluation.company.admin_user_id:
        return evaluation.company.admin_user
    return evaluation.created_by


def _account_owner_email(evaluation):
    """The login email for the account this evaluation belongs to - the
    company's admin_user for B2B (not necessarily whoever on the team
    actually scheduled it), or the scheduler themselves for B2C, where
    there's no separate company/owner distinction."""
    if evaluation.company_id and evaluation.company.admin_user_id:
        return evaluation.company.admin_user.email
    return evaluation.created_by.email


def _join_link(evaluation):
    """The link candidates are sent: the same short link the dashboard
    shows (meeting_short_link), or the full meeting link when it can't be
    shortened."""
    from api.payments.short_links import meeting_short_link

    if not evaluation.meeting_link:
        return ""
    return meeting_short_link(evaluation.meeting_link) or evaluation.meeting_link


def send_evaluation_scheduled_email(evaluation, request=None):

    scheduled_date = _format_date(evaluation.scheduled_date)
    scheduled_time = _format_time(evaluation.scheduled_date)
    duration = evaluation.duration_minutes
    
    evaluation_type_display = evaluation.get_evaluation_type_display()
    
    creator_name = evaluation.created_by.get_full_name()
    
    location_details = ""
    if evaluation.meeting_link:
        location_details = f"""
Meeting Link: {_join_link(evaluation)}"""
        if evaluation.meeting_id:
            location_details += f"""
Meeting ID: {evaluation.meeting_id}"""
        if evaluation.meeting_password:
            location_details += f"""
Meeting Password: {evaluation.meeting_password}"""
    elif evaluation.location:
        location_details = f"""
Location: {evaluation.location}"""
    else:
        location_details = "\nLocation: To be announced"
    
    if _candidate_lang(evaluation) == ARABIC:
        if evaluation.meeting_link:
            where = f"رابط الاجتماع: {_join_link(evaluation)}"
            if evaluation.meeting_id:
                where += f"\nمعرّف الاجتماع: {evaluation.meeting_id}"
            if evaluation.meeting_password:
                where += f"\nكلمة مرور الاجتماع: {evaluation.meeting_password}"
        elif evaluation.location:
            where = f"الموقع: {evaluation.location}"
        else:
            where = "الموقع: سيُعلن لاحقًا"
        ar_subject = f"تم تحديد موعد التقييم: {_type_ar(evaluation)} - {_date_ar(evaluation.scheduled_date)}"
        ar_message = f"""مرحبًا {evaluation.candidate_first_name} {evaluation.candidate_last_name}،

تم تحديد موعد تقييم لك على MeritLense.

تفاصيل التقييم:
- النوع: {_type_ar(evaluation)}
- التاريخ: {_date_ar(evaluation.scheduled_date)}
- الوقت: {_time_ar(evaluation.scheduled_date)}
- المدة: {duration} دقيقة
- حدّد الموعد: {creator_name}
{where}

بياناتك:
- الاسم: {evaluation.candidate_first_name} {evaluation.candidate_last_name}
- البريد الإلكتروني: {evaluation.candidate_email}
- رقم جواز السفر / الهوية: {evaluation.candidate_passport_id}

يرجى التأكد من:
1. أن تكون متاحًا في الموعد المحدد
2. تجهيز وثيقة الهوية
3. اختبار الصوت والفيديو إذا كان الاجتماع عبر الإنترنت
4. الانضمام قبل الموعد بخمس دقائق

إذا احتجت إلى تغيير الموعد أو كانت لديك أسئلة، يرجى التواصل مع المقيّم.

{SIGNATURE[ARABIC]}
"""
    subject = f"Evaluation Scheduled: {evaluation_type_display} - {scheduled_date}"
    
    message = f"""
Hello {evaluation.candidate_first_name} {evaluation.candidate_last_name},

An evaluation has been scheduled for you on MeritLense.

Evaluation Details:
- Type: {evaluation_type_display}
- Date: {scheduled_date}
- Time: {scheduled_time}
- Duration: {duration} minutes
- Scheduled by: {creator_name}
{location_details}

Your Information:
- Name: {evaluation.candidate_first_name} {evaluation.candidate_last_name}
- Email: {evaluation.candidate_email}
- Passport/ID: {evaluation.candidate_passport_id}

Please make sure to:
1. Be available at the scheduled time
2. Have your identification document ready
3. Test your audio/video if it's an online meeting
4. Join 5 minutes before the scheduled time

If you need to reschedule or have any questions, please contact your evaluator.

Best regards,
MeritLense Team
"""

    if _candidate_lang(evaluation) == ARABIC:
        _send(ar_subject, ar_message, [evaluation.candidate_email])
    else:
        _send(subject, message, [evaluation.candidate_email])

    owner_email = _account_owner_email(evaluation)
    if user_language(_account_owner(evaluation)) == ARABIC:
        _send(
            f"تم تحديد موعد مقابلة: {evaluation.candidate_first_name} {evaluation.candidate_last_name} - {_date_ar(evaluation.scheduled_date)}",
            f"""مرحبًا {evaluation.created_by.get_full_name()}،

تم تحديد موعد مقابلة في حسابك على MeritLense.

تفاصيل التقييم:
- المرشح: {evaluation.candidate_first_name} {evaluation.candidate_last_name}
- البريد الإلكتروني للمرشح: {evaluation.candidate_email}
- النوع: {_type_ar(evaluation)}
- التاريخ: {_date_ar(evaluation.scheduled_date)}
- الوقت: {_time_ar(evaluation.scheduled_date)}
- المدة: {duration} دقيقة
- حدّد الموعد: {creator_name}

أُرسل إلى المرشح رابطه الخاص للانضمام في الموعد المحدد، ويمكنك الانضمام من لوحة التحكم في MeritLense عند بدء المقابلة.

{SIGNATURE[ARABIC]}
""",
            [owner_email],
        )
        return
    owner_subject = f"Interview scheduled: {evaluation.candidate_first_name} {evaluation.candidate_last_name} - {scheduled_date}"
    owner_message = f"""
Hello {evaluation.created_by.get_full_name()},

An interview has been scheduled on your MeritLense account.

Evaluation Details:
- Candidate: {evaluation.candidate_first_name} {evaluation.candidate_last_name}
- Candidate Email: {evaluation.candidate_email}
- Type: {evaluation_type_display}
- Date: {scheduled_date}
- Time: {scheduled_time}
- Duration: {duration} minutes
- Scheduled by: {creator_name}

The candidate has been emailed their own link to join at the scheduled time.
You can join from your MeritLense dashboard once the interview starts.

Best regards,
MeritLense Team
"""

    _send(owner_subject, owner_message, [owner_email])

def send_evaluation_rescheduled_email(evaluation, old_date, request=None):

    old_date_str = _format_datetime(old_date)
    new_date_str = _format_datetime(evaluation.scheduled_date)
    
    evaluation_type_display = evaluation.get_evaluation_type_display()
    # The reschedule email had no link, so candidates had to find the
    # original invitation. The link itself doesn't change on reschedule.
    join_link = _join_link(evaluation)
    link_line = f"\n- Meeting Link: {join_link}" if join_link else ""

    if _candidate_lang(evaluation) == ARABIC:
        ar_link = f"\n- رابط الاجتماع: {join_link}" if join_link else ""
        _send(f"تم تغيير موعد التقييم: {_type_ar(evaluation)}", f"""مرحبًا {evaluation.candidate_first_name} {evaluation.candidate_last_name}،

تم تغيير موعد تقييمك.

الموعد السابق:
{_datetime_ar(old_date)}

الموعد الجديد:
{_datetime_ar(evaluation.scheduled_date)}

تفاصيل التقييم:
- النوع: {_type_ar(evaluation)}
- المدة: {evaluation.duration_minutes} دقيقة{ar_link}

يرجى تحديث تقويمك.

إذا لم تطلب هذا التغيير أو كانت لديك أسئلة، يرجى التواصل مع المقيّم.

{SIGNATURE[ARABIC]}
""", [evaluation.candidate_email])
        return

    subject = f"Evaluation Rescheduled: {evaluation_type_display}"
    
    message = f"""
Hello {evaluation.candidate_first_name} {evaluation.candidate_last_name},

Your evaluation has been rescheduled.

Previous Schedule:
{old_date_str}

New Schedule:
{new_date_str}

Evaluation Details:
- Type: {evaluation_type_display}
- Duration: {evaluation.duration_minutes} minutes{link_line}

Please update your calendar accordingly.

If you did not request this change or have any questions, please contact your evaluator.

Best regards,
MeritLense Team
"""
    
    _send(subject, message, [evaluation.candidate_email])
    

def send_evaluation_cancelled_email(evaluation, request=None):

    scheduled_date = _format_datetime(evaluation.scheduled_date)
    evaluation_type_display = evaluation.get_evaluation_type_display()
    
    reason_text = ""
    if evaluation.cancellation_reason:
        reason_text = f"\nReason: {evaluation.cancellation_reason}"
    
    if _candidate_lang(evaluation) == ARABIC:
        ar_reason = f"\nالسبب: {evaluation.cancellation_reason}" if evaluation.cancellation_reason else ""
        _send(f"تم إلغاء التقييم: {_type_ar(evaluation)}", f"""مرحبًا {evaluation.candidate_first_name} {evaluation.candidate_last_name}،

تم إلغاء التقييم المحدد لك.

التقييم الملغى:
- النوع: {_type_ar(evaluation)}
- الموعد الأصلي: {_datetime_ar(evaluation.scheduled_date)}{ar_reason}

إذا كانت لديك أسئلة، يرجى التواصل مع المقيّم.

{SIGNATURE[ARABIC]}
""", [evaluation.candidate_email])
        return

    subject = f"Evaluation Cancelled: {evaluation_type_display}"
    
    message = f"""
Hello {evaluation.candidate_first_name} {evaluation.candidate_last_name},

Your scheduled evaluation has been cancelled.

Cancelled Evaluation:
- Type: {evaluation_type_display}
- Originally scheduled for: {scheduled_date}{reason_text}

If you have any questions, please contact your evaluator.

Best regards,
MeritLense Team
"""
    
    _send(subject, message, [evaluation.candidate_email])

def send_evaluation_completed_email(evaluation, request=None):

    evaluation_type_display = evaluation.get_evaluation_type_display()
    completed_date = _format_datetime(evaluation.completed_at) if evaluation.completed_at else "Recently"
    
    certificate_info = ""
    if evaluation.certificate_status == 'ISSUED' and evaluation.certificate_url:
        certificate_info = f"""
Certificate Status: Issued
You can download your certificate here: {evaluation.certificate_url}
"""
    elif evaluation.certificate_status == 'PENDING':
        certificate_info = """
Certificate Status: Pending - Your certificate will be available soon.
"""
    
    score_info = ""
    if evaluation.score:
        score_info = f"\nScore: {evaluation.score}"
    
    if _candidate_lang(evaluation) == ARABIC:
        done = _datetime_ar(evaluation.completed_at) if evaluation.completed_at else "مؤخرًا"
        cert = ""
        if evaluation.certificate_status == "ISSUED" and evaluation.certificate_url:
            cert = f"\nحالة الشهادة: صادرة\nيمكنك تنزيل شهادتك من هنا: {evaluation.certificate_url}\n"
        elif evaluation.certificate_status == "PENDING":
            cert = "\nحالة الشهادة: قيد الإعداد، وستكون متاحة قريبًا.\n"
        score = f"\nالدرجة: {evaluation.score}" if evaluation.score else ""
        _send(f"اكتمل التقييم: {_type_ar(evaluation)}", f"""مرحبًا {evaluation.candidate_first_name} {evaluation.candidate_last_name}،

اكتمل تقييمك.

تفاصيل التقييم:
- النوع: {_type_ar(evaluation)}
- تاريخ الإكمال: {done}{score}
{cert}
ستصلك قريبًا معلومات عن الخطوات التالية.

شكرًا لمشاركتك.

{SIGNATURE[ARABIC]}
""", [evaluation.candidate_email])
        return

    subject = f"Evaluation Completed: {evaluation_type_display}"
    
    message = f"""
Hello {evaluation.candidate_first_name} {evaluation.candidate_last_name},

Your evaluation has been completed.

Evaluation Details:
- Type: {evaluation_type_display}
- Completed on: {completed_date}{score_info}
{certificate_info}
You will receive further communication about next steps soon.

Thank you for your participation.

Best regards,
MeritLense Team
"""
    
    _send(subject, message, [evaluation.candidate_email])
    