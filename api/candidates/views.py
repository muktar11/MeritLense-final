from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.db.models import Q

from api.audit.services import AuditLogService
from .models import Candidate
from .serializers import (
    CandidateSerializer, 
    CandidateCreateSerializer,
    CandidateUpdateSerializer,
    CandidateShareSerializer
)
from .permissions import CanManageCandidate, CanViewCandidate
from api.core.constants import Roles
from api.core.constants import AuditLogCategory, AuditLogAction
from api.accounts.models import User


class CandidateViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = CandidateSerializer
    
    def get_queryset(self):
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
        if self.action in ['create', 'update', 'partial_update', 'destroy']:
            self.permission_classes = [IsAuthenticated, CanManageCandidate]
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
        return CandidateSerializer
    
    def perform_create(self, serializer):
        candidate = serializer.save()
        
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
    
    @action(detail=True, methods=['post'])
    def share(self, request, pk=None):
        candidate = self.get_object()
        
        user = request.user
        if not (user.role in [Roles.ADMIN, Roles.SUPERADMIN] or 
                candidate.created_by == user or
                (hasattr(user, 'managed_company') and candidate.company == user.managed_company) or
                (hasattr(user, 'company_profile') and candidate.company == user.company_profile.company)):
            return Response(
                {'error': 'You do not have permission to share this candidate.'},
                status=status.HTTP_403_FORBIDDEN
            )
        
        team_member_profile_ids = request.data.get('user_ids', [])
        if not team_member_profile_ids:
            return Response(
                {'error': 'Please provide team member profile IDs to share.'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        if isinstance(team_member_profile_ids, (int, str)):
            team_member_profile_ids = [team_member_profile_ids]
        
        from api.accounts.models import TeamMemberProfile
        
        company = None
        if hasattr(request.user, 'company_profile'):
            company = request.user.company_profile.company
        elif candidate.company:
            company = candidate.company
        
        if not company:
            return Response(
                {'error': 'Could not determine company'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        valid_team_profiles = TeamMemberProfile.objects.filter(
            id__in=team_member_profile_ids,
            company=company
        ).select_related('user')
        
        users_to_share = [profile.user for profile in valid_team_profiles]
        
        invalid_ids = [
            pid for pid in team_member_profile_ids 
            if pid not in [profile.id for profile in valid_team_profiles]
        ]
        
        if not users_to_share:
            return Response({
                'error': 'No valid team members found to share with',
                'invalid_ids': invalid_ids
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
        
        if invalid_ids:
            response_data['warning'] = f'{len(invalid_ids)} team member profile IDs were invalid: {invalid_ids}'
        
        return Response(response_data)


    @action(detail=True, methods=['post'])
    def unshare(self, request, pk=None):
        candidate = self.get_object()
        
        user = request.user
        if not (user.role in [Roles.ADMIN, Roles.SUPERADMIN] or 
                candidate.created_by == user or
                (hasattr(user, 'managed_company') and candidate.company == user.managed_company) or
                (hasattr(user, 'company_profile') and candidate.company == user.company_profile.company)):
            return Response(
                {'error': 'You do not have permission to modify sharing for this candidate.'},
                status=status.HTTP_403_FORBIDDEN
            )
        
        team_member_profile_ids = request.data.get('user_ids', [])
        if not team_member_profile_ids:
            return Response(
                {'error': 'Please provide team member profile IDs to unshare.'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        if isinstance(team_member_profile_ids, (int, str)):
            team_member_profile_ids = [team_member_profile_ids]
        
        from api.accounts.models import TeamMemberProfile
        
        company = None
        if hasattr(request.user, 'company_profile'):
            company = request.user.company_profile.company
        elif candidate.company:
            company = candidate.company
        
        if not company:
            return Response(
                {'error': 'Could not determine company'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        valid_team_profiles = TeamMemberProfile.objects.filter(
            id__in=team_member_profile_ids,
            company=company
        ).select_related('user')
        
        users_to_remove = [profile.user for profile in valid_team_profiles]
        
        invalid_ids = [
            pid for pid in team_member_profile_ids 
            if pid not in [profile.id for profile in valid_team_profiles]
        ]
        
        if not users_to_remove:
            return Response({
                'error': 'No valid team members found to unshare',
                'invalid_ids': invalid_ids
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
        
        if invalid_ids:
            response_data['warning'] = f'{len(invalid_ids)} team member profile IDs were invalid: {invalid_ids}'
        
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