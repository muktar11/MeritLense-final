from django.utils import timezone
import secrets
import logging
from django.core.mail import EmailMultiAlternatives
from django.conf import settings
from api.core.constants import CompanyTeamPermissions, Roles

logger = logging.getLogger(__name__)


def safe_send_mail(subject, message, recipients, attachments=None, html_message=None, email=None, calendar=None):
    """Sends a branded email (see api/core/emails.py).

    - email: a structured api.core.emails.Email - its HTML and plain-text
      versions are both generated from it (subject/message are ignored).
    - html_message: a ready-made HTML version; `message` is the plain text.
    - neither: the plain-text `message` is wrapped in the branded layout.
    - calendar: (ics_text, method) to send a calendar invite.
    """
    from api.core.emails import build_message, email_from_plain_text, render_html

    if not settings.EMAIL_HOST:
        logger.warning(
            "Email skipped because EMAIL_HOST is not configured. subject=%s recipients=%s",
            subject if email is None else email.subject,
            recipients,
        )
        return 0

    try:
        if email is not None:
            return build_message(email, recipients, attachments=attachments, calendar=calendar).send(fail_silently=False)
        from_email = settings.DEFAULT_FROM_EMAIL or "no-reply@localhost"
        msg = EmailMultiAlternatives(subject, message, from_email, recipients)
        msg.attach_alternative(html_message or render_html(email_from_plain_text(subject, message)), "text/html")
        for attachment in attachments or []:
            msg.attach(*attachment)
        return msg.send(fail_silently=False)
    except Exception:
        if settings.DEBUG:
            logger.exception(
                "Email sending failed in DEBUG mode. subject=%s recipients=%s",
                subject,
                recipients,
            )
            return 0
        raise


def send_verification_email(user, request=None):
    from api.core.emails import Email

    code = user.email_verification_code
    intro = ["Thanks for signing up. Enter this code in MeritLense to verify your email address."]
    notes = ["The code expires in 24 hours. If you didn't create a MeritLense account, you can ignore this email."]
    if user.role == Roles.B2C:
        subject, title = "Verify your MeritLense account", "Verify your email address"
    elif user.role == Roles.B2B:
        company_name = getattr(getattr(user, "company_profile", None), "company_name", "") or "your company"
        subject, title = "Verify your MeritLense company account", f"Verify your email for {company_name}"
        intro.append("After verifying, upload your trade license from your profile. Company access opens "
                     "once an administrator approves it.")
    else:
        subject, title = "Verify your MeritLense account", "Verify your email address"
    safe_send_mail(None, None, [user.email], email=Email(
        subject=subject, eyebrow="Email verification", title=title, greeting=f"Hello {user.first_name},",
        intro=intro, code=code, code_label="Your verification code", notes=notes,
        preheader=f"Your MeritLense verification code is {code}",
    ))


def generate_password_reset_token(user):
    token = secrets.token_urlsafe(32)
    user.password_reset_token = token
    user.password_reset_token_created_at = timezone.now()
    user.save(update_fields=['password_reset_token', 'password_reset_token_created_at'])
    return token


def send_password_reset_email(user, request):
    from api.core.emails import Email

    token = generate_password_reset_token(user)
    locale = 'en'
    if hasattr(user, 'preferred_language'):
        locale = user.preferred_language.lower()
    reset_url = f"{settings.FRONTEND_URL}/{locale}/auth/reset-password?token={token}"
    account = ""
    if user.role == Roles.B2B:
        company_name = getattr(getattr(user, "company_profile", None), "company_name", "")
        account = f" for {company_name}" if company_name else ""
    safe_send_mail(None, None, [user.email], email=Email(
        subject="Reset your MeritLense password", eyebrow="Password reset", title="Reset your password",
        greeting=f"Hello {user.first_name},",
        intro=[f"We received a request to reset the password for your MeritLense account{account}."],
        button=("Reset password", reset_url),
        notes=["This link expires in 24 hours.",
               "If you didn't ask to reset your password, you can ignore this email. Your password won't change."],
    ))


