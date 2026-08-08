from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.db.models import Q
from django.utils import timezone

from api.audit.services import AuditLogService
from api.core.permisssions import IsAdminOrSuperAdmin

from .models import CandidateScore, ScoreSet, ScoreCategory
from .serializers import (
    CandidateScoreSerializer,
    ScoreSetSerializer,
    CreateScoreSetSerializer,
    UpdateScoreSetSerializer,
    ScoreCategorySerializer,
    JobRoleScoreAreasSerializer
)
from .permissions import CanManageScores, CanViewScores
from api.core.constants import Roles, JOB_ROLE_SCORE_AREAS, ScoreArea
from api.core.constants import AuditLogCategory, AuditLogSeverity
from api.core.public_ids import PublicIdLookupMixin, filter_by_identifier


class ScoreCategoryViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated, IsAdminOrSuperAdmin]
    queryset = ScoreCategory.objects.filter(is_active=True)
    serializer_class = ScoreCategorySerializer
    lookup_field = 'code'
    
    def perform_create(self, serializer):
        category = serializer.save()
        
        AuditLogService.log(
            user=self.request.user,
            action='SCORE_CATEGORY_CREATED',
            category=AuditLogCategory.SYSTEM,
            description=f"Score category created: {category.name}",
            resource=category,
            data={
                'name': category.name,
                'code': category.code,
                'applicable_job_roles': category.applicable_job_roles
            },
            request=self.request
        )
    
    def perform_update(self, serializer):
        old_category = self.get_object()
        old_data = {
            'name': old_category.name,
            'description': old_category.description,
            'is_active': old_category.is_active,
            'applicable_job_roles': old_category.applicable_job_roles
        }
        
        category = serializer.save()
        
        changes = {}
        new_data = {
            'name': category.name,
            'description': category.description,
            'is_active': category.is_active,
            'applicable_job_roles': category.applicable_job_roles
        }
        
        for field, new_value in new_data.items():
            if old_data.get(field) != new_value:
                changes[field] = {
                    'old': old_data.get(field),
                    'new': new_value
                }
        
        if changes:
            AuditLogService.log(
                user=self.request.user,
                action='SCORE_CATEGORY_UPDATED',
                category=AuditLogCategory.SYSTEM,
                description=f"Score category updated: {category.name}",
                resource=category,
                data={'changes': changes},
                request=self.request
            )
    
    def perform_destroy(self, instance):
        category_data = {
            'name': instance.name,
            'code': instance.code,
            'applicable_job_roles': instance.applicable_job_roles
        }
        
        AuditLogService.log(
            user=self.request.user,
            action='SCORE_CATEGORY_DELETED',
            category=AuditLogCategory.SYSTEM,
            description=f"Score category deleted: {instance.name}",
            data=category_data,
            request=self.request,
            severity=AuditLogSeverity.WARNING
        )
        
        instance.delete()


