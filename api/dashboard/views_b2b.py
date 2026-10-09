from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.db.models import Avg, BooleanField, Count, Exists, ExpressionWrapper, Max, OuterRef, Q
from django.db.models.functions import Coalesce, TruncDate, TruncMonth
from django.utils import timezone
from datetime import timedelta

from api.core.constants import CandidateJobRoles, EvaluationStatus, Languages, ReadinessStatus, Roles
from api.candidates.models import Candidate
from api.core.permisssions import IsB2BTeamMember, IsB2BUser, RequireFullTeamAccess
from api.evaluations.models import Evaluation
from api.accounts.models import User
from api.payments.entitlement_services import EntitlementService
from api.payments.models import PackageBalance
from api.reports.models import EvaluationReport
from .comparison_services import (
    build_candidate_comparison_entry,
    build_full_comparison,
    compute_key_differences,
    get_comparable_roles,
    get_eligible_candidates,
)
from .comparison_pdf_services import render_comparison_pdf
from .dashboard_layout import DEFAULT_WIDGETS, WIDGET_IDS, resolve_layout, validate_widgets
from .serializers import (
    DashboardStatsSerializer, RecentCandidateSerializer, RecentEvaluationSerializer,
    ScoreDistributionSerializer, EvaluationTrendSerializer, LanguageDistributionSerializer,
    PerformanceMetricSerializer, EvaluationStatusDistributionSerializer,
    MonthlyActivitySerializer
)


