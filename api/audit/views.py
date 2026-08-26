from django.db import models
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.db.models import Q
from django.utils import timezone
from datetime import timedelta

from api.core.permisssions import IsAdminOrSuperAdmin
from api.accounts.models import Company, User
from api.core.public_ids import PublicIdLookupMixin, get_by_identifier
from .models import AuditLog
from .serializers import AuditLogSerializer, AuditLogFilterSerializer
from api.core.constants import AuditLogCategory, AuditLogAction, AuditLogSeverity


class AuditLogPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = 'page_size'
    max_page_size = 100


class AuditLogViewSet(PublicIdLookupMixin, viewsets.ReadOnlyModelViewSet):
    permission_classes = [IsAuthenticated, IsAdminOrSuperAdmin]
    serializer_class = AuditLogSerializer
    pagination_class = AuditLogPagination

    def get_queryset(self):
        queryset = AuditLog.objects.all()
        
        filters = AuditLogFilterSerializer(data=self.request.query_params)
        filters.is_valid(raise_exception=True)
        data = filters.validated_data

        if data.get('user_id'):
            try:
                queryset = queryset.filter(user=get_by_identifier(User.objects.all(), data['user_id']))
            except User.DoesNotExist:
                return queryset.none()

        if data.get('company_id'):
            try:
                queryset = queryset.filter(company=get_by_identifier(Company.objects.all(), data['company_id']))
            except Company.DoesNotExist:
                return queryset.none()

        if data.get('category'):
            queryset = queryset.filter(category=data['category'])

        if data.get('action'):
            queryset = queryset.filter(action=data['action'])

        if data.get('severity'):
            queryset = queryset.filter(severity=data['severity'])

        if data.get('resource_type'):
            queryset = queryset.filter(resource_type_name__icontains=data['resource_type'])

        if data.get('resource_id'):
            queryset = queryset.filter(resource_id=data['resource_id'])

        if data.get('date_from'):
            queryset = queryset.filter(created_at__gte=data['date_from'])

        if data.get('date_to'):
            queryset = queryset.filter(created_at__lte=data['date_to'])

        if data.get('search'):
            queryset = queryset.filter(
                Q(description__icontains=data['search']) |
                Q(user_email__icontains=data['search']) |
                Q(user_name__icontains=data['search']) |
                Q(resource_name__icontains=data['search'])
            )

        return queryset
    
    @action(detail=False, methods=['get'])
    def categories(self, request):
        return Response([
            {'key': key, 'label': label}
            for key, label in AuditLogCategory.CHOICES
        ])
    
    @action(detail=False, methods=['get'])
    def actions(self, request):
        return Response([
            {'key': key, 'label': label}
            for key, label in AuditLogAction.CHOICES
        ])
    
    @action(detail=False, methods=['get'])
    def summary(self, request):
        days = int(request.query_params.get('days', 30))
        start_date = timezone.now() - timedelta(days=days)
        
        by_category = []
        for key, label in AuditLogCategory.CHOICES:
            count = AuditLog.objects.filter(
                category=key,
                created_at__gte=start_date
            ).count()
            if count > 0:
                by_category.append({
                    'category': key,
                    'category_display': label,
                    'count': count
                })
        
        by_severity = []
        for key, label in AuditLogSeverity.CHOICES:
            count = AuditLog.objects.filter(
                severity=key,
                created_at__gte=start_date
            ).count()
            if count > 0:
                by_severity.append({
                    'severity': key,
                    'severity_display': label,
                    'count': count
                })
        
        daily_activity = AuditLog.objects.filter(
            created_at__gte=start_date
        ).extra(
            {'day': "date(created_at)"}
        ).values('day').annotate(
            count=models.Count('id')
        ).order_by('day')
        
        return Response({
            'total_logs': AuditLog.objects.filter(created_at__gte=start_date).count(),
            'by_category': by_category,
            'by_severity': by_severity,
            'daily_activity': [
                {'date': item['day'], 'count': item['count']}
                for item in daily_activity
            ]
        })
    
    @action(detail=False, methods=['get'])
    def user_activity(self, request):
        days = int(request.query_params.get('days', 30))
        start_date = timezone.now() - timedelta(days=days)
        limit = int(request.query_params.get('limit', 10))
        
        user_activity = AuditLog.objects.filter(
            created_at__gte=start_date,
            user__isnull=False
        ).values(
            'user', 'user_email', 'user_name'
        ).annotate(
            activity_count=models.Count('id')
        ).order_by('-activity_count')[:limit]
        
        return Response(user_activity)
