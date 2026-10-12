from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.db.models import Count, Q, Sum
from django.db.models.functions import TruncDate, TruncMonth
from django.utils import timezone
from datetime import timedelta

from api.audit.services import AuditLogService
from api.core.constants import AuditLogCategory, Roles, EvaluationStatus
from api.accounts.models import User, Company
from api.candidates.models import Candidate
from api.core.permisssions import IsAdminOrSuperAdmin
from api.evaluations.models import Evaluation
from api.payments.models import Subscription, Payment, Invoice
from django.http import HttpResponse

from .comparison_pdf_services import render_comparison_pdf
from .readiness_services import readiness_distribution
from .comparison_services import (
    build_full_comparison,
    compute_key_differences,
    get_comparable_roles,
    get_comparison_accounts,
    get_eligible_candidates,
    resolve_comparison_owner,
)

class AdminDashboardStatsView(APIView):
    permission_classes = [IsAuthenticated, IsAdminOrSuperAdmin]
    
    def get(self, request):
        active_candidates = Candidate.objects.filter(status='ACTIVE').count()
        
        total_users = User.objects.filter(
            role__in=[Roles.B2B, Roles.B2C]
        ).count()
        
        b2c_users = User.objects.filter(role=Roles.B2C).count()
        
        b2b_users = User.objects.filter(role=Roles.B2B).count()
        
        active_agencies = Company.objects.filter(is_verified=True).count()
        
        total_evaluations = Evaluation.objects.count()
        
        completed_evaluations = Evaluation.objects.filter(
            status=EvaluationStatus.COMPLETED
        ).count()
        
        active_subscriptions = Subscription.objects.filter(
            Q(status__iexact='active') | Q(status__iexact='trialing')
        ).select_related('stripe_price')

        # A true headcount of active/trialing subscriptions, independent of
        # whether we can price them below - a subscription whose Price was
        # since deleted (stripe_price -> NULL via on_delete=SET_NULL) is
        # still a real active subscription and must still be counted here,
        # even though it can't contribute to the revenue sum below.
        active_count = active_subscriptions.count()

        # Monthly Recurring Revenue: the normalized monthly value of
        # currently active subscriptions - a snapshot of "if nothing
        # changes, what should renew next month", not a record of money
        # already collected (that's AdminRevenueTrendView, sourced from
        # real Payment/Invoice rows instead of this projection).
        mrr = 0
        for sub in active_subscriptions:
            if not sub.stripe_price or sub.stripe_price.currency.lower() != 'eur':
                # No price to read (deleted Price), or a currency this
                # summary doesn't convert/aggregate across - skip pricing
                # only, the subscription is still counted in active_count.
                continue
            interval = (sub.stripe_price.interval or '').upper()
            if interval in ('MONTH', 'MONTHLY'):
                mrr += float(sub.stripe_price.unit_amount) * sub.quantity
            elif interval in ('YEAR', 'YEARLY'):
                mrr += (float(sub.stripe_price.unit_amount) / 12) * sub.quantity
            elif interval in ('QUARTER', 'QUARTERLY'):
                mrr += (float(sub.stripe_price.unit_amount) / 3) * sub.quantity
            else:
                mrr += float(sub.stripe_price.unit_amount) * sub.quantity

        return Response({
            'active_candidates': active_candidates,
            'total_users': total_users,
            'b2c_users': b2c_users,
            'b2b_users': b2b_users,
            'active_agencies': active_agencies,
            'total_evaluations': total_evaluations,
            'completed_evaluations': completed_evaluations,
            'monthly_recurring_revenue': round(mrr, 2),
            'active_subscriptions_count': active_count,
            'average_success_rate': self.get_average_success_rate(),
        })
    
    def get_average_success_rate(self):
        completed = Evaluation.objects.filter(
            status=EvaluationStatus.COMPLETED,
            score__isnull=False
        )
        
        if completed.count() == 0:
            return 0
        
        successful = completed.filter(score__gte=70).count()
        return round((successful / completed.count() * 100), 2)


