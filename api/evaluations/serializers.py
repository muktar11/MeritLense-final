from rest_framework import serializers
from django.utils import timezone
from .models import Evaluation
from api.core.constants import CertificateStatus
from api.candidates.serializers import CandidateSerializer


class EvaluationSerializer(serializers.ModelSerializer):
    candidate_details = CandidateSerializer(source='candidate', read_only=True)
    evaluation_type_display = serializers.CharField(source='get_evaluation_type_display', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    certificate_status_display = serializers.CharField(source='get_certificate_status_display', read_only=True)
    created_by_name = serializers.SerializerMethodField()
    
    class Meta:
        model = Evaluation
        fields = [
            'id', 'candidate', 'candidate_details',
            'candidate_first_name', 'candidate_last_name', 'candidate_email',
            'candidate_passport_id', 'candidate_job_role', 'candidate_preferred_language',
            'evaluation_type', 'evaluation_type_display',
            'status', 'status_display',
            'scheduled_date', 'duration_minutes',
            'certificate_status', 'certificate_status_display',
            'certificate_issued_at', 'certificate_url',
            'last_evaluation_date',
            'score', 'feedback',
            'meeting_link', 'meeting_id', 'meeting_password',
            'location',
            'created_by', 'created_by_name',
            'company',
            'completed_at', 'cancelled_at', 'cancellation_reason',
            'created_at', 'updated_at'
        ]
        read_only_fields = [
            'id', 'candidate_first_name', 'candidate_last_name', 'candidate_email',
            'candidate_passport_id', 'candidate_job_role', 'candidate_preferred_language',
            'created_by', 'company', 'completed_at', 'cancelled_at',
            'created_at', 'updated_at'
        ]
    
    def get_created_by_name(self, obj):
        return obj.created_by.get_full_name()
    
    def validate_scheduled_date(self, value):
        if value <= timezone.now():
            raise serializers.ValidationError("Scheduled date must be in the future")
        return value


class EvaluationCreateSerializer(serializers.ModelSerializer):
    
    class Meta:
        model = Evaluation
        fields = [
            'candidate', 'evaluation_type', 'scheduled_date', 'duration_minutes',
            'meeting_link', 'meeting_id', 'meeting_password', 'location'
        ]
    
    def validate_candidate(self, value):
        request = self.context.get('request')
        user = request.user
        
        if not value.can_access(user):
            raise serializers.ValidationError("You don't have permission to schedule evaluations for this candidate")
        
        return value
    
    def create(self, validated_data):
        request = self.context.get('request')
        validated_data['created_by'] = request.user
        
        candidate = validated_data.get('candidate')
        if candidate.company:
            validated_data['company'] = candidate.company
        
        return super().create(validated_data)


class EvaluationUpdateSerializer(serializers.ModelSerializer):
    
    class Meta:
        model = Evaluation
        fields = [
            'evaluation_type', 'scheduled_date', 'duration_minutes',
            'meeting_link', 'meeting_id', 'meeting_password', 'location'
        ]
    
    def validate_scheduled_date(self, value):
        if value and value <= timezone.now():
            raise serializers.ValidationError("Scheduled date must be in the future")
        return value


class EvaluationCompleteSerializer(serializers.Serializer):
    score = serializers.DecimalField(max_digits=5, decimal_places=2, required=False)
    feedback = serializers.CharField(required=False, allow_blank=True)
    certificate_status = serializers.ChoiceField(
        choices=CertificateStatus.CHOICES,
        required=False,
        default=CertificateStatus.NOT_ISSUED
    )
    certificate_url = serializers.URLField(required=False, allow_blank=True)


class EvaluationRescheduleSerializer(serializers.Serializer):
    new_date = serializers.DateTimeField()
    reason = serializers.CharField(required=False, allow_blank=True)
    
    def validate_new_date(self, value):
        if value <= timezone.now():
            raise serializers.ValidationError("New date must be in the future")
        return value


class EvaluationCancelSerializer(serializers.Serializer):
    reason = serializers.CharField(required=False, allow_blank=True)


class EvaluationListSerializer(serializers.ModelSerializer):
    candidate_name = serializers.SerializerMethodField()
    evaluation_type_display = serializers.CharField(source='get_evaluation_type_display', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    
    class Meta:
        model = Evaluation
        fields = [
            'id', 'candidate_name', 'evaluation_type', 'evaluation_type_display',
            'status', 'status_display', 'scheduled_date', 'duration_minutes',
            'score', 'created_by', 'created_at'
        ]
    
    def get_candidate_name(self, obj):
        return f"{obj.candidate_first_name} {obj.candidate_last_name}"