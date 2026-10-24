"""The one layout every MeritLense email is sent in.

Fixed identity: the original logo (the same https://meritlense.com/logo.png
the site uses), brand colours, type, spacing, the approved signature and the
confidentiality notice. Variable content: an eyebrow, a title, the greeting,
the message and whatever modules the event needs - a verification code,
a details table, a list, one action button, a security notice.

Two ways in:

- render_email(...) builds the HTML from explicit blocks. The verification
  and signing-code emails and email_button_html() use it.
- brand_message(message) runs on every outgoing email in the email backend
  (api/core/email_links.py). A plain-text email gets an HTML version built
  from its text ("Label: value" lines become a details table, "- item"
  lines a list, a URL on its own line the action button), so senders that
  only write text, and any email added later, still go out in this layout.
  The plain-text version is sent unchanged alongside it.

Table-based with inline styles so Gmail, Outlook (desktop, web, mobile) and
Apple Mail lay it out the same; Arabic renders right-to-left.
"""
import html
import re
import textwrap

from django.core.mail.message import EmailAlternative

from api.core.email_i18n import ARABIC, ENGLISH, SIGNATURE

LOGO_URL = "https://meritlense.com/logo.png"  # the site's own asset, 506x459
LOGO_WIDTH, LOGO_HEIGHT = 64, 58
CONTACT_EMAIL = "info@meritlense.com"
SITE_URL = "https://www.meritlense.com"
SITE_LABEL = "www.meritlense.com"

# The approved confidentiality notice, inserted verbatim under the
# signature. Left empty until the approved wording is supplied - the block
# is hidden while empty, so nothing unapproved is ever sent.
CONFIDENTIALITY_NOTICE = {
    ENGLISH: "",
    ARABIC: "",
}

# Filled with the email's subject (and an eyebrow derived from it) by
# brand_message(), for HTML built without a title, e.g. by email_button_html.
TITLE_SLOT = "<!--ml:title-->"
EYEBROW_SLOT = "<!--ml:eyebrow-->"

NAVY = "#0f1a45"
TEXT = "#1c1e26"
MUTED = "#4b5563"
SUBTLE = "#6b7280"
PURPLE = "#6d28d9"
BLUE = "#3d72fc"
LINK = "#2563eb"
PANEL = "#f4f5fb"
BORDER = "#e4e6f2"
PAGE = "#eef1f8"

_TEXT = {
    ENGLISH: {
        "fallback": "If the button doesn't work, open this link:",
        "notice": "Confidentiality notice",
        "code_label": "Your verification code",
        "dont_share": "Do not share this code.",
        "never_ask": "MeritLense will never ask for your verification code by phone or email.",
        "open": "Open link",
    },
    ARABIC: {
        "fallback": "إذا لم يعمل الزر، افتح هذا الرابط:",
        "notice": "إشعار السرية",
        "code_label": "رمز التحقق الخاص بك",
        "dont_share": "لا تشارك هذا الرمز.",
        "never_ask": "لن تطلب منك MeritLense رمز التحقق عبر الهاتف أو البريد الإلكتروني.",
        "open": "فتح الرابط",
    },
}


def text(key, language):
    return _TEXT[ARABIC if language == ARABIC else ENGLISH][key]


def code_security_notice(language):
    """The standard notice shown under a verification or signing code."""
    return ("notice", (text("dont_share", language), text("never_ask", language)))


# --- rendering -------------------------------------------------------------

_URL = re.compile(r"""https?://[^\s<>"']+""")


def _inline(value):
    """Escape text and turn the URLs in it into links."""
    out, last = [], 0
    for match in _URL.finditer(value or ""):
        url = match.group(0).rstrip(".,;:!?)")
        out.append(html.escape(value[last:match.start()]))
        out.append(f'<a href="{html.escape(url, quote=True)}" style="color:{LINK};text-decoration:underline;" '
                   f'dir="ltr">{html.escape(url)}</a>')
        last = match.start() + len(url)
    out.append(html.escape((value or "")[last:]))
    return "".join(out).replace("\n", "<br>")


def _tracking(rtl, px):
    # Letter-spacing breaks the joins between Arabic letters.
    return "" if rtl else f"letter-spacing:{px}px;"


def _p(value, *, size=15, color=MUTED, margin="0 0 14px", weight="normal"):
    return (f'<p style="margin:{margin};font-size:{size}px;line-height:1.6;color:{color};'
            f'font-weight:{weight};">{_inline(value)}</p>')