class AdminSystemLoadTrendView(APIView):
    permission_classes = [IsAuthenticated, IsAdminOrSuperAdmin]
    
    def get(self, request):
        days = int(request.query_params.get('days', 30))
        
        start_date = timezone.now() - timedelta(days=days)
        
        evaluations = Evaluation.objects.filter(
            created_at__gte=start_date
        ).annotate(
            date=TruncDate('created_at')
        ).values('date').annotate(
            evaluations_created=Count('id'),
            evaluations_completed=Count('id', filter=Q(status=EvaluationStatus.COMPLETED)),
        ).order_by('date')
        
        candidates = Candidate.objects.filter(
            created_at__gte=start_date
        ).annotate(
            date=TruncDate('created_at')
        ).values('date').annotate(
            candidates_created=Count('id')
        ).order_by('date')
        
        users = User.objects.filter(
            created_at__gte=start_date,
            role=Roles.B2C
        ).annotate(
            date=TruncDate('created_at')
        ).values('date').annotate(
            users_registered=Count('id')
        ).order_by('date')
        
        companies = Company.objects.filter(
            created_at__gte=start_date
        ).annotate(
            date=TruncDate('created_at')
        ).values('date').annotate(
            companies_registered=Count('id')
        ).order_by('date')
        
        eval_dict = {item['date']: item for item in evaluations}
        candidate_dict = {item['date']: item['candidates_created'] for item in candidates}
        user_dict = {item['date']: item['users_registered'] for item in users}
        company_dict = {item['date']: item['companies_registered'] for item in companies}
        
        result = []
        current_date = start_date.date()
        end_date = timezone.now().date()
        
        while current_date <= end_date:
            eval_data = eval_dict.get(current_date, {})
            
            result.append({
                'date': current_date.strftime('%Y-%m-%d'),
                'evaluations_created': eval_data.get('evaluations_created', 0),
                'evaluations_completed': eval_data.get('evaluations_completed', 0),
                'candidates_created': candidate_dict.get(current_date, 0),
                'users_registered': user_dict.get(current_date, 0),
                'companies_registered': company_dict.get(current_date, 0),
            })
            
            current_date += timedelta(days=1)
        
        return Response(result)

class AdminUserGrowthTrendView(APIView):
    permission_classes = [IsAuthenticated, IsAdminOrSuperAdmin]
    
    def get(self, request):
        months = int(request.query_params.get('months', 12))
        
        start_date = timezone.now() - timedelta(days=30 * months)
        
        users = User.objects.filter(
            created_at__gte=start_date,
            role__in=[Roles.B2B, Roles.B2C, Roles.B2B_TEAM_MEMBER]
        ).annotate(
            month=TruncMonth('created_at')
        ).values('month').annotate(
            b2c_count=Count('id', filter=Q(role=Roles.B2C)),
            b2b_count=Count('id', filter=Q(role=Roles.B2B)),
            team_count=Count('id', filter=Q(role=Roles.B2B_TEAM_MEMBER)),
            total=Count('id')
        ).order_by('month')
        
        result = []
        for item in users:
            if item['month']:
                result.append({
                    'month': item['month'].strftime('%Y-%m'),
                    'b2c_users': item['b2c_count'],
                    'b2b_users': item['b2b_count'],
                    'team_members': item['team_count'],
                    'total_users': item['total'],
                })
        
        return Response(result)


class AdminEvaluationTypesDistributionView(APIView):
    permission_classes = [IsAuthenticated, IsAdminOrSuperAdmin]
    
    def get(self, request):
        from api.core.constants import EvaluationType
        
        evaluation_types = Evaluation.objects.values('evaluation_type').annotate(
            count=Count('id')
        ).order_by('-count')
        
        total = Evaluation.objects.count()
        
        type_dict = dict(EvaluationType.CHOICES)
        
        result = []
        for item in evaluation_types:
            type_code = item['evaluation_type']
            if type_code:
                result.append({
                    'type': type_code,
                    'type_display': type_dict.get(type_code, type_code),
                    'count': item['count'],
                    'percentage': round((item['count'] / total * 100), 2) if total > 0 else 0
                })
        
        return Response(result)


