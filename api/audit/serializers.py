from rest_framework import serializers
from .models import AuditLog
from api.core.constants import AuditLogCategory, AuditLogAction, AuditLogSeverity
from api.core.serializers import PublicIdModelSerializer


class AuditLogSerializer(PublicIdModelSerializer):
    action_display = serializers.CharField(source='get_action_display', read_only=True)
    category_display = serializers.CharField(source='get_category_display', read_only=True)
    severity_display = serializers.CharField(source='get_severity_display', read_only=True)
    
    class Meta:
        model = AuditLog
        fields = [
            'id', 'user', 'user_email', 'user_name', 'user_role',
            'company', 'action', 'action_display',
            'category', 'category_display',
            'severity', 'severity_display',
            'resource_type', 'resource_id', 'resource_name', 'resource_type_name',
            'description', 'data',
            'ip_address', 'user_agent',
            'request_method', 'request_path',
            'created_at'
        ]
        read_only_fields = fields


class AuditLogCreateSerializer(serializers.Serializer):
    action = serializers.ChoiceField(choices=AuditLogAction.CHOICES)
    category = serializers.ChoiceField(choices=AuditLogCategory.CHOICES)
    severity = serializers.ChoiceField(
        choices=AuditLogSeverity.CHOICES,
        default=AuditLogSeverity.INFO
    )
    description = serializers.CharField()
    resource_type = serializers.CharField(required=False, allow_blank=True)
    resource_id = serializers.IntegerField(required=False, allow_null=True)
    resource_name = serializers.CharField(required=False, allow_blank=True)
    data = serializers.JSONField(required=False, default=dict)
    
    def create(self, validated_data):
        request = self.context.get('request')
        
        log_entry = AuditLog.objects.create(
            user=request.user if request and request.user.is_authenticated else None,
            user_email=request.user.email if request and request.user.is_authenticated else 'system',
            user_name=request.user.get_full_name() if request and request.user.is_authenticated else 'System',
            user_role=request.user.role if request and request.user.is_authenticated else 'SYSTEM',
            company=getattr(request.user, 'company_profile', None).company if request and hasattr(request.user, 'company_profile') else None,
            action=validated_data['action'],
            category=validated_data['category'],
            severity=validated_data['severity'],
            description=validated_data['description'],
            resource_name=validated_data.get('resource_name', ''),
            data=validated_data.get('data', {}),
            ip_address=self.get_client_ip(request),
            user_agent=request.META.get('HTTP_USER_AGENT', '') if request else '',
            request_method=request.method if request else '',
            request_path=request.path if request else '',
        )
        
        return log_entry
    
    def get_client_ip(self, request):
        if not request:
            return None
        
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            ip = x_forwarded_for.split(',')[0]
        else:
            ip = request.META.get('REMOTE_ADDR')
        return ip


class AuditLogFilterSerializer(serializers.Serializer):
    user_id = serializers.IntegerField(required=False)
    company_id = serializers.IntegerField(required=False)
    category = serializers.CharField(required=False)
    action = serializers.CharField(required=False)
    severity = serializers.CharField(required=False)
    resource_type = serializers.CharField(required=False)
    resource_id = serializers.IntegerField(required=False)
    date_from = serializers.DateTimeField(required=False)
    date_to = serializers.DateTimeField(required=False)
    search = serializers.CharField(required=False)
