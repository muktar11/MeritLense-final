from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.db.models import Count, Avg, Max, Q
from django.db.models.functions import TruncDate, TruncMonth
from django.utils import timezone
from datetime import timedelta

from api.core.constants import EvaluationStatus, candidateJobRoles, Languages
from api.candidates.models import Candidate
from api.core.permisssions import IsB2CUser
from api.evaluations.models import Evaluation
from api.payments.entitlement_services import EntitlementService
from api.payments.models import PackageBalance
from .comparison_services import build_candidate_comparison_entry
from .serializers import (
    DashboardStatsSerializer, RecentCandidateSerializer, RecentEvaluationSerializer,
    CandidateComparisonSerializer, EvaluationStatusDistributionSerializer,
    EvaluationTrendSerializer, LanguageDistributionSerializer, MonthlyActivitySerializer,
)


class B2CDashboardStatsView(APIView):
    permission_classes = [IsAuthenticated, IsB2CUser]
    
    def get(self, request):
        user = request.user
        
        candidates = Candidate.objects.filter(created_by=user)
        
        evaluations = Evaluation.objects.filter(candidate__in=candidates)
        completed_evaluations = evaluations.filter(status=EvaluationStatus.COMPLETED)
        
        certificates_issued = evaluations.filter(
            certificate_status='ISSUED',
            status=EvaluationStatus.COMPLETED
        ).count()
        
        successful_evaluations = completed_evaluations.filter(score__gte=70).count()
        total_completed = completed_evaluations.count()
        success_rate = (successful_evaluations / total_completed * 100) if total_completed > 0 else 0

        # AI interviews (self-serve, async) vs. scheduled assessments
        # (evaluator-conducted live calls) - see the matching comment in
        # B2BDashboardStatsView for why session__isnull=False is explicit.
        completed_ai_interviews = completed_evaluations.filter(
            session__isnull=False, session__scheduled_start_at__isnull=True
        ).count()
        completed_scheduled_assessments = completed_evaluations.filter(session__scheduled_start_at__isnull=False).count()

        balances = EntitlementService.get_balance_summary("USER", user)

        stats = {
            'total_candidates': candidates.count(),
            'total_evaluations': evaluations.count(),
            'completed_evaluations': total_completed,
            'completed_ai_interviews': completed_ai_interviews,
            'completed_scheduled_assessments': completed_scheduled_assessments,
            'certificates_issued': certificates_issued,
            'success_rate': round(success_rate, 2),
            'remaining_slots': balances[PackageBalance.SLOTS]['remaining'],
            'slot_limit': balances[PackageBalance.SLOTS]['limit'],
            'slots_unlimited': balances[PackageBalance.SLOTS]['unlimited'],
            'reserved_slots': balances[PackageBalance.SLOTS]['reserved'],
            'consumed_slots': balances[PackageBalance.SLOTS]['consumed'],
            'pending_sessions': balances[PackageBalance.SLOTS]['pending_sessions'],
            'remaining_points': balances[PackageBalance.POINTS]['remaining'],
            'points_limit': balances[PackageBalance.POINTS]['limit'],
            'points_unlimited': balances[PackageBalance.POINTS]['unlimited'],
        }

        serializer = DashboardStatsSerializer(stats)
        return Response(serializer.data)


class B2CRecentCandidatesView(APIView):
    permission_classes = [IsAuthenticated, IsB2CUser]
    
    def get(self, request):
        limit = int(request.query_params.get('limit', 10))
        
        candidates = Candidate.objects.filter(
            created_by=request.user
        ).annotate(
            evaluation_count=Count('evaluations'),
            latest_evaluation_date=Max('evaluations__scheduled_date')
        ).order_by('-created_at')[:limit]
        
        serializer = RecentCandidateSerializer(candidates, many=True)
        return Response(serializer.data)


class B2CRecentEvaluationsView(APIView):
    permission_classes = [IsAuthenticated, IsB2CUser]
    
    def get(self, request):
        limit = int(request.query_params.get('limit', 10))
        status_filter = request.query_params.get('status')
        
        candidates = Candidate.objects.filter(created_by=request.user)
        
        evaluations = Evaluation.objects.filter(candidate__in=candidates)
        
        if status_filter:
            if status_filter == 'scheduled':
                evaluations = evaluations.filter(
                    status__in=[EvaluationStatus.SCHEDULED, EvaluationStatus.RESCHEDULED]
                )
            elif status_filter == 'in_progress':
                evaluations = evaluations.filter(status=EvaluationStatus.IN_PROGRESS)
            elif status_filter == 'completed':
                evaluations = evaluations.filter(status=EvaluationStatus.COMPLETED)
        
        evaluations = evaluations.select_related('candidate').order_by('-created_at')[:limit]
        
        serializer = RecentEvaluationSerializer(evaluations, many=True)
        return Response(serializer.data)