class AdminPackageContributionView(APIView):
    permission_classes = [IsAuthenticated, IsAdminOrSuperAdmin]
    
    def get(self, request):
        active_subs = Subscription.objects.filter(
            Q(status__iexact='active') | Q(status__iexact='trialing')
        ).select_related('stripe_price')
        
        package_stats = {}
        
        for sub in active_subs:
            # A subscription whose Price was since deleted (stripe_price ->
            # NULL via on_delete=SET_NULL) is still a real active
            # subscriber - bucket it under a clearly-labeled placeholder
            # instead of silently dropping it from the chart entirely.
            price_name = sub.stripe_price.name if sub.stripe_price else "Unknown Package"
            target_user_type = sub.stripe_price.target_user_type if sub.stripe_price else None
            if price_name not in package_stats:
                package_stats[price_name] = {
                    'package_name': price_name,
                    'subscriber_count': 0,
                    'revenue': 0,
                    'target_user_type': target_user_type,
                }

            package_stats[price_name]['subscriber_count'] += sub.quantity

            if sub.stripe_price and sub.stripe_price.currency.lower() == 'eur':
                interval = (sub.stripe_price.interval or '').upper()
                if interval in ('MONTH', 'MONTHLY'):
                    package_stats[price_name]['revenue'] += float(sub.stripe_price.unit_amount) * sub.quantity
                elif interval in ('YEAR', 'YEARLY'):
                    package_stats[price_name]['revenue'] += (float(sub.stripe_price.unit_amount) / 12) * sub.quantity
                elif interval in ('QUARTER', 'QUARTERLY'):
                    package_stats[price_name]['revenue'] += (float(sub.stripe_price.unit_amount) / 3) * sub.quantity
                else:
                    package_stats[price_name]['revenue'] += float(sub.stripe_price.unit_amount) * sub.quantity
        
        total_subscribers = sum(stat['subscriber_count'] for stat in package_stats.values())
        total_revenue = sum(stat['revenue'] for stat in package_stats.values())
        
        result = []
        for stat in package_stats.values():
            result.append({
                'package_name': stat['package_name'],
                'subscriber_count': stat['subscriber_count'],
                'subscriber_percentage': round((stat['subscriber_count'] / total_subscribers * 100), 2) if total_subscribers > 0 else 0,
                'revenue': round(stat['revenue'], 2),
                'revenue_percentage': round((stat['revenue'] / total_revenue * 100), 2) if total_revenue > 0 else 0,
                'target_user_type': stat['target_user_type'],
            })
        
        result.sort(key=lambda x: x['subscriber_count'], reverse=True)
        
        return Response(result)


class AdminEvaluationStatusDistributionView(APIView):
    permission_classes = [IsAuthenticated, IsAdminOrSuperAdmin]
    
    def get(self, request):
        from api.core.constants import EvaluationStatus
        
        status_counts = Evaluation.objects.values('status').annotate(
            count=Count('id')
        ).order_by('-count')
        
        total = Evaluation.objects.count()
        
        status_dict = dict(EvaluationStatus.CHOICES)
        
        result = []
        for item in status_counts:
            status_code = item['status']
            if status_code:
                result.append({
                    'status': status_code,
                    'status_display': status_dict.get(status_code, status_code),
                    'count': item['count'],
                    'percentage': round((item['count'] / total * 100), 2) if total > 0 else 0
                })
        
        return Response(result)


class AdminReadinessDistributionView(APIView):
    """Platform-wide Overall Readiness Index: every completed evaluation by
    its actual readiness outcome - same calculation as the B2B/B2C ones."""
    permission_classes = [IsAuthenticated, IsAdminOrSuperAdmin]

    def get(self, request):
        return Response(readiness_distribution(Evaluation.objects.all()))


class AdminUserTypeDistributionView(APIView):
    permission_classes = [IsAuthenticated, IsAdminOrSuperAdmin]
    
    def get(self, request):
        user_counts = {
            'B2C': User.objects.filter(role=Roles.B2C).count(),
            'B2B': User.objects.filter(role=Roles.B2B).count(),
            'B2B_TEAM': User.objects.filter(role=Roles.B2B_TEAM_MEMBER).count(),
            'ADMIN': User.objects.filter(role=Roles.ADMIN).count(),
            'SUPERADMIN': User.objects.filter(role=Roles.SUPERADMIN).count(),
        }
        
        total = sum(user_counts.values())
        
        result = []
        for role, count in user_counts.items():
            if count > 0:
                result.append({
                    'role': role,
                    'role_display': dict(Roles.CHOICES).get(role, role),
                    'count': count,
                    'percentage': round((count / total * 100), 2) if total > 0 else 0
                })
        
        return Response(result)


