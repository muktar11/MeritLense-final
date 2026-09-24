import logging

from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.db.models import Q
from drf_spectacular.utils import OpenApiExample, extend_schema, extend_schema_view

from api.audit.services import AuditLogService
from api.payments.entitlement_services import EntitlementService
from api.payments.mixins import SubscriptionUsageMixin
from api.payments.notifications import send_certificate_reused_email
from .certificate_reuse_services import certificate_preview, find_reusable_certificate, grant_certificate_access
from .models import Candidate
from .serializers import (
    CandidateSerializer,
    CandidateCreateSerializer,
    CandidateUpdateSerializer,
    CandidateShareSerializer
)
from .permissions import CanManageCandidate, CanViewCandidate
from api.core.permisssions import IsCompanyApproved
from api.core.constants import Roles
from api.core.constants import AuditLogCategory, AuditLogAction
from api.accounts.models import User

logger = logging.getLogger(__name__)


@extend_schema_view(
    create=extend_schema(
        summary="Create a candidate",
        description="Create a candidate with a required passport/ID document upload.",
        request={'multipart/form-data': CandidateCreateSerializer},
        responses={201: CandidateSerializer},
        examples=[
            OpenApiExample(
                "Candidate create example",
                summary="Candidate with document upload",
                description="Use multipart/form-data in Swagger and attach a real file for passport_document.",
                value={
                    "first_name": "Jane",
                    "last_name": "Doe",
                    "email": "jane.doe@example.com",
                    "passport_id": "PASS-1001",
                    "job_role": "NA",
                    "core_skills": "communication, patience",
                    "preferred_language": "EN",
                    "passport_document": "(binary file)",
                    "profile_photo": "(binary file, optional)",
                    "verification_photo": "(binary file, optional)",
                },
                request_only=True,
            ),
        ],
    ),
    update=extend_schema(
        request={'multipart/form-data': CandidateUpdateSerializer},
        responses={200: CandidateSerializer},
    ),
    partial_update=extend_schema(
        request={'multipart/form-data': CandidateUpdateSerializer},
        responses={200: CandidateSerializer},
    ),
)
class CandidateViewSet(SubscriptionUsageMixin, viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = CandidateSerializer
    parser_classes = [MultiPartParser, FormParser, JSONParser]
    lookup_field = "public_id"
    lookup_url_kwarg = "id"
    lookup_value_regex = "[0-9a-fA-F-]{36}"
    
    def get_queryset(self):
        if getattr(self, 'swagger_fake_view', False):
            return Candidate.objects.none()

        user = self.request.user
        
        if user.role in [Roles.ADMIN, Roles.SUPERADMIN]:
            return Candidate.objects.all()
        
        if user.role == Roles.B2C:
            return Candidate.objects.filter(created_by=user)
        
        if user.role in [Roles.B2B, Roles.B2B_TEAM_MEMBER]:
            company = None
            if user.role == Roles.B2B and hasattr(user, 'company_profile'):
                company = user.company_profile.company
            elif user.role == Roles.B2B_TEAM_MEMBER and hasattr(user, 'team_member_profile'):
                company = user.team_member_profile.company
            
            if company:
                queryset = Candidate.objects.filter(company=company)
                
                if user.role == Roles.B2B_TEAM_MEMBER:
                    queryset = queryset.filter(
                        Q(created_by=user) | Q(shared_with=user)
                    ).distinct()
                
                return queryset
        
        return Candidate.objects.none()
        
    def get_permissions(self):
        if self.action in ['create', 'update', 'partial_update', 'destroy', 'reuse_certificate']:
            self.permission_classes = [IsAuthenticated, CanManageCandidate, IsCompanyApproved]
        elif self.action in ['list', 'retrieve']:
            self.permission_classes = [IsAuthenticated, CanViewCandidate]
        elif self.action == 'share':
            self.permission_classes = [IsAuthenticated]
        
        return super().get_permissions()
    
    def get_serializer_class(self):
        if self.action == 'create':
            return CandidateCreateSerializer
        elif self.action in ['update', 'partial_update']:
            return CandidateUpdateSerializer
        elif self.action in ['share', 'unshare']:
            return CandidateShareSerializer
        return CandidateSerializer

    def _can_manage_sharing(self, user, candidate):
        if user.role in [Roles.ADMIN, Roles.SUPERADMIN]:
            return True
        if candidate.created_by == user:
            return True
        if hasattr(user, 'managed_company') and candidate.company == user.managed_company:
            return True
        if hasattr(user, 'company_profile') and candidate.company == user.company_profile.company:
            return True
        return False
    
    def create(self, request, *args, **kwargs):
        # Checked here, before serializer validation/file uploads run, both
        # to avoid wasted work on a request we already know will be
        # rejected, and so the response carries a machine-readable `code`
        # the frontend can act on (offer to buy a package) without having
        # to string-match a message that may one day be localized.
        if not self.check_subscription_limit(request.user, 'candidate_limit'):
            return Response(
                {
                    'detail': "You've reached the candidate limit for your current package. "
                              "Upgrade your plan to add more candidates.",
                    'code': 'candidate_limit_reached',
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        # passport_id is globally unique (Candidate.passport_id), not
        # scoped per company - CandidateCreateSerializer.validate only
        # checks the requester's OWN scope, so a passport_id already used
        # by a DIFFERENT account would otherwise reach serializer.save()
        # and blow up on the DB's unique constraint. Caught here, before
        # that, so it can offer the real alternative (reuse that
        # candidate's existing certificate) instead of a raw 500.
        passport_id = (request.data or {}).get('passport_id')
        if passport_id:
            owner_type, owner = EntitlementService.resolve_owner_for_user(request.user)
            requesting_company = owner if owner_type == 'COMPANY' else None
            existing_candidate, certificate = find_reusable_certificate(
                passport_id=passport_id,
                requesting_company=requesting_company,
                requesting_user=request.user,
            )
            if certificate is not None:
                return Response(
                    {
                        'detail': "A candidate with this passport ID already has an issued certificate "
                                  "from a previous evaluation. You can reuse it instead of running a new "
                                  "interview - this will deduct 1 Assessment Slot from your package.",
                        'code': 'candidate_certificate_available',
                        'certificate': certificate_preview(certificate),
                    },
                    status=status.HTTP_409_CONFLICT,
                )
            if existing_candidate is not None:
                return Response(
                    {
                        'detail': "A candidate with this passport ID already exists under a different "
                                  "account and has no certificate available to reuse yet.",
                        'code': 'passport_id_taken_elsewhere',
                    },
                    status=status.HTTP_409_CONFLICT,
                )

        return super().create(request, *args, **kwargs)

    @action(detail=False, methods=['post'], url_path='reuse-certificate')
    def reuse_certificate(self, request):
        """Confirms reusing a DIFFERENT account's existing certificate
        (offered by create() above via the candidate_certificate_available
        response) instead of adding a new candidate - deducts 1 Assessment
        Slot and records a CertificateAccessGrant. Re-derives the
        certificate from passport_id server-side rather than trusting a
        client-supplied certificate id, so a stale/tampered id can't be
        used to draw a Slot for the wrong certificate."""
        passport_id = (request.data or {}).get('passport_id')
        if not passport_id:
            return Response({'detail': "passport_id is required."}, status=status.HTTP_400_BAD_REQUEST)

        owner_type, owner = EntitlementService.resolve_owner_for_user(request.user)
        requesting_company = owner if owner_type == 'COMPANY' else None
        existing_candidate, certificate = find_reusable_certificate(
            passport_id=passport_id,
            requesting_company=requesting_company,
            requesting_user=request.user,
        )
        if certificate is None:
            return Response(
                {'detail': "No reusable certificate was found for this passport ID."},
                status=status.HTTP_404_NOT_FOUND,
            )

        reference = f"cert-reuse:{certificate.public_id}:{owner_type}:{owner.pk}"
        try:
            balance = EntitlementService.consume_slot_for_certificate_reuse(
                owner_type=owner_type, owner=owner, reference=reference, actor=request.user,
            )
        except ValueError as exc:
            return Response(
                {'detail': str(exc), 'code': 'no_slots_available'},
                status=status.HTTP_402_PAYMENT_REQUIRED,
            )

        grant_certificate_access(
            certificate=certificate,
            requesting_user=request.user,
            owner_type=owner_type,
            owner=owner,
            reference=reference,
        )

        AuditLogService.log(
            user=request.user,
            action=AuditLogAction.CANDIDATE_CREATED,
            category=AuditLogCategory.CANDIDATE,
            description=f"Reused existing certificate for candidate: {certificate.candidate.get_full_name()}",
            resource=certificate.candidate,
            data={
                'certificate_id': certificate.certificate_id,
                'candidate_name': certificate.candidate.get_full_name(),
                'slot_reference': reference,
            },
        )

        try:
            send_certificate_reused_email(
                candidate=certificate.candidate,
                requested_by=request.user,
                company=requesting_company,
                remaining=balance.current_balance if balance else 0,
            )
        except Exception:
            logger.exception("Failed to send certificate-reused notification for %s", reference)

        return Response({
            'certificate_id': certificate.certificate_id,
            'pdf_url': request.build_absolute_uri(certificate.pdf_file.url) if certificate.pdf_file else None,
            'issued_at': certificate.issued_at,
            'candidate_name': certificate.candidate.get_full_name(),
            'job_role': certificate.evaluation.get_candidate_job_role_display(),
            'slots_remaining': balance.current_balance if balance else None,
        })

    def perform_create(self, serializer):
        candidate = serializer.save()
        self.increment_subscription_usage(self.request.user, 'candidate_limit')

        AuditLogService.log(
            user=self.request.user,
            action=AuditLogAction.CANDIDATE_CREATED,
            category=AuditLogCategory.CANDIDATE,
            description=f"Candidate created: {candidate.get_full_name()}",
            resource=candidate,
            data={
                'candidate_name': candidate.get_full_name(),
                'email': candidate.email,
                'job_role': candidate.job_role
            },
            request=self.request
        )
    
    def perform_update(self, serializer):
        old_candidate = self.get_object()
        old_data = {
            'first_name': old_candidate.first_name,
            'last_name': old_candidate.last_name,
            'email': old_candidate.email,
            'job_role': old_candidate.job_role,
            'status': old_candidate.status
        }
        
        candidate = serializer.save()
        
        changes = {}
        new_data = {
            'first_name': candidate.first_name,
            'last_name': candidate.last_name,
            'email': candidate.email,
            'job_role': candidate.job_role,
            'status': candidate.status
        }
        
        for field, new_value in new_data.items():
            if old_data.get(field) != new_value:
                changes[field] = {
                    'old': old_data.get(field),
                    'new': new_value
                }
        
        AuditLogService.log(
            user=self.request.user,
            action=AuditLogAction.CANDIDATE_UPDATED,
            category=AuditLogCategory.CANDIDATE,
            description=f"Candidate updated: {candidate.get_full_name()}",
            resource=candidate,
            data={'changes': changes},
            request=self.request
        )
    
    def perform_destroy(self, instance):
        candidate_name = instance.get_full_name()
        candidate_id = instance.id
        candidate_email = instance.email
        
        AuditLogService.log(
            user=self.request.user,
            action=AuditLogAction.CANDIDATE_DELETED,
            category=AuditLogCategory.CANDIDATE,
            description=f"Candidate deleted: {candidate_name}",
            data={
                'candidate_id': candidate_id,
                'candidate_name': candidate_name,
                'candidate_email': candidate_email,
                'deleted_by': self.request.user.email
            },
            request=self.request
        )

        instance.delete()
        self.decrement_subscription_usage(self.request.user, 'candidate_limit')

    @action(detail=True, methods=['post'])
    def share(self, request, id=None):
        candidate = self.get_object()
        
        user = request.user
        if not self._can_manage_sharing(user, candidate):
            return Response(
                {'error': 'You do not have permission to share this candidate.'},
                status=status.HTTP_403_FORBIDDEN
            )
        
        raw_user_ids = request.data.get('user_ids', [])
        if isinstance(raw_user_ids, (int, str)):
            raw_user_ids = [raw_user_ids]
        serializer = self.get_serializer(
            data={'user_ids': raw_user_ids},
            context={'request': request, 'candidate': candidate}
        )
        serializer.is_valid(raise_exception=True)
        serializer.validated_data['user_ids']
        
        company = candidate.company
        if not company:
            return Response(
                {'error': 'Could not determine company'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        valid_team_profiles = serializer.context.get('team_profiles', [])
        
        users_to_share = [profile.user for profile in valid_team_profiles]

        if not users_to_share:
            return Response({
                'error': 'No valid team members found to share with'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        candidate.shared_with.add(*users_to_share)
        
        AuditLogService.log(
            user=request.user,
            action=AuditLogAction.CANDIDATE_SHARED,
            category=AuditLogCategory.CANDIDATE,
            description=f"Candidate shared: {candidate.get_full_name()} with {len(users_to_share)} team member(s)",
            resource=candidate,
            data={
                'shared_with': [
                    {
                        'team_member_profile_id': profile.id,
                        'user_id': profile.user.id,
                        'name': profile.user.get_full_name(), 
                        'email': profile.user.email
                    } for profile in valid_team_profiles
                ],
                'shared_by': request.user.email
            },
            request=request
        )
        
        response_data = {
            'message': f'Candidate shared with {len(users_to_share)} team member(s).',
            'shared_with': [
                {
                    'team_member_profile_id': profile.id,
                    'user_id': profile.user.id,
                    'name': profile.user.get_full_name(),
                    'email': profile.user.email
                } for profile in valid_team_profiles
            ]
        }

        return Response(response_data)


    @action(detail=True, methods=['post'])
    def unshare(self, request, id=None):
        candidate = self.get_object()
        
        user = request.user
        if not self._can_manage_sharing(user, candidate):
            return Response(
                {'error': 'You do not have permission to modify sharing for this candidate.'},
                status=status.HTTP_403_FORBIDDEN
            )
        
        raw_user_ids = request.data.get('user_ids', [])
        if isinstance(raw_user_ids, (int, str)):
            raw_user_ids = [raw_user_ids]
        serializer = self.get_serializer(
            data={'user_ids': raw_user_ids},
            context={'request': request, 'candidate': candidate}
        )
        serializer.is_valid(raise_exception=True)
        serializer.validated_data['user_ids']
        
        company = candidate.company
        if not company:
            return Response(
                {'error': 'Could not determine company'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        valid_team_profiles = serializer.context.get('team_profiles', [])
        
        users_to_remove = [profile.user for profile in valid_team_profiles]

        if not users_to_remove:
            return Response({
                'error': 'No valid team members found to unshare'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        candidate.shared_with.remove(*users_to_remove)
        
        AuditLogService.log(
            user=request.user,
            action=AuditLogAction.CANDIDATE_SHARED,
            category=AuditLogCategory.CANDIDATE,
            description=f"Candidate sharing removed: {candidate.get_full_name()} from {len(users_to_remove)} team member(s)",
            resource=candidate,
            data={
                'removed_from': [
                    {
                        'team_member_profile_id': profile.id,
                        'user_id': profile.user.id,
                        'name': profile.user.get_full_name(),
                        'email': profile.user.email
                    } for profile in valid_team_profiles
                ],
                'unshared_by': request.user.email
            },
            request=request
        )
        
        response_data = {
            'message': f'Sharing removed from {len(users_to_remove)} team member(s).',
            'removed_from': [
                {
                    'team_member_profile_id': profile.id,
                    'user_id': profile.user.id,
                    'name': profile.user.get_full_name(),
                    'email': profile.user.email
                } for profile in valid_team_profiles
            ]
        }

        return Response(response_data)
    
    @action(detail=False, methods=['get'])
    def my_candidates(self, request):
        candidates = Candidate.objects.filter(created_by=request.user)
        serializer = self.get_serializer(candidates, many=True)
        
        AuditLogService.log(
            user=request.user,
            action='VIEW_MY_CANDIDATES',
            category=AuditLogCategory.CANDIDATE,
            description=f"User viewed their candidates list",
            data={'count': candidates.count()},
            request=request
        )
        
        return Response(serializer.data)
    
    @action(detail=False, methods=['get'])
    def shared_with_me(self, request):
        if request.user.role != Roles.B2B_TEAM_MEMBER:
            return Response(
                {'error': 'Only team members can access shared candidates.'},
                status=status.HTTP_403_FORBIDDEN
            )
        
        candidates = Candidate.objects.filter(shared_with=request.user)
        serializer = self.get_serializer(candidates, many=True)
        
        AuditLogService.log(
            user=request.user,
            action='VIEW_SHARED_CANDIDATES',
            category=AuditLogCategory.CANDIDATE,
            description=f"Team member viewed shared candidates",
            data={'count': candidates.count()},
            request=request
        )
        
        return Response(serializer.data)
    
    def retrieve(self, request, *args, **kwargs):
        response = super().retrieve(request, *args, **kwargs)
        
        candidate = self.get_object()
        AuditLogService.log(
            user=request.user,
            action='VIEW_CANDIDATE',
            category=AuditLogCategory.CANDIDATE,
            description=f"Candidate details viewed: {candidate.get_full_name()}",
            resource=candidate,
            request=request
        )
        
        return response
    
    def list(self, request, *args, **kwargs):
        response = super().list(request, *args, **kwargs)
        
        AuditLogService.log(
            user=request.user,
            action='VIEW_CANDIDATES_LIST',
            category=AuditLogCategory.CANDIDATE,
            description=f"Candidate list viewed",
            data={'count': len(response.data) if hasattr(response, 'data') else 0},
            request=request
        )
        
        return response
