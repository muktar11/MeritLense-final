from django.utils import timezone
import secrets
import logging
from django.core.mail import EmailMultiAlternatives, send_mail
from django.conf import settings
from api.core.constants import CompanyTeamPermissions, Roles
from api.core.email_i18n import (
    ARABIC,
    DEAL_TYPES_AR,
    SIGNATURE,
    TEAM_PERMISSIONS_AR,
    company_language,
    email_language,
    format_date,
    site_url_locale,
    user_language,
)

logger = logging.getLogger(__name__)


def safe_send_mail(subject, message, recipients, attachments=None, html_message=None):
    """html_message, when given, is sent as the HTML alternative; `message`
    stays the plain-text version for clients that don't render HTML."""
    from_email = settings.DEFAULT_FROM_EMAIL or "no-reply@localhost"

    if not settings.EMAIL_HOST:
        logger.warning(
            "Email skipped because EMAIL_HOST is not configured. subject=%s recipients=%s",
            subject,
            recipients,
        )
        return 0

    try:
        if attachments or html_message:
            email = EmailMultiAlternatives(subject, message, from_email, recipients)
            if html_message:
                email.attach_alternative(html_message, "text/html")
            for attachment in attachments or []:
                email.attach(*attachment)
            return email.send(fail_silently=False)

        return send_mail(
            subject,
            message,
            from_email,
            recipients,
            fail_silently=False,
        )
    except Exception:
        if settings.DEBUG:
            logger.exception(
                "Email sending failed in DEBUG mode. subject=%s recipients=%s",
                subject,
                recipients,
            )
            return 0
        raise


def _verification_html(user, code, lang, intro, extra, expires):
    """The verification email in the shared layout: the code as plain,
    selectable text in its own panel (no copy button)."""
    from api.core.email_template import code_security_notice, render_email

    if lang == ARABIC:
        title, greeting, use = ("تحقق من بريدك الإلكتروني", f"مرحبًا {user.first_name}،",
                                "استخدم الرمز أدناه لتأكيد حسابك في MeritLense.")
        eyebrow, expiry = "التحقق من الحساب", "تنتهي صلاحية هذا الرمز خلال 24 ساعة." if expires else None
    else:
        title, greeting, use = ("Verify your email", f"Hello {user.first_name},",
                                "Use the code below to verify your MeritLense account.")
        eyebrow, expiry = "Account verification", "Expires in 24 hours." if expires else None
    blocks = [("p", " ".join(part for part in (intro, use) if part)),
              ("code", {"code": code, "expiry": expiry}),
              code_security_notice(lang)]
    if extra:
        blocks.append(("p", " ".join(extra.split())))
    return render_email(language=lang, title=title, eyebrow=eyebrow, greeting=greeting, blocks=blocks,
                        preheader=use)