def _code_block(code, label, expiry, align):
    expiry_html = (f'<p style="margin:10px 0 0;font-size:14px;line-height:1.5;color:{MUTED};">'
                   f'{_inline(expiry)}</p>') if expiry else ""
    # Plain text on purpose: selectable and copyable in every client, no
    # copy button. user-select:all lets one tap select the whole code.
    return f"""<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:6px 0 20px;">
<tr><td align="center" bgcolor="{PANEL}" style="background:{PANEL};border:1px solid {BORDER};border-radius:12px;padding:22px 16px;text-align:center;">
<p style="margin:0 0 8px;font-size:{13 if align == 'right' else 12}px;line-height:1.4;{_tracking(align == 'right', 2)}text-transform:uppercase;font-weight:bold;color:{PURPLE};">{html.escape(label)}</p>
<p class="ml-code" dir="ltr" style="margin:0;font-family:'Segoe UI',Arial,Helvetica,sans-serif;font-size:38px;line-height:1.2;letter-spacing:10px;font-weight:bold;color:{NAVY};-webkit-user-select:all;user-select:all;">{html.escape(str(code))}</p>
{expiry_html}
</td></tr></table>"""


def _details_block(rows, align):
    cells = "".join(
        f'<tr><td valign="top" style="padding:9px 14px;font-size:13px;line-height:1.5;color:{SUBTLE};'
        f'width:38%;text-align:{align};border-bottom:1px solid {BORDER};">{html.escape(label)}</td>'
        f'<td valign="top" style="padding:9px 14px;font-size:14px;line-height:1.5;color:{TEXT};'
        f'font-weight:bold;text-align:{align};border-bottom:1px solid {BORDER};word-break:break-word;">'
        f'{_inline(value)}</td></tr>'
        for label, value in rows
    )
    return (f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" bgcolor="{PANEL}" '
            f'style="margin:4px 0 18px;background:{PANEL};border:1px solid {BORDER};border-radius:12px;'
            f'border-collapse:separate;overflow:hidden;">{cells}</table>')


def _list_block(items, align):
    padding = "padding-left:10px;" if align == "left" else "padding-right:10px;"
    rows = "".join(
        f'<tr><td valign="top" style="width:14px;font-size:15px;line-height:1.6;color:{PURPLE};">&#8226;</td>'
        f'<td valign="top" style="{padding}font-size:15px;line-height:1.6;color:{MUTED};text-align:{align};'
        f'padding-bottom:4px;">{_inline(item)}</td></tr>'
        for item in items
    )
    return (f'<table role="presentation" cellpadding="0" cellspacing="0" style="margin:0 0 16px;">'
            f'{rows}</table>')


def _button_block(label, url, language):
    href = html.escape(url, quote=True)
    gradient = f"background-image:linear-gradient(90deg,{BLUE},#8a4fff);"
    return f"""<table role="presentation" cellpadding="0" cellspacing="0" style="margin:8px 0 14px;">
<tr><td bgcolor="{BLUE}" style="border-radius:10px;background:{BLUE};{gradient}mso-padding-alt:13px 30px;">
<a href="{href}" target="_blank" style="display:inline-block;padding:13px 30px;font-size:15px;line-height:1.2;font-weight:bold;color:#ffffff;text-decoration:none;border-radius:10px;">{html.escape(label)}</a>
</td></tr></table>
<p style="margin:0 0 18px;font-size:12px;line-height:1.5;color:{SUBTLE};word-break:break-all;">{html.escape(text("fallback", language))}<br><a href="{href}" dir="ltr" style="color:{LINK};">{html.escape(url)}</a></p>"""


def _notice_block(heading, body, align):
    return f"""<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:0 0 20px;">
<tr><td bgcolor="{PANEL}" style="background:{PANEL};border:1px solid {BORDER};border-radius:12px;padding:14px 16px;">
<table role="presentation" cellpadding="0" cellspacing="0"><tr>
<td valign="top" style="font-size:22px;line-height:1;padding-{'right' if align == 'left' else 'left'}:12px;">&#128737;&#65039;</td>
<td valign="top" style="text-align:{align};"><p style="margin:0 0 2px;font-size:14px;line-height:1.5;font-weight:bold;color:{NAVY};">{html.escape(heading)}</p>
<p style="margin:0;font-size:13px;line-height:1.5;color:{MUTED};">{_inline(body)}</p></td>
</tr></table>
</td></tr></table>"""