class AdminRevenueTrendView(APIView):
    permission_classes = [IsAuthenticated, IsAdminOrSuperAdmin]
    
    def get(self, request):
        months = int(request.query_params.get('months', 12))
        
        end_date = timezone.now()
        start_date = end_date - timedelta(days=30 * months)
        
        months_data = {}
        current_date = start_date
        while current_date <= end_date:
            month_key = current_date.strftime('%Y-%m')
            months_data[month_key] = {
                'month': month_key,
                'payment_revenue': 0,
                'subscription_revenue': 0,
                'total_revenue': 0,
                'new_subscriptions': 0,
                'payment_count': 0
            }
            next_month = current_date.replace(day=28) + timedelta(days=4)
            current_date = next_month.replace(day=1)
        
        # payment_revenue: one-time (B2C package) purchases only.
        # subscription-linked Payment rows are excluded here because they'd
        # double-count real subscription billing already captured by
        # Invoice below (a subscription's first invoice creates both a
        # Payment, at signup, and later an Invoice, via the
        # invoice.payment_succeeded webhook - see StripeService.
        # handle_invoice_paid/create_subscription).
        payments = Payment.objects.filter(
            status='SUCCEEDED',
            subscription__isnull=True,
            currency__iexact='eur',
            created_at__gte=start_date,
        )

        for payment in payments:
            month_key = payment.created_at.strftime('%Y-%m')
            if month_key in months_data:
                months_data[month_key]['payment_revenue'] += float(payment.amount)
                months_data[month_key]['payment_count'] += 1

        # subscription_revenue: real subscription billing history (initial
        # + renewal invoices), not a retroactive projection - this is what
        # actually happened each month, so a canceled subscription's past
        # invoices still count for the months they were really paid, and a
        # currently-active subscription doesn't get credited for months it
        # never actually renewed in.
        invoices = Invoice.objects.filter(
            status='PAID',
            currency__iexact='eur',
            created_at__gte=start_date,
        )

        for invoice in invoices:
            reference_date = invoice.paid_at or invoice.created_at
            month_key = reference_date.strftime('%Y-%m')
            if month_key in months_data:
                months_data[month_key]['subscription_revenue'] += float(invoice.amount_paid)
                months_data[month_key]['payment_count'] += 1

        # New subscriptions, by creation month, regardless of current
        # status - a subscription created in March and canceled in June
        # should still count as a new subscription in March.
        all_subs_in_window = Subscription.objects.filter(created_at__gte=start_date)
        for sub in all_subs_in_window:
            creation_month = sub.created_at.strftime('%Y-%m')
            if creation_month in months_data:
                months_data[creation_month]['new_subscriptions'] += 1
        
        result = []
        cumulative = 0
        for month_key in sorted(months_data.keys()):
            data = months_data[month_key]
            total = data['payment_revenue'] + data['subscription_revenue']
            cumulative += total
            result.append({
                'month': month_key,
                'payment_revenue': round(data['payment_revenue'], 2),
                'subscription_revenue': round(data['subscription_revenue'], 2),
                'total_revenue': round(total, 2),
                'cumulative_revenue': round(cumulative, 2),
                'new_subscriptions': data['new_subscriptions'],
                'payment_count': data['payment_count']
            })
        
        AuditLogService.log(
            user=request.user,
            action='VIEW_REVENUE_TREND',
            category=AuditLogCategory.BILLING,
            description="Revenue trend viewed",
            data={'months': months, 'total_revenue': cumulative},
            request=request
        )
        
        return Response(result)

class AdminTopPerformingCandidatesView(APIView):
    permission_classes = [IsAuthenticated, IsAdminOrSuperAdmin]
    
    def get(self, request):
        limit = int(request.query_params.get('limit', 10))
        
        from api.scores.models import ScoreSet
        
        top_candidates = ScoreSet.objects.filter(
            average_score__isnull=False
        ).select_related('candidate').order_by('-average_score')[:limit]
        
        result = []
        for score_set in top_candidates:
            candidate = score_set.candidate
            result.append({
                'candidate_id': candidate.id,
                'candidate_name': candidate.get_full_name(),
                'email': candidate.email,
                'job_role': candidate.job_role,
                'average_score': float(score_set.average_score),
                'evaluation_count': Evaluation.objects.filter(candidate=candidate, status=EvaluationStatus.COMPLETED).count(),
                'company_name': candidate.company.name if candidate.company else None,
            })
        
        return Response(result)