def send_verification_email(user, request=None):
    code = user.email_verification_code
    lang = email_language(user, request)

    if lang == ARABIC:
        if user.role == Roles.B2C:
            subject = "تأكيد حسابك كصاحب عمل فردي"
            intro = "شكرًا لتسجيلك كصاحب عمل فردي."
            extra = ""
        elif user.role == Roles.B2B:
            company_name = getattr(getattr(user, "company_profile", None), "company_name", None) or "شركتك"
            subject = "تأكيد حساب شركتك"
            intro = f"شكرًا لتسجيل {company_name} كشركة صاحبة عمل."
            extra = ("\nحسابك جاهز للإعداد، لكن الوصول إلى الشركة يبقى مقيّدًا حتى ترفع الرخصة التجارية "
                     "من ملفك الشخصي ويوافق عليها أحد المسؤولين.\n")
        else:
            subject = "تأكيد حسابك"
            intro = ""
            extra = ""
        message = f"""مرحبًا {user.first_name}،

{intro}

رمز التحقق الخاص بك هو: {code}

أدخل هذا الرمز المكوّن من 5 أرقام في التطبيق لتأكيد بريدك الإلكتروني.

تنتهي صلاحية هذا الرمز خلال 24 ساعة.
{extra}
{SIGNATURE[ARABIC]}
"""
        safe_send_mail(subject, message, [user.email],
                       html_message=_verification_html(user, code, lang, intro, extra, expires=True))
        return

    intro, extra, expires = "", "", False
    if user.role == Roles.B2C:
        intro, expires = "Thank you for registering as an Individual Employer.", True
        subject = "Verify Your Individual Employer Account"
        message = f"""
        Hello {user.first_name},

        Thank you for registering as an Individual Employer.

        Your verification code is: {code}

        Please enter this 5-digit code in the app to verify your email address.

        This code will expire in 24 hours.

        Best regards,
        MeritLense Team
        """
    elif user.role == Roles.B2B:
        subject = "Verify Your Company Account"
        company_name = "Your Company"
        if hasattr(user, 'company_profile'):
            company_name = user.company_profile.company_name
        intro, expires = f"Thank you for registering {company_name} as a Company Employer.", True
        extra = ("Your account is ready for setup, but company access remains restricted until you upload "
                 "your trade license from your profile and an administrator approves it.")

        message = f"""
        Hello {user.first_name},

        Thank you for registering {company_name} as a Company Employer.

        Your verification code is: {code}

        Please enter this 5-digit code in the app to verify your email address.

        This code will expire in 24 hours.

        Your account is ready for setup, but company access remains restricted
        until you upload your trade license from your profile and an administrator
        approves it.

        Best regards,
        MeritLense Team
        """
    else:
        subject = "Verify Your Account"
        message = f"""
        Hello {user.first_name},

        Your verification code is: {code}

        Please enter this 5-digit code to verify your email address.

        Best regards,
        MeritLense Team
        """

    safe_send_mail(subject, message, [user.email],
                   html_message=_verification_html(user, code, lang, intro, extra, expires))

def generate_password_reset_token(user):
    token = secrets.token_urlsafe(32)
    user.password_reset_token = token
    user.password_reset_token_created_at = timezone.now()
    user.save(update_fields=['password_reset_token', 'password_reset_token_created_at'])
    return token


def send_password_reset_email(user, request):
    token = generate_password_reset_token(user)
    lang = email_language(user, request)
    # The link opens the site in the same language as the email (this used
    # to read a user.preferred_language attribute that doesn't exist, so it
    # was always English).
    reset_url = f"{settings.FRONTEND_URL}/{site_url_locale(lang)}/auth/reset-password?token={token}"

    if lang == ARABIC:
        if user.role == Roles.B2B:
            company_name = getattr(getattr(user, "company_profile", None), "company_name", None) or "شركتك"
            subject = "إعادة تعيين كلمة مرور حساب الشركة"
            about = f"تلقينا طلبًا لإعادة تعيين كلمة المرور لحساب {company_name}."
        elif user.role == Roles.B2C:
            subject = "إعادة تعيين كلمة مرور حسابك كصاحب عمل فردي"
            about = "تلقينا طلبًا لإعادة تعيين كلمة المرور لحسابك كصاحب عمل فردي."
        else:
            subject = "إعادة تعيين كلمة المرور"
            about = "تلقينا طلبًا لإعادة تعيين كلمة المرور الخاصة بك."
        message = f"""مرحبًا {user.first_name}،

{about}

اضغط على الرابط التالي لإعادة تعيين كلمة المرور:
{reset_url}

تنتهي صلاحية هذا الرابط خلال 24 ساعة.

إذا لم تطلب ذلك، يمكنك تجاهل هذه الرسالة أو التواصل مع الدعم.

{SIGNATURE[ARABIC]}
"""
        safe_send_mail(subject, message, [user.email])
        return

    if user.role == Roles.B2C:
        subject = "Reset Your Individual Employer Password"
        message = f"""
        Hello {user.first_name},

        We received a request to reset your password for your Individual Employer account.

        Please click the link below to reset your password:
        {reset_url}

        This link will expire in 24 hours.

        If you didn't request this, please ignore this email or contact support.

        Best regards,
        MeritLense Team
        """
    elif user.role == Roles.B2B:
        company_name = "Your Company"
        if hasattr(user, 'company_profile'):
            company_name = user.company_profile.company_name

        subject = "Reset Your Company Account Password"
        message = f"""
        Hello {user.first_name},

        We received a request to reset your password for {company_name}'s account.

        Please click the link below to reset your password:
        {reset_url}

        This link will expire in 24 hours.

        If you didn't request this, please ignore this email or contact support.

        Best regards,
        MeritLense Team
        """
    else:
        subject = "Reset Your Password"
        message = f"""
        Hello {user.first_name},

        We received a request to reset your password.

        Please click the link below to reset your password:
        {reset_url}

        This link will expire in 24 hours.

        If you didn't request this, please ignore this email or contact support.

        Best regards,
        MeritLense Team
        """

    safe_send_mail(subject, message, [user.email])

