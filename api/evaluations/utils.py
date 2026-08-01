from zoneinfo import ZoneInfo

from django.core.mail import send_mail
from django.conf import settings
from django.utils import timezone

# All scheduled_date/completed_at values are stored as UTC instants
# (TIME_ZONE="UTC" in settings) - emails were previously formatting them
# with raw strftime(), which silently printed UTC as if it were the
# recipient's local time. Display everything in Arabian Standard Time
# (UTC+3, no DST) instead, matching the GCC market this platform serves,
# and label it explicitly so recipients never have to guess which zone
# a time is in.
DISPLAY_TZ = ZoneInfo("Asia/Riyadh")
DISPLAY_TZ_LABEL = "AST"


def _format_date(value):
    return timezone.localtime(value, DISPLAY_TZ).strftime("%A, %B %d, %Y")


def _format_time(value):
    return f"{timezone.localtime(value, DISPLAY_TZ).strftime('%I:%M %p')} {DISPLAY_TZ_LABEL}"


def _format_datetime(value):
    return f"{timezone.localtime(value, DISPLAY_TZ).strftime('%A, %B %d, %Y at %I:%M %p')} {DISPLAY_TZ_LABEL}"


def send_evaluation_scheduled_email(evaluation, request=None):

    scheduled_date = _format_date(evaluation.scheduled_date)
    scheduled_time = _format_time(evaluation.scheduled_date)
    duration = evaluation.duration_minutes
    
    evaluation_type_display = evaluation.get_evaluation_type_display()
    
    creator_name = evaluation.created_by.get_full_name()
    
    location_details = ""
    if evaluation.meeting_link:
        location_details = f"""
Meeting Link: {evaluation.meeting_link}"""
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
    
    subject = f"Evaluation Scheduled: {evaluation_type_display} - {scheduled_date}"
    
    message = f"""
Hello {evaluation.candidate_first_name} {evaluation.candidate_last_name},

An evaluation has been scheduled for you on Meritlense.

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
Meritlense Team
"""
    
    send_mail(
        subject,
        message,
        settings.DEFAULT_FROM_EMAIL,
        [evaluation.candidate_email],
        fail_silently=False,
    )

def send_evaluation_rescheduled_email(evaluation, old_date, request=None):

    old_date_str = _format_datetime(old_date)
    new_date_str = _format_datetime(evaluation.scheduled_date)
    
    evaluation_type_display = evaluation.get_evaluation_type_display()
    
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
- Duration: {evaluation.duration_minutes} minutes

Please update your calendar accordingly.

If you did not request this change or have any questions, please contact your evaluator.

Best regards,
Meritlense Team
"""
    
    send_mail(
        subject,
        message,
        settings.DEFAULT_FROM_EMAIL,
        [evaluation.candidate_email],
        fail_silently=False,
    )
    

def send_evaluation_cancelled_email(evaluation, request=None):

    scheduled_date = _format_datetime(evaluation.scheduled_date)
    evaluation_type_display = evaluation.get_evaluation_type_display()
    
    reason_text = ""
    if evaluation.cancellation_reason:
        reason_text = f"\nReason: {evaluation.cancellation_reason}"
    
    subject = f"Evaluation Cancelled: {evaluation_type_display}"
    
    message = f"""
Hello {evaluation.candidate_first_name} {evaluation.candidate_last_name},

Your scheduled evaluation has been cancelled.

Cancelled Evaluation:
- Type: {evaluation_type_display}
- Originally scheduled for: {scheduled_date}{reason_text}

If you have any questions, please contact your evaluator.

Best regards,
Meritlense Team
"""
    
    send_mail(
        subject,
        message,
        settings.DEFAULT_FROM_EMAIL,
        [evaluation.candidate_email],
        fail_silently=False,
    )

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
Meritlense Team
"""
    
    send_mail(
        subject,
        message,
        settings.DEFAULT_FROM_EMAIL,
        [evaluation.candidate_email],
        fail_silently=False,
    )
    