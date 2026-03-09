from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.db.models import Count, Avg, Max
from django.db.models.functions import TruncDate
from django.utils import timezone
from datetime import timedelta

from api.core.constants import EvaluationStatus, ScoreArea, candidateJobRoles
from api.candidates.models import Candidate
from api.core.permisssions import IsB2CUser
from api.evaluations.models import Evaluation
from api.scores.models import ScoreSet, CandidateScore
from .serializers import (
    DashboardStatsSerializer, RecentCandidateSerializer, RecentEvaluationSerializer,
    CandidateComparisonSerializer, EvaluationStatusDistributionSerializer,
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
        
        stats = {
            'total_candidates': candidates.count(),
            'total_evaluations': evaluations.count(),
            'completed_evaluations': total_completed,
            'certificates_issued': certificates_issued,
            'success_rate': round(success_rate, 2),
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
        
        if not candidates.exists():
            print(f"No candidates found for user {user.id}")
            return Response([])
        
        result = []
        for candidate in candidates:
            latest_score_set = ScoreSet.objects.filter(
                candidate=candidate
            ).order_by('-created_at').first()
            
            if latest_score_set and latest_score_set.average_score:
                scores = CandidateScore.objects.filter(
                    candidate=candidate,
                    evaluation=latest_score_set.evaluation
                )
                
                scores_by_area = {}
                for score in scores:
                    area_display = dict(ScoreArea.CHOICES).get(score.area, score.area)
                    scores_by_area[area_display] = float(score.score)
                
                result.append({
                    'candidate_id': candidate.id,
                    'candidate_name': candidate.get_full_name(),
                    'job_role': candidate.get_job_role_display(),
                    'average_score': float(latest_score_set.average_score),
                    'scores_by_area': scores_by_area
                })
            else:
                result.append({
                    'candidate_id': candidate.id,
                    'candidate_name': candidate.get_full_name(),
                    'job_role': candidate.get_job_role_display(),
                    'average_score': 0,
                    'scores_by_area': {}
                })
        
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