def send_password_reset_confirmation_email(user):
    """Sent after a forgot-password reset actually completes (not the
    initial reset-link email above) - a standard security notification so
    the account owner finds out immediately if a reset they didn't request
    just succeeded."""
    if user_language(user) == ARABIC:
        subject = "تم تغيير كلمة مرور حسابك في MeritLense"
        message = f"""مرحبًا {user.first_name}،

نؤكد لك أن كلمة المرور لحسابك في MeritLense ({user.email}) قد تم تغييرها للتو.

إذا كنت أنت من أجرى هذا التغيير، فلا حاجة لأي إجراء آخر.

إذا لم تطلب هذا التغيير، فقد يكون حسابك معرّضًا للخطر، يرجى التواصل مع الدعم فورًا.

{SIGNATURE[ARABIC]}
"""
        safe_send_mail(subject, message, [user.email])
        return
    subject = "Your MeritLense Password Has Been Changed"
    message = f"""
    Hello {user.first_name},

    This is a confirmation that the password for your MeritLense account ({user.email}) was just changed.

    If you made this change, no further action is needed.

    If you did not request this change, your account may be compromised - please contact support immediately.

    Best regards,
    MeritLense Team
    """

    safe_send_mail(subject, message, [user.email])

def send_admin_credentials_email(user, permissions, request=None):
    locale = request.GET.get('locale', 'en') if request else 'en'
    login_url = f"{settings.FRONTEND_URL}/{locale}/auth/login"

    permission_descriptions = {
        'can_manage_users': 'Manage Staff',
        'can_verify_companies': 'Company Verification',
        'can_verify_documents': 'Document Verification',
        'can_access_financial': 'Financial Access',
        'can_access_reports': 'Reports Access',
    }

    permissions_text = ""
    for perm in permissions:
        desc = permission_descriptions.get(perm, perm.replace('_', ' ').title())
        permissions_text += f"  • {desc}\n"

    if not permissions_text:
        permissions_text = "  • No additional permissions assigned\n"

    subject = "Your MeritLense Admin Account"

    message = f"""
Hello {user.first_name},

An administrator account has been created for you on MeritLense.

Login Email: {user.email}

Your Access Permissions:
{permissions_text}

To sign in, go to:
{login_url}

If you don't already have your password, use "Forgot Password" on the login page to set one.

Best regards,
MeritLense Team
"""

    safe_send_mail(subject, message, [user.email])


def send_welcome_email(user, request=None):
    """Sent once, right after a self-registered user verifies their email
    (EmailVerificationView) - distinct from send_employer_welcome_email,
    which is only for accounts an admin created on someone's behalf."""
    lang = email_language(user, request)
    login_url = f"{settings.FRONTEND_URL}/{site_url_locale(lang)}/auth/login"

    if lang == ARABIC:
        account_type = {Roles.B2C: "صاحب عمل فردي", Roles.B2B: "شركة صاحبة عمل"}.get(user.role, "صاحب عمل")
        message = f"""مرحبًا {user.first_name}،

مرحبًا بك في MeritLense! تم التحقق من حسابك ({account_type}) وأصبح جاهزًا للاستخدام.

لتسجيل الدخول، انتقل إلى:
{login_url}

{SIGNATURE[ARABIC]}
"""
        safe_send_mail("مرحبًا بك في MeritLense", message, [user.email])
        return

    if user.role == Roles.B2C:
        account_type = "Individual Employer"
    elif user.role == Roles.B2B:
        account_type = "Company Employer"
    else:
        account_type = "Employer"

    subject = "Welcome to MeritLense"
    message = f"""
Hello {user.first_name},

Welcome to MeritLense! Your {account_type} account is verified and ready to use.

To sign in, go to:
{login_url}

Best regards,
MeritLense Team
"""
    safe_send_mail(subject, message, [user.email])


