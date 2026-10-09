"""Branded, professional emails for every MeritLense notification.

One layout for all client emails: logo header, a card with an eyebrow label,
a title, optional event tile (calendar-style date block), details panel,
highlighted code, one call-to-action button, notes, and a consistent
footer. Each email is also sent as plain text, generated from the same
content, for clients that don't render HTML.

Interview emails can carry a real calendar invite (iCalendar, RFC 5545):
Gmail/Outlook then show the calendar card with Yes/Maybe/No and add the
interview to the recipient's calendar. See build_calendar_invite().
"""
import re
import textwrap
from dataclasses import dataclass, field
from email.utils import parseaddr
from html import escape
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.utils import timezone

BRAND = "MeritLense"
PRIMARY = "#3D72FC"
PURPLE = "#8A4FFF"
INK = "#14213D"
MUTED = "#5B6478"
LINE = "#E6E9F2"
PANEL = "#F6F8FD"
PAGE = "#F1F4FA"
SUPPORT_EMAIL = "info@meritlense.com"
DISPLAY_TZ = ZoneInfo("Asia/Riyadh")
DISPLAY_TZ_LABEL = "AST (GMT+3)"


def _logo_url():
    return getattr(settings, "EMAIL_LOGO_URL", "") or "https://meritlense.com/logo.png"


def _site_url():
    return (getattr(settings, "FRONTEND_URL", "") or "https://meritlense.com").rstrip("/")


@dataclass
class EventTile:
    """Calendar-style date block, like a calendar invite preview."""
    start: object  # aware datetime
    end: object = None
    title: str = ""
    subtitle: str = ""  # e.g. "Online · AI interview"


@dataclass
class Email:
    subject: str
    title: str
    eyebrow: str = ""
    greeting: str = ""
    intro: list = field(default_factory=list)          # paragraphs
    event: EventTile = None
    details: list = field(default_factory=list)        # [(label, value)]
    code: str = ""                                      # big highlighted code (OTP etc.)
    code_label: str = ""
    button: tuple = None                                # (label, url)
    after_button: list = field(default_factory=list)   # paragraphs
    checklist_title: str = ""
    checklist: list = field(default_factory=list)
    notes: list = field(default_factory=list)          # small muted lines
    tone: str = "info"                                  # info | success | warning | danger
    preheader: str = ""


TONE_COLORS = {"info": PRIMARY, "success": "#16A34A", "warning": "#D97706", "danger": "#DC2626"}


def format_local(dt, fmt="%A, %d %B %Y · %I:%M %p"):
    return f"{timezone.localtime(dt, DISPLAY_TZ).strftime(fmt)} {DISPLAY_TZ_LABEL}"


def _time_range(event):
    start = timezone.localtime(event.start, DISPLAY_TZ)
    text = start.strftime("%I:%M %p").lstrip("0")
    if event.end:
        end = timezone.localtime(event.end, DISPLAY_TZ)
        text += " – " + end.strftime("%I:%M %p").lstrip("0")
    return f"{start.strftime('%A, %d %B %Y')} · {text} {DISPLAY_TZ_LABEL}"


def _p(text, size=15, color=INK, margin="0 0 14px"):
    return (f'<p style="margin:{margin};font-size:{size}px;line-height:1.6;color:{color};">'
            f"{escape(str(text))}</p>")


