from rest_framework import serializers
from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from .models import Company, TeamInvitation, TeamMemberProfile, User, IndividualEmployerProfile, CompanyEmployerProfile, AdminProfile
from api.core.constants import AdminPermissions, CompanyTeamPermissions, Roles
from api.core.serializers import PublicIdModelSerializer
import random

class CustomTokenObtainPairSerializer(TokenObtainPairSerializer):
    username_field = "email"
    
    def validate(self, attrs):
        data = super().validate(attrs)

        if not self.user.is_verified:
            raise AuthenticationFailed("Email verification is required before login.")
        
        data['role'] = self.user.role
        data['is_superuser'] = self.user.is_superuser
        data['is_staff'] = self.user.is_staff
        data['is_verified'] = self.user.is_verified
        data['documents_verified'] = self.user.documents_verified
        data['full_name'] = self.user.get_full_name()
        
        return data


class UserRegistrationSerializer(serializers.Serializer):
    email = serializers.EmailField()
    first_name = serializers.CharField(max_length=150)
    last_name = serializers.CharField(max_length=150)
    password = serializers.CharField(write_only=True, min_length=8)
    confirm_password = serializers.CharField(write_only=True, min_length=8)
    
    def validate_email(self, value):
        if User.objects.filter(email=value).exists():
            raise serializers.ValidationError("User with this email already exists.")
        return value
    
    def validate(self, data):
        if data['password'] != data['confirm_password']:
            raise serializers.ValidationError({"confirm_password": "Passwords do not match."})
        return data
    
    def create_user(self, role, **kwargs):
        verification_code = ''.join([str(random.randint(0, 9)) for _ in range(5)])
        
        user = User.objects.create_user(
            email=kwargs['email'],
            password=kwargs['password'],
            first_name=kwargs['first_name'],
            last_name=kwargs['last_name'],
            role=role,
            is_active=True,
            is_verified=False,
            email_verification_code=verification_code
        )
        return user


class B2CRegistrationSerializer(UserRegistrationSerializer):
    passport_id = serializers.CharField(max_length=50)
    job_role = serializers.ChoiceField(choices=[])
    nationality = serializers.ChoiceField(choices=[])
    preferred_language = serializers.ChoiceField(choices=[])
    phone_number = serializers.CharField(max_length=20)
    date_of_birth = serializers.DateField(required=False, allow_null=True)
    address = serializers.CharField(required=False, allow_blank=True, max_length=255)
    
    id_document = serializers.FileField()
    resume_document = serializers.FileField()
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from api.core.constants import JobRoles, Nationalities, Languages
        self.fields['job_role'].choices = JobRoles.CHOICES
        self.fields['nationality'].choices = Nationalities.CHOICES
        self.fields['preferred_language'].choices = Languages.CHOICES

    def validate_passport_id(self, value):
        if IndividualEmployerProfile.objects.filter(passport_id=value).exists():
            raise serializers.ValidationError("Passport ID already exists.")
        return value
    
    def create(self, validated_data):
        profile_data = {
            'passport_id': validated_data.pop('passport_id'),
            'job_role': validated_data.pop('job_role'),
            'nationality': validated_data.pop('nationality'),
            'preferred_language': validated_data.pop('preferred_language'),
            'phone_number': validated_data.pop('phone_number'),
            'date_of_birth': validated_data.pop('date_of_birth', None),
            'address': validated_data.pop('address', ''),
            'id_document': validated_data.pop('id_document'),
            'resume_document': validated_data.pop('resume_document'),
        }
        
        user = self.create_user(Roles.B2C, **validated_data)
        self.context['profile_data'] = profile_data
        self.context['user'] = user
        return user