def send_password_reset_confirmation_email(user):
    """Sent after a forgot-password reset actually completes (not the
    initial reset-link email above) - a standard security notification so
    the account owner finds out immediately if a reset they didn't request
    just succeeded."""
    from api.core.emails import Email, format_local

    safe_send_mail(None, None, [user.email], email=Email(
        subject="Your MeritLense Password Has Been Changed", eyebrow="Security notice",
        title="Your password was changed", greeting=f"Hello {user.first_name},",
        intro=["The password for your MeritLense account was just changed."],
        details=[("Account", user.email), ("Changed", format_local(timezone.now()))],
        after_button=["If you made this change, no further action is needed."],
        notes=["If you did not make this change, contact us immediately at info@meritlense.com."],
        tone="warning",
    ))


def send_admin_credentials_email(user, permissions, request=None):
    from api.core.emails import Email

    locale = request.GET.get('locale', 'en') if request else 'en'
    login_url = f"{settings.FRONTEND_URL}/{locale}/auth/login"
    permission_descriptions = {
        'can_manage_users': 'Manage Staff',
        'can_verify_companies': 'Company Verification',
        'can_verify_documents': 'Document Verification',
        'can_access_financial': 'Financial Access',
        'can_access_reports': 'Reports Access',
    }
    access = [permission_descriptions.get(p, p.replace('_', ' ').title()) for p in permissions]
    safe_send_mail(None, None, [user.email], email=Email(
        subject="Your MeritLense Admin Account", eyebrow="Admin access", title="Your admin account is ready",
        greeting=f"Hello {user.first_name},",
        intro=["An administrator account has been created for you on MeritLense."],
        details=[("Login email", user.email)],
        checklist_title="Your access", checklist=access or ["No additional permissions assigned"],
        button=("Sign in to MeritLense", login_url),
        notes=['No password yet? Use "Forgot password" on the sign-in page to set one.'],
    ))


def send_welcome_email(user, request=None):
    """Sent once, right after a self-registered user verifies their email
    (EmailVerificationView) - distinct from send_employer_welcome_email,
    which is only for accounts an admin created on someone's behalf."""
    from api.core.emails import Email

    locale = request.GET.get('locale', 'en') if request else 'en'
    login_url = f"{settings.FRONTEND_URL}/{locale}/auth/login"
    account_type = {Roles.B2C: "individual employer", Roles.B2B: "company"}.get(user.role, "employer")
    safe_send_mail(None, None, [user.email], email=Email(
        subject="Welcome to MeritLense", eyebrow="Welcome", title=f"Welcome to MeritLense, {user.first_name}",
        greeting=f"Hello {user.first_name},",
        intro=[f"Your {account_type} account is verified and ready to use."],
        checklist_title="Get started in three steps",
        checklist=["Add your first candidate.", "Schedule an AI interview for the role you're hiring for.",
                   "Review the readiness report and certificate."],
        button=("Sign in to MeritLense", login_url), tone="success",
    ))


def send_account_approved_email(user):
    from api.core.emails import Email

    safe_send_mail(None, None, [user.email], email=Email(
        subject="Your MeritLense Account Has Been Approved", eyebrow="Account approved",
        title="Your account is approved", greeting=f"Hello {user.first_name},",
        intro=["Good news: your documents have been reviewed and your MeritLense account is approved. "
               "You now have full access to the platform."],
        button=("Sign in to MeritLense", f"{settings.FRONTEND_URL}/en/auth/login") if settings.FRONTEND_URL else None,
        tone="success",
    ))


def send_account_rejected_email(user, reason):
    from api.core.emails import Email

    safe_send_mail(None, None, [user.email], email=Email(
        subject="Update on Your MeritLense Account", eyebrow="Account review",
        title="We couldn't verify your documents", greeting=f"Hello {user.first_name},",
        intro=["We reviewed the documents you submitted but couldn't verify them."],
        details=[("Reason", reason or "Not specified")],
        after_button=["Please review and resubmit your documents from your profile."],
        notes=["Questions? Reply to this email or contact info@meritlense.com."], tone="danger",
    ))


def send_license_received_email(user):
    from api.core.emails import Email

    company_name = getattr(getattr(user, "company_profile", None), "company_name", "your company")
    safe_send_mail(None, None, [user.email], email=Email(
        subject="Trade license received", eyebrow="Verification in progress",
        title="We've received your trade license", greeting=f"Hello {user.first_name},",
        intro=[f"Thanks, we received the trade license for {company_name}. An administrator is reviewing it, "
               "and company access stays restricted until it's approved. We'll email you as soon as there's a decision."],
    ))


