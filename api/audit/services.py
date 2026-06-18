from django.contrib.contenttypes.models import ContentType
from .models import AuditLog
from api.core.constants import AuditLogSeverity


class AuditLogService:
    
    @classmethod
    def log(cls, user, action, category, description, resource=None, 
            severity=AuditLogSeverity.INFO, data=None, request=None):
        resource_type = None
        resource_id = None
        resource_name = ''
        
        if resource:
            content_type = ContentType.objects.get_for_model(resource)
            resource_type = content_type
            resource_id = resource.id
            
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
        
        ip_address = None
        user_agent = ''
        request_method = ''
        request_path = ''
        
        if request:
            x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
            if x_forwarded_for:
                ip_address = x_forwarded_for.split(',')[0]
            else:
                ip_address = request.META.get('REMOTE_ADDR')
            
            user_agent = request.META.get('HTTP_USER_AGENT', '')
            request_method = request.method
            request_path = request.path
        
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
            description=description,
            data=data or {},
            ip_address=ip_address,
            user_agent=user_agent,
            request_method=request_method,
            request_path=request_path,
        )
        
        return log_entry
    
    @classmethod
    def log_system(cls, action, category, description, resource=None, 
                   severity=AuditLogSeverity.INFO, data=None):
        
        resource_type = None
        resource_id = None
        resource_name = ''
        
        if resource:
            content_type = ContentType.objects.get_for_model(resource)
            resource_type = content_type
            resource_id = resource.id
            
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
            description=description,
            data=data or {},
        )
        
        return log_entry