class B2BRegistrationSerializer(UserRegistrationSerializer):
    company_name = serializers.CharField(max_length=255)
    company_registration_number = serializers.CharField(max_length=100)
    company_size = serializers.ChoiceField(choices=[])
    country = serializers.CharField(max_length=100)
    city = serializers.CharField(max_length=100)
    preferred_language = serializers.ChoiceField(choices=[])
    phone_number = serializers.CharField(max_length=20)
    website = serializers.URLField(required=False, allow_blank=True, allow_null=True)
    industry = serializers.CharField(required=False, allow_blank=True, max_length=100)
    address = serializers.CharField(required=False, allow_blank=True, max_length=255)
    
    registration_certificate = serializers.FileField()
    resachetified_license = serializers.FileField()
    tax_id_document = serializers.FileField(required=False, allow_null=True)
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from api.core.constants import CompanySize, Languages
        self.fields['company_size'].choices = CompanySize.CHOICES
        self.fields['preferred_language'].choices = Languages.CHOICES

    def validate_company_registration_number(self, value):
        if CompanyEmployerProfile.objects.filter(company_registration_number=value).exists():
            raise serializers.ValidationError("Company registration number already exists.")
        if Company.objects.filter(registration_number=value).exists():
            raise serializers.ValidationError("Company registration number already exists.")
        return value
    
    def create(self, validated_data):
        profile_data = {
            'company_name': validated_data.pop('company_name'),
            'company_registration_number': validated_data.pop('company_registration_number'),
            'company_size': validated_data.pop('company_size'),
            'country': validated_data.pop('country'),
            'city': validated_data.pop('city'),
            'preferred_language': validated_data.pop('preferred_language'),
            'phone_number': validated_data.pop('phone_number'),
            'website': validated_data.pop('website', ''),
            'industry': validated_data.pop('industry', ''),
            'address': validated_data.pop('address', ''),
            'registration_certificate': validated_data.pop('registration_certificate'),
            'resachetified_license': validated_data.pop('resachetified_license'),
            'tax_id_document': validated_data.pop('tax_id_document', None),
        }
        
        user = self.create_user(Roles.B2B, **validated_data)
        self.context['profile_data'] = profile_data
        self.context['user'] = user
        return user


class EmailVerificationSerializer(serializers.Serializer):
    email = serializers.EmailField()
    code = serializers.CharField(max_length=6)


class ResendVerificationSerializer(serializers.Serializer):
    email = serializers.EmailField()



class ForgotPasswordSerializer(serializers.Serializer):
    email = serializers.EmailField()
    
    def validate_email(self, value):
        try:
            user = User.objects.get(email=value, is_active=True)
            self.context['user'] = user
        except User.DoesNotExist:
            pass
        return value


class ResetPasswordSerializer(serializers.Serializer):
    token = serializers.CharField()
    password = serializers.CharField(write_only=True, min_length=8)
    confirm_password = serializers.CharField(write_only=True, min_length=8)
    
    def validate(self, data):
        if data['password'] != data['confirm_password']:
            raise serializers.ValidationError({"confirm_password": "Passwords do not match."})
        return data


class ChangePasswordSerializer(serializers.Serializer):
    current_password = serializers.CharField(write_only=True)
    new_password = serializers.CharField(write_only=True, min_length=8)
    confirm_new_password = serializers.CharField(write_only=True, min_length=8)
    
    def validate_current_password(self, value):
        user = self.context['request'].user
        if not user.check_password(value):
            raise serializers.ValidationError("Current password is incorrect.")
        return value
    
    def validate(self, data):
        if data['new_password'] != data['confirm_new_password']:
            raise serializers.ValidationError({"confirm_new_password": "New passwords do not match."})
        
        if data['current_password'] == data['new_password']:
            raise serializers.ValidationError({"new_password": "New password must be different from current password."})
        
        return data

