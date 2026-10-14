import ipaddress

from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ObjectDoesNotExist
from django.db import models
from django.db.models import Count, Max, Min
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
from .trail import candidate_entries_filter, candidate_trail
from api.core.constants import AuditLogCategory, AuditLogAction, AuditLogSeverity


TRAIL_KEYS = ('candidate_id', 'passport_id', 'session_id', 'evaluation_id')


def _resolve_candidate(params):
    from api.candidates.models import Candidate
    from api.evaluations.models import Evaluation
    from api.sessions.models import InterviewSession

    try:
        if params.get('candidate_id'):
            return get_by_identifier(Candidate.objects.all(), params['candidate_id'])
        if params.get('passport_id'):
            return Candidate.objects.get(passport_id__iexact=params['passport_id'].strip())
        if params.get('session_id'):
            return get_by_identifier(InterviewSession.objects.all(), params['session_id']).candidate
        if params.get('evaluation_id'):
            return get_by_identifier(Evaluation.objects.all(), params['evaluation_id']).candidate
    except (ObjectDoesNotExist, ValueError):
        return None
    return None


class AuditLogPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = 'page_size'
    max_page_size = 100


class AuditLogViewSet(PublicIdLookupMixin, viewsets.ReadOnlyModelViewSet):
    permission_classes = [IsAuthenticated, IsAdminOrSuperAdmin]
    serializer_class = AuditLogSerializer
    pagination_class = AuditLogPagination

    def get_queryset(self):
        queryset = AuditLog.objects.prefetch_related('resource')
        
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

        if data.get('ip_address'):
            queryset = queryset.filter(ip_address=data['ip_address'])

        if data.get('candidate_id'):
            candidate = _resolve_candidate({'candidate_id': data['candidate_id']})
            if candidate is None:
                return queryset.none()
            queryset = queryset.filter(candidate_entries_filter(candidate))

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
    def trail(self, request):
        """Provenance of one candidate: who registered it and from which IP,
        who created each assessment and from where, and the IPs the
        candidate used on the interview link. Identify the candidate by
        ?candidate_id=, ?passport_id=, ?session_id= or ?evaluation_id=.
        The events themselves: GET logs?candidate_id=<id>[&ip_address=]."""
        if not any(request.query_params.get(k) for k in TRAIL_KEYS):
            return Response({'detail': "Give candidate_id, passport_id, session_id or evaluation_id.",
                             'code': 'invalid_query'}, status=400)
        candidate = _resolve_candidate(request.query_params)
        if candidate is None:
            return Response({'detail': "No candidate found.", 'code': 'candidate_not_found'}, status=404)
        return Response(candidate_trail(candidate))

    IP_TRACE_LIMIT = 100

    @action(detail=False, methods=['get'], url_path='ip-activity')
    def ip_activity(self, request):
        """IP tracer. ?email= (or ?user_id=) lists every IP an account
        acted from, plus the IPs its candidates used on their interview
        links; ?ip= lists every account seen on that IP. ?days= (default 90)."""
        try:
            days = min(max(int(request.query_params.get('days', 90)), 1), 3650)
        except ValueError:
            return Response({'detail': "days must be a number.", 'code': 'invalid_query'}, status=400)
        since = timezone.now() - timedelta(days=days)
        recent = AuditLog.objects.filter(created_at__gte=since, ip_address__isnull=False)

        ip = (request.query_params.get('ip') or '').strip()
        if ip:
            try:
                ip = str(ipaddress.ip_address(ip))
            except ValueError:
                return Response({'detail': "Not a valid IP address.", 'code': 'invalid_query'}, status=400)
            on_ip = recent.filter(ip_address=ip)
            accounts = (
                on_ip.filter(user__isnull=False)
                .values('user__public_id', 'user_email', 'user_name', 'user_role')
                .annotate(first_seen=Min('created_at'), last_seen=Max('created_at'), events=Count('id'),
                          logins=Count('id', filter=Q(action=AuditLogAction.LOGIN_SUCCESS)),
                          failed_logins=Count('id', filter=Q(action=AuditLogAction.LOGIN_FAILED)))
                .order_by('-last_seen')[:self.IP_TRACE_LIMIT]
            )
            return Response({
                'mode': 'ip',
                'ip': ip,
                'days': days,
                'accounts': [
                    {'user_id': str(row['user__public_id']), 'email': row['user_email'], 'name': row['user_name'],
                     'role': row['user_role'], 'first_seen': row['first_seen'], 'last_seen': row['last_seen'],
                     'events': row['events'], 'logins': row['logins'], 'failed_logins': row['failed_logins']}
                    for row in accounts
                ],
                # Candidates on interview links, unknown sign-in emails and
                # other activity not tied to an account.
                'unattributed_events': on_ip.filter(user__isnull=True).count(),
            })

        identifier = (request.query_params.get('user_id') or '').strip()
        email = (request.query_params.get('email') or '').strip()
        try:
            if identifier:
                user = get_by_identifier(User.objects.all(), identifier)
            elif email:
                user = User.objects.get(email__iexact=email)
            else:
                return Response({'detail': "Give an email, user_id or ip.", 'code': 'invalid_query'}, status=400)
        except (User.DoesNotExist, ValueError):
            return Response({'detail': "No account found.", 'code': 'account_not_found'}, status=404)

        rows = list(
            recent.filter(user=user)
            .values('ip_address')
            .annotate(first_seen=Min('created_at'), last_seen=Max('created_at'), events=Count('id'),
                      logins=Count('id', filter=Q(action=AuditLogAction.LOGIN_SUCCESS)),
                      failed_logins=Count('id', filter=Q(action=AuditLogAction.LOGIN_FAILED)))
            .order_by('-last_seen')[:self.IP_TRACE_LIMIT]
        )
        ips = [row['ip_address'] for row in rows]
        agents, others = {}, {}
        for entry in (recent.filter(user=user, ip_address__in=ips).exclude(user_agent='')
                      .order_by().values('ip_address', 'user_agent').distinct()):
            agents.setdefault(entry['ip_address'], []).append(entry['user_agent'])
        for entry in (recent.filter(ip_address__in=ips, user__isnull=False).exclude(user=user)
                      .order_by().values('ip_address').annotate(n=Count('user', distinct=True))):
            others[entry['ip_address']] = entry['n']

        from api.sessions.models import InterviewSession

        session_ids = InterviewSession.objects.filter(created_by=user).values('id')
        candidate_rows = (
            recent.filter(user__isnull=True, resource_type=ContentType.objects.get_for_model(InterviewSession),
                          resource_id__in=session_ids)
            .values('ip_address')
            .annotate(first_seen=Min('created_at'), last_seen=Max('created_at'), events=Count('id'),
                      sessions=Count('resource_id', distinct=True))
            .order_by('-last_seen')[:self.IP_TRACE_LIMIT]
        )
        return Response({
            'mode': 'account',
            'days': days,
            'account': {'user_id': str(user.public_id), 'email': user.email, 'name': user.get_full_name(),
                        'role': user.role},
            'ips': [
                {**row, 'user_agents': agents.get(row['ip_address'], [])[:5],
                 'other_accounts': others.get(row['ip_address'], 0)}
                for row in rows
            ],
            'candidate_ips': list(candidate_rows),
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