def render_html(email: Email) -> str:
    accent = TONE_COLORS.get(email.tone, PRIMARY)
    parts = []
    if email.eyebrow:
        parts.append(f'<p style="margin:0 0 8px;font-size:12px;font-weight:700;letter-spacing:1.4px;'
                     f'text-transform:uppercase;color:{PURPLE};">{escape(email.eyebrow)}</p>')
    parts.append(f'<h1 style="margin:0 0 18px;font-size:22px;line-height:1.3;font-weight:700;color:{INK};">'
                 f"{escape(email.title)}</h1>")
    if email.greeting:
        parts.append(_p(email.greeting))
    parts.extend(_p(t) for t in email.intro if t)

    if email.event:
        start = timezone.localtime(email.event.start, DISPLAY_TZ)
        parts.append(f"""
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:6px 0 20px;border:1px solid {LINE};border-radius:12px;background:{PANEL};">
<tr>
<td width="76" valign="top" style="padding:16px 0 16px 16px;">
  <table role="presentation" cellpadding="0" cellspacing="0" style="width:60px;border-radius:10px;background:#EEF0FF;">
  <tr><td align="center" style="padding:8px 0 0;font-size:12px;font-weight:700;letter-spacing:1px;color:{PURPLE};">{start.strftime('%b').upper()}</td></tr>
  <tr><td align="center" style="padding:0 0 8px;font-size:26px;font-weight:700;color:{PURPLE};line-height:1.1;">{start.day}</td></tr>
  </table>
</td>
<td valign="top" style="padding:16px 16px 16px 4px;">
  <p style="margin:0 0 4px;font-size:16px;font-weight:700;color:{INK};">{escape(email.event.title)}</p>
  <p style="margin:0 0 4px;font-size:14px;color:{MUTED};">{escape(_time_range(email.event))}</p>
  {f'<p style="margin:0;font-size:14px;color:{MUTED};">{escape(email.event.subtitle)}</p>' if email.event.subtitle else ''}
</td>
</tr>
</table>""")

    if email.code:
        parts.append(f"""
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:6px 0 20px;">
<tr><td align="center" style="padding:18px;border-radius:12px;background:{PANEL};border:1px dashed #C9D3F5;">
  {f'<p style="margin:0 0 6px;font-size:12px;letter-spacing:1px;text-transform:uppercase;color:{MUTED};">{escape(email.code_label)}</p>' if email.code_label else ''}
  <p style="margin:0;font-size:30px;font-weight:700;letter-spacing:8px;color:{INK};font-family:'Courier New',monospace;">{escape(email.code)}</p>
</td></tr>
</table>""")

    rows = [(label, value) for label, value in email.details if value not in (None, "")]
    if rows:
        cells = "".join(
            f'<tr><td style="padding:9px 14px;font-size:13px;color:{MUTED};width:38%;vertical-align:top;'
            f'border-bottom:1px solid {LINE};">{escape(str(label))}</td>'
            f'<td style="padding:9px 14px;font-size:14px;color:{INK};font-weight:600;vertical-align:top;'
            f'border-bottom:1px solid {LINE};word-break:break-word;">{escape(str(value))}</td></tr>'
            for label, value in rows)
        parts.append(f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
                     f'style="margin:4px 0 22px;border:1px solid {LINE};border-radius:10px;background:#FFFFFF;'
                     f'border-collapse:separate;overflow:hidden;">{cells}</table>')

    if email.button:
        label, url = email.button
        safe_url = escape(url, quote=True)
        parts.append(f"""
<table role="presentation" cellpadding="0" cellspacing="0" style="margin:4px 0 10px;">
<tr><td style="border-radius:999px;background:{accent};background-image:linear-gradient(135deg,{PRIMARY},{PURPLE});">
<a href="{safe_url}" target="_blank" style="display:inline-block;padding:13px 30px;font-size:15px;font-weight:700;color:#FFFFFF;text-decoration:none;border-radius:999px;">{escape(label)}</a>
</td></tr>
</table>
<p style="margin:0 0 20px;font-size:12px;line-height:1.5;color:{MUTED};">Button not working? Copy this link into your browser:<br>
<a href="{safe_url}" style="color:{PRIMARY};word-break:break-all;">{escape(url)}</a></p>""")

    parts.extend(_p(t) for t in email.after_button if t)

    if email.checklist:
        items = "".join(
            f'<tr><td valign="top" style="padding:4px 10px 4px 0;font-size:14px;color:{PRIMARY};">&#10003;</td>'
            f'<td style="padding:4px 0;font-size:14px;line-height:1.5;color:{INK};">{escape(item)}</td></tr>'
            for item in email.checklist)
        title = (f'<p style="margin:0 0 8px;font-size:14px;font-weight:700;color:{INK};">'
                 f'{escape(email.checklist_title)}</p>') if email.checklist_title else ""
        parts.append(f'<div style="margin:4px 0 18px;">{title}<table role="presentation" cellpadding="0" '
                     f'cellspacing="0">{items}</table></div>')

    parts.extend(_p(t, size=13, color=MUTED, margin="0 0 8px") for t in email.notes if t)
    parts.append(f'<p style="margin:22px 0 0;font-size:15px;line-height:1.6;color:{INK};">'
                 f'Best regards,<br><strong>The {BRAND} Team</strong></p>')

    preheader = escape(email.preheader or (email.intro[0] if email.intro else email.title))
    body = "\n".join(parts)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light"><title>{escape(email.subject)}</title></head>
<body style="margin:0;padding:0;background:{PAGE};font-family:'Segoe UI',Roboto,Helvetica,Arial,sans-serif;">
<div style="display:none;max-height:0;overflow:hidden;opacity:0;">{preheader}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{PAGE};padding:28px 12px;">
<tr><td align="center">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:600px;">
<tr><td align="center" style="padding:0 0 18px;">
  <a href="{escape(_site_url(), quote=True)}" target="_blank" style="text-decoration:none;">
  <img src="{escape(_logo_url(), quote=True)}" alt="{BRAND}" width="56" height="51" style="display:block;border:0;width:56px;height:auto;"></a>