class ScoreSetViewSet(PublicIdLookupMixin, viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = ScoreSetSerializer
    
    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return ScoreSet.objects.none()

        user = self.request.user
        queryset = ScoreSet.objects.all()
        
        candidate_id = self.request.query_params.get('candidate_id')
        if candidate_id:
            queryset = filter_by_identifier(queryset, "candidate", candidate_id)
        
        evaluation_id = self.request.query_params.get('evaluation_id')
        if evaluation_id:
            queryset = filter_by_identifier(queryset, "evaluation", evaluation_id)
        
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
    
    def get_permissions(self):
        if self.action in ['create', 'update', 'partial_update', 'destroy']:
            self.permission_classes = [IsAuthenticated, CanManageScores]
        elif self.action in ['list', 'retrieve']:
            self.permission_classes = [IsAuthenticated, CanViewScores]
        
        return super().get_permissions()
    
    def get_serializer_class(self):
        if self.action == 'create':
            return CreateScoreSetSerializer
        elif self.action in ['update', 'partial_update']:
            return UpdateScoreSetSerializer
        return ScoreSetSerializer
    
    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        score_set = serializer.save()
        
        candidate = score_set.candidate
        
        AuditLogService.log(
            user=request.user,
            action='SCORE_SET_CREATED',
            category=AuditLogCategory.EVALUATION,
            description=f"Score set created for candidate: {candidate.get_full_name()}",
            resource=score_set,
            data={
                'candidate_id': candidate.id,
                'candidate_name': candidate.get_full_name(),
                'evaluation_id': score_set.evaluation.id if score_set.evaluation else None,
                'average_score': float(score_set.average_score) if score_set.average_score else None,
                'created_by': request.user.email
            },
            request=request
        )
        
        return Response(
            ScoreSetSerializer(score_set, context={'request': request}).data,
            status=status.HTTP_201_CREATED
        )
    
    def perform_update(self, serializer):
        old_score_set = self.get_object()
        old_average = old_score_set.average_score
        
        score_set = serializer.save()
        
        AuditLogService.log(
            user=self.request.user,
            action='SCORE_SET_UPDATED',
            category=AuditLogCategory.EVALUATION,
            description=f"Score set updated for candidate: {score_set.candidate.get_full_name()}",
            resource=score_set,
            data={
                'candidate_name': score_set.candidate.get_full_name(),
                'old_average': float(old_average) if old_average else None,
                'new_average': float(score_set.average_score) if score_set.average_score else None,
                'updated_by': self.request.user.email
            },
            request=self.request
        )
    
    def perform_destroy(self, instance):
        score_set_data = {
            'id': instance.id,
            'candidate_name': instance.candidate.get_full_name(),
            'average_score': float(instance.average_score) if instance.average_score else None,
            'evaluation_id': instance.evaluation.id if instance.evaluation else None
        }
        
        AuditLogService.log(
            user=self.request.user,
            action='SCORE_SET_DELETED',
            category=AuditLogCategory.EVALUATION,
            description=f"Score set deleted for candidate: {instance.candidate.get_full_name()}",
            data=score_set_data,
            request=self.request,
            severity=AuditLogSeverity.WARNING
        )
        
        instance.delete()
    
    def retrieve(self, request, *args, **kwargs):
        response = super().retrieve(request, *args, **kwargs)
        
        score_set = self.get_object()
        AuditLogService.log(
            user=request.user,
            action='VIEW_SCORE_SET',
            category=AuditLogCategory.EVALUATION,
            description=f"Score set viewed for candidate: {score_set.candidate.get_full_name()}",
            resource=score_set,
            data={
                'candidate_name': score_set.candidate.get_full_name(),
                'average_score': float(score_set.average_score) if score_set.average_score else None
            },
            request=request
        )
        
        return response
    
    def list(self, request, *args, **kwargs):
        response = super().list(request, *args, **kwargs)
        
        AuditLogService.log(
            user=request.user,
            action='VIEW_SCORE_SETS_LIST',
            category=AuditLogCategory.EVALUATION,
            description="Score sets list viewed",
            data={'count': len(response.data) if hasattr(response, 'data') else 0},
            request=request
        )
        
        return response
    
    @action(detail=True, methods=['get'])
    def scores(self, request, id=None):
        score_set = self.get_object()
        scores = CandidateScore.objects.filter(
            candidate=score_set.candidate,
            evaluation=score_set.evaluation
        )
        serializer = CandidateScoreSerializer(scores, many=True)
        
        AuditLogService.log(
            user=request.user,
            action='VIEW_SCORES_IN_SET',
            category=AuditLogCategory.EVALUATION,
            description=f"Viewed individual scores in score set for candidate: {score_set.candidate.get_full_name()}",
            resource=score_set,
            data={
                'candidate_name': score_set.candidate.get_full_name(),
                'score_count': scores.count()
            },
            request=request
        )
        
        return Response(serializer.data)
    
    @action(detail=True, methods=['post'])
    def recalculate(self, request, id=None):
        score_set = self.get_object()
        old_average = score_set.average_score
        average = score_set.calculate_average()
        
        AuditLogService.log(
            user=request.user,
            action='SCORE_SET_RECALCULATED',
            category=AuditLogCategory.EVALUATION,
            description=f"Score set average recalculated for candidate: {score_set.candidate.get_full_name()}",
            resource=score_set,
            data={
                'candidate_name': score_set.candidate.get_full_name(),
                'old_average': float(old_average) if old_average else None,
                'new_average': float(average) if average else None,
                'recalculated_by': request.user.email
            },
            request=request
        )
        
        return Response({
            'message': 'Average recalculated successfully',
            'average_score': average
        })


class CandidateScoreViewSet(PublicIdLookupMixin, viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = CandidateScoreSerializer
    
    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return CandidateScore.objects.none()

        user = self.request.user
        queryset = CandidateScore.objects.all()
        
        candidate_id = self.request.query_params.get('candidate_id')
        if candidate_id:
            queryset = filter_by_identifier(queryset, "candidate", candidate_id)
        
        evaluation_id = self.request.query_params.get('evaluation_id')
        if evaluation_id:
            queryset = filter_by_identifier(queryset, "evaluation", evaluation_id)
        
        area = self.request.query_params.get('area')
        if area:
            queryset = queryset.filter(area=area)
        
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
    
    def get_permissions(self):
        if self.action in ['create', 'update', 'partial_update', 'destroy']:
            self.permission_classes = [IsAuthenticated, CanManageScores]
        else:
            self.permission_classes = [IsAuthenticated, CanViewScores]
        return super().get_permissions()
    
    def perform_create(self, serializer):
        candidate = serializer.validated_data.get('candidate')
        score = serializer.save(
            created_by=self.request.user,
            company=candidate.company if candidate.company else None
        )
        
        AuditLogService.log(
            user=self.request.user,
            action='INDIVIDUAL_SCORE_CREATED',
            category=AuditLogCategory.EVALUATION,
            description=f"Individual score created for candidate: {candidate.get_full_name()}",
            resource=score,
            data={
                'candidate_name': candidate.get_full_name(),
                'area': score.area,
                'score': float(score.score),
                'evaluation_id': score.evaluation.id if score.evaluation else None,
                'created_by': self.request.user.email
            },
            request=self.request
        )
    
    def perform_update(self, serializer):
        old_score = self.get_object()
        old_value = float(old_score.score)
        old_notes = old_score.notes
        
        score = serializer.save()
        
        changes = {}
        if old_value != float(score.score):
            changes['score'] = {
                'old': old_value,
                'new': float(score.score)
            }
        if old_notes != score.notes:
            changes['notes'] = {
                'old': old_notes,
                'new': score.notes
            }
        
        if changes:
            AuditLogService.log(
                user=self.request.user,
                action='INDIVIDUAL_SCORE_UPDATED',
                category=AuditLogCategory.EVALUATION,
                description=f"Individual score updated for candidate: {score.candidate.get_full_name()}",
                resource=score,
                data={
                    'candidate_name': score.candidate.get_full_name(),
                    'area': score.area,
                    'changes': changes,
                    'updated_by': self.request.user.email
                },
                request=self.request
            )
    
    def perform_destroy(self, instance):
        score_data = {
            'candidate_name': instance.candidate.get_full_name(),
            'area': instance.area,
            'score': float(instance.score),
            'evaluation_id': instance.evaluation.id if instance.evaluation else None
        }
        
        AuditLogService.log(
            user=self.request.user,
            action='INDIVIDUAL_SCORE_DELETED',
            category=AuditLogCategory.EVALUATION,
            description=f"Individual score deleted for candidate: {instance.candidate.get_full_name()}",
            data=score_data,
            request=self.request,
            severity=AuditLogSeverity.WARNING
        )
        
        instance.delete()
    
    def retrieve(self, request, *args, **kwargs):
        response = super().retrieve(request, *args, **kwargs)
        
        score = self.get_object()
        AuditLogService.log(
            user=request.user,
            action='VIEW_INDIVIDUAL_SCORE',
            category=AuditLogCategory.EVALUATION,
            description=f"Individual score viewed for candidate: {score.candidate.get_full_name()}",
            resource=score,
            data={
                'candidate_name': score.candidate.get_full_name(),
                'area': score.area,
                'score': float(score.score)
            },
            request=request
        )
        
        return response
    
    def list(self, request, *args, **kwargs):
        response = super().list(request, *args, **kwargs)
        
        AuditLogService.log(
            user=request.user,
            action='VIEW_INDIVIDUAL_SCORES_LIST',
            category=AuditLogCategory.EVALUATION,
            description="Individual scores list viewed",
            data={'count': len(response.data) if hasattr(response, 'data') else 0},
            request=request
        )
        
        return response


class JobRoleScoreAreasView(viewsets.ViewSet):
    permission_classes = [IsAuthenticated]
    
    def list(self, request):
        from api.core.constants import CandidateJobRoles
        
        result = []
        role_dict = dict(CandidateJobRoles.CHOICES)
        
        for role_code, role_label in role_dict.items():
            areas = JOB_ROLE_SCORE_AREAS.get(role_code, JOB_ROLE_SCORE_AREAS.get('OT', []))
            
            area_dict = dict(ScoreArea.CHOICES)
            area_list = [
                {'code': area, 'name': area_dict.get(area, area)}
                for area in areas
            ]
            
            result.append({
                'job_role': role_code,
                'job_role_display': role_label,
                'areas': area_list
            })
        
        AuditLogService.log(
            user=request.user,
            action='VIEW_JOB_ROLE_AREAS',
            category=AuditLogCategory.SYSTEM,
            description="Job role score areas viewed",
            data={'count': len(result)},
            request=request
        )
        
        serializer = JobRoleScoreAreasSerializer(result, many=True)
        return Response(serializer.data)
    
    @action(detail=False, methods=['get'], url_path='by-role/(?P<role_code>[^/.]+)')
    def by_role(self, request, role_code=None):
        from api.core.constants import CandidateJobRoles
        
        role_dict = dict(CandidateJobRoles.CHOICES)
        if role_code not in role_dict:
            return Response(
                {'error': f'Invalid job role code: {role_code}'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        areas = JOB_ROLE_SCORE_AREAS.get(role_code, JOB_ROLE_SCORE_AREAS.get('OT', []))
        
        area_dict = dict(ScoreArea.CHOICES)
        area_list = [
            {'code': area, 'name': area_dict.get(area, area)}
            for area in areas
        ]
        
        AuditLogService.log(
            user=request.user,
            action='VIEW_JOB_ROLE_AREAS_BY_ROLE',
            category=AuditLogCategory.SYSTEM,
            description=f"Score areas viewed for job role: {role_dict[role_code]}",
            data={'job_role': role_code, 'areas_count': len(area_list)},
            request=request
        )
        
        return Response({
            'job_role': role_code,
            'job_role_display': role_dict[role_code],
            'areas': area_list
        })