def _block_html(kind, value, align, language):
    if kind == "p":
        return _p(value)
    if kind == "heading":
        return _p(value, size=14, color=NAVY, margin="4px 0 8px", weight="bold")
    if kind == "code":
        return _code_block(value["code"], value.get("label") or text("code_label", language),
                           value.get("expiry"), align)
    if kind == "details":
        return _details_block(value, align) if value else ""
    if kind == "list":
        return _list_block(value, align) if value else ""
    if kind == "button":
        label, url = value
        return _button_block(label, url, language) if url else ""
    if kind == "notice":
        return _notice_block(*value, align)
    raise ValueError(f"Unknown email block {kind!r}")


def _signature_html(language, align):
    lines = SIGNATURE[ARABIC if language == ARABIC else ENGLISH].split("\n")
    closing, team = lines[0], " ".join(lines[1:])
    notice = CONFIDENTIALITY_NOTICE.get(ARABIC if language == ARABIC else ENGLISH, "").strip()
    notice_html = f"""<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:20px 0 0;">
<tr><td bgcolor="{PANEL}" style="background:{PANEL};border-radius:12px;padding:14px 16px;text-align:{align};">
<p style="margin:0 0 4px;font-size:13px;line-height:1.5;font-weight:bold;color:{NAVY};">{html.escape(text("notice", language))}</p>
<p style="margin:0;font-size:12px;line-height:1.6;color:{SUBTLE};">{html.escape(notice).replace(chr(10), "<br>")}</p>
</td></tr></table>""" if notice else ""
    return f"""<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:10px 0 0;">
<tr><td style="border-top:1px solid {BORDER};padding-top:18px;text-align:{align};">
<p style="margin:0;font-size:14px;line-height:1.6;color:{MUTED};">{html.escape(closing)}</p>
<p style="margin:0 0 6px;font-size:15px;line-height:1.6;font-weight:bold;color:{NAVY};">{html.escape(team)}</p>
<p style="margin:0;font-size:14px;line-height:1.6;color:{LINK};" dir="ltr"><a href="mailto:{CONTACT_EMAIL}" style="color:{LINK};text-decoration:none;">{CONTACT_EMAIL}</a>&nbsp;&nbsp;|&nbsp;&nbsp;<a href="{SITE_URL}" style="color:{LINK};text-decoration:none;">{SITE_LABEL}</a></p>
</td></tr></table>{notice_html}"""


def render_email(*, language=ENGLISH, title=None, eyebrow=None, greeting=None, blocks=(), preheader=None):
    """The branded HTML email. blocks is a sequence of (kind, value):
    ("p", text), ("heading", text), ("code", {"code", "label", "expiry"}),
    ("details", [(label, value)]), ("list", [text]), ("button", (label, url)),
    ("notice", (heading, text)). title/eyebrow left as None are filled from
    the subject when the email is sent (see brand_message)."""
    rtl = language == ARABIC
    align = "right" if rtl else "left"
    dir_attr = "rtl" if rtl else "ltr"
    font = "Tahoma,Arial,Helvetica,sans-serif" if rtl else "'Segoe UI',Arial,Helvetica,sans-serif"
    title_html = html.escape(title) if title else TITLE_SLOT
    eyebrow_html = html.escape(eyebrow) if eyebrow else EYEBROW_SLOT
    body = "".join(_block_html(kind, value, align, language) for kind, value in blocks)
    greeting_html = _p(greeting, size=16, color=TEXT, margin="0 0 8px") if greeting else ""
    if preheader is None:
        preheader = next((v for k, v in blocks if k == "p"), "")
    preheader_html = (f'<div style="display:none;max-height:0;overflow:hidden;mso-hide:all;font-size:1px;'
                      f'line-height:1px;color:{PAGE};opacity:0;">{html.escape(preheader)}</div>') if preheader else ""
    return f"""<!doctype html>
<html lang="{'ar' if rtl else 'en'}" dir="{dir_attr}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="x-apple-disable-message-reformatting">
<meta name="color-scheme" content="light">
<meta name="supported-color-schemes" content="light">
<title>{title_html}</title>
<style>
body{{margin:0;padding:0;}}
a{{color:{LINK};}}
@media only screen and (max-width:620px){{
.ml-card{{border-radius:0 !important;}}
.ml-pad{{padding-left:20px !important;padding-right:20px !important;}}
.ml-title{{font-size:23px !important;}}
.ml-code{{font-size:32px !important;letter-spacing:7px !important;}}
}}
</style>
</head>
<body dir="{dir_attr}" style="margin:0;padding:0;background:{PAGE};-webkit-text-size-adjust:100%;-ms-text-size-adjust:100%;">
{preheader_html}
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" bgcolor="{PAGE}" style="background:{PAGE};">
<tr><td align="center" style="padding:24px 8px;">
<!--[if mso]><table role="presentation" width="600" align="center" cellpadding="0" cellspacing="0"><tr><td><![endif]-->
<table role="presentation" class="ml-card" width="100%" cellpadding="0" cellspacing="0" dir="{dir_attr}" bgcolor="#ffffff" style="max-width:600px;background:#ffffff;border:1px solid {BORDER};border-radius:16px;border-collapse:separate;overflow:hidden;font-family:{font};text-align:{align};">
<tr><td height="5" bgcolor="{BLUE}" style="height:5px;line-height:5px;font-size:0;background:{BLUE};background-image:linear-gradient(90deg,{BLUE},#8a4fff,#ff5bae);">&nbsp;</td></tr>
<tr><td align="center" style="padding:26px 32px 6px;">
<a href="{SITE_URL}" target="_blank" style="text-decoration:none;"><img src="{LOGO_URL}" width="{LOGO_WIDTH}" height="{LOGO_HEIGHT}" alt="MeritLense" style="display:block;width:{LOGO_WIDTH}px;height:{LOGO_HEIGHT}px;border:0;outline:none;text-decoration:none;"></a>
</td></tr>
<tr><td class="ml-pad" style="padding:18px 40px 30px;text-align:{align};">
<p style="margin:0 0 6px;font-size:{13 if rtl else 12}px;line-height:1.4;{_tracking(rtl, 1.5)}text-transform:uppercase;font-weight:bold;color:{PURPLE};">{eyebrow_html}</p>
<h1 class="ml-title" style="margin:0 0 18px;font-size:27px;line-height:1.25;font-weight:bold;color:{NAVY};">{title_html}</h1>
{greeting_html}
{body}
{_signature_html(language, align)}
</td></tr>
</table>
<!--[if mso]></td></tr></table><![endif]-->
</td></tr>
</table>
</body>
</html>"""


