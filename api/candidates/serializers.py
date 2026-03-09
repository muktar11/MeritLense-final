from rest_framework import serializers
from .models import Candidate

class CandidateSerializer(serializers.ModelSerializer):
    full_name = serializers.SerializerMethodField()
    skills_list = serializers.SerializerMethodField()
    created_by_name = serializers.SerializerMethodField()
    company_name = serializers.CharField(source='company.name', read_only=True)
    
    class Meta:
        model = Candidate
        fields = [
            'id', 'first_name', 'last_name', 'full_name', 'email',
            'passport_id', 'job_role', 'core_skills', 'skills_list',
            'preferred_language', 'status', 'passport_document',
            'profile_photo', 'created_by', 'created_by_name',
            'company', 'company_name', 'shared_with',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at', 'created_by']
    
    def get_full_name(self, obj):
        return obj.get_full_name()
    
    def get_skills_list(self, obj):
        return obj.get_skills_list()
    
    def get_created_by_name(self, obj):
        return obj.created_by.get_full_name()
    
    def validate_email(self, value):
        request = self.context.get('request')
        if request and request.user:
            if hasattr(request.user, 'company_profile'):
                company = request.user.company_profile.company
                if Candidate.objects.filter(
                    email=value, 
                    company=company
                ).exclude(pk=self.instance.pk if self.instance else None).exists():
                    raise serializers.ValidationError(
                        "A candidate with this email already exists in your company."
                    )
            else:
                if Candidate.objects.filter(
                    email=value, 
                    created_by=request.user
                ).exclude(pk=self.instance.pk if self.instance else None).exists():
                    raise serializers.ValidationError(
                        "You already have a candidate with this email."
                    )
        return value


class CandidateCreateSerializer(serializers.ModelSerializer):
    """Serializer for creating candidates"""
    passport_document = serializers.FileField(
        write_only=True,
        help_text="Upload passport/ID document"
    )
    profile_photo = serializers.ImageField(
        required=False,
        allow_null=True,
        write_only=True,
        help_text="Upload profile photo (optional)"
    )
    
    class Meta:
        model = Candidate
        fields = [
            'first_name', 'last_name', 'email', 'passport_id',
            'job_role', 'core_skills', 'preferred_language',
            'passport_document', 'profile_photo'
        ]
    
    def validate_core_skills(self, value):
        if value:
            skills = [skill.strip() for skill in value.split(',')]
            if len(skills) < 1:
                raise serializers.ValidationError("At least one skill is required.")
            if len(skills) > 20:
                raise serializers.ValidationError("Maximum 20 skills allowed.")
        return value
    
    def create(self, validated_data):
        request = self.context.get('request')
        user = request.user
        
        validated_data['created_by'] = user
        
        if hasattr(user, 'company_profile'):
            validated_data['company'] = user.company_profile.company
        
        candidate = super().create(validated_data)
        
        if user.role == 'B2B_TEAM_MEMBER':
            candidate.shared_with.add(user)
        
        return candidate


class CandidateUpdateSerializer(serializers.ModelSerializer):
    passport_document = serializers.FileField(
        required=False,
        write_only=True,
        help_text="Upload new passport/ID document (optional)"
    )
    profile_photo = serializers.ImageField(
        required=False,
        write_only=True,
        help_text="Upload new profile photo (optional)"
    )
    
    class Meta:
        model = Candidate
        fields = [
            'first_name', 'last_name', 'email', 'passport_id',
            'job_role', 'core_skills', 'preferred_language',
            'status', 'passport_document', 'profile_photo'
        ]
    
    def validate_core_skills(self, value):
        if value:
            skills = [skill.strip() for skill in value.split(',')]
            if len(skills) > 20:
                raise serializers.ValidationError("Maximum 20 skills allowed.")
        return value


class CandidateShareSerializer(serializers.Serializer):
    user_ids = serializers.ListField(
        child=serializers.IntegerField(),
        help_text="List of user IDs to share with"
    )
    
    def validate_user_ids(self, value):
        from api.accounts.models import User, TeamMemberProfile        
        request = self.context.get('request')
        if not request:
            raise serializers.ValidationError("Request context required")
        
        candidate = self.context.get('candidate')
        
        company = None
        if hasattr(request.user, 'company_profile'):
            company = request.user.company_profile.company
            print(f"Company from admin: {company.id if company else None} - {company.name if company else None}")
        elif candidate and candidate.company:
            company = candidate.company
            print(f"Company from candidate: {company.id if company else None} - {company.name if company else None}")
        
        if not company:
            raise serializers.ValidationError("Could not determine company for sharing")
        
        team_profiles = TeamMemberProfile.objects.filter(
            company=company
        ).select_related('user')
        
        print(f"Found {team_profiles.count()} team profiles")
        
        team_user_ids = []
        for profile in team_profiles:
            team_user_ids.append(profile.user.id)
            print(f"Team member: profile_id={profile.id}, user_id={profile.user.id}, email={profile.user.email}")
        
        if company.admin_user and company.admin_user.id not in team_user_ids:
            team_user_ids.append(company.admin_user.id)
            print(f"Added company admin: user_id={company.admin_user.id}")
        
        print(f"Valid team user IDs: {team_user_ids}")
        print(f"Requested user IDs: {value}")
        
        valid_users = []
        invalid_ids = []
        
        for user_id in value:
            if user_id in team_user_ids:
                try:
                    user = User.objects.get(id=user_id)
                    valid_users.append(user)
                    print(f"Valid user: {user_id} - {user.email}")
                except User.DoesNotExist:
                    invalid_ids.append(user_id)
                    print(f"User not found: {user_id}")
            else:
                invalid_ids.append(user_id)
                print(f"Invalid user (not in team): {user_id}")
        
        if invalid_ids:
            raise serializers.ValidationError(
                f"User IDs {invalid_ids} are not team members of your company"
            )
        
        self.context['users'] = valid_users
        return value