class AdminRecentActivityView(APIView):
    permission_classes = [IsAuthenticated, IsAdminOrSuperAdmin]
    
    def get(self, request):
        limit = int(request.query_params.get('limit', 20))
        
        activities = []
        
        new_users = User.objects.filter(
            role__in=[Roles.B2C, Roles.B2B]
        ).order_by('-created_at')[:5]
        
        for user in new_users:
            activities.append({
                'type': 'new_user',
                'description': f"New {user.get_role_display()} registered: {user.get_full_name()}",
                'user': user.get_full_name(),
                'user_email': user.email,
                'timestamp': user.created_at,
                'icon': 'user'
            })
        
        new_companies = Company.objects.order_by('-created_at')[:5]
        
        for company in new_companies:
            activities.append({
                'type': 'new_company',
                'description': f"New company registered: {company.name}",
                'company': company.name,
                'timestamp': company.created_at,
                'icon': 'building'
            })
        
        completed_evals = Evaluation.objects.filter(
            status=EvaluationStatus.COMPLETED
        ).order_by('-completed_at')[:5]
        
        for eval in completed_evals:
            activities.append({
                'type': 'evaluation_completed',
                'description': f"Evaluation completed for {eval.candidate_first_name} {eval.candidate_last_name}",
                'candidate': f"{eval.candidate_first_name} {eval.candidate_last_name}",
                'score': eval.score,
                'timestamp': eval.completed_at or eval.updated_at,
                'icon': 'check-circle'
            })
        
        new_subs = Subscription.objects.order_by('-created_at')[:5]
        
        for sub in new_subs:
            plan_name = sub.stripe_price.name if sub.stripe_price else 'Unknown'
            activities.append({
                'type': 'new_subscription',
                'description': f"New subscription: {plan_name}",
                'user': sub.user.get_full_name(),
                'plan': plan_name,
                'timestamp': sub.created_at,
                'icon': 'credit-card'
            })
        
        activities.sort(key=lambda x: x['timestamp'], reverse=True)
        
        return Response(activities[:limit])


class AdminGeographicDistributionView(APIView):
    permission_classes = [IsAuthenticated, IsAdminOrSuperAdmin]
    
    def get(self, request):
        companies = Company.objects.exclude(country__isnull=True).exclude(country='')
        
        country_counts = {}
        for company in companies:
            country = company.country
            if country in country_counts:
                country_counts[country] += 1
            else:
                country_counts[country] = 1
        
        result = [
            {'country': country, 'count': count}
            for country, count in country_counts.items()
        ]
        result.sort(key=lambda x: x['count'], reverse=True)

        return Response(result)


# -- Candidate Comparison (Admin / Superadmin) --------------------------------
# Same role-based flow as the B2B and B2C dashboards (Select Job Role ->
# Select 2-4 Eligible Candidates -> Compare), with one extra first step:
# pick the account (B2B company or B2C user) whose candidates to compare.
# Comparison never mixes accounts, so every step below takes the selected
# owner_type + owner_id and reuses the same comparison_services functions
# those dashboards use.

def _resolve_admin_comparison_owner(request):
    """(owner_type, owner, error_response) from ?owner_type=&owner_id=."""
    owner_type = request.query_params.get('owner_type', '').strip().upper()
    owner_id = request.query_params.get('owner_id', '').strip()
    if owner_type not in ('COMPANY', 'USER') or not owner_id:
        return None, None, Response({'error': 'owner_type (COMPANY or USER) and owner_id are required'}, status=400)
    owner = resolve_comparison_owner(owner_type, owner_id)
    if owner is None:
        return None, None, Response({'error': 'Account not found'}, status=404)
    return owner_type, owner, None


def _admin_comparison_params(request):
    role_code = request.query_params.get('role_code', '').strip()
    raw_ids = request.query_params.get('candidate_ids', '')
    candidate_ids = [v.strip() for v in raw_ids.split(',') if v.strip()]
    language = "ar" if request.query_params.get('lang') == "ar" else "en"
    return role_code, candidate_ids, language


def _admin_role_name(owner_type, owner, role_code):
    roles = get_comparable_roles(owner_type=owner_type, owner=owner)
    return dict((r['role_code'], r['role_name']) for r in roles).get(role_code, role_code)


