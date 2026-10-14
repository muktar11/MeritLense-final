from django.contrib.contenttypes.models import ContentType
from .models import AuditLog
from .request_context import request_audit_fields
from api.core.constants import AuditLogAction, AuditLogCategory, AuditLogSeverity


class AuditLogService:
    
    @classmethod
    def log(cls, user, action, category, description, resource=None, 
            severity=AuditLogSeverity.INFO, data=None, request=None):
        resource_type = None
        resource_id = None
        resource_type_name = ''
        resource_name = ''
        
        if resource:
            content_type = ContentType.objects.get_for_model(resource)
            resource_type = content_type
            resource_id = resource.id
            resource_type_name = str(resource._meta.verbose_name).title()
            
            if hasattr(resource, 'name'):
                resource_name = str(resource.name)
            elif hasattr(resource, 'title'):
                resource_name = str(resource.title)
            elif hasattr(resource, 'email'):
                resource_name = str(resource.email)
            elif hasattr(resource, 'get_full_name'):
                resource_name = resource.get_full_name()
            else:
                resource_name = str(resource)
        
        company = None
        if hasattr(user, 'company_profile'):
            company = user.company_profile.company
        elif hasattr(user, 'managed_company'):
            company = user.managed_company
        
        log_entry = AuditLog.objects.create(
            user=user,
            user_email=user.email,
            user_name=user.get_full_name(),
            user_role=user.role,
            company=company,
            action=action,
            category=category,
            severity=severity,
            resource_type=resource_type,
            resource_id=resource_id,
            resource_name=resource_name,
            resource_type_name=resource_type_name,
            description=description,
            data=data or {},
            **request_audit_fields(request),
        )

        return log_entry

    @classmethod
    def log_login(cls, user, request, method):
        return cls.log(
            user=user,
            action=AuditLogAction.LOGIN_SUCCESS,
            category=AuditLogCategory.SECURITY,
            description=f"Signed in ({method}): {user.email}",
            resource=user,
            data={'login_method': method},
            request=request,
        )

    @classmethod
    def log_login_failed(cls, email, request, reason):
        """A failed sign-in is recorded against the account when one exists
        for that email, so the tracer shows attempts on an account even
        when they don't come from its owner."""
        from api.accounts.models import User

        email = (email or '').strip()[:254]
        user = User.objects.filter(email__iexact=email).first() if email else None
        if user is not None:
            return cls.log(
                user=user,
                action=AuditLogAction.LOGIN_FAILED,
                category=AuditLogCategory.SECURITY,
                description=f"Failed sign-in for {user.email}: {reason}",
                severity=AuditLogSeverity.WARNING,
                data={'email': email, 'reason': reason},
                request=request,
            )
        return cls.log_system(
            action=AuditLogAction.LOGIN_FAILED,
            category=AuditLogCategory.SECURITY,
            description=f"Failed sign-in for unknown email {email or '(blank)'}",
            severity=AuditLogSeverity.WARNING,
            data={'email': email, 'reason': reason},
            request=request,
        )
    
    @classmethod
    def log_system(cls, action, category, description, resource=None,
                   severity=AuditLogSeverity.INFO, data=None, request=None):
        
        resource_type = None
        resource_id = None
        resource_type_name = ''
        resource_name = ''
        
        if resource:
            content_type = ContentType.objects.get_for_model(resource)
            resource_type = content_type
            resource_id = resource.id
            resource_type_name = str(resource._meta.verbose_name).title()
            
            if hasattr(resource, 'name'):
                resource_name = str(resource.name)
            elif hasattr(resource, 'title'):
                resource_name = str(resource.title)
            elif hasattr(resource, 'email'):
                resource_name = str(resource.email)
            else:
                resource_name = str(resource)
        
        log_entry = AuditLog.objects.create(
            user=None,
            user_email='system',
            user_name='System',
            user_role='SYSTEM',
            action=action,
            category=category,
            severity=severity,
            resource_type=resource_type,
            resource_id=resource_id,
            resource_name=resource_name,
            resource_type_name=resource_type_name,
            description=description,
            data=data or {},
            # Inside a request (e.g. a candidate on their interview link)
            # this is where it came from; blank for background work.
            **request_audit_fields(request),
        )

        return log_entry
