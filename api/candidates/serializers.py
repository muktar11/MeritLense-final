from rest_framework import serializers
from .models import Candidate
from api.core.constants import Roles
from api.core.serializers import PublicIdModelSerializer

class CandidateSerializer(PublicIdModelSerializer):
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

    def validate(self, attrs):
        request = self.context.get('request')
        user = getattr(request, 'user', None)
        if not user:
            return attrs

        email = attrs.get('email')
        passport_id = attrs.get('passport_id')
        company = None

        if hasattr(user, 'company_profile'):
            company = user.company_profile.company
        elif user.role == Roles.B2B_TEAM_MEMBER and hasattr(user, 'team_member_profile'):
            company = user.team_member_profile.company

        queryset = Candidate.objects.all()
        if company:
            queryset = queryset.filter(company=company)
        else:
            queryset = queryset.filter(created_by=user)

        if email and queryset.filter(email=email).exists():
            raise serializers.ValidationError({
                'email': "A candidate with this email already exists in your scope."
            })

        if passport_id and queryset.filter(passport_id=passport_id).exists():
            raise serializers.ValidationError({
                'passport_id': "A candidate with this passport ID already exists in your scope."
            })

        return attrs
    
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
        elif user.role == Roles.B2B_TEAM_MEMBER and hasattr(user, 'team_member_profile'):
            validated_data['company'] = user.team_member_profile.company
        
        candidate = super().create(validated_data)
        
        if user.role == Roles.B2B_TEAM_MEMBER:
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

    def validate(self, attrs):
        request = self.context.get('request')
        user = getattr(request, 'user', None)
        instance = getattr(self, 'instance', None)
        if not user or not instance:
            return attrs

        email = attrs.get('email', instance.email)
        passport_id = attrs.get('passport_id', instance.passport_id)
        company = instance.company

        queryset = Candidate.objects.exclude(pk=instance.pk)
        if company:
            queryset = queryset.filter(company=company)
        else:
            queryset = queryset.filter(created_by=instance.created_by)

        if queryset.filter(email=email).exists():
            raise serializers.ValidationError({
                'email': "A candidate with this email already exists in your scope."
            })

        if queryset.filter(passport_id=passport_id).exists():
            raise serializers.ValidationError({
                'passport_id': "A candidate with this passport ID already exists in your scope."
            })

        return attrs
    
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
        from api.accounts.models import TeamMemberProfile
        request = self.context.get('request')
        if not request:
            raise serializers.ValidationError("Request context required")
        
        candidate = self.context.get('candidate')
        
        company = None
        if hasattr(request.user, 'company_profile'):
            company = request.user.company_profile.company
        elif candidate and candidate.company:
            company = candidate.company
        
        if not company:
            raise serializers.ValidationError("Could not determine company for sharing")
        
        team_profiles = TeamMemberProfile.objects.filter(
            company=company
        ).select_related('user')
        valid_profile_map = {}
        for profile in team_profiles:
            valid_profile_map[profile.id] = profile
            valid_profile_map[profile.user.id] = profile

        valid_profiles = []
        invalid_ids = []
        
        for identifier in value:
            profile = valid_profile_map.get(identifier)
            if profile:
                valid_profiles.append(profile)
            else:
                invalid_ids.append(identifier)
        
        if invalid_ids:
            raise serializers.ValidationError(
                f"User IDs {invalid_ids} are not team members of your company"
            )
        
        deduped_profiles = list({profile.id: profile for profile in valid_profiles}.values())
        self.context['team_profiles'] = deduped_profiles
        self.context['users'] = [profile.user for profile in deduped_profiles]
        return value