def send_account_approved_email(user):
    if user_language(user) == ARABIC:
        safe_send_mail("تمت الموافقة على حسابك في MeritLense", f"""مرحبًا {user.first_name}،

أخبار سارة: تمت مراجعة مستنداتك والموافقة على حسابك في MeritLense، وأصبح لديك الآن وصول كامل إلى المنصة.

{SIGNATURE[ARABIC]}
""", [user.email])
        return
    subject = "Your MeritLense Account Has Been Approved"
    message = f"""
Hello {user.first_name},

Good news - your documents have been reviewed and your MeritLense account
has been approved. You now have full access to the platform.

Best regards,
MeritLense Team
"""
    safe_send_mail(subject, message, [user.email])


def send_account_rejected_email(user, reason):
    if user_language(user) == ARABIC:
        safe_send_mail("تحديث بشأن حسابك في MeritLense", f"""مرحبًا {user.first_name}،

تعذّر التحقق من المستندات التي أرسلتها.

السبب: {reason or 'غير محدد'}

يرجى مراجعة مستنداتك وإعادة إرسالها، أو التواصل مع الدعم إذا كانت لديك أي أسئلة.

{SIGNATURE[ARABIC]}
""", [user.email])
        return
    subject = "Update on Your MeritLense Account"
    message = f"""
Hello {user.first_name},

Your submitted documents could not be verified.

Reason: {reason or 'Not specified'}

Please review and resubmit your documents, or contact support if you have
any questions.

Best regards,
MeritLense Team
"""
    safe_send_mail(subject, message, [user.email])


def send_license_received_email(user):
    if user_language(user) == ARABIC:
        company_name = getattr(getattr(user, "company_profile", None), "company_name", None) or "شركتك"
        safe_send_mail("تم استلام الرخصة التجارية", f"""مرحبًا {user.first_name}،

استلمنا الرخصة التجارية الخاصة بـ {company_name}. سيبقى الوصول إلى الشركة مقيّدًا أثناء مراجعتها من قبل أحد المسؤولين، وسنراسلك عند اتخاذ القرار.

{SIGNATURE[ARABIC]}
""", [user.email])
        return
    company_name = getattr(getattr(user, "company_profile", None), "company_name", "your company")
    safe_send_mail(
        "Trade license received",
        f"""Hello {user.first_name},

We received the trade license for {company_name}. Company access will remain
restricted while an administrator reviews it. We will email you when a decision
has been made.

Best regards,
MeritLense Team
""",
        [user.email],
    )


def send_document_request_email(user, document_name):
    if user_language(user) == ARABIC:
        company_name = getattr(getattr(user, "company_profile", None), "company_name", None) or "شركتك"
        safe_send_mail(f"مطلوب مستند إضافي: {document_name}", f"""مرحبًا {user.first_name}،

طلب أحد المسؤولين المستند التالي الخاص بـ {company_name}:

{document_name}

يرجى تسجيل الدخول ورفعه من قسم المستندات المطلوبة في ملفك الشخصي.

{SIGNATURE[ARABIC]}
""", [user.email])
        return
    company_name = getattr(getattr(user, "company_profile", None), "company_name", "your company")
    safe_send_mail(
        f"Additional document requested: {document_name}",
        f"""Hello {user.first_name},

An administrator requested the following document for {company_name}:

{document_name}

Please sign in and upload it from your profile's requested documents section.

Best regards,
MeritLense Team
""",
        [user.email],
    )


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
    its "Pay Online" option, plus bank transfer details - the email text
    only needs to point the requester at it, not repeat everything."""
    lang = user_language(user)
    if lang == ARABIC:
        _send_package_request_approved_email_ar(user, package_request)
        return
    subject = f"Your {package_request.get_deal_type_display()} Request Has Been Approved - Payment Required"
    slot_line = f"Assessment Slots: {package_request.approved_slot_grant}\n" if package_request.approved_slot_grant is not None else ""
    points_line = f"Points: {package_request.approved_points_grant}\n" if package_request.approved_points_grant is not None else ""
    amount = _format_currency_amount(package_request.unit_amount, package_request.currency)
    billing_line = "billed once" if package_request.billing_type == "ONE_TIME" else "billed monthly"
    invoice_line = f"Invoice: {package_request.invoice.number}\n" if package_request.invoice_id else ""
    from api.payments.short_links import email_button_html, short_url

    pay_link = short_url(package_request.stripe_payment_link_url, purpose="package_request_payment")
    message = f"""