def send_document_request_email(user, document_name):
    from api.core.emails import Email

    company_name = getattr(getattr(user, "company_profile", None), "company_name", "your company")
    safe_send_mail(None, None, [user.email], email=Email(
        subject=f"Additional document requested: {document_name}", eyebrow="Action needed",
        title="Please upload an additional document", greeting=f"Hello {user.first_name},",
        intro=[f"To continue reviewing {company_name}, we need one more document."],
        details=[("Document", document_name)],
        after_button=["Sign in and upload it from the requested documents section of your profile."],
        tone="warning",
    ))


def _format_currency_amount(unit_amount, currency):
    if unit_amount is None:
        return "N/A"
    symbol = {"eur": "€", "usd": "$", "gbp": "£"}.get((currency or "eur").lower(), (currency or "").upper() + " ")
    return f"{symbol}{unit_amount:,.2f}"


def send_package_request_approved_email(user, package_request):
    """Sent at approval time - the package isn't active yet, this is the
    payment request. Activation (DealRecord creation) happens separately,
    once Stripe confirms the payment made through this link - see
    send_package_request_payment_confirmed_email. The attached invoice
    (if its PDF generated successfully) carries the same payment link as
    its "Pay Online" option, plus bank transfer details."""
    from api.core.emails import Email
    from api.payments.short_links import short_url

    deal = package_request.get_deal_type_display()
    amount = _format_currency_amount(package_request.unit_amount, package_request.currency)
    billing = "One-time payment" if package_request.billing_type == "ONE_TIME" else "Billed monthly"
    pay_link = short_url(package_request.stripe_payment_link_url, purpose="package_request_payment")
    attachments = _invoice_email_attachments(package_request.invoice) if package_request.invoice_id else []
    safe_send_mail(None, None, [user.email], attachments=attachments, email=Email(
        subject=f"Your {deal} Request Has Been Approved - Payment Required",
        eyebrow="Request approved", title=f"Your {deal} package is approved",
        greeting=f"Hello {user.first_name},",
        intro=[f"Good news: your {deal} request for {package_request.company.name} has been approved. "
               "Complete payment to activate it."],
        details=[
            ("Package", deal),
            ("Assessment slots", package_request.approved_slot_grant),
            ("Points", package_request.approved_points_grant),
            ("Invoice", package_request.invoice.number if package_request.invoice_id else ""),
            ("Amount due", amount),
            ("Billing", billing),
        ],
        button=("💳  Pay now", pay_link) if pay_link else None,
        after_button=["Your package activates automatically as soon as payment is confirmed."],
        notes=["Prefer bank transfer? The attached invoice has our bank details."
               if attachments else "Prefer bank transfer? Contact info@meritlense.com for our bank details."],
        preheader=f"Amount due: {amount}. Pay online to activate your package.",
    ))


def send_package_request_payment_confirmed_email(user, package_request, deal_record):
    """Sent once activate_after_payment has created the real DealRecord -
    this is when the package actually becomes usable. The attached invoice
    is the same one sent at approval, now regenerated as PAID/€0.00 due."""
    from api.core.emails import Email

    deal = package_request.get_deal_type_display()
    attachments = _invoice_email_attachments(package_request.invoice) if package_request.invoice_id else []
    safe_send_mail(None, None, [user.email], attachments=attachments, email=Email(
        subject=f"Your {deal} Package Is Now Active", eyebrow="Payment received",
        title=f"Your {deal} package is active", greeting=f"Hello {user.first_name},",
        intro=[f"Thank you, we've received your payment. The {deal} package for "
               f"{package_request.company.name} is now active."],
        details=[("Assessment slots", deal_record.slot_grant), ("Points", deal_record.points_grant),
                 ("Invoice", package_request.invoice.number if package_request.invoice_id else "")],
        button=("Go to your dashboard", f"{settings.FRONTEND_URL}/en/auth/login") if settings.FRONTEND_URL else None,
        notes=["Your paid invoice is attached." if attachments else ""], tone="success",
    ))


def _invoice_email_attachments(invoice):
    from api.payments.services import invoice_pdf_email_attachments
    return invoice_pdf_email_attachments(invoice)