</td></tr>
<tr><td style="background:#FFFFFF;border:1px solid {LINE};border-radius:16px;overflow:hidden;">
  <div style="height:5px;background:{accent};background-image:linear-gradient(90deg,{PRIMARY},{PURPLE},#FF5BAE);font-size:0;line-height:0;">&nbsp;</div>
  <div style="padding:30px 32px 30px;">
{body}
  </div>
</td></tr>
<tr><td align="center" style="padding:20px 16px 0;">
  <p style="margin:0 0 6px;font-size:12px;color:{MUTED};"><strong style="color:{INK};">{BRAND}</strong> · Workforce readiness assessments</p>
  <p style="margin:0;font-size:12px;line-height:1.6;color:{MUTED};">Questions? Contact us at
  <a href="mailto:{SUPPORT_EMAIL}" style="color:{PRIMARY};text-decoration:none;">{SUPPORT_EMAIL}</a> ·
  <a href="{escape(_site_url(), quote=True)}" style="color:{PRIMARY};text-decoration:none;">meritlense.com</a></p>
</td></tr>
</table>
</td></tr>
</table>
</body></html>"""


def render_text(email: Email) -> str:
    lines = [email.title, ""]
    if email.greeting:
        lines += [email.greeting, ""]
    for t in email.intro:
        if t:
            lines += [t, ""]
    if email.event:
        lines += [email.event.title, _time_range(email.event)]
        if email.event.subtitle:
            lines.append(email.event.subtitle)
        lines.append("")
    if email.code:
        lines += [f"{email.code_label or 'Code'}: {email.code}", ""]
    rows = [(label, value) for label, value in email.details if value not in (None, "")]
    if rows:
        lines += [f"{label}: {value}" for label, value in rows] + [""]
    if email.button:
        lines += [f"{email.button[0]}: {email.button[1]}", ""]
    for t in email.after_button:
        if t:
            lines += [t, ""]
    if email.checklist:
        if email.checklist_title:
            lines.append(email.checklist_title)
        lines += [f"- {item}" for item in email.checklist] + [""]
    for t in email.notes:
        if t:
            lines += [t, ""]
    lines += ["Best regards,", f"The {BRAND} Team", "", f"Questions? {SUPPORT_EMAIL}"]
    return "\n".join(lines)


# --------------------------------------------------------------------------- calendar

def _ics_escape(value):
    return (str(value or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
            .replace("\r\n", "\\n").replace("\n", "\\n"))


def _ics_fold(line):
    """RFC 5545: lines longer than 75 octets are folded."""
    out, current = [], ""
    for ch in line:
        if len((current + ch).encode("utf-8")) > 74:
            out.append(current)
            current = " " + ch
        else:
            current += ch
    out.append(current)
    return "\r\n".join(out)


def _ics_time(dt):
    return timezone.localtime(dt, ZoneInfo("UTC")).strftime("%Y%m%dT%H%M%SZ")


def build_calendar_invite(*, uid, start, end, summary, description="", location="", url="",
                          attendee_email="", attendee_name="", method="REQUEST", sequence=None):
    """An iCalendar invite (METHOD REQUEST, or CANCEL with the same uid to
    remove it). Sending a newer REQUEST with the same uid and a higher
    sequence updates the event in the recipient's calendar."""
    organizer_name, organizer_email = parseaddr(settings.DEFAULT_FROM_EMAIL or "")
    organizer_email = organizer_email or "no-reply@meritlense.com"
    organizer_name = organizer_name or BRAND
    sequence = int(timezone.now().timestamp() // 60) if sequence is None else sequence
    cancelled = method == "CANCEL"
    lines = [
        "BEGIN:VCALENDAR",
        "PRODID:-//MeritLense//Interviews//EN",
        "VERSION:2.0",
        "CALSCALE:GREGORIAN",
        f"METHOD:{method}",
        "BEGIN:VEVENT",
        f"UID:{uid}",
        f"DTSTAMP:{_ics_time(timezone.now())}",
        f"DTSTART:{_ics_time(start)}",
        f"DTEND:{_ics_time(end)}",
        f"SEQUENCE:{sequence}",
        f"SUMMARY:{_ics_escape(summary)}",
        f"DESCRIPTION:{_ics_escape(description)}",
        f"LOCATION:{_ics_escape(location)}",
        f"STATUS:{'CANCELLED' if cancelled else 'CONFIRMED'}",
        "TRANSP:OPAQUE",
        f"ORGANIZER;CN={_ics_escape(organizer_name)}:mailto:{organizer_email}",
    ]
    if url:
        lines.append(f"URL:{url}")
    if attendee_email:
        lines.append(f"ATTENDEE;CN={_ics_escape(attendee_name or attendee_email)};ROLE=REQ-PARTICIPANT;"
                     f"PARTSTAT=NEEDS-ACTION;RSVP=TRUE:mailto:{attendee_email}")
    if not cancelled:
        lines += ["BEGIN:VALARM", "TRIGGER:-PT15M", "ACTION:DISPLAY",
                  f"DESCRIPTION:{_ics_escape(summary)}", "END:VALARM"]
    lines += ["END:VEVENT", "END:VCALENDAR"]
    return "\r\n".join(_ics_fold(line) for line in lines) + "\r\n"


# --------------------------------------------------------------------------- sending

def build_message(email: Email, recipients, *, attachments=None, calendar=None, from_email=None):
    """calendar: (ics_text, method) - added both as the text/calendar
    alternative (Gmail/Outlook show the invite card) and as invite.ics."""
    message = EmailMultiAlternatives(email.subject, render_text(email),
                                     from_email or settings.DEFAULT_FROM_EMAIL or "no-reply@localhost",
                                     list(recipients))
    message.attach_alternative(render_html(email), "text/html")
    if calendar:
        ics, method = calendar
        message.attach_alternative(ics, f"text/calendar; method={method}; charset=UTF-8")
        message.attach("invite.ics", ics, f"application/ics; method={method}")
    for attachment in attachments or []:
        message.attach(*attachment)
    return message


def send_email(email: Email, recipients, *, attachments=None, calendar=None, fail_silently=False):
    return build_message(email, recipients, attachments=attachments, calendar=calendar).send(
        fail_silently=fail_silently)


# --------------------------------------------------------------------------- plain-text fallback

_URL = re.compile(r"^https?://\S+$")
_KV = re.compile(r"^[-•*]?\s*([A-Z][A-Za-z /&()'.-]{1,40}):\s+(.+)$")
_SIGN_OFF = re.compile(r"^(best regards|kind regards|regards|thanks|thank you)[,!.]?$", re.I)
_TEAM = re.compile(r"^(the )?meritlense team$", re.I)


_ACTIONS = (("sign in", "Sign in"), ("log in", "Sign in"), ("login", "Sign in"), ("reset", "Reset password"),
            ("accept", "Accept invitation"), ("pay", "Pay now"), ("invoice", "View invoice"),
            ("certificate", "View certificate"), ("report", "View report"), ("join", "Join"))


def _action_label(text):
    """A short button label from the sentence that introduced the link."""
    lowered = (text or "").lower()
    for needle, label in _ACTIONS:
        if needle in lowered:
            return label
    return "Open MeritLense"


def email_from_plain_text(subject, message):
    """Wrap an older plain-text email in the branded layout: the first
    "Hello ..." line becomes the greeting, "Label: value" lines the details
    panel, a lone URL the button, and the sign-off is replaced by the
    layout's own."""
    text = textwrap.dedent(str(message or "")).strip("\n")
    lines = [line.strip() for line in text.splitlines()]
    greeting, intro, details, notes = "", [], [], []
    button_url, pending_label = "", ""
    paragraph = []

    def flush():
        if paragraph:
            intro.append(" ".join(paragraph))
            paragraph.clear()

    for line in lines:
        if not line:
            flush()
            continue
        if _SIGN_OFF.match(line) or _TEAM.match(line):
            flush()
            continue
        if not greeting and not intro and not paragraph and line.lower().startswith(("hello", "hi ", "dear")):
            greeting = line
            continue
        if _URL.match(line):
            flush()
            if not button_url:
                button_url = line
                if intro and intro[-1].endswith(":"):
                    pending_label = intro.pop().rstrip(":")
            else:
                details.append(("Link", line))
            continue
        kv = _KV.match(line)
        if kv and not _URL.match(kv.group(2)):
            flush()
            details.append((kv.group(1).strip(), kv.group(2).strip()))
            continue
        if kv and _URL.match(kv.group(2)) and not button_url:
            flush()
            button_url, pending_label = kv.group(2), kv.group(1)
            continue
        paragraph.append(line.lstrip("-•* ").strip() if line[:1] in "-•*" else line)
    flush()

    button = None
    if button_url:
        button = (_action_label(pending_label), button_url)
    return Email(subject=subject, title=subject, greeting=greeting, intro=intro, details=details,
                 button=button, notes=notes)
