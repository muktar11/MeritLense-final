from rest_framework import serializers
from api.candidates.models import Candidate
from api.core.serializers import PublicIdModelSerializer
from api.evaluations.models import Evaluation

class DashboardStatsSerializer(serializers.Serializer):
    total_candidates = serializers.IntegerField()
    total_evaluations = serializers.IntegerField()
    completed_evaluations = serializers.IntegerField()
    certificates_issued = serializers.IntegerField()
    success_rate = serializers.FloatField()
    team_members_count = serializers.IntegerField(required=False)


class RecentCandidateSerializer(PublicIdModelSerializer):
    full_name = serializers.SerializerMethodField()
    evaluation_count = serializers.IntegerField(read_only=True)
    last_evaluation_date = serializers.DateTimeField(read_only=True)
    
    class Meta:
        model = Candidate
        fields = [
            'id', 'full_name', 'email', 'job_role', 
            'status', 'evaluation_count', 'last_evaluation_date',
            'created_at'
        ]
    
    def get_full_name(self, obj):
        return obj.get_full_name()


class RecentEvaluationSerializer(PublicIdModelSerializer):
    candidate_name = serializers.SerializerMethodField()
    evaluation_type_display = serializers.CharField(source='get_evaluation_type_display')
    status_display = serializers.CharField(source='get_status_display')
    
    class Meta:
        model = Evaluation
        fields = [
            'id', 'candidate_name', 'evaluation_type', 'evaluation_type_display',
            'status', 'status_display', 'scheduled_date', 'score',
            'created_at'
        ]
    
    def get_candidate_name(self, obj):
        return f"{obj.candidate_first_name} {obj.candidate_last_name}"


class ScoreDistributionSerializer(serializers.Serializer):
    job_role = serializers.CharField()
    job_role_display = serializers.CharField()
    average_score = serializers.FloatField()
    min_score = serializers.FloatField()
    max_score = serializers.FloatField()
    evaluation_count = serializers.IntegerField()


class EvaluationTrendSerializer(serializers.Serializer):
    date = serializers.DateField()
    scheduled_count = serializers.IntegerField()
    completed_count = serializers.IntegerField()
    cancelled_count = serializers.IntegerField()


class LanguageDistributionSerializer(serializers.Serializer):
    language = serializers.CharField()
    language_display = serializers.CharField()
    count = serializers.IntegerField()
    percentage = serializers.FloatField()


class PerformanceMetricSerializer(serializers.Serializer):
    period = serializers.CharField()
    average_score = serializers.FloatField()
    evaluation_count = serializers.IntegerField()
    pass_rate = serializers.FloatField()


class CandidateComparisonSerializer(serializers.Serializer):
    candidate_id = serializers.IntegerField()
    candidate_name = serializers.CharField()
    job_role = serializers.CharField()
    average_score = serializers.FloatField()
    scores_by_area = serializers.DictField()


class EvaluationStatusDistributionSerializer(serializers.Serializer):
    status = serializers.CharField()
    status_display = serializers.CharField()
    count = serializers.IntegerField()
    percentage = serializers.FloatField()


class MonthlyActivitySerializer(serializers.Serializer):
    month = serializers.CharField()
    candidates_added = serializers.IntegerField()
    evaluations_completed = serializers.IntegerField()
    certificates_issued = serializers.IntegerField()
