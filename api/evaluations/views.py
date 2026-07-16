from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.db.models import Q
from django.utils import timezone
from api.audit.services import AuditLogService
from api.payments.mixins import SubscriptionUsageMixin
from .utils import (
    send_evaluation_scheduled_email,
    send_evaluation_rescheduled_email,
    send_evaluation_cancelled_email,
    send_evaluation_completed_email
)
from .models import Evaluation
from .serializers import (
    CompetencyEvaluationResultSerializer,
    EvaluationReadinessDecisionRecordSerializer,
    EvaluationSerializer,
    EvaluationCreateSerializer,
    EvaluationUpdateSerializer,
    EvaluationListSerializer,
    EvaluationCompleteSerializer,
    EvaluationRescheduleSerializer,
    EvaluationCancelSerializer,
    ResponseEvaluationResultSerializer,
    ScoringRuleSetSerializer,
    SessionEvaluationSummarySerializer,
)
from .permissions import CanManageEvaluation, CanViewEvaluation
from api.core.constants import Roles, EvaluationStatus
from api.core.constants import AuditLogCategory, AuditLogAction, AuditLogSeverity
from api.core.public_ids import PublicIdLookupMixin, filter_by_identifier, get_by_identifier
from .models import EvaluationReadinessDecisionRecord, ScoringRuleSet
from .scoring_services import Week6ScoringError, Week6ScoringService