# --- eyebrows ---------------------------------------------------------------

# First match wins, so the more specific kinds come first.
_EYEBROWS = (
    (r"invoice|payment|paid|package|plan\b|slot|refund|subscription|فاتورة|الدفع|دفع|باقة|خانة|خانات|استرداد|اشتراك",
     "Billing & payments", "الفوترة والمدفوعات"),
    (r"password|كلمة المرور", "Account security", "أمان الحساب"),
    (r"signing code|رمز التوقيع", "Agreement signing", "توقيع الاتفاقية"),
    (r"verif|تأكيد حساب|رمز التحقق", "Account verification", "التحقق من الحساب"),
    (r"invitation|invited|دعوة", "Invitation", "دعوة"),
    # Before results: a document name such as "... certificate" is not a result.
    (r"document|upload|licen[cs]e|مستند|رفع|رخصة", "Account documents", "مستندات الحساب"),
    (r"complete|result|report|certificate|اكتمل|نتيجة|نتائج|تقرير|شهادة", "Results", "النتائج"),
    (r"evaluation|interview|assessment|schedul|تقييم|مقابلة|موعد", "Assessment update", "تحديث التقييم"),
    (r"account|licen[cs]e|document|welcome|حساب|رخصة|مستند|مرحبًا بك", "Account update", "تحديث الحساب"),
)


def eyebrow_for(subject, language):
    for pattern, en, ar in _EYEBROWS:
        if re.search(pattern, subject or "", re.IGNORECASE):
            return ar if language == ARABIC else en
    return "MeritLense"


# --- plain text to blocks -----------------------------------------------------

_ARABIC_CHARS = re.compile(r"[؀-ۿ]")
_BULLET = re.compile(r"^\s*(?:[-•*]|\d+[.)])\s+(.*)$")
_LABEL_VALUE = re.compile(r"^([^:\n]{1,45}?):\s+(\S.*)$")
_BARE_URL = re.compile(r"^https?://\S+$")
_GREETING = re.compile(r"^(hello|hi|dear|مرحبًا|مرحبا|عزيزي)\b", re.IGNORECASE)
_SIGN_OFF = re.compile(r"^(best regards|kind regards|regards|thanks|thank you,|مع أطيب التحيات|مع خالص التحيات)",
                       re.IGNORECASE)
_TEAM_LINE = re.compile(r"^(the )?meritlense team$|^فريق meritlense$", re.IGNORECASE)

