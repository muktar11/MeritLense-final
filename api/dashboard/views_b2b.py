from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.db.models import Count, Avg, Q, Max
from django.db.models.functions import TruncDate, TruncMonth
from django.utils import timezone
from datetime import timedelta

from api.core.constants import EvaluationStatus, JobRoles, Languages, Roles
from api.candidates.models import Candidate
from api.core.permisssions import IsB2BTeamMember, IsB2BUser
from api.evaluations.models import Evaluation
from api.accounts.models import User
from .serializers import (
    DashboardStatsSerializer, RecentCandidateSerializer, RecentEvaluationSerializer,
    ScoreDistributionSerializer, EvaluationTrendSerializer, LanguageDistributionSerializer,
    PerformanceMetricSerializer, EvaluationStatusDistributionSerializer,
    MonthlyActivitySerializer
)


class B2BDashboardStatsView(APIView):
    permission_classes = [IsAuthenticated, (IsB2BUser | IsB2BTeamMember)]
    
    def get_company(self, user):
        if user.role == Roles.B2B and hasattr(user, 'company_profile'):
            return user.company_profile.company
        elif user.role == Roles.B2B_TEAM_MEMBER and hasattr(user, 'team_member_profile'):
            return user.team_member_profile.company
        return None
    
    def get(self, request):
        company = self.get_company(request.user)
        if not company:
            return Response({'error': 'Company not found'}, status=400)
        
        candidates = Candidate.objects.filter(company=company)
        
        evaluations = Evaluation.objects.filter(company=company)
        completed_evaluations = evaluations.filter(status=EvaluationStatus.COMPLETED)
        
        certificates_issued = evaluations.filter(
            certificate_status='ISSUED',
            status=EvaluationStatus.COMPLETED
        ).count()
        
        successful_evaluations = completed_evaluations.filter(score__gte=70).count()
        total_completed = completed_evaluations.count()
        success_rate = (successful_evaluations / total_completed * 100) if total_completed > 0 else 0
        
        team_members = User.objects.filter(
            company=company,
            role=Roles.B2B_TEAM_MEMBER,
            is_active=True
        ).count()
        
        stats = {
            'total_candidates': candidates.count(),
            'total_evaluations': evaluations.count(),
            'completed_evaluations': total_completed,
            'certificates_issued': certificates_issued,
            'success_rate': round(success_rate, 2),
            'team_members_count': team_members,
        }
        
        serializer = DashboardStatsSerializer(stats)
        return Response(serializer.data)


class B2BRecentCandidatesView(APIView):
    permission_classes = [IsAuthenticated, (IsB2BUser | IsB2BTeamMember)]
    
    def get(self, request):
        limit = int(request.query_params.get('limit', 10))
        
        company = None
        if request.user.role == Roles.B2B and hasattr(request.user, 'company_profile'):
            company = request.user.company_profile.company
        elif request.user.role == Roles.B2B_TEAM_MEMBER and hasattr(request.user, 'team_member_profile'):
            company = request.user.team_member_profile.company
        
        if not company:
            return Response({'error': 'Company not found'}, status=400)
        
        candidates = Candidate.objects.filter(company=company).annotate(
            evaluation_count=Count('evaluations'),
            latest_evaluation_date=Max('evaluations__scheduled_date')
        ).order_by('-created_at')[:limit]
        
        serializer = RecentCandidateSerializer(candidates, many=True)
        return Response(serializer.data)


class B2BRecentEvaluationsView(APIView):
    permission_classes = [IsAuthenticated, (IsB2BUser | IsB2BTeamMember)]
    
    def get(self, request):
        limit = int(request.query_params.get('limit', 10))
        
        company = None
        if request.user.role == Roles.B2B and hasattr(request.user, 'company_profile'):
            company = request.user.company_profile.company
        elif request.user.role == Roles.B2B_TEAM_MEMBER and hasattr(request.user, 'team_member_profile'):
            company = request.user.team_member_profile.company
        
        if not company:
            return Response({'error': 'Company not found'}, status=400)
        
        evaluations = Evaluation.objects.filter(
            company=company
        ).select_related('candidate').order_by('-created_at')[:limit]
        
        serializer = RecentEvaluationSerializer(evaluations, many=True)
        return Response(serializer.data)