def send_package_request_denied_email(user, package_request, reason):
    from api.core.emails import Email

    deal = package_request.get_deal_type_display()
    safe_send_mail(None, None, [user.email], email=Email(
        subject=f"Update on Your {deal} Request", eyebrow="Package request",
        title=f"Your {deal} request wasn't approved", greeting=f"Hello {user.first_name},",
        intro=[f"We reviewed your {deal} request for {package_request.company.name} and couldn't approve it."],
        details=[("Reason", reason or "Not specified")],
        notes=["Want to discuss it? Reply to this email or contact info@meritlense.com."],
    ))


def send_admin_contact_email(user, message, sender):
    """A lighter-weight alternative to rejecting: an admin flags an issue
    with submitted documents without changing the account's verification
    status at all, so the applicant isn't blocked from resubmitting or
    left in a REJECTED state over something that just needs clarifying."""
    from api.core.emails import Email

    safe_send_mail(None, None, [user.email], email=Email(
        subject="A question about your MeritLense account documents", eyebrow="Message from our team",
        title="We have a question about your documents", greeting=f"Hello {user.first_name},",
        intro=[f"{sender.get_full_name()} from the MeritLense team sent you this message:", message],
        after_button=["Please reply to this email so we can continue reviewing your account."],
    ))


def notify_superadmins(subject, message):
    """Best-effort alert to every active SuperAdmin - e.g. a new
    registration or a freshly-signed B2B contract awaiting review. Failure
    to notify admins should never break the action that triggered it, so
    this intentionally swallows send errors rather than propagating them
    (safe_send_mail already re-raises outside DEBUG)."""
    from .models import User

    recipients = list(
        User.objects.filter(role=Roles.SUPERADMIN, is_active=True).values_list('email', flat=True)
    )
    if not recipients:
        return
    try:
        safe_send_mail(subject, message, recipients)
    except Exception:
        logger.exception("Failed to notify superadmins. subject=%s", subject)


def send_employer_welcome_email(user, request=None):
    from api.core.emails import Email

    locale = request.GET.get('locale', 'en') if request else 'en'
    login_url = f"{settings.FRONTEND_URL}/{locale}/auth/login"
    account_type = {Roles.B2C: "individual employer", Roles.B2B: "company"}.get(user.role, "employer")
    safe_send_mail(None, None, [user.email], email=Email(
        subject="Your MeritLense Account Has Been Created", eyebrow="Welcome",
        title="Your MeritLense account is ready", greeting=f"Hello {user.first_name},",
        intro=[f"An administrator has created a {account_type} account for you on MeritLense."],
        details=[("Login email", user.email)],
        button=("Sign in to MeritLense", login_url),
        notes=['No password yet? Use "Forgot password" on the sign-in page to set one.'],
    ))


def send_team_invitation_email(invitation, request):
    from api.core.emails import Email

    locale = request.GET.get('locale', 'en') if request else 'ar'
    accept_url = f"{settings.FRONTEND_URL}/{locale}/auth/accept-invitation?token={invitation.token}"
    company_name = invitation.company.name
    inviter_name = invitation.invited_by.get_full_name()
    permission_descriptions = dict(CompanyTeamPermissions.CHOICES)
    access = [permission_descriptions.get(p, p.replace('_', ' ').title()) for p in invitation.permissions]
    safe_send_mail(None, None, [invitation.email], email=Email(
        subject=f"Invitation to join {company_name} on MeritLense", eyebrow="Team invitation",
        title=f"Join {company_name} on MeritLense", greeting=f"Hello {invitation.first_name},",
        intro=[f"{inviter_name} has invited you to join the {company_name} team on MeritLense."],
        details=[("Company", company_name), ("Your role", invitation.job_title), ("Invited by", inviter_name),
                 ("Invitation expires", invitation.expires_at.strftime("%d %B %Y"))],
        checklist_title="Your access",
        checklist=access or ["No permissions assigned yet. Your company admin can add them."],
        button=("Accept invitation", accept_url),
        notes=["If you weren't expecting this invitation, you can ignore this email."],
    ))


def invalidate_user_sessions(user):
    """Call whenever a password is changed or reset.

    Stamps password_changed_at (checked by PasswordChangeAwareJWTAuthentication
    to reject any already-issued access token) and blacklists every
    outstanding refresh token for the user (stops a stale refresh token from
    minting a fresh access token afterwards).
    """
    from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken

    user.password_changed_at = timezone.now()
    user.save(update_fields=["password_changed_at"])

    for outstanding in OutstandingToken.objects.filter(user=user):
        BlacklistedToken.objects.get_or_create(token=outstanding)
        