class B2CCandidateComparisonView(APIView):
    permission_classes = [IsAuthenticated, IsB2CUser]

    def get(self, request):
        user = request.user

        candidates = Candidate.objects.filter(created_by=user)

        # When specific candidates are requested (the multi-candidate
        # comparison page), return exactly those - including ones with no
        # score yet, so a selected candidate never silently vanishes from
        # the comparison. Without this param, keep the original top-scored-
        # only leaderboard behavior the dashboard widget already relies on.
        raw_ids = request.query_params.get('candidate_ids', '')
        requested_ids = [v.strip() for v in raw_ids.split(',') if v.strip()]
        if requested_ids:
            # candidate_ids from the frontend are public_id UUIDs (the only
            # candidate identifier the API ever exposes as "id" elsewhere -
            # see PublicIdModelSerializer), not the internal integer PK.
            candidates = candidates.filter(public_id__in=requested_ids)

        if not candidates.exists():
            return Response([])

        language = "ar" if request.query_params.get('lang') == "ar" else "en"
        result = [build_candidate_comparison_entry(c, language=language) for c in candidates]

        if requested_ids:
            return Response(result)

        result_with_scores = [item for item in result if item['average_score'] > 0]
        result_with_scores.sort(key=lambda x: x['average_score'], reverse=True)

        return Response(result_with_scores)

class B2CEvaluationTimeRangeView(APIView):
    permission_classes = [IsAuthenticated, IsB2CUser]
    
    def get(self, request):
        user = request.user
        candidates = Candidate.objects.filter(created_by=user)
        evaluations = Evaluation.objects.filter(candidate__in=candidates)
        
        time_ranges = {
            'morning': {'name': 'Morning (6am-12pm)', 'count': 0},
            'afternoon': {'name': 'Afternoon (12pm-6pm)', 'count': 0},
            'evening': {'name': 'Evening (6pm-12am)', 'count': 0},
            'night': {'name': 'Night (12am-6am)', 'count': 0}
        }
        
        for eval in evaluations:
            hour = eval.scheduled_date.hour
            if 6 <= hour < 12:
                time_ranges['morning']['count'] += 1
            elif 12 <= hour < 18:
                time_ranges['afternoon']['count'] += 1
            elif 18 <= hour < 24:
                time_ranges['evening']['count'] += 1
            else:
                time_ranges['night']['count'] += 1
        
        result = [
            {'range': data['name'], 'count': data['count']}
            for data in time_ranges.values()
        ]
        
        return Response(result)


class B2CEvaluationStatusDistributionView(APIView):
    permission_classes = [IsAuthenticated, IsB2CUser]
    
    def get(self, request):
        user = request.user
        candidates = Candidate.objects.filter(created_by=user)
        evaluations = Evaluation.objects.filter(candidate__in=candidates)
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



class B2CJobRoleDistributionView(APIView):
    permission_classes = [IsAuthenticated, IsB2CUser]
    
    def get(self, request):
        user = request.user
        candidates = Candidate.objects.filter(created_by=user)
        total = candidates.count()
        
        if total == 0:
            print(f"No candidates found for user {user.id}")
            return Response([])
        
        role_dict = dict(candidateJobRoles.CHOICES)
        distribution = []
        
        for role_code, role_name in role_dict.items():
            count = candidates.filter(job_role=role_code).count()
            if count > 0:
                distribution.append({
                    'job_role': role_code,
                    'job_role_display': role_name,
                    'count': count,
                    'percentage': round((count / total * 100), 2)
                })
        
        distribution.sort(key=lambda x: x['count'], reverse=True)
        
        return Response(distribution)


class B2CScoreTrendView(APIView):
    permission_classes = [IsAuthenticated, IsB2CUser]
    
    def get(self, request):
        days = int(request.query_params.get('days', 30))
        
        user = request.user
        candidates = Candidate.objects.filter(created_by=user)
        
        start_date = timezone.now() - timedelta(days=days)
        
        evaluations = Evaluation.objects.filter(
            candidate__in=candidates,
            status=EvaluationStatus.COMPLETED,
            score__isnull=False,
            created_at__gte=start_date
        ).annotate(
            date=TruncDate('created_at')
        ).values('date').annotate(
            avg_score=Avg('score')
        ).order_by('date')
        
        result = [
            {'date': item['date'].strftime('%Y-%m-%d'), 'avg_score': round(item['avg_score'], 2)}
            for item in evaluations
        ]

        return Response(result)


class B2CLanguageDistributionView(APIView):
    permission_classes = [IsAuthenticated, IsB2CUser]

    def get(self, request):
        user = request.user
        candidates = Candidate.objects.filter(created_by=user)
        evaluations = Evaluation.objects.filter(candidate__in=candidates)
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


class B2CEvaluationTrendView(APIView):
    permission_classes = [IsAuthenticated, IsB2CUser]

    def get(self, request):
        days = int(request.query_params.get('days', 30))

        user = request.user
        candidates = Candidate.objects.filter(created_by=user)

        start_date = timezone.now() - timedelta(days=days)

        trends = Evaluation.objects.filter(
            candidate__in=candidates,
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


class B2CMonthlyActivityView(APIView):
    permission_classes = [IsAuthenticated, IsB2CUser]

    def get(self, request):
        months = int(request.query_params.get('months', 6))

        user = request.user
        candidates_qs = Candidate.objects.filter(created_by=user)

        start_date = timezone.now() - timedelta(days=30 * months)

        candidates = candidates_qs.filter(
            created_at__gte=start_date
        ).annotate(
            month=TruncMonth('created_at')
        ).values('month').annotate(
            candidates_added=Count('id')
        )

        evaluations = Evaluation.objects.filter(
            candidate__in=candidates_qs,
            status=EvaluationStatus.COMPLETED,
            created_at__gte=start_date
        ).annotate(
            month=TruncMonth('created_at')
        ).values('month').annotate(
            evaluations_completed=Count('id')
        )

        certificates = Evaluation.objects.filter(
            candidate__in=candidates_qs,
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