def _log_admin_comparison(request, action, description, owner_type, owner, role_code, entries):
    AuditLogService.log(
        user=request.user,
        action=action,
        category=AuditLogCategory.CANDIDATE,
        description=description,
        data={
            'owner_type': owner_type,
            'owner_id': str(owner.public_id),
            'role_code': role_code,
            'candidate_ids': [e['candidate_id'] for e in entries],
        },
        request=request,
    )


class AdminComparisonAccountsView(APIView):
    """Step 0: accounts with at least one scored candidate."""
    permission_classes = [IsAuthenticated, IsAdminOrSuperAdmin]

    def get(self, request):
        return Response(get_comparison_accounts())


class AdminComparisonRolesView(APIView):
    """Step 1: Job Roles with comparable candidates in the chosen account."""
    permission_classes = [IsAuthenticated, IsAdminOrSuperAdmin]

    def get(self, request):
        owner_type, owner, error = _resolve_admin_comparison_owner(request)
        if error:
            return error
        return Response(get_comparable_roles(owner_type=owner_type, owner=owner))


class AdminComparisonEligibleCandidatesView(APIView):
    """Step 2: candidates eligible for comparison under a chosen role."""
    permission_classes = [IsAuthenticated, IsAdminOrSuperAdmin]

    def get(self, request):
        owner_type, owner, error = _resolve_admin_comparison_owner(request)
        if error:
            return error
        role_code = request.query_params.get('role_code', '').strip()
        if not role_code:
            return Response({'error': 'role_code is required'}, status=400)
        return Response(get_eligible_candidates(owner_type=owner_type, owner=owner, role_code=role_code))


class AdminComparisonFullView(APIView):
    """Steps 3+4: Candidate Summary + Competency Comparison for 2-4
    selected, role-eligible candidates of the chosen account. Logged, since
    it is an admin viewing a customer's candidate results."""
    permission_classes = [IsAuthenticated, IsAdminOrSuperAdmin]

    def get(self, request):
        owner_type, owner, error = _resolve_admin_comparison_owner(request)
        if error:
            return error
        role_code, candidate_ids, language = _admin_comparison_params(request)
        if not role_code or not (2 <= len(candidate_ids) <= 4):
            return Response({'error': 'role_code and 2-4 candidate_ids are required'}, status=400)

        entries = build_full_comparison(
            owner_type=owner_type, owner=owner, role_code=role_code,
            candidate_ids=candidate_ids, language=language, actor=request.user,
        )
        _log_admin_comparison(request, 'VIEW_CANDIDATE_COMPARISON', "Candidate comparison viewed by admin",
                              owner_type, owner, role_code, entries)
        return Response({
            'role_code': role_code,
            'role_name': _admin_role_name(owner_type, owner, role_code),
            'candidates': entries,
            'key_differences': compute_key_differences(entries, language=language),
        })


class AdminComparisonPdfView(APIView):
    """Bilingual Comparison PDF, same document as the B2B/B2C download."""
    permission_classes = [IsAuthenticated, IsAdminOrSuperAdmin]

    def get(self, request):
        owner_type, owner, error = _resolve_admin_comparison_owner(request)
        if error:
            return error
        role_code, candidate_ids, language = _admin_comparison_params(request)
        if not role_code or not (2 <= len(candidate_ids) <= 4):
            return Response({'error': 'role_code and 2-4 candidate_ids are required'}, status=400)

        entries = build_full_comparison(
            owner_type=owner_type, owner=owner, role_code=role_code,
            candidate_ids=candidate_ids, language=language, actor=request.user,
        )
        if len(entries) < 2:
            return Response({'error': 'At least 2 comparable candidates are required.'}, status=400)

        _log_admin_comparison(request, 'EXPORT_CANDIDATE_COMPARISON', "Candidate comparison PDF exported by admin",
                              owner_type, owner, role_code, entries)
        key_differences = compute_key_differences(entries, language=language)
        pdf_bytes = render_comparison_pdf(
            role_name=_admin_role_name(owner_type, owner, role_code),
            entries=entries, key_differences=key_differences, language=language,
        )
        response = HttpResponse(pdf_bytes, content_type='application/pdf')
        response['Content-Disposition'] = 'attachment; filename="candidate-comparison.pdf"'
        return response