class B2BScoreDistributionView(APIView):
    permission_classes = [IsAuthenticated, (IsB2BUser | IsB2BTeamMember)]
    
    def get(self, request):
        company = None
        if request.user.role == Roles.B2B and hasattr(request.user, 'company_profile'):
            company = request.user.company_profile.company
        elif request.user.role == Roles.B2B_TEAM_MEMBER and hasattr(request.user, 'team_member_profile'):
            company = request.user.team_member_profile.company
        
        if not company:
            return Response({'error': 'Company not found'}, status=400)
        
        evaluations = Evaluation.objects.filter(
            company=company,
            status=EvaluationStatus.COMPLETED,
            score__isnull=False
        ).select_related('candidate')
        
        role_distribution = {}
        role_dict = dict(JobRoles.CHOICES)
        
        for eval in evaluations:
            role = eval.candidate_job_role
            if role not in role_distribution:
                role_distribution[role] = {
                    'scores': [],
                    'count': 0
                }
            role_distribution[role]['scores'].append(float(eval.score))
            role_distribution[role]['count'] += 1
        
        result = []
        for role, data in role_distribution.items():
            if data['scores']:
                avg_score = sum(data['scores']) / len(data['scores'])
                result.append({
                    'job_role': role,
                    'job_role_display': role_dict.get(role, role),
                    'average_score': round(avg_score, 2),
                    'min_score': round(min(data['scores']), 2),
                    'max_score': round(max(data['scores']), 2),
                    'evaluation_count': data['count']
                })
        
        result.sort(key=lambda x: x['evaluation_count'], reverse=True)
        
        serializer = ScoreDistributionSerializer(result, many=True)
        return Response(serializer.data)


class B2BEvaluationTrendView(APIView):
    permission_classes = [IsAuthenticated, (IsB2BUser | IsB2BTeamMember)]
    
    def get(self, request):
        days = int(request.query_params.get('days', 30))
        
        company = None
        if request.user.role == Roles.B2B and hasattr(request.user, 'company_profile'):
            company = request.user.company_profile.company
        elif request.user.role == Roles.B2B_TEAM_MEMBER and hasattr(request.user, 'team_member_profile'):
            company = request.user.team_member_profile.company
        
        if not company:
            return Response({'error': 'Company not found'}, status=400)
        
        start_date = timezone.now() - timedelta(days=days)
        
        trends = Evaluation.objects.filter(
            company=company,
            created_at__gte=start_date
        ).annotate(
            date=TruncDate('created_at')
        ).values('date').annotate(
            scheduled_count=Count('id', filter=Q(status=EvaluationStatus.SCHEDULED)),
            completed_count=Count('id', filter=Q(status=EvaluationStatus.COMPLETED)),
            cancelled_count=Count('id', filter=Q(status=EvaluationStatus.CANCELLED))
        ).order_by('date')
        
        serializer = EvaluationTrendSerializer(trends, many=True)
        return Response(serializer.data)


class B2BLanguageDistributionView(APIView):
    permission_classes = [IsAuthenticated, (IsB2BUser | IsB2BTeamMember)]
    
    def get(self, request):
        company = None
        if request.user.role == Roles.B2B and hasattr(request.user, 'company_profile'):
            company = request.user.company_profile.company
        elif request.user.role == Roles.B2B_TEAM_MEMBER and hasattr(request.user, 'team_member_profile'):
            company = request.user.team_member_profile.company
        
        if not company:
            return Response({'error': 'Company not found'}, status=400)
        
        evaluations = Evaluation.objects.filter(company=company)
        total = evaluations.count()
        
        if total == 0:
            return Response([])
        
        lang_dict = dict(Languages.CHOICES)
        distribution = []
        
        for lang_code, lang_name in lang_dict.items():
            count = evaluations.filter(candidate_preferred_language=lang_code).count()
            if count > 0:
                distribution.append({
                    'language': lang_code,
                    'language_display': lang_name,
                    'count': count,
                    'percentage': round((count / total * 100), 2)
                })
        
        distribution.sort(key=lambda x: x['count'], reverse=True)
        
        serializer = LanguageDistributionSerializer(distribution, many=True)
        return Response(serializer.data)