class IndividualProfileSerializer(PublicIdModelSerializer):
    email = serializers.EmailField(source='user.email', read_only=True)
    first_name = serializers.CharField(source='user.first_name')
    last_name = serializers.CharField(source='user.last_name')
    full_name = serializers.SerializerMethodField()
    
    class Meta:
        model = IndividualEmployerProfile
        fields = [
            'id', 'email', 'first_name', 'last_name', 'full_name',
            'passport_id', 'phone_number', 'date_of_birth', 'address',
            'job_role', 'nationality', 'preferred_language',
            'id_document', 'resume_document', 'additional_documents',
            'documents_verified', 'verified_at', 'verification_notes',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['documents_verified', 'verified_at', 'verification_notes', 'created_at', 'updated_at']
    
    def get_full_name(self, obj):
        return obj.user.get_full_name()
    
    def update(self, instance, validated_data):
        user_data = validated_data.pop('user', {})
        if user_data:
            user = instance.user
            if 'first_name' in user_data:
                user.first_name = user_data['first_name']
            if 'last_name' in user_data:
                user.last_name = user_data['last_name']
            user.save()
        
        return super().update(instance, validated_data)

class CompanyProfileSerializer(PublicIdModelSerializer):
    email = serializers.EmailField(source='user.email', read_only=True)
    first_name = serializers.CharField(source='user.first_name')
    last_name = serializers.CharField(source='user.last_name')
    admin_full_name = serializers.SerializerMethodField()
    
    class Meta:
        model = CompanyEmployerProfile
        fields = [
            'id', 'email', 'first_name', 'last_name', 'admin_full_name',
            'company_name', 'company_registration_number', 'company_size',
            'industry', 'phone_number', 'country', 'city', 'address',
            'website', 'preferred_language',
            'registration_certificate', 'resachetified_license',
            'tax_id_document', 'additional_documents',
            'documents_verified', 'verified_at', 'verification_notes',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['documents_verified', 'verified_at', 'verification_notes', 'created_at', 'updated_at']
    
    def get_admin_full_name(self, obj):
        return obj.user.get_full_name()
    
    def update(self, instance, validated_data):
        user_data = validated_data.pop('user', {})
        if user_data:
            user = instance.user
            if 'first_name' in user_data:
                user.first_name = user_data['first_name']
            if 'last_name' in user_data:
                user.last_name = user_data['last_name']
            user.save()
        
        return super().update(instance, validated_data)


class AdminProfileSerializer(PublicIdModelSerializer):
    email = serializers.EmailField(source='user.email', read_only=True)
    first_name = serializers.CharField(source='user.first_name')
    last_name = serializers.CharField(source='user.last_name')
    role = serializers.CharField(source='user.role', read_only=True)
    
    class Meta:
        model = AdminProfile
        fields = [
            'id', 'email', 'first_name', 'last_name', 'role',
            'department', 'phone_number',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['created_at', 'updated_at']
    
    def update(self, instance, validated_data):
        user_data = validated_data.pop('user', {})
        
        if user_data:
            user = instance.user
            if 'first_name' in user_data:
                user.first_name = user_data['first_name']
            if 'last_name' in user_data:
                user.last_name = user_data['last_name']
            user.save()
        
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        
        instance.save()
        return instance
    
class TeamMemberProfileSerializer(PublicIdModelSerializer):
    email = serializers.EmailField(source='user.email', read_only=True)
    first_name = serializers.CharField(source='user.first_name', read_only=True)
    last_name = serializers.CharField(source='user.last_name', read_only=True)
    full_name = serializers.SerializerMethodField()
    company_name = serializers.CharField(source='company.name', read_only=True)
    company_id = serializers.IntegerField(source='company.id', read_only=True)
    invited_by_name = serializers.SerializerMethodField()
    permissions_display = serializers.SerializerMethodField()
    
    class Meta:
        model = TeamMemberProfile
        fields = [
            'id', 'email', 'first_name', 'last_name', 'full_name',
            'company_id', 'company_name', 'job_title', 'department',
            'phone_number', 'permissions', 'permissions_display',
            'invited_by_name', 'invitation_accepted_at',
            'created_at', 'updated_at'
        ]
    
    def get_full_name(self, obj):
        return obj.user.get_full_name()
    
    def get_invited_by_name(self, obj):
        if obj.invited_by:
            return obj.invited_by.get_full_name()
        return None
    
    def get_permissions_display(self, obj):
        from api.core.constants import CompanyTeamPermissions
        perm_dict = dict(CompanyTeamPermissions.CHOICES)
        return [perm_dict.get(p, p) for p in obj.permissions]
class ProfileSerializer(serializers.Serializer):
    
    def to_representation(self, instance):
        if isinstance(instance, User):
            user = instance
        else:
            user = instance
        
        if user.role == Roles.B2C:
            profile = getattr(user, 'individual_profile', None)
            if profile:
                return IndividualProfileSerializer(profile).data
        elif user.role == Roles.B2B:
            profile = getattr(user, 'company_profile', None)
            if profile:
                return CompanyProfileSerializer(profile).data
        elif user.role in [Roles.ADMIN, Roles.SUPERADMIN]:
            profile = getattr(user, 'admin_profile', None)
            if profile:
                return AdminProfileSerializer(profile).data
        elif user.role == Roles.B2B_TEAM_MEMBER:
            profile = getattr(user, 'team_member_profile', None)
            if profile:
                return TeamMemberProfileSerializer(profile).data
            else:
                return {
                    'id': str(user.public_id),
                    'email': user.email,
                    'first_name': user.first_name,
                    'last_name': user.last_name,
                    'role': user.role,
                    'is_verified': user.is_verified,
                    'department': '',
                    'phone_number': ''
                }
        
        return {
            'id': str(user.public_id),
            'email': user.email,
            'first_name': user.first_name,
            'last_name': user.last_name,
            'role': user.role,
            'is_verified': user.is_verified
        }

class AdminUserSerializer(PublicIdModelSerializer):
    full_name = serializers.SerializerMethodField()
    profile_type = serializers.SerializerMethodField()
    
    class Meta:
        model = User
        fields = [
            'id', 'email', 'first_name', 'last_name', 'full_name',
            'role', 'is_active', 'is_verified', 'is_staff',
            'admin_permissions', 'profile_type',
            'created_at', 'updated_at', 'last_login'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at', 'last_login']
    
    def get_full_name(self, obj):
        return obj.get_full_name()
    
    def get_profile_type(self, obj):
        if obj.role == Roles.SUPERADMIN:
            return "Super Admin"
        elif obj.role == Roles.ADMIN:
            return "Admin"
        return "User"


class CreateAdminSerializer(serializers.Serializer):
    """Serializer for creating admin users"""
    email = serializers.EmailField()
    first_name = serializers.CharField(max_length=150)
    last_name = serializers.CharField(max_length=150)
    password = serializers.CharField(write_only=True, min_length=8, required=False, allow_blank=True)
    confirm_password = serializers.CharField(write_only=True, min_length=8, required=False, allow_blank=True)
    permissions = serializers.ListField(
        child=serializers.ChoiceField(choices=AdminPermissions.CHOICES),
        required=False,
        default=list
    )
    department = serializers.CharField(required=False, allow_blank=True, max_length=100)
    phone_number = serializers.CharField(required=False, allow_blank=True, max_length=20)
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.existing_user = None
    
    def validate_email(self, value):
        try:
            self.existing_user = User.objects.get(email=value)
        except User.DoesNotExist:
            self.existing_user = None
        return value
    
    def validate(self, data):
        if not self.existing_user:
            if not data.get('password'):
                raise serializers.ValidationError({"password": "Password is required for new users."})
            if data['password'] != data['confirm_password']:
                raise serializers.ValidationError({"confirm_password": "Passwords do not match."})
        return data
    
    def create(self, validated_data):
        permissions = validated_data.pop('permissions', [])
        department = validated_data.pop('department', '')
        phone_number = validated_data.pop('phone_number', '')
        validated_data.pop('confirm_password', None)
        password = validated_data.pop('password', None)
        
        if self.existing_user:
            user = self.existing_user
            
            user.first_name = validated_data.get('first_name', user.first_name)
            user.last_name = validated_data.get('last_name', user.last_name)
            user.role = Roles.ADMIN
            user.is_staff = True
            user.is_verified = True
            user.admin_permissions = permissions
            
            if password:
                user.set_password(password)
            
            user.save()
            
            from api.accounts.models import AdminProfile
            
            try:
                profile = AdminProfile.objects.get(user=user)
                profile.department = department
                profile.phone_number = phone_number
                profile.save()
            except AdminProfile.DoesNotExist:
                AdminProfile.objects.create(
                    user=user,
                    department=department,
                    phone_number=phone_number
                )
            
            return user
        
        else:
            user = User.objects.create_user(
                **validated_data,
                role=Roles.ADMIN,
                is_staff=True,
                is_verified=True,
                admin_permissions=permissions
            )
            
            if password:
                user.set_password(password)
                user.save()
            
            from api.accounts.models import AdminProfile
            AdminProfile.objects.create(
                user=user,
                department=department,
                phone_number=phone_number
            )
            
            return user

class EmployerListSerializer(PublicIdModelSerializer):
    full_name = serializers.SerializerMethodField()
    profile_details = serializers.SerializerMethodField()
    documents_status = serializers.SerializerMethodField()
    
    class Meta:
        model = User
        fields = [
            'id', 'email', 'first_name', 'last_name', 'full_name',
            'role', 'is_active', 'is_verified', 'documents_verified',
            'documents_verification_status', 'profile_details',
            'documents_status', 'created_at'
        ]
    
    def get_full_name(self, obj):
        return obj.get_full_name()
    
    def get_profile_details(self, obj):
        profile = obj.get_profile()
        if not profile:
            return {}
        
        if obj.role == Roles.B2C:
            return {
                'passport_id': profile.passport_id,
                'phone_number': profile.phone_number,
                'nationality': profile.nationality,
                'job_role': profile.job_role
            }
        elif obj.role == Roles.B2B:
            return {
                'company_name': profile.company_name,
                'company_registration_number': profile.company_registration_number,
                'company_size': profile.company_size,
                'country': profile.country,
                'city': profile.city
            }
        return {}
    
    def get_documents_status(self, obj):
        profile = obj.get_profile()
        if not profile:
            return {}
        
        if obj.role == Roles.B2C:
            return {
                'id_document': bool(profile.id_document),
                'resume_document': bool(profile.resume_document),
                'verified': profile.documents_verified
            }
        elif obj.role == Roles.B2B:
            return {
                'registration_certificate': bool(profile.registration_certificate),
                'resachetified_license': bool(profile.resachetified_license),
                'tax_id_document': bool(profile.tax_id_document),
                'verified': profile.documents_verified
            }
        return {}


class UserStatusUpdateSerializer(serializers.Serializer):
    is_active = serializers.BooleanField(required=False)
    is_verified = serializers.BooleanField(required=False)
    rejection_reason = serializers.CharField(required=False, allow_blank=True)
    
    def validate(self, data):
        if 'is_verified' in data and not data['is_verified']:
            if 'rejection_reason' not in data or not data['rejection_reason']:
                raise serializers.ValidationError(
                    "Rejection reason is required when rejecting verification."
                )
        return data


class DocumentVerificationSerializer(serializers.Serializer):
    user_id = serializers.IntegerField()
    status = serializers.ChoiceField(choices=['APPROVED', 'REJECTED'])
    rejection_reason = serializers.CharField(required=False, allow_blank=True)
    verification_notes = serializers.CharField(required=False, allow_blank=True)
    
    def validate(self, data):
        if data['status'] == 'REJECTED' and not data.get('rejection_reason'):
            raise serializers.ValidationError(
                "Rejection reason is required when rejecting documents."
            )
        return data

class CompanySerializer(PublicIdModelSerializer):
    admin_name = serializers.SerializerMethodField()
    team_member_count = serializers.SerializerMethodField()
    admin_user_email = serializers.EmailField(source='admin_user.email', read_only=True)
    
    class Meta:
        model = Company
        fields = [
            'id', 'name', 'registration_number', 'company_size',
            'industry', 'phone_number', 'country', 'city',
            'address', 'website', 'admin_user', 'admin_user_email', 'admin_name',
            'is_verified', 'verified_at', 'team_member_count',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'admin_user', 'is_verified', 'verified_at', 'created_at', 'updated_at']
    
    def get_admin_name(self, obj):
        if obj.admin_user:
            return obj.admin_user.get_full_name()
        return None
    
    def get_team_member_count(self, obj):
        from api.accounts.models import User
        return User.objects.filter(company=obj, role=Roles.B2B_TEAM_MEMBER).count()


class TeamMemberProfileSerializer(PublicIdModelSerializer):
    email = serializers.EmailField(source='user.email', read_only=True)
    first_name = serializers.CharField(source='user.first_name', read_only=True)
    last_name = serializers.CharField(source='user.last_name', read_only=True)
    full_name = serializers.SerializerMethodField()
    company_name = serializers.CharField(source='company.name', read_only=True)
    is_active = serializers.BooleanField(source='user.is_active', read_only=True)
    
    class Meta:
        model = TeamMemberProfile
        fields = [
            'id', 'email', 'first_name', 'last_name', 'full_name',
            'company', 'company_name', 'job_title', 'department',
            'phone_number', 'permissions', 'is_active',
            'invitation_accepted_at', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'company', 'invitation_accepted_at', 'created_at', 'updated_at']
    
    def get_full_name(self, obj):
        return obj.user.get_full_name()
    
    def to_representation(self, instance):
        data = super().to_representation(instance)
        perm_dict = dict(CompanyTeamPermissions.CHOICES)
        data['permissions_display'] = [perm_dict.get(p, p) for p in instance.permissions]
        return data


class InviteTeamMemberSerializer(serializers.Serializer):
    email = serializers.EmailField()
    first_name = serializers.CharField(max_length=150)
    last_name = serializers.CharField(max_length=150)
    job_title = serializers.CharField(max_length=100)
    department = serializers.CharField(required=False, allow_blank=True, max_length=100)
    phone_number = serializers.CharField(max_length=20)
    permissions = serializers.ListField(
        child=serializers.ChoiceField(choices=CompanyTeamPermissions.CHOICES),
        required=False,
        default=['view_candidates']
    )
    
    def validate_email(self, value):
        if User.objects.filter(email=value).exists():
            raise serializers.ValidationError("User with this email already exists.")
        return value


class TeamInvitationSerializer(PublicIdModelSerializer):
    company_name = serializers.CharField(source='company.name', read_only=True)
    invited_by_name = serializers.SerializerMethodField()
    
    class Meta:
        model = TeamInvitation
        fields = [
            'id', 'company', 'company_name', 'invited_by', 'invited_by_name',
            'email', 'first_name', 'last_name', 'job_title',
            'permissions', 'token', 'status', 'expires_at',
            'created_at'
        ]
        read_only_fields = ['id', 'token', 'status', 'created_at']
    
    def get_invited_by_name(self, obj):
        if obj.invited_by:
            return obj.invited_by.get_full_name()
        return None


class AcceptInvitationSerializer(serializers.Serializer):
    token = serializers.CharField()
    password = serializers.CharField(write_only=True, min_length=8)
    confirm_password = serializers.CharField(write_only=True, min_length=8)
    
    def validate(self, data):
        if data['password'] != data['confirm_password']:
            raise serializers.ValidationError({"confirm_password": "Passwords do not match."})
        return data


class TeamMemberUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = TeamMemberProfile
        fields = ['job_title', 'department', 'phone_number', 'permissions']
    
    def validate_permissions(self, value):
        valid_permissions = [p[0] for p in CompanyTeamPermissions.CHOICES]
        for perm in value:
            if perm not in valid_permissions:
                raise serializers.ValidationError(f"Invalid permission: {perm}")
        return value
