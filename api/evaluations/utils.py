"""Interview emails to candidates and account owners.

Designed emails (api/core/emails.py) with a real calendar invite: the
candidate's invitation shows as a calendar card in Gmail/Outlook (Yes /
Maybe / No) and adds the interview to their calendar. A reschedule
updates the same calendar event (same UID, newer sequence) and a
cancellation removes it.

All times are stored in UTC and shown in Arabian Standard Time (UTC+3, no
DST), labelled explicitly, matching the GCC market this platform serves.
"""
from datetime import timedelta

from api.core.emails import (
    DISPLAY_TZ_LABEL,
    Email,
    EventTile,
    build_calendar_invite,
    format_local,
    send_email,
)


def _account_owner_email(evaluation):
    """The login email for the account this evaluation belongs to - the
    company's admin_user for B2B (not necessarily whoever on the team
    actually scheduled it), or the scheduler themselves for B2C, where
    there's no separate company/owner distinction."""
    if evaluation.company_id and evaluation.company.admin_user_id:
        return evaluation.company.admin_user.email
    return evaluation.created_by.email


def _candidate_name(evaluation):
    return f"{evaluation.candidate_first_name} {evaluation.candidate_last_name}".strip()


def _organisation(evaluation):
    if evaluation.company_id:
        return evaluation.company.name
    return evaluation.created_by.get_full_name()


def _role(evaluation):
    session = getattr(evaluation, "session", None)
    return (getattr(session, "role_name", "") or evaluation.candidate_job_role or "").strip()


def _event_title(evaluation):
    role = _role(evaluation)
    return f"{role} interview · {_organisation(evaluation)}" if role else f"Interview · {_organisation(evaluation)}"


def _end(evaluation):
    return evaluation.scheduled_date + timedelta(minutes=evaluation.duration_minutes or 45)


def _where(evaluation):
    if evaluation.meeting_link:
        return "Online · MeritLense AI interview"
    return evaluation.location or "Location to be announced"


def _uid(evaluation):
    return f"evaluation-{evaluation.public_id}@meritlense.com"


def _invite(evaluation, *, attendee_email, attendee_name, method="REQUEST"):
    description_lines = [
        f"{evaluation.get_evaluation_type_display()} with {_organisation(evaluation)} on MeritLense.",
    ]
    if evaluation.meeting_link and method != "CANCEL":
        description_lines += ["", f"Join your interview: {evaluation.meeting_link}",
                              "Please join 5 minutes early with your ID document ready."]
    ics = build_calendar_invite(
        uid=_uid(evaluation),
        start=evaluation.scheduled_date,
        end=_end(evaluation),
        summary=_event_title(evaluation),
        description="\n".join(description_lines),
        location=evaluation.meeting_link or evaluation.location or "Online",
        url=evaluation.meeting_link or "",
        attendee_email=attendee_email,
        attendee_name=attendee_name,
        method=method,
    )
    return ics, method


def _meeting_details(evaluation):
    rows = [
        ("Interview type", evaluation.get_evaluation_type_display()),
        ("Role", _role(evaluation)),
        ("With", _organisation(evaluation)),
        ("Duration", f"{evaluation.duration_minutes} minutes" if evaluation.duration_minutes else ""),
    ]
    if evaluation.meeting_id:
        rows.append(("Meeting ID", evaluation.meeting_id))
    if evaluation.meeting_password:
        rows.append(("Meeting password", evaluation.meeting_password))
    if not evaluation.meeting_link:
        rows.append(("Location", evaluation.location or "To be announced"))
    return rows