Hello {user.first_name},

Good news - your {package_request.get_deal_type_display()} request for {package_request.company.name}
has been approved.

{slot_line}{points_line}{invoice_line}
Amount due: {amount} ({billing_line})

To activate your package, complete payment here:
{pay_link}

The attached invoice also includes this payment link and bank transfer
details as an alternative.

Your package will be activated automatically as soon as payment is confirmed.
If you have any questions, please contact support.

Best regards,
MeritLense Team
"""
    html_message = email_button_html(
        greeting=f"Hello {user.first_name},",
        paragraphs=[
            f"Good news - your {package_request.get_deal_type_display()} request for "
            f"{package_request.company.name} has been approved.",
            " · ".join(line.strip() for line in (slot_line, points_line, invoice_line) if line.strip()),
            f"Amount due: {amount} ({billing_line})",
            "To activate your package, complete payment:",
        ],
        button_label="Pay now",
        button_url=pay_link,
        footer_lines=[
            "The attached invoice also includes bank transfer details as an alternative.",
            "Your package will be activated automatically as soon as payment is confirmed.",
            "",
            "Best regards,",
            "MeritLense Team",
        ],
    ) if pay_link else None
    attachments = _invoice_email_attachments(package_request.invoice) if package_request.invoice_id else []
    safe_send_mail(subject, message, [user.email], attachments=attachments, html_message=html_message)


def send_package_request_payment_confirmed_email(user, package_request, deal_record):
    """Sent once activate_after_payment has created the real DealRecord -
    this is when the package actually becomes usable. The attached invoice
    is the same one sent at approval, now regenerated as PAID/€0.00 due."""
    if user_language(user) == ARABIC:
        deal = _deal_type_ar(package_request)
        lines = []
        if deal_record.slot_grant is not None:
            lines.append(f"خانات التقييم: {deal_record.slot_grant}")
        if deal_record.points_grant is not None:
            lines.append(f"النقاط: {deal_record.points_grant}")
        detail_lines = "\n".join(lines)
        attachments = _invoice_email_attachments(package_request.invoice) if package_request.invoice_id else []
        safe_send_mail(f"أصبحت {deal} مفعّلة الآن", f"""مرحبًا {user.first_name}،

تم استلام الدفع، وأصبحت {deal} الخاصة بـ {package_request.company.name} مفعّلة الآن.

{detail_lines}

سيتواصل معك فريقنا بشأن الخطوات التالية. إذا كانت لديك أي أسئلة، يرجى التواصل مع الدعم.

{SIGNATURE[ARABIC]}
""", [user.email], attachments=attachments)
        return
    subject = f"Your {package_request.get_deal_type_display()} Package Is Now Active"
    slot_line = f"Assessment Slots: {deal_record.slot_grant}\n" if deal_record.slot_grant is not None else ""
    points_line = f"Points: {deal_record.points_grant}\n" if deal_record.points_grant is not None else ""
    message = f"""
Hello {user.first_name},

Payment received - your {package_request.get_deal_type_display()} package for
{package_request.company.name} is now active.

{slot_line}{points_line}
Our team will be in touch with next steps. If you have any questions, please
contact support.

Best regards,
MeritLense Team
"""
    attachments = _invoice_email_attachments(package_request.invoice) if package_request.invoice_id else []
    safe_send_mail(subject, message, [user.email], attachments=attachments)


def _invoice_email_attachments(invoice):
    from api.payments.services import invoice_pdf_email_attachments
    return invoice_pdf_email_attachments(invoice)


def _deal_type_ar(package_request):
    return DEAL_TYPES_AR.get(package_request.deal_type, package_request.get_deal_type_display())


def _send_package_request_approved_email_ar(user, package_request):
    from api.payments.short_links import email_button_html, short_url

    deal = _deal_type_ar(package_request)
    details = []
    if package_request.approved_slot_grant is not None:
        details.append(f"خانات التقييم: {package_request.approved_slot_grant}")
    if package_request.approved_points_grant is not None:
        details.append(f"النقاط: {package_request.approved_points_grant}")
    if package_request.invoice_id:
        details.append(f"الفاتورة: {package_request.invoice.number}")
    amount = _format_currency_amount(package_request.unit_amount, package_request.currency)
    billing = "دفعة واحدة" if package_request.billing_type == "ONE_TIME" else "شهريًا"
    pay_link = short_url(package_request.stripe_payment_link_url, purpose="package_request_payment")
    detail_lines = "\n".join(details)
    message = f"""مرحبًا {user.first_name}،

