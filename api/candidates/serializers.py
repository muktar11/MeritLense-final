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
            'preferred_language', 'country_of_residence', 'target_market', 'timezone',
            'status', 'passport_document',
            'profile_photo', 'verification_photo', 'created_by', 'created_by_name',
            'company', 'company_name',
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
    verification_photo = serializers.ImageField(
        required=False,
        allow_null=True,
        write_only=True,
        help_text=(
            "AI-cropped, user-confirmed close-up face photo used as the identity "
            "verification reference instead of the full passport document (optional)"
        )
    )

    class Meta:
        model = Candidate
        fields = [
            'first_name', 'last_name', 'email', 'passport_id',
            'job_role', 'core_skills', 'preferred_language',
            'country_of_residence', 'target_market', 'timezone',
            'passport_document', 'profile_photo', 'verification_photo'
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
        
        # A team member sees assessments only for candidates they added
        # themselves; this link is what grants it. (Sharing candidates with
        # other team members was removed, 14 Oct 2026.)
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
    verification_photo = serializers.ImageField(
        required=False,
        write_only=True,
        help_text=(
            "AI-cropped, user-confirmed close-up face photo used as the identity "
            "verification reference instead of the full passport document (optional)"
        )
    )

    class Meta:
        model = Candidate
        fields = [
            'first_name', 'last_name', 'email', 'passport_id',
            'job_role', 'core_skills', 'preferred_language',
            'country_of_residence', 'target_market', 'timezone',
            'status', 'passport_document', 'profile_photo', 'verification_photo'
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

    def update(self, instance, validated_data):
        # verification_photo is the identity-verification reference image
        # and always wins over profile_photo when both are set (see
        # InterviewSessionPrecheckService.resolve_reference_image_file). If
        # a caller uploads a new profile_photo without also supplying a
        # matching verification_photo, the old one is now a stale photo of
        # the candidate - clear it so the reference resolution falls
        # through to the fresh profile_photo instead of silently continuing
        # to verify against how the candidate looked before this edit.
        if 'profile_photo' in validated_data and 'verification_photo' not in validated_data:
            instance.verification_photo = None
        return super().update(instance, validated_data)