def send_evaluation_scheduled_email(evaluation, request=None):
    name = _candidate_name(evaluation)
    tile = EventTile(start=evaluation.scheduled_date, end=_end(evaluation), title=_event_title(evaluation),
                     subtitle=_where(evaluation))

    candidate_email = Email(
        subject=f"Interview invitation: {_event_title(evaluation)} on "
                f"{format_local(evaluation.scheduled_date, '%d %b %Y, %I:%M %p')}",
        eyebrow="Interview invitation",
        title="You're invited to an interview",
        greeting=f"Hello {evaluation.candidate_first_name},",
        intro=[f"{_organisation(evaluation)} has invited you to an interview on MeritLense. "
               "The details are below, and a calendar invite is attached so you can add it to your calendar."],
        event=tile,
        details=_meeting_details(evaluation) + [("Your ID on file", evaluation.candidate_passport_id)],
        button=("Join your interview", evaluation.meeting_link) if evaluation.meeting_link else None,
        after_button=["Your link is personal to you. Please don't share it."] if evaluation.meeting_link else [],
        checklist_title="Before you start",
        checklist=[
            "Join 5 minutes before the start time.",
            "Have your passport or ID document ready.",
            "Use a quiet room with a working camera and microphone.",
            "Use a recent version of Chrome, Edge or Safari.",
        ],
        notes=[f"Times are shown in {DISPLAY_TZ_LABEL}. Need to change the time? Contact {_organisation(evaluation)}."],
        preheader=f"{format_local(evaluation.scheduled_date)} · {_where(evaluation)}",
    )
    send_email(candidate_email, [evaluation.candidate_email],
               calendar=_invite(evaluation, attendee_email=evaluation.candidate_email, attendee_name=name))

    owner_email = _account_owner_email(evaluation)
    owner = Email(
        subject=f"Interview scheduled: {name} · {format_local(evaluation.scheduled_date, '%d %b %Y, %I:%M %p')}",
        eyebrow="Interview scheduled",
        title=f"Interview scheduled with {name}",
        greeting=f"Hello {evaluation.created_by.first_name or evaluation.created_by.get_full_name()},",
        intro=["An interview has been scheduled on your MeritLense account. The candidate has received "
               "their invitation and personal link to join."],
        event=tile,
        details=[
            ("Candidate", name),
            ("Candidate email", evaluation.candidate_email),
            ("Interview type", evaluation.get_evaluation_type_display()),
            ("Role", _role(evaluation)),
            ("Duration", f"{evaluation.duration_minutes} minutes" if evaluation.duration_minutes else ""),
            ("Scheduled by", evaluation.created_by.get_full_name()),
        ],
        notes=["You can follow the interview and see the results from your MeritLense dashboard."],
    )
    send_email(owner, [owner_email],
               calendar=_invite(evaluation, attendee_email=owner_email,
                                attendee_name=evaluation.created_by.get_full_name()))


def send_evaluation_rescheduled_email(evaluation, old_date, request=None):
    name = _candidate_name(evaluation)
    email = Email(
        subject=f"Interview rescheduled: now {format_local(evaluation.scheduled_date, '%d %b %Y, %I:%M %p')}",
        eyebrow="Interview rescheduled",
        title="Your interview has a new time",
        greeting=f"Hello {evaluation.candidate_first_name},",
        intro=[f"{_organisation(evaluation)} has moved your interview. The calendar invite attached "
               "updates the event in your calendar."],
        event=EventTile(start=evaluation.scheduled_date, end=_end(evaluation), title=_event_title(evaluation),
                        subtitle=_where(evaluation)),
        details=[("Previous time", format_local(old_date))] + _meeting_details(evaluation),
        button=("Join your interview", evaluation.meeting_link) if evaluation.meeting_link else None,
        notes=[f"If you didn't expect this change, please contact {_organisation(evaluation)}."],
        tone="warning",
    )
    send_email(email, [evaluation.candidate_email],
               calendar=_invite(evaluation, attendee_email=evaluation.candidate_email, attendee_name=name))


def send_evaluation_cancelled_email(evaluation, request=None):
    name = _candidate_name(evaluation)
    email = Email(
        subject=f"Interview cancelled: {_event_title(evaluation)}",
        eyebrow="Interview cancelled",
        title="Your interview has been cancelled",
        greeting=f"Hello {evaluation.candidate_first_name},",
        intro=[f"{_organisation(evaluation)} has cancelled your interview. It has been removed from your calendar."],
        details=[
            ("Interview", _event_title(evaluation)),
            ("Was scheduled for", format_local(evaluation.scheduled_date)),
            ("Reason", evaluation.cancellation_reason),
        ],
        notes=[f"If you have any questions, please contact {_organisation(evaluation)}."],
        tone="danger",
    )
    send_email(email, [evaluation.candidate_email],
               calendar=_invite(evaluation, attendee_email=evaluation.candidate_email, attendee_name=name,
                                method="CANCEL"))


def send_evaluation_completed_email(evaluation, request=None):
    has_certificate = evaluation.certificate_status == "ISSUED" and evaluation.certificate_url
    email = Email(
        subject=f"Interview completed: {_event_title(evaluation)}",
        eyebrow="Interview completed",
        title="Thank you for completing your interview",
        greeting=f"Hello {evaluation.candidate_first_name},",
        intro=[f"Your interview with {_organisation(evaluation)} is complete. Thank you for your time."],
        details=[
            ("Interview", _event_title(evaluation)),
            ("Completed", format_local(evaluation.completed_at) if evaluation.completed_at else "Recently"),
            ("Certificate", "Issued" if has_certificate else
             "Being prepared" if evaluation.certificate_status == "PENDING" else ""),
        ],
        button=("View your certificate", evaluation.certificate_url) if has_certificate else None,
        after_button=[f"{_organisation(evaluation)} will contact you about next steps."],
        tone="success",
    )
    send_email(email, [evaluation.candidate_email])