أخبار سارة: تمت الموافقة على طلب {deal} الخاص بـ {package_request.company.name}.

{detail_lines}
المبلغ المستحق: {amount} ({billing})

لتفعيل باقتك، أكمل الدفع من هنا:
{pay_link}

تتضمن الفاتورة المرفقة أيضًا رابط الدفع هذا وتفاصيل التحويل البنكي كبديل.

سيتم تفعيل باقتك تلقائيًا فور تأكيد الدفع. إذا كانت لديك أي أسئلة، يرجى التواصل مع الدعم.

{SIGNATURE[ARABIC]}
"""
    html_message = email_button_html(
        greeting=f"مرحبًا {user.first_name}،",
        paragraphs=[
            f"أخبار سارة: تمت الموافقة على طلب {deal} الخاص بـ {package_request.company.name}.",
            " · ".join(details),
            f"المبلغ المستحق: {amount} ({billing})",
            "لتفعيل باقتك، أكمل الدفع:",
        ],
        button_label="ادفع الآن",
        button_url=pay_link,
        footer_lines=[
            "تتضمن الفاتورة المرفقة أيضًا تفاصيل التحويل البنكي كبديل.",
            "سيتم تفعيل باقتك تلقائيًا فور تأكيد الدفع.",
            "",
            *SIGNATURE[ARABIC].split("\n"),
        ],
        language=ARABIC,
    ) if pay_link else None
    attachments = _invoice_email_attachments(package_request.invoice) if package_request.invoice_id else []
    safe_send_mail(f"تمت الموافقة على طلب {deal}، والدفع مطلوب", message, [user.email],
                   attachments=attachments, html_message=html_message)


def send_package_request_denied_email(user, package_request, reason):
    if user_language(user) == ARABIC:
        deal = _deal_type_ar(package_request)
        safe_send_mail(f"تحديث بشأن طلب {deal}", f"""مرحبًا {user.first_name}،

لم تتم الموافقة على طلب {deal} الخاص بـ {package_request.company.name}.

السبب: {reason or 'غير محدد'}

يرجى التواصل مع الدعم إذا كانت لديك أي أسئلة أو رغبت في مناقشة الأمر.

{SIGNATURE[ARABIC]}
""", [user.email])
        return
    subject = f"Update on Your {package_request.get_deal_type_display()} Request"
    message = f"""
Hello {user.first_name},

Your {package_request.get_deal_type_display()} request for {package_request.company.name} was not approved.

Reason: {reason or 'Not specified'}

Please contact support if you have any questions or would like to discuss
this further.

Best regards,
MeritLense Team
"""
    safe_send_mail(subject, message, [user.email])


def send_admin_contact_email(user, message, sender):
    """A lighter-weight alternative to rejecting: an admin flags an issue
    with submitted documents without changing the account's verification
    status at all, so the applicant isn't blocked from resubmitting or
    left in a REJECTED state over something that just needs clarifying."""
    if user_language(user) == ARABIC:
        safe_send_mail("استفسار بشأن مستندات حسابك في MeritLense", f"""مرحبًا {user.first_name}،

لدى {sender.get_full_name()} من فريق MeritLense استفسار بشأن المستندات التي أرسلتها:

{message}

يرجى الرد على هذه الرسالة أو التواصل مع الدعم حتى نتمكن من متابعة مراجعة حسابك.

{SIGNATURE[ARABIC]}
""", [user.email])
        return
    subject = "A question about your MeritLense account documents"
    body = f"""
Hello {user.first_name},

{sender.get_full_name()} from the MeritLense team has a question about your
submitted documents:

{message}