class B2BDashboardStatsView(APIView):
    permission_classes = [IsAuthenticated, (IsB2BUser | IsB2BTeamMember), RequireFullTeamAccess]
    
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

        # AI interviews (self-serve, async) vs. scheduled assessments
        # (evaluator-conducted live calls) - distinguished by whether the
        # linked session was ever scheduled (InterviewSession.is_scheduled_interview).
        # A completed evaluation with no linked session at all (session=SET_NULL
        # on delete) falls into neither bucket - session__isnull=False is
        # required explicitly, since a NULL session would otherwise also
        # satisfy session__scheduled_start_at__isnull=True via the outer join.
        completed_ai_interviews = completed_evaluations.filter(
            session__isnull=False, session__scheduled_start_at__isnull=True
        ).count()
        completed_scheduled_assessments = completed_evaluations.filter(session__scheduled_start_at__isnull=False).count()

        team_members = User.objects.filter(
            company=company,
            role=Roles.B2B_TEAM_MEMBER,
            is_active=True
        ).count()
        
        balances = EntitlementService.get_balance_summary("COMPANY", company)

        stats = {
            'total_candidates': candidates.count(),
            'total_evaluations': evaluations.count(),
            'completed_evaluations': total_completed,
            'completed_ai_interviews': completed_ai_interviews,
            'completed_scheduled_assessments': completed_scheduled_assessments,
            'certificates_issued': certificates_issued,
            'success_rate': round(success_rate, 2),
            'team_members_count': team_members,
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


class B2BRecentCandidatesView(APIView):
    permission_classes = [IsAuthenticated, (IsB2BUser | IsB2BTeamMember), RequireFullTeamAccess]
    
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
    permission_classes = [IsAuthenticated, (IsB2BUser | IsB2BTeamMember), RequireFullTeamAccess]
    
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
    permission_classes = [IsAuthenticated, (IsB2BUser | IsB2BTeamMember), RequireFullTeamAccess]
    
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
        role_dict = dict(CandidateJobRoles.CHOICES)
        
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
    permission_classes = [IsAuthenticated, (IsB2BUser | IsB2BTeamMember), RequireFullTeamAccess]
    
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
    permission_classes = [IsAuthenticated, (IsB2BUser | IsB2BTeamMember), RequireFullTeamAccess]
    
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
    permission_classes = [IsAuthenticated, (IsB2BUser | IsB2BTeamMember), RequireFullTeamAccess]
    
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
    permission_classes = [IsAuthenticated, (IsB2BUser | IsB2BTeamMember), RequireFullTeamAccess]
    
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
    permission_classes = [IsAuthenticated, (IsB2BUser | IsB2BTeamMember), RequireFullTeamAccess]
    
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


class B2BJobRoleDistributionView(APIView):
    permission_classes = [IsAuthenticated, (IsB2BUser | IsB2BTeamMember), RequireFullTeamAccess]

    def get(self, request):
        company = None
        if request.user.role == Roles.B2B and hasattr(request.user, 'company_profile'):
            company = request.user.company_profile.company
        elif request.user.role == Roles.B2B_TEAM_MEMBER and hasattr(request.user, 'team_member_profile'):
            company = request.user.team_member_profile.company

        if not company:
            return Response({'error': 'Company not found'}, status=400)

        candidates = Candidate.objects.filter(company=company)
        total = candidates.count()

        if total == 0:
            return Response([])

        role_dict = dict(CandidateJobRoles.CHOICES)
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


class B2BEvaluationTimeRangeView(APIView):
    permission_classes = [IsAuthenticated, (IsB2BUser | IsB2BTeamMember), RequireFullTeamAccess]

    def get(self, request):
        company = None
        if request.user.role == Roles.B2B and hasattr(request.user, 'company_profile'):
            company = request.user.company_profile.company
        elif request.user.role == Roles.B2B_TEAM_MEMBER and hasattr(request.user, 'team_member_profile'):
            company = request.user.team_member_profile.company

        if not company:
            return Response({'error': 'Company not found'}, status=400)

        evaluations = Evaluation.objects.filter(company=company)

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


class B2BCandidateComparisonView(APIView):
    permission_classes = [IsAuthenticated, (IsB2BUser | IsB2BTeamMember), RequireFullTeamAccess]

    def get(self, request):
        company = None
        if request.user.role == Roles.B2B and hasattr(request.user, 'company_profile'):
            company = request.user.company_profile.company
        elif request.user.role == Roles.B2B_TEAM_MEMBER and hasattr(request.user, 'team_member_profile'):
            company = request.user.team_member_profile.company

        if not company:
            return Response({'error': 'Company not found'}, status=400)

        candidates = Candidate.objects.filter(company=company)

        # Same contract as B2CCandidateComparisonView: when specific
        # candidates are requested, return exactly those (including
        # unscored ones) rather than the top-scored-only leaderboard slice.
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


def _resolve_b2b_company(user):
    if user.role == Roles.B2B and hasattr(user, 'company_profile'):
        return user.company_profile.company
    if user.role == Roles.B2B_TEAM_MEMBER and hasattr(user, 'team_member_profile'):
        return user.team_member_profile.company
    return None


class B2BComparisonRolesView(APIView):
    """Step 1 of role-based Candidate Comparison: which Job Roles actually
    have comparable (scored) candidates to choose from."""
    permission_classes = [IsAuthenticated, (IsB2BUser | IsB2BTeamMember), RequireFullTeamAccess]

    def get(self, request):
        company = _resolve_b2b_company(request.user)
        if not company:
            return Response({'error': 'Company not found'}, status=400)
        return Response(get_comparable_roles(owner_type="COMPANY", owner=company))


class B2BComparisonEligibleCandidatesView(APIView):
    """Step 2: candidates eligible for comparison under a chosen role."""
    permission_classes = [IsAuthenticated, (IsB2BUser | IsB2BTeamMember), RequireFullTeamAccess]

    def get(self, request):
        company = _resolve_b2b_company(request.user)
        if not company:
            return Response({'error': 'Company not found'}, status=400)
        role_code = request.query_params.get('role_code', '').strip()
        if not role_code:
            return Response({'error': 'role_code is required'}, status=400)
        return Response(get_eligible_candidates(owner_type="COMPANY", owner=company, role_code=role_code))


class B2BComparisonFullView(APIView):
    """Steps 3+4: the real Candidate Summary + Competency Comparison data
    for 2-4 selected, role-eligible candidates."""
    permission_classes = [IsAuthenticated, (IsB2BUser | IsB2BTeamMember), RequireFullTeamAccess]

    def get(self, request):
        company = _resolve_b2b_company(request.user)
        if not company:
            return Response({'error': 'Company not found'}, status=400)

        role_code = request.query_params.get('role_code', '').strip()
        raw_ids = request.query_params.get('candidate_ids', '')
        candidate_ids = [v.strip() for v in raw_ids.split(',') if v.strip()]
        if not role_code or not (2 <= len(candidate_ids) <= 4):
            return Response({'error': 'role_code and 2-4 candidate_ids are required'}, status=400)

        language = "ar" if request.query_params.get('lang') == "ar" else "en"
        entries = build_full_comparison(
            owner_type="COMPANY", owner=company, role_code=role_code,
            candidate_ids=candidate_ids, language=language, actor=request.user,
        )
        return Response({
            'role_code': role_code,
            'role_name': dict((r['role_code'], r['role_name']) for r in get_comparable_roles(owner_type="COMPANY", owner=company)).get(role_code, role_code),
            'candidates': entries,
            'key_differences': compute_key_differences(entries, language=language),
        })


class B2BComparisonPdfView(APIView):
    """Spec item 8: backend-rendered bilingual Comparison PDF, same
    structure as the on-screen page - Candidate Summary, Competency
    Comparison, Key Differences, Radar Chart."""
    permission_classes = [IsAuthenticated, (IsB2BUser | IsB2BTeamMember), RequireFullTeamAccess]

    def get(self, request):
        company = _resolve_b2b_company(request.user)
        if not company:
            return Response({'error': 'Company not found'}, status=400)

        role_code = request.query_params.get('role_code', '').strip()
        raw_ids = request.query_params.get('candidate_ids', '')
        candidate_ids = [v.strip() for v in raw_ids.split(',') if v.strip()]
        if not role_code or not (2 <= len(candidate_ids) <= 4):
            return Response({'error': 'role_code and 2-4 candidate_ids are required'}, status=400)

        language = "ar" if request.query_params.get('lang') == "ar" else "en"
        role_name = dict((r['role_code'], r['role_name']) for r in get_comparable_roles(owner_type="COMPANY", owner=company)).get(role_code, role_code)
        entries = build_full_comparison(
            owner_type="COMPANY", owner=company, role_code=role_code,
            candidate_ids=candidate_ids, language=language, actor=request.user,
        )
        if len(entries) < 2:
            return Response({'error': 'At least 2 comparable candidates are required.'}, status=400)

        key_differences = compute_key_differences(entries, language=language)
        pdf_bytes = render_comparison_pdf(role_name=role_name, entries=entries, key_differences=key_differences, language=language)
        from django.http import HttpResponse
        response = HttpResponse(pdf_bytes, content_type='application/pdf')
        response['Content-Disposition'] = 'attachment; filename="candidate-comparison.pdf"'
        return response


class B2BDashboardLayoutView(APIView):
    """Company-level overview dashboard customization (which widgets are
    visible, in what order). Every company user reads the same layout;
    only the company's B2B admin can change it, so one team member's
    preferences can't silently rearrange everyone else's dashboard."""
    permission_classes = [IsAuthenticated, (IsB2BUser | IsB2BTeamMember), RequireFullTeamAccess]

    def _payload(self, request, company):
        return {
            **resolve_layout(company),
            'available_widgets': list(WIDGET_IDS),
            'default_widgets': list(DEFAULT_WIDGETS),
            'can_edit': request.user.role == Roles.B2B,
        }

    def get(self, request):
        company = _resolve_b2b_company(request.user)
        if not company:
            return Response({'error': 'Company not found'}, status=400)
        return Response(self._payload(request, company))

    def put(self, request):
        company = _resolve_b2b_company(request.user)
        if not company:
            return Response({'error': 'Company not found'}, status=400)
        if request.user.role != Roles.B2B:
            return Response({'error': 'Only the company administrator can customize the dashboard.'}, status=403)

        if request.data.get('reset'):
            company.dashboard_layout = {}
        else:
            try:
                widgets = validate_widgets(request.data.get('widgets'))
            except ValueError as exc:
                return Response({'error': str(exc)}, status=400)
            company.dashboard_layout = {'widgets': widgets}
        company.save(update_fields=['dashboard_layout', 'updated_at'])
        return Response(self._payload(request, company))


def _completed_with_current_readiness(company):
    """Completed evaluations that carry a readiness result, annotated with
    the readiness as it stands today - an EvaluationReadinessCorrection
    supersedes the original decision (see
    ReadinessRecordService.current_readiness_status), done in SQL here so
    the dashboard doesn't run a query per evaluation."""
    return Evaluation.objects.filter(
        company=company,
        status=EvaluationStatus.COMPLETED,
        readiness_indicator_enabled=True,
    ).annotate(
        current_readiness=Coalesce(
            'readiness_correction__corrected_readiness_status', 'readiness_status'
        ),
    )


class B2BReadinessDistributionView(APIView):
    """Overall Readiness Index: completed evaluations by their actual
    readiness outcome (not by evaluation status)."""
    permission_classes = [IsAuthenticated, (IsB2BUser | IsB2BTeamMember), RequireFullTeamAccess]

    def get(self, request):
        company = _resolve_b2b_company(request.user)
        if not company:
            return Response({'error': 'Company not found'}, status=400)

        counts = dict(
            _completed_with_current_readiness(company)
            .values('current_readiness')
            .annotate(n=Count('id'))
            .values_list('current_readiness', 'n')
        )
        total = sum(counts.values())
        distribution = [
            {
                'status': status,
                'status_display': display,
                'count': counts.get(status, 0),
                'percentage': round(counts.get(status, 0) / total * 100, 2) if total else 0,
            }
            for status, display in ReadinessStatus.CHOICES
        ]
        decided = sum(
            counts.get(s, 0)
            for s in (ReadinessStatus.READY, ReadinessStatus.PARTIALLY_READY, ReadinessStatus.NOT_READY)
        )
        ready_rate = round(counts.get(ReadinessStatus.READY, 0) / decided * 100, 2) if decided else None
        return Response({'total': total, 'ready_rate': ready_rate, 'distribution': distribution})


class B2BRequiresAttentionView(APIView):
    """Decision-oriented queue: completed evaluations an employer should
    look at next - the scoring engine flagged them for human review (and
    no readiness correction has been recorded since), or there wasn't
    enough evidence to reach any readiness decision."""
    permission_classes = [IsAuthenticated, (IsB2BUser | IsB2BTeamMember), RequireFullTeamAccess]

    REASON_HUMAN_REVIEW = 'HUMAN_REVIEW'
    REASON_INSUFFICIENT_EVIDENCE = 'INSUFFICIENT_EVIDENCE'

    def get(self, request):
        company = _resolve_b2b_company(request.user)
        if not company:
            return Response({'error': 'Company not found'}, status=400)
        try:
            limit = max(1, min(int(request.query_params.get('limit', 5)), 50))
        except ValueError:
            limit = 5

        flagged_report = EvaluationReport.objects.filter(
            evaluation=OuterRef('pk'),
            report_status=EvaluationReport.STATUS_ACTIVE,
            requires_human_review=True,
        )
        needs_review = Q(Exists(flagged_report)) & Q(readiness_correction__isnull=True)
        insufficient = Q(current_readiness=ReadinessStatus.INCOMPLETE)
        queryset = (
            _completed_with_current_readiness(company)
            .filter(needs_review | insufficient)
            .annotate(
                needs_review=ExpressionWrapper(needs_review, output_field=BooleanField()),
                activity_at=Coalesce('last_evaluation_date', 'updated_at'),
            )
        )
        counts = queryset.aggregate(
            total=Count('id'),
            human_review=Count('id', filter=needs_review),
            insufficient_evidence=Count('id', filter=insufficient),
        )

        role_dict = dict(CandidateJobRoles.CHOICES)
        items = []
        for evaluation in queryset.select_related('candidate').order_by('-activity_at')[:limit]:
            reasons = []
            if evaluation.needs_review:
                reasons.append(self.REASON_HUMAN_REVIEW)
            if evaluation.current_readiness == ReadinessStatus.INCOMPLETE:
                reasons.append(self.REASON_INSUFFICIENT_EVIDENCE)
            name = f"{evaluation.candidate_first_name} {evaluation.candidate_last_name}".strip()
            items.append({
                'evaluation_id': str(evaluation.public_id),
                'candidate_name': name or (evaluation.candidate.get_full_name() if evaluation.candidate else ''),
                'job_role': evaluation.candidate_job_role,
                'job_role_display': role_dict.get(evaluation.candidate_job_role, evaluation.candidate_job_role),
                'readiness_status': evaluation.current_readiness,
                # Withheld while awaiting human review (release safeguard).
                'score': float(evaluation.score) if evaluation.score is not None and not evaluation.is_held_for_review else None,
                'reasons': reasons,
                'activity_at': evaluation.activity_at,
            })
        return Response({**counts, 'items': items})
