from .services import AuditLogService


class AuditLogMixin:
    
    def get_audit_log_action(self, action):
        action_map = {
            'create': 'CREATE',
            'update': 'UPDATE',
            'partial_update': 'UPDATE',
            'destroy': 'DELETE',
        }
        return action_map.get(action, action.upper())
    
    def get_audit_log_category(self, obj=None):
        return 'SYSTEM'
    
    def get_audit_log_description(self, action, obj=None):
        if obj:
            return f"{action} {obj.__class__.__name__}: {obj}"
        return f"{action} performed"
    
    def perform_create(self, serializer):
        response = super().perform_create(serializer)
        if hasattr(self, 'get_serializer'):
            obj = serializer.instance
            AuditLogService.log(
                user=self.request.user,
                action=self.get_audit_log_action('create'),
                category=self.get_audit_log_category(obj),
                description=self.get_audit_log_description('Created', obj),
                resource=obj,
                request=self.request
            )
        return response
    
    def perform_update(self, serializer):
        old_obj = self.get_object()
        response = super().perform_update(serializer)
        obj = serializer.instance
        AuditLogService.log(
            user=self.request.user,
            action=self.get_audit_log_action('update'),
            category=self.get_audit_log_category(obj),
            description=self.get_audit_log_description('Updated', obj),
            resource=obj,
            data={'changes': self.get_changes(old_obj, obj)},
            request=self.request
        )
        return response
    
    def perform_destroy(self, instance):
        obj = instance
        AuditLogService.log(
            user=self.request.user,
            action=self.get_audit_log_action('destroy'),
            category=self.get_audit_log_category(obj),
            description=self.get_audit_log_description('Deleted', obj),
            resource=obj,
            request=self.request
        )
        return super().perform_destroy(instance)
    
    def get_changes(self, old_obj, new_obj):
        changes = {}
        if hasattr(old_obj, '_meta'):
            for field in old_obj._meta.fields:
                field_name = field.name
                old_value = getattr(old_obj, field_name)
                new_value = getattr(new_obj, field_name)
                if old_value != new_value:
                    changes[field_name] = {
                        'old': str(old_value),
                        'new': str(new_value)
                    }
        return changes