Please reply to this email or contact support so we can continue reviewing
your account.

Best regards,
MeritLense Team
"""
    safe_send_mail(subject, body, [user.email])


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
    lang = email_language(user, request)
    login_url = f"{settings.FRONTEND_URL}/{site_url_locale(lang)}/auth/login"

    if lang == ARABIC:
        account_type = {Roles.B2C: "صاحب عمل فردي", Roles.B2B: "شركة صاحبة عمل"}.get(user.role, "صاحب عمل")
        safe_send_mail("تم إنشاء حسابك في MeritLense", f"""مرحبًا {user.first_name}،

أنشأ أحد المسؤولين حسابًا لك في MeritLense بصفة {account_type}.

البريد الإلكتروني لتسجيل الدخول: {user.email}

لتسجيل الدخول، انتقل إلى:
{login_url}

إذا لم تكن لديك كلمة مرور بعد، استخدم "نسيت كلمة المرور" في صفحة تسجيل الدخول لتعيينها.

{SIGNATURE[ARABIC]}
""", [user.email])
        return

    if user.role == Roles.B2C:
        account_type = "Individual Employer"
    elif user.role == Roles.B2B:
        account_type = "Company Employer"
    else:
        account_type = "Employer"

    subject = "Your MeritLense Account Has Been Created"

    message = f"""
Hello {user.first_name},

An administrator has created a {account_type} account for you on MeritLense.

Login Email: {user.email}

To sign in, go to:
{login_url}

If you don't already have your password, use "Forgot Password" on the login page to set one.

Best regards,
MeritLense Team
"""

    safe_send_mail(subject, message, [user.email])


def send_team_invitation_email(invitation, request):
    
    lang = email_language(getattr(invitation, "invited_by", None), request)
    if request is None or not request.GET.get("locale"):
        lang = company_language(invitation.company)

    accept_url = f"{settings.FRONTEND_URL}/{site_url_locale(lang)}/auth/accept-invitation?token={invitation.token}"
    
    company_name = invitation.company.name
    
    inviter_name = invitation.invited_by.get_full_name()
    
    expiration_date = invitation.expires_at.strftime("%B %d, %Y")
    
    permission_descriptions = dict(CompanyTeamPermissions.CHOICES)

    permissions_text = ""
    for perm in invitation.permissions:
        desc = permission_descriptions.get(perm, perm.replace('_', ' ').title())
        permissions_text += f"  • {desc}\n"

    if not permissions_text:
        permissions_text = "  • No permissions assigned yet - contact your company admin"
    
    if lang == ARABIC:
        perms = "".join(f"  • {TEAM_PERMISSIONS_AR.get(p, p)}\n" for p in invitation.permissions) \
            or "  • لم تُحدَّد صلاحيات بعد، تواصل مع مسؤول شركتك\n"
        expires = format_date(invitation.expires_at, ARABIC, with_weekday=False)
        safe_send_mail(f"دعوة للانضمام إلى {company_name} على MeritLense", f"""مرحبًا {invitation.first_name}،

دعاك {inviter_name} للانضمام إلى {company_name} على MeritLense.

تفاصيل الدعوة:
- الشركة: {company_name}
- الدور: {invitation.job_title}

صلاحيات الوصول الخاصة بك:
{perms}
لقبول الدعوة وإعداد حسابك، اضغط على الرابط التالي:
{accept_url}

تنتهي صلاحية رابط الدعوة في {expires}.

إذا لم تكن تتوقع هذه الدعوة، يمكنك تجاهل هذه الرسالة أو التواصل مع الدعم.

{SIGNATURE[ARABIC]}
""", [invitation.email])
        return

    subject = f"Invitation to join {company_name} on MeritLense"
    
    message = f"""
Hello {invitation.first_name},

You have been invited to join {company_name} on MeritLense by {inviter_name}.

Invitation Details:
- Company: {company_name}
- Role: {invitation.job_title}

Your Access Permissions:
{permissions_text}

To accept this invitation and set up your account, please click the link below:
{accept_url}

This invitation link will expire on {expiration_date}.

If you did not expect this invitation, please ignore this email or contact support.

Best regards,
MeritLense Team
"""
    
    safe_send_mail(subject, message, [invitation.email])


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
        
