from rest_framework import serializers
from django.db import transaction
from .models import CandidateScore, ScoreSet, ScoreCategory
from api.core.constants import JOB_ROLE_SCORE_AREAS
from api.core.serializers import PublicIdModelSerializer
from api.candidates.serializers import CandidateSerializer
from api.evaluations.serializers import EvaluationSerializer


class ScoreCategorySerializer(PublicIdModelSerializer):
    applicable_roles_list = serializers.SerializerMethodField()
    
    class Meta:
        model = ScoreCategory
        fields = ['id', 'name', 'code', 'description', 'is_active', 
                  'applicable_job_roles', 'applicable_roles_list', 
                  'created_at', 'updated_at']
    
    def get_applicable_roles_list(self, obj):
        return obj.get_applicable_roles_list()


class CandidateScoreSerializer(PublicIdModelSerializer):
    area_display = serializers.CharField(source='get_area_display', read_only=True)
    candidate_name = serializers.CharField(source='candidate.get_full_name', read_only=True)
    created_by_name = serializers.CharField(source='created_by.get_full_name', read_only=True)
    
    class Meta:
        model = CandidateScore
        fields = [
            'id', 'candidate', 'candidate_name', 'evaluation',
            'area', 'area_display', 'score', 'notes',
            'created_by', 'created_by_name', 'company',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_by', 'company', 'created_at', 'updated_at']


class ScoreSetSerializer(PublicIdModelSerializer):
    candidate_details = CandidateSerializer(source='candidate', read_only=True)
    evaluation_details = EvaluationSerializer(source='evaluation', read_only=True)
    scores = serializers.SerializerMethodField()
    scores_dict = serializers.SerializerMethodField()
    created_by_name = serializers.CharField(source='created_by.get_full_name', read_only=True)
    
    class Meta:
        model = ScoreSet
        fields = [
            'id', 'candidate', 'candidate_details', 'evaluation', 'evaluation_details',
            'average_score', 'scores', 'scores_dict', 'notes',
            'created_by', 'created_by_name', 'company',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'average_score', 'created_by', 'company', 'created_at', 'updated_at']
    
    def get_scores(self, obj):
        scores = CandidateScore.objects.filter(
            candidate=obj.candidate,
            evaluation=obj.evaluation
        )
        return CandidateScoreSerializer(scores, many=True).data
    
    def get_scores_dict(self, obj):
        return obj.get_scores_dict()


class CreateScoreSetSerializer(serializers.Serializer):
    candidate_id = serializers.IntegerField()
    evaluation_id = serializers.IntegerField(required=False, allow_null=True)
    scores = serializers.DictField(
        child=serializers.DecimalField(max_digits=5, decimal_places=2),
        help_text="Dictionary of area -> score"
    )
    notes = serializers.CharField(required=False, allow_blank=True)
    
    def validate_candidate_id(self, value):
        from api.candidates.models import Candidate
        request = self.context.get('request')
        
        try:
            candidate = Candidate.objects.get(id=value)
        except Candidate.DoesNotExist:
            raise serializers.ValidationError("Candidate not found")
        
        if not candidate.can_access(request.user):
            raise serializers.ValidationError("You don't have permission to score this candidate")
        
        self.context['candidate'] = candidate
        return value
    
    def validate_evaluation_id(self, value):
        from api.evaluations.models import Evaluation
        candidate = self.context.get('candidate')
        
        if value:
            try:
                evaluation = Evaluation.objects.get(id=value)
            except Evaluation.DoesNotExist:
                raise serializers.ValidationError("Evaluation not found")
            
            if evaluation.candidate.id != candidate.id:
                raise serializers.ValidationError("Evaluation does not belong to this candidate")
            
            self.context['evaluation'] = evaluation
        
        return value
    
    def validate_scores(self, value):
        candidate = self.context.get('candidate')
        
        job_role = candidate.job_role
        allowed_areas = JOB_ROLE_SCORE_AREAS.get(job_role, JOB_ROLE_SCORE_AREAS.get('OT', []))
        
        for area in value.keys():
            if area not in allowed_areas:
                raise serializers.ValidationError(
                    f"Area '{area}' is not valid for job role {job_role}"
                )
        
        for area, score in value.items():
            if score < 0 or score > 100:
                raise serializers.ValidationError(
                    f"Score for {area} must be between 0 and 100"
                )
        
        return value
    
    @transaction.atomic
    def create(self, validated_data):
        request = self.context.get('request')
        candidate = self.context.get('candidate')
        evaluation = self.context.get('evaluation')
        scores_data = validated_data.get('scores', {})
        notes = validated_data.get('notes', '')
        
        score_set = ScoreSet.objects.create(
            candidate=candidate,
            evaluation=evaluation,
            created_by=request.user,
            notes=notes,
            company=candidate.company if candidate.company else None
        )
        
        for area, score_value in scores_data.items():
            CandidateScore.objects.create(
                candidate=candidate,
                evaluation=evaluation,
                area=area,
                score=score_value,
                created_by=request.user,
                company=candidate.company if candidate.company else None
            )
        
        score_set.calculate_average()
        
        return score_set


class UpdateScoreSetSerializer(serializers.Serializer):
    scores = serializers.DictField(
        child=serializers.DecimalField(max_digits=5, decimal_places=2),
        required=False,
        help_text="Dictionary of area -> score to update"
    )
    notes = serializers.CharField(required=False, allow_blank=True)
    
    def validate_scores(self, value):
        for area, score in value.items():
            if score < 0 or score > 100:
                raise serializers.ValidationError(
                    f"Score for {area} must be between 0 and 100"
                )
        return value
    
    @transaction.atomic
    def update(self, instance, validated_data):
        scores_data = validated_data.get('scores', {})
        notes = validated_data.get('notes')
        
        if notes is not None:
            instance.notes = notes
            instance.save()
        
        if scores_data:
            for area, score_value in scores_data.items():
                CandidateScore.objects.update_or_create(
                    candidate=instance.candidate,
                    evaluation=instance.evaluation,
                    area=area,
                    defaults={
                        'score': score_value,
                        'created_by': instance.created_by,
                        'company': instance.company
                    }
                )
            
            instance.calculate_average()
        
        return instance


class JobRoleScoreAreasSerializer(serializers.Serializer):
    job_role = serializers.CharField()
    job_role_display = serializers.CharField()
    areas = serializers.ListField(
        child=serializers.DictField()
    )