# Button labels for known destinations; checked against the URL's path.
_BUTTONS = (
    ("/auth/reset-password", "Reset password", "إعادة تعيين كلمة المرور"),
    ("/auth/accept-invitation", "Accept invitation", "قبول الدعوة"),
    ("/auth/login", "Sign in", "تسجيل الدخول"),
    ("/interview", "Start assessment", "بدء التقييم"),
    ("/p/", "Pay now", "ادفع الآن"),
    ("stripe.com", "Pay now", "ادفع الآن"),
)
# Otherwise by what the line before the link says.
_LEAD_BUTTONS = (
    (r"certificate|شهادة", "Download certificate", "تنزيل الشهادة"),
    (r"invoice|فاتورة", "View invoice", "عرض الفاتورة"),
    (r"report|تقرير", "View report", "عرض التقرير"),
)


def detect_language(*texts):
    return ARABIC if any(_ARABIC_CHARS.search(t or "") for t in texts) else ENGLISH


def _label_value(line):
    """("Type", "Interview") for "Type: Interview"; None for a sentence that
    merely contains a colon ("Good news: your request was approved.") or
    that leads into a link ("Download your certificate here: https://...")."""
    match = _LABEL_VALUE.match(line.strip())
    if not match or "http" in match.group(1).lower():
        return None
    label, value = match.group(1).strip(), match.group(2).strip()
    if value[-1:] in ".!?؟" or (_BARE_URL.match(value) and len(label.split()) > 3):
        return None
    return label, value


def _button_label(url, lead, language):
    for needle, en, ar in _BUTTONS:
        if needle in url:
            return ar if language == ARABIC else en
    for pattern, en, ar in _LEAD_BUTTONS:
        if lead and re.search(pattern, lead, re.IGNORECASE):
            return ar if language == ARABIC else en
    if lead:
        label = re.sub(r"\s*(here|below|من هنا|أدناه)$", "", lead.rstrip(":： ").strip(), flags=re.IGNORECASE)
        if 0 < len(label) <= 40:
            return label
    return text("open", language)


def _unwrap(lines):
    """Rejoin a paragraph hard-wrapped for plain text, so it flows on a
    phone; a line ending a sentence still starts a new line."""
    out = lines[0]
    for line in lines[1:]:
        out += ("\n" if out.rstrip()[-1:] in ".!?:؟" else " ") + line
    return out


def _structured(lines):
    """A details table when every line (bulleted or not) is "Label: value",
    a list when every line is a bullet; otherwise None."""
    bullets = [_BULLET.match(line) for line in lines]
    items = [b.group(1).strip() if b else line.strip() for line, b in zip(lines, bullets)]
    pairs = [_label_value(item) for item in items]
    if all(pairs):
        return ("details", pairs)
    if all(bullets):
        return ("list", items)
    return None


def _paragraph_blocks(lines):
    """Blocks for one paragraph (lines separated by no blank line)."""
    structured = _structured(lines)
    if structured:
        return [structured]
    if len(lines) > 1 and lines[0].rstrip().endswith(":"):
        head = lines[0].rstrip().rstrip(":")
        rest = _structured(lines[1:])
        if rest:
            return [("heading", head), rest]
        if len(lines) == 2 and not _BARE_URL.match(lines[1].strip()):
            return [("details", [(head, lines[1].strip())])]
    # Mixed lines: each "Label: value" is a details row, a link (alone, or
    # after "...here:") an action, the rest flowing text.
    blocks, run = [], []

    def flush():
        if run:
            blocks.append(("p", _unwrap(run)))
            run.clear()

    for line in lines:
        line = line.strip()
        lead, _, tail = line.rpartition(": ")
        pair = _label_value(line)
        if _BARE_URL.match(line):
            flush()
            blocks.append(("url", line))
        elif pair:
            flush()
            if blocks and blocks[-1][0] == "details":
                blocks[-1][1].append(pair)
            else:
                blocks.append(("details", [pair]))
        elif lead and _BARE_URL.match(tail):
            run.append(lead + ":")
            flush()
            blocks.append(("url", tail))
        else:
            run.append(line)
    flush()
    return blocks