class B2BPerformanceMetricsView(APIView):
    permission_classes = [IsAuthenticated, (IsB2BUser | IsB2BTeamMember)]
    
    def get(self, request):
        months = int(request.query_params.get('months', 6))
        
        company = None
        if request.user.role == Roles.B2B and hasattr(request.user, 'company_profile'):
            company = request.user.company_profile.company
        elif request.user.role == Roles.B2B_TEAM_MEMBER and hasattr(request.user, 'team_member_profile'):
            company = request.user.team_member_profile.company
        
        if not company:
            return Response({'error': 'Company not found'}, status=400)
        
        start_date = timezone.now() - timedelta(days=30 * months)
        
        metrics = Evaluation.objects.filter(
            company=company,
            status=EvaluationStatus.COMPLETED,
            created_at__gte=start_date
        ).annotate(
            month=TruncMonth('created_at')
        ).values('month').annotate(
            average_score=Avg('score'),
            evaluation_count=Count('id'),
            pass_count=Count('id', filter=Q(score__gte=70))
        ).order_by('month')
        
        result = []
        for item in metrics:
            pass_rate = (item['pass_count'] / item['evaluation_count'] * 100) if item['evaluation_count'] > 0 else 0
            result.append({
                'period': item['month'].strftime('%Y-%m'),
                'average_score': round(item['average_score'], 2) if item['average_score'] else 0,
                'evaluation_count': item['evaluation_count'],
                'pass_rate': round(pass_rate, 2)
            })
        
        serializer = PerformanceMetricSerializer(result, many=True)
        return Response(serializer.data)


class B2BEvaluationStatusDistributionView(APIView):
    permission_classes = [IsAuthenticated, (IsB2BUser | IsB2BTeamMember)]
    
    def get(self, request):
        company = None
        if request.user.role == Roles.B2B and hasattr(request.user, 'company_profile'):
            company = request.user.company_profile.company
        elif request.user.role == Roles.B2B_TEAM_MEMBER and hasattr(request.user, 'team_member_profile'):
            company = request.user.team_member_profile.company
        
        if not company:
            return Response({'error': 'Company not found'}, status=400)
        
        evaluations = Evaluation.objects.filter(company=company)
        total = evaluations.count()
        
        status_dict = dict(EvaluationStatus.CHOICES)
        distribution = []
        
        for status_code, status_name in status_dict.items():
            count = evaluations.filter(status=status_code).count()
            if count > 0:
                distribution.append({
                    'status': status_code,
                    'status_display': status_name,
                    'count': count,
                    'percentage': round((count / total * 100), 2) if total > 0 else 0
                })
        
        serializer = EvaluationStatusDistributionSerializer(distribution, many=True)
        return Response(serializer.data)


class B2BMonthlyActivityView(APIView):
    permission_classes = [IsAuthenticated, (IsB2BUser | IsB2BTeamMember)]
    
    def get(self, request):
        months = int(request.query_params.get('months', 6))
        
        company = None
        if request.user.role == Roles.B2B and hasattr(request.user, 'company_profile'):
            company = request.user.company_profile.company
        elif request.user.role == Roles.B2B_TEAM_MEMBER and hasattr(request.user, 'team_member_profile'):
            company = request.user.team_member_profile.company
        
        if not company:
            return Response({'error': 'Company not found'}, status=400)
        
        start_date = timezone.now() - timedelta(days=30 * months)
        
        candidates = Candidate.objects.filter(
            company=company,
            created_at__gte=start_date
        ).annotate(
            month=TruncMonth('created_at')
        ).values('month').annotate(
            candidates_added=Count('id')
        )
        
        evaluations = Evaluation.objects.filter(
            company=company,
            status=EvaluationStatus.COMPLETED,
            created_at__gte=start_date
        ).annotate(
            month=TruncMonth('created_at')
        ).values('month').annotate(
            evaluations_completed=Count('id')
        )
        
        certificates = Evaluation.objects.filter(
            company=company,
            certificate_status='ISSUED',
            created_at__gte=start_date
        ).annotate(
            month=TruncMonth('created_at')
        ).values('month').annotate(
            certificates_issued=Count('id')
        )
        
        months_data = {}
        
        for item in candidates:
            month = item['month'].strftime('%Y-%m')
            months_data[month] = {
                'month': month,
                'candidates_added': item['candidates_added'],
                'evaluations_completed': 0,
                'certificates_issued': 0
            }
        
        for item in evaluations:
            month = item['month'].strftime('%Y-%m')
            if month in months_data:
                months_data[month]['evaluations_completed'] = item['evaluations_completed']
            else:
                months_data[month] = {
                    'month': month,
                    'candidates_added': 0,
                    'evaluations_completed': item['evaluations_completed'],
                    'certificates_issued': 0
                }
        
        for item in certificates:
            month = item['month'].strftime('%Y-%m')
            if month in months_data:
                months_data[month]['certificates_issued'] = item['certificates_issued']
            else:
                months_data[month] = {
                    'month': month,
                    'candidates_added': 0,
                    'evaluations_completed': 0,
                    'certificates_issued': item['certificates_issued']
                }
        
        result = list(months_data.values())
        result.sort(key=lambda x: x['month'])
        
        serializer = MonthlyActivitySerializer(result, many=True)
        return Response(serializer.data)