class EvaluationViewSet(SubscriptionUsageMixin, PublicIdLookupMixin, viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = EvaluationSerializer
    
    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Evaluation.objects.none()

        user = self.request.user
        queryset = Evaluation.objects.all()
        
        status = self.request.query_params.get('status')
        if status:
            queryset = queryset.filter(status=status)
        
        evaluation_type = self.request.query_params.get('evaluation_type')
        if evaluation_type:
            queryset = queryset.filter(evaluation_type=evaluation_type)
        
        candidate_id = self.request.query_params.get('candidate_id')
        if candidate_id:
            queryset = filter_by_identifier(queryset, "candidate", candidate_id)
        
        from_date = self.request.query_params.get('from_date')
        if from_date:
            queryset = queryset.filter(scheduled_date__gte=from_date)
        
        to_date = self.request.query_params.get('to_date')
        if to_date:
            queryset = queryset.filter(scheduled_date__lte=to_date)
        
        if user.role in [Roles.ADMIN, Roles.SUPERADMIN]:
            return queryset
        
        if user.role == Roles.B2C:
            return queryset.filter(created_by=user)
        
        if user.role == Roles.B2B:
            if hasattr(user, 'company_profile'):
                return queryset.filter(company=user.company_profile.company)
            return queryset.none()
        
        if user.role == Roles.B2B_TEAM_MEMBER:
            return queryset.filter(
                Q(created_by=user) |
                Q(candidate__shared_with=user)
            ).distinct()
        
        return queryset.none()
    
    def get_serializer_class(self):
        if self.action == 'create':
            return EvaluationCreateSerializer
        elif self.action in ['update', 'partial_update']:
            return EvaluationUpdateSerializer
        elif self.action == 'list':
            return EvaluationListSerializer
        return EvaluationSerializer
    
    def get_permissions(self):
        if self.action in ['create', 'update', 'partial_update', 'destroy']:
            self.permission_classes = [IsAuthenticated, CanManageEvaluation]
        elif self.action in ['list', 'retrieve']:
            self.permission_classes = [IsAuthenticated, CanViewEvaluation]
        elif self.action in ['complete', 'reschedule', 'cancel']:
            self.permission_classes = [IsAuthenticated, CanManageEvaluation]
        
        return super().get_permissions()
    
    def perform_create(self, serializer):
        if not self.check_subscription_limit(self.request.user, 'evaluation_limit'):
            raise PermissionDenied(
                "You've reached the evaluation limit for your current package. "
                "Upgrade your plan to schedule more evaluations."
            )

        evaluation = serializer.save()
        self.increment_subscription_usage(self.request.user, 'evaluation_limit')

        AuditLogService.log(
            user=self.request.user,
            action=AuditLogAction.EVALUATION_CREATED,
            category=AuditLogCategory.EVALUATION,
            description=f"Evaluation created for candidate: {evaluation.candidate_first_name} {evaluation.candidate_last_name}",
            resource=evaluation,
            data={
                'candidate_name': f"{evaluation.candidate_first_name} {evaluation.candidate_last_name}",
                'candidate_email': evaluation.candidate_email,
                'evaluation_type': evaluation.evaluation_type,
                'scheduled_date': evaluation.scheduled_date.isoformat() if evaluation.scheduled_date else None,
                'created_by': self.request.user.email
            },
            request=self.request
        )
        
        send_evaluation_scheduled_email(evaluation, self.request)
    
    def perform_update(self, serializer):
        old_evaluation = self.get_object()
        old_data = {
            'evaluation_type': old_evaluation.evaluation_type,
            'scheduled_date': old_evaluation.scheduled_date,
            'duration_minutes': old_evaluation.duration_minutes,
            'status': old_evaluation.status
        }
        
        evaluation = serializer.save()
        
        changes = {}
        new_data = {
            'evaluation_type': evaluation.evaluation_type,
            'scheduled_date': evaluation.scheduled_date,
            'duration_minutes': evaluation.duration_minutes,
            'status': evaluation.status
        }
        
        for field, new_value in new_data.items():
            old_value = old_data.get(field)
            if old_value != new_value:
                if isinstance(old_value, timezone.datetime):
                    old_value = old_value.isoformat() if old_value else None
                if isinstance(new_value, timezone.datetime):
                    new_value = new_value.isoformat() if new_value else None
                changes[field] = {
                    'old': str(old_value) if old_value else None,
                    'new': str(new_value) if new_value else None
                }
        
        if changes:
            AuditLogService.log(
                user=self.request.user,
                action=AuditLogAction.EVALUATION_UPDATED,
                category=AuditLogCategory.EVALUATION,
                description=f"Evaluation updated for candidate: {evaluation.candidate_first_name} {evaluation.candidate_last_name}",
                resource=evaluation,
                data={
                    'candidate_name': f"{evaluation.candidate_first_name} {evaluation.candidate_last_name}",
                    'changes': changes,
                    'updated_by': self.request.user.email
                },
                request=self.request
            )
    
    def perform_destroy(self, instance):
        evaluation_data = {
            'id': instance.id,
            'candidate_name': f"{instance.candidate_first_name} {instance.candidate_last_name}",
            'candidate_email': instance.candidate_email,
            'evaluation_type': instance.evaluation_type,
            'status': instance.status
        }
        
        AuditLogService.log(
            user=self.request.user,
            action='EVALUATION_DELETED',
            category=AuditLogCategory.EVALUATION,
            description=f"Evaluation deleted for candidate: {instance.candidate_first_name} {instance.candidate_last_name}",
            data={
                **evaluation_data,
                'deleted_by': self.request.user.email
            },
            request=self.request,
            severity=AuditLogSeverity.WARNING
        )

        instance.delete()
        self.decrement_subscription_usage(self.request.user, 'evaluation_limit')

    def retrieve(self, request, *args, **kwargs):
        response = super().retrieve(request, *args, **kwargs)
        
        evaluation = self.get_object()
        AuditLogService.log(
            user=request.user,
            action='VIEW_EVALUATION',
            category=AuditLogCategory.EVALUATION,
            description=f"Evaluation details viewed for candidate: {evaluation.candidate_first_name} {evaluation.candidate_last_name}",
            resource=evaluation,
            data={
                'candidate_name': f"{evaluation.candidate_first_name} {evaluation.candidate_last_name}",
                'evaluation_type': evaluation.evaluation_type,
                'status': evaluation.status
            },
            request=request
        )
        
        return response
    
    def list(self, request, *args, **kwargs):
        response = super().list(request, *args, **kwargs)
        
        AuditLogService.log(
            user=request.user,
            action='VIEW_EVALUATIONS_LIST',
            category=AuditLogCategory.EVALUATION,
            description="Evaluation list viewed",
            data={'count': len(response.data) if hasattr(response, 'data') else 0},
            request=request
        )
        
        return response
    
    @action(detail=True, methods=['post'])
    def complete(self, request, id=None):
        evaluation = self.get_object()
        
        if evaluation.status not in [EvaluationStatus.SCHEDULED, EvaluationStatus.RESCHEDULED]:
            return Response(
                {'error': f'Cannot complete evaluation with status {evaluation.status}'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        serializer = EvaluationCompleteSerializer(data=request.data)
        if serializer.is_valid():
            if not evaluation.certificate_enabled and serializer.validated_data.get('certificate_status') == 'ISSUED':
                return Response(
                    {'error': 'Certificate issuance is disabled for this evaluation tier'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            old_status = evaluation.status
            evaluation.complete(
                score=serializer.validated_data.get('score'),
                feedback=serializer.validated_data.get('feedback')
            )

            if not evaluation.readiness_indicator_enabled:
                evaluation.readiness_status = 'PENDING'
                evaluation.readiness_override_applied = False
                evaluation.readiness_override_reason = ''
            
            certificate_info = {}
            if 'certificate_status' in serializer.validated_data:
                requested_certificate_status = serializer.validated_data['certificate_status']
                evaluation.certificate_status = requested_certificate_status if evaluation.certificate_enabled else 'NOT_ISSUED'
                if evaluation.certificate_status == 'ISSUED':
                    evaluation.certificate_issued_at = timezone.now()
                    evaluation.certificate_url = serializer.validated_data.get('certificate_url', '')
                    certificate_info = {
                        'certificate_issued': True,
                        'certificate_url': evaluation.certificate_url
                    }
                evaluation.save()
            
            AuditLogService.log(
                user=request.user,
                action=AuditLogAction.EVALUATION_COMPLETED,
                category=AuditLogCategory.EVALUATION,
                description=f"Evaluation completed for candidate: {evaluation.candidate_first_name} {evaluation.candidate_last_name}",
                resource=evaluation,
                data={
                    'candidate_name': f"{evaluation.candidate_first_name} {evaluation.candidate_last_name}",
                    'old_status': old_status,
                    'new_status': evaluation.status,
                    'score': float(evaluation.score) if evaluation.score else None,
                    'certificate_status': evaluation.certificate_status,
                    **certificate_info,
                    'completed_by': request.user.email
                },
                request=request
            )
            
            send_evaluation_completed_email(evaluation, request)
            
            return Response(EvaluationSerializer(evaluation).data)
        
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=['post'], url_path='run-scoring')
    def run_scoring(self, request, id=None):
        evaluation = self.get_object()
        rule_set_id = request.data.get("rule_set_id")
        rule_set = None
        try:
            if rule_set_id:
                rule_set = get_by_identifier(ScoringRuleSet.objects.all(), rule_set_id)
            summary = Week6ScoringService.run_for_evaluation(
                evaluation=evaluation,
                actor=request.user,
                rule_set=rule_set,
            )
        except (ScoringRuleSet.DoesNotExist, Week6ScoringError) as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        serializer = SessionEvaluationSummarySerializer(summary)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @action(detail=True, methods=['get'], url_path='scoring-summary')
    def scoring_summary(self, request, id=None):
        evaluation = self.get_object()
        summary = evaluation.session_summaries.select_related("rule_set").first()
        if summary is None:
            return Response({"detail": "No scoring summary has been generated yet."}, status=status.HTTP_404_NOT_FOUND)
        return Response(SessionEvaluationSummarySerializer(summary).data)

    @action(detail=True, methods=['get'], url_path='response-results')
    def response_results(self, request, id=None):
        evaluation = self.get_object()
        results = evaluation.response_results.select_related("rule_set", "question", "response").all()
        return Response(ResponseEvaluationResultSerializer(results, many=True).data)

    @action(detail=True, methods=['get'], url_path='competency-results')
    def competency_results(self, request, id=None):
        evaluation = self.get_object()
        results = evaluation.competency_results.select_related("rule_set").all()
        return Response(CompetencyEvaluationResultSerializer(results, many=True).data)

    @action(detail=True, methods=['get'], url_path='readiness-legal-record')
    def readiness_legal_record(self, request, id=None):
        evaluation = self.get_object()
        try:
            record = evaluation.readiness_legal_record
        except EvaluationReadinessDecisionRecord.DoesNotExist:
            record = None
        if record is None:
            return Response(
                {"detail": "No readiness legal record has been generated yet."},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(EvaluationReadinessDecisionRecordSerializer(record).data)

    @action(detail=False, methods=['get'])
    def upcoming(self, request):
        queryset = self.get_queryset().filter(
            status__in=[EvaluationStatus.SCHEDULED, EvaluationStatus.RESCHEDULED],
            scheduled_date__gte=timezone.now()
        ).order_by('scheduled_date')[:20]
        
        AuditLogService.log(
            user=request.user,
            action='VIEW_UPCOMING_EVALUATIONS',
            category=AuditLogCategory.EVALUATION,
            description="Viewed upcoming evaluations",
            data={'count': queryset.count()},
            request=request
        )
        
        serializer = EvaluationListSerializer(queryset, many=True)
        return Response(serializer.data)
    
    @action(detail=False, methods=['get'])
    def past(self, request):
        queryset = self.get_queryset().filter(
            scheduled_date__lt=timezone.now()
        ).order_by('-scheduled_date')[:50]
        
        AuditLogService.log(
            user=request.user,
            action='VIEW_PAST_EVALUATIONS',
            category=AuditLogCategory.EVALUATION,
            description="Viewed past evaluations",
            data={'count': queryset.count()},
            request=request
        )
        
        serializer = EvaluationListSerializer(queryset, many=True)
        return Response(serializer.data)
    
    @action(detail=False, methods=['get'])
    def my_evaluations(self, request):
        queryset = self.get_queryset().filter(created_by=request.user)
        
        AuditLogService.log(
            user=request.user,
            action='VIEW_MY_EVALUATIONS',
            category=AuditLogCategory.EVALUATION,
            description="Viewed evaluations created by user",
            data={'count': queryset.count()},
            request=request
        )
        
        serializer = EvaluationListSerializer(queryset, many=True)
        return Response(serializer.data)
    
    @action(detail=True, methods=['post'])
    def reschedule(self, request, id=None):
        evaluation = self.get_object()
        
        if evaluation.status not in [EvaluationStatus.SCHEDULED, EvaluationStatus.RESCHEDULED]:
            return Response(
                {'error': f'Cannot reschedule evaluation with status {evaluation.status}'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        serializer = EvaluationRescheduleSerializer(data=request.data)
        if serializer.is_valid():
            old_date = evaluation.scheduled_date
            new_date = serializer.validated_data['new_date']
            reason = serializer.validated_data.get('reason', '')
            
            evaluation.reschedule(new_date)
            
            AuditLogService.log(
                user=request.user,
                action=AuditLogAction.EVALUATION_RESCHEDULED,
                category=AuditLogCategory.EVALUATION,
                description=f"Evaluation rescheduled for candidate: {evaluation.candidate_first_name} {evaluation.candidate_last_name}",
                resource=evaluation,
                data={
                    'candidate_name': f"{evaluation.candidate_first_name} {evaluation.candidate_last_name}",
                    'old_date': old_date.isoformat() if old_date else None,
                    'new_date': new_date.isoformat() if new_date else None,
                    'reason': reason,
                    'rescheduled_by': request.user.email
                },
                request=request
            )
            
            send_evaluation_rescheduled_email(evaluation, old_date, request)
            
            return Response({
                'message': 'Evaluation rescheduled successfully',
                'old_date': old_date,
                'new_date': evaluation.scheduled_date,
                'evaluation': EvaluationSerializer(evaluation).data
            })
        
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    
    @action(detail=True, methods=['post'])
    def cancel(self, request, id=None):
        evaluation = self.get_object()
        
        if evaluation.status in [EvaluationStatus.COMPLETED, EvaluationStatus.CANCELLED]:
            return Response(
                {'error': f'Cannot cancel evaluation with status {evaluation.status}'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        serializer = EvaluationCancelSerializer(data=request.data)
        if serializer.is_valid():
            old_status = evaluation.status
            reason = serializer.validated_data.get('reason', '')
            
            evaluation.cancel(reason=reason)
            
            AuditLogService.log(
                user=request.user,
                action=AuditLogAction.EVALUATION_CANCELLED,
                category=AuditLogCategory.EVALUATION,
                description=f"Evaluation cancelled for candidate: {evaluation.candidate_first_name} {evaluation.candidate_last_name}",
                resource=evaluation,
                data={
                    'candidate_name': f"{evaluation.candidate_first_name} {evaluation.candidate_last_name}",
                    'old_status': old_status,
                    'new_status': evaluation.status,
                    'reason': reason,
                    'cancelled_by': request.user.email
                },
                request=request,
                severity=AuditLogSeverity.WARNING if reason else AuditLogSeverity.INFO
            )
            
            send_evaluation_cancelled_email(evaluation, request)
            
            return Response({
                'message': 'Evaluation cancelled successfully',
                'evaluation': EvaluationSerializer(evaluation).data
            })
        
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class ScoringRuleSetViewSet(PublicIdLookupMixin, viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = ScoringRuleSetSerializer

    def get_queryset(self):
        queryset = ScoringRuleSet.objects.prefetch_related("rules").all()
        role_code = self.request.query_params.get("role_code")
        if role_code:
            queryset = queryset.filter(role_code=role_code)
        evaluation_tier = self.request.query_params.get("evaluation_tier")
        if evaluation_tier:
            queryset = queryset.filter(evaluation_tier=evaluation_tier)
        is_active = self.request.query_params.get("is_active")
        if is_active in {"true", "false"}:
            queryset = queryset.filter(is_active=is_active == "true")
        return queryset

    def create(self, request, *args, **kwargs):
        if request.user.role not in [Roles.ADMIN, Roles.SUPERADMIN, Roles.B2B, Roles.B2C]:
            raise PermissionDenied("You do not have permission to create scoring rule sets")
        return super().create(request, *args, **kwargs)

    def update(self, request, *args, **kwargs):
        if request.user.role not in [Roles.ADMIN, Roles.SUPERADMIN, Roles.B2B, Roles.B2C]:
            raise PermissionDenied("You do not have permission to update scoring rule sets")
        return super().update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        if request.user.role not in [Roles.ADMIN, Roles.SUPERADMIN]:
            raise PermissionDenied("Only admins can delete scoring rule sets")
        return super().destroy(request, *args, **kwargs)
    
    @action(detail=False, methods=['get'])
    def for_candidate(self, request):
        candidate_id = request.query_params.get('candidate_id')
        if not candidate_id:
            return Response(
                {'error': 'candidate_id parameter is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        queryset = filter_by_identifier(self.get_queryset(), "candidate", candidate_id)
        
        AuditLogService.log(
            user=request.user,
            action='VIEW_CANDIDATE_EVALUATIONS',
            category=AuditLogCategory.EVALUATION,
            description=f"Viewed evaluations for candidate ID: {candidate_id}",
            data={'candidate_id': candidate_id, 'count': queryset.count()},
            request=request
        )
        
        serializer = EvaluationListSerializer(queryset, many=True)
        return Response(serializer.data)