def blocks_from_text(body, language):
    """(greeting, blocks) for a plain-text email body. The sign-off is
    dropped: the layout adds the approved signature itself."""
    body = textwrap.dedent((body or "").replace("\r\n", "\n")).strip()
    paragraphs = [[line.strip() for line in chunk.split("\n") if line.strip()]
                  for chunk in re.split(r"\n\s*\n", body)]
    paragraphs = [p for p in paragraphs if p]

    # Drop the sign-off and the "MeritLense Team" line wherever they are.
    cleaned = []
    for lines in paragraphs:
        kept = [line for line in lines if not _SIGN_OFF.match(line) and not _TEAM_LINE.match(line)]
        if kept:
            cleaned.append(kept)
    paragraphs = cleaned

    greeting = None
    if paragraphs and len(paragraphs[0]) == 1 and _GREETING.match(paragraphs[0][0]):
        greeting = paragraphs.pop(0)[0]

    raw = []
    for lines in paragraphs:
        raw.extend(_paragraph_blocks(lines))

    blocks, has_button = [], False
    for kind, value in raw:
        if kind == "url":
            lead = blocks[-1][1] if blocks and blocks[-1][0] == "p" and "\n" not in blocks[-1][1] else ""
            label = _button_label(value, lead, language)
            if lead and lead.rstrip(":： ").strip().startswith(label):
                blocks.pop()  # the button now says it
            if has_button:
                blocks.append(("p", value))
            else:
                blocks.append(("button", (label, value)))
                has_button = True
        elif kind == "details" and blocks and blocks[-1][0] == "details":
            blocks[-1] = ("details", blocks[-1][1] + value)
        else:
            blocks.append((kind, value))
    if not has_button:
        blocks = _promote_link_row(blocks, language)
    return greeting, blocks


def _promote_link_row(blocks, language):
    """With no other action, a details row that is just a link ("Meeting
    Link: https://...") becomes the email's button."""
    for i, (kind, value) in enumerate(blocks):
        if kind != "details":
            continue
        for j, (label, row_value) in enumerate(value):
            if _BARE_URL.match(row_value) and re.search(r"link|رابط", label, re.IGNORECASE):
                if re.search(r"meeting|اجتماع", label, re.IGNORECASE):
                    button = "الانضمام إلى الاجتماع" if language == ARABIC else "Join meeting"
                else:
                    button = _button_label(row_value, "", language)
                rows = value[:j] + value[j + 1:]
                return blocks[:i] + ([("details", rows)] if rows else []) + [("button", (button, row_value))] \
                    + blocks[i + 1:]
    return blocks


def render_text_email(subject, body, language=None):
    language = language or detect_language(subject, body)
    greeting, blocks = blocks_from_text(body, language)
    return render_email(language=language, title=subject, eyebrow=eyebrow_for(subject, language),
                        greeting=greeting, blocks=blocks)


def fill_subject_slots(content, subject):
    if TITLE_SLOT not in content and EYEBROW_SLOT not in content:
        return content
    eyebrow = eyebrow_for(subject, detect_language(content))
    return content.replace(TITLE_SLOT, html.escape(subject or "")).replace(EYEBROW_SLOT, html.escape(eyebrow))


def _is_html(alternative):
    return isinstance(alternative[0], str) and (alternative[1] or "").startswith("text/html")


def brand_message(message):
    """Give an outgoing email the branded HTML version (see module doc)."""
    if getattr(message, "content_subtype", "plain") == "html":
        return message
    alternatives = getattr(message, "alternatives", None) or []
    if any(_is_html(alt) for alt in alternatives):
        message.alternatives = [
            EmailAlternative(fill_subject_slots(alt[0], message.subject), alt[1]) if _is_html(alt) else alt
            for alt in alternatives
        ]
        return message
    if hasattr(message, "attach_alternative") and (message.body or "").strip():
        message.attach_alternative(render_text_email(message.subject, message.body), "text/html")
    return message


# --- adapters -----------------------------------------------------------------

def blocks_from_paragraphs(paragraphs, language):
    """Blocks for email_button_html's paragraphs: "A: 1 · B: 2" lines become
    a details table, everything else stays a paragraph."""
    blocks = []
    for paragraph in paragraphs:
        if not paragraph:
            continue
        parts = [part.strip() for part in str(paragraph).split(" · ") if part.strip()]
        pairs = [_label_value(part) for part in parts]
        if parts and all(pairs):
            if blocks and blocks[-1][0] == "details":
                blocks[-1] = ("details", blocks[-1][1] + pairs)
            else:
                blocks.append(("details", pairs))
        else:
            blocks.append(("p", paragraph))
    return blocks


def without_sign_off(lines):
    return [line for line in lines if line and not _SIGN_OFF.match(line.strip())
            and not _TEAM_LINE.match(line.strip())]
