from django.utils import timezone
import secrets
import logging
from django.core.mail import send_mail
from django.conf import settings
from api.core.constants import Roles

logger = logging.getLogger(__name__)


def safe_send_mail(subject, message, recipients):
    from_email = settings.DEFAULT_FROM_EMAIL or "no-reply@localhost"

    if not settings.EMAIL_HOST:
        logger.warning(
            "Email skipped because EMAIL_HOST is not configured. subject=%s recipients=%s",
            subject,
            recipients,
        )
        return 0

    try:
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


def send_verification_email(user, request=None):
    code = user.email_verification_code
    
    if user.role == Roles.B2C:
        subject = "Verify Your Individual Employer Account"
        message = f"""
        Hello {user.first_name},
        
        Thank you for registering as an Individual Employer.
        
        Your verification code is: {code}
        
        Please enter this 5-digit code in the app to verify your email address.
        
        This code will expire in 24 hours.
        
        Best regards,
        Meritlense Team
        """
    elif user.role == Roles.B2B:
        subject = "Verify Your Company Account"
        company_name = "Your Company"
        if hasattr(user, 'company_profile'):
            company_name = user.company_profile.company_name
        
        message = f"""
        Hello {user.first_name},
        
        Thank you for registering {company_name} as a Company Employer.
        
        Your verification code is: {code}
        
        Please enter this 5-digit code in the app to verify your email address.
        
        This code will expire in 24 hours.
        
        Best regards,
        Meritlense Team
        """
    else:
        subject = "Verify Your Account"
        message = f"""
        Hello {user.first_name},
        
        Your verification code is: {code}
        
        Please enter this 5-digit code to verify your email address.
        
        Best regards,
        Meritlense Team
        """
    
    safe_send_mail(subject, message, [user.email])
    
def generate_password_reset_token(user):
    token = secrets.token_urlsafe(32)
    user.password_reset_token = token
    user.password_reset_token_created_at = timezone.now()
    user.save(update_fields=['password_reset_token', 'password_reset_token_created_at'])
    return token


def send_password_reset_email(user, request):
    token = generate_password_reset_token(user)
    locale = 'en'
    if hasattr(user, 'preferred_language'):
        locale = user.preferred_language.lower()
    reset_url = f"{settings.FRONTEND_URL}/{locale}/auth/reset-password?token={token}"
    
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
        Meritlense Team
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
        Meritlense Team
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
        Meritlense Team
        """
    
    safe_send_mail(subject, message, [user.email])

def send_admin_credentials_email(user, permissions, request=None):
    locale = request.GET.get('locale', 'en') if request else 'en'
    login_url = f"{settings.FRONTEND_URL}/{locale}/auth/login"

    permission_descriptions = {
        'can_manage_users': 'Manage Users',
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

    subject = "Your Meritlense Admin Account"

    message = f"""
Hello {user.first_name},

An administrator account has been created for you on Meritlense.

Login Email: {user.email}

Your Access Permissions:
{permissions_text}

To sign in, go to:
{login_url}

If you don't already have your password, use "Forgot Password" on the login page to set one.

Best regards,
Meritlense Team
"""

    safe_send_mail(subject, message, [user.email])


def send_employer_welcome_email(user, request=None):
    locale = request.GET.get('locale', 'en') if request else 'en'
    login_url = f"{settings.FRONTEND_URL}/{locale}/auth/login"

    if user.role == Roles.B2C:
        account_type = "Individual Employer"
    elif user.role == Roles.B2B:
        account_type = "Company Employer"
    else:
        account_type = "Employer"

    subject = "Your Meritlense Account Has Been Created"

    message = f"""
Hello {user.first_name},

An administrator has created a {account_type} account for you on Meritlense.

Login Email: {user.email}

To sign in, go to:
{login_url}

If you don't already have your password, use "Forgot Password" on the login page to set one.

Best regards,
Meritlense Team
"""

    safe_send_mail(subject, message, [user.email])


def send_team_invitation_email(invitation, request):
    
    locale = request.GET.get('locale', 'en') if request else 'ar'
    
    accept_url = f"{settings.FRONTEND_URL}/{locale}/auth/accept-invitation?token={invitation.token}"
    
    company_name = invitation.company.name
    
    inviter_name = invitation.invited_by.get_full_name()
    
    expiration_date = invitation.expires_at.strftime("%B %d, %Y")
    
    permission_descriptions = {
        'view_candidates': 'View Candidates',
        'evaluate_candidates': 'Evaluate Candidates',
        'create_evaluations': 'Create Evaluations',
        'view_reports': 'View Reports',
    }
    
    permissions_text = ""
    for perm in invitation.permissions:
        desc = permission_descriptions.get(perm, perm.replace('_', ' ').title())
        permissions_text += f"  • {desc}\n"
    
    if not permissions_text:
        permissions_text = "  • View Candidates (default)"
    
    subject = f"Invitation to join {company_name} on Meritlense"
    
    message = f"""
Hello {invitation.first_name},

You have been invited to join {company_name} on Meritlense by {inviter_name}.

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
Meritlense Team
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
        
