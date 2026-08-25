from django.utils import timezone
from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin, BaseUserManager
from django.db import models
from django.core.validators import FileExtensionValidator
from api.core.models import SoftDeleteModel, TimeStampedModel
from api.core.constants import CompanySize, CompanyTeamPermissions, DocumentStatus, JobRoles, Languages, Nationalities, Roles
from django.db.models.signals import post_save
from django.dispatch import receiver
import random


class UserManager(BaseUserManager):

    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError("Email is required")

        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("is_verified", True)
        extra_fields.setdefault("role", Roles.SUPERADMIN)

        return self.create_user(email, password, **extra_fields)
    
    def get_admins(self):
        return self.filter(role__in=[Roles.SUPERADMIN, Roles.ADMIN])
    
    def get_employers(self):
        return self.filter(role__in=[Roles.B2B, Roles.B2C, Roles.B2B_TEAM_MEMBER])


class User(AbstractBaseUser, PermissionsMixin, TimeStampedModel):

    email = models.EmailField(unique=True)
    first_name = models.CharField(max_length=150)
    last_name = models.CharField(max_length=150)

    role = models.CharField(
        max_length=15,
        choices=Roles.CHOICES,
        default=Roles.B2C
    )
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    is_verified = models.BooleanField(default=False)
    
    email_verification_code = models.CharField(max_length=6, null=True, blank=True)
    email_verified_at = models.DateTimeField(null=True, blank=True)
    
    password_reset_token = models.CharField(max_length=255, null=True, blank=True, unique=True)
    password_reset_token_created_at = models.DateTimeField(null=True, blank=True)

    password_changed_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Set whenever the password is changed or reset. Access tokens issued before this are rejected.",
    )

    documents_verified = models.BooleanField(default=False)
    documents_verified_at = models.DateTimeField(null=True, blank=True)
    documents_verification_status = models.CharField(
        max_length=10,
        choices=DocumentStatus.CHOICES,
        default=DocumentStatus.PENDING
    )
    
    rejection_reason = models.TextField(null=True, blank=True)
    
    admin_permissions = models.JSONField(default=list, blank=True)

    profile_picture = models.ImageField(
        upload_to='profile_pictures/',
        null=True,
        blank=True,
        validators=[FileExtensionValidator(['jpg', 'jpeg', 'png', 'webp'])],
    )

    company = models.ForeignKey(
        'Company',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='team_members'
    )
    
    invited_by = models.ForeignKey(
        'self',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='invited_team_members'
    )

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["first_name", "last_name"]

    def __str__(self):
        role_display = dict(Roles.CHOICES).get(self.role, self.role)
        return f"{self.email} ({role_display})"
    
    def get_full_name(self):
        return f"{self.first_name} {self.last_name}".strip()
    
    def generate_verification_code(self):
        code = ''.join([str(random.randint(0, 9)) for _ in range(5)])
        self.email_verification_code = code
        self.save(update_fields=['email_verification_code'])
        return code
    
    def is_password_reset_token_valid(self):
        if not self.password_reset_token_created_at:
            return False
        expiry_time = self.password_reset_token_created_at + timezone.timedelta(hours=24)
        return timezone.now() <= expiry_time
    
    def has_admin_permission(self, permission):
        if self.role == Roles.SUPERADMIN:
            return True
        if self.role == Roles.ADMIN:
            return permission in self.admin_permissions
        return False
    
    def get_profile(self):
        if self.role == Roles.B2C:
            return getattr(self, 'individual_profile', None)
        elif self.role == Roles.B2B:
            return getattr(self, 'company_profile', None)
        elif self.role == Roles.B2B_TEAM_MEMBER:
            return getattr(self, 'team_member_profile', None)
        return None

class IndividualEmployerProfile(TimeStampedModel, SoftDeleteModel):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='individual_profile')
    
    passport_id = models.CharField(max_length=50, unique=True)
    phone_number = models.CharField(max_length=20)
    date_of_birth = models.DateField(null=True, blank=True)
    address = models.CharField(max_length=255, blank=True)
    
    job_role = models.CharField(max_length=2, choices=JobRoles.CHOICES)
    nationality = models.CharField(max_length=2, choices=Nationalities.CHOICES)
    preferred_language = models.CharField(max_length=3, choices=Languages.CHOICES, default=Languages.ENGLISH)
    
    id_document = models.FileField(
        upload_to='b2c/documents/id/',
        validators=[FileExtensionValidator(['pdf', 'jpg', 'jpeg', 'png'])]
    )
    resume_document = models.FileField(
        upload_to='b2c/documents/resume/',
        validators=[FileExtensionValidator(['pdf', 'doc', 'docx'])]
    )
    
    additional_documents = models.FileField(
        upload_to='b2c/documents/additional/',
        null=True, 
        blank=True
    )
    
    documents_verified = models.BooleanField(default=False)
    verified_at = models.DateTimeField(null=True, blank=True)
    verification_notes = models.TextField(null=True, blank=True)
    
    class Meta:
        verbose_name = "Individual Employer Profile"
        verbose_name_plural = "Individual Employer Profiles"
    
    def __str__(self):
        return f"{self.user.get_full_name()} - {self.passport_id}"


class CompanyEmployerProfile(TimeStampedModel, SoftDeleteModel):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='company_profile')
    
    company_name = models.CharField(max_length=255)
    company_registration_number = models.CharField(max_length=100, unique=True)
    company_size = models.CharField(max_length=10, choices=CompanySize.CHOICES)
    industry = models.CharField(max_length=100, blank=True)
    
    phone_number = models.CharField(max_length=20)
    country = models.CharField(max_length=100)
    city = models.CharField(max_length=100)
    address = models.CharField(max_length=255, blank=True)
    website = models.URLField(blank=True)
    
    preferred_language = models.CharField(max_length=3, choices=Languages.CHOICES, default=Languages.ENGLISH)
    
    registration_certificate = models.FileField(
        upload_to='b2b/documents/registration/',
        validators=[FileExtensionValidator(['pdf', 'jpg', 'jpeg', 'png'])]
    )
    resachetified_license = models.FileField(
        upload_to='b2b/documents/license/',
        validators=[FileExtensionValidator(['pdf', 'jpg', 'jpeg', 'png'])]
    )
    tax_id_document = models.FileField(
        upload_to='b2b/documents/tax/',
        null=True,
        blank=True,
        validators=[FileExtensionValidator(['pdf', 'jpg', 'jpeg', 'png'])]
    )
    
    additional_documents = models.FileField(
        upload_to='b2b/documents/additional/',
        null=True,
        blank=True
    )
    
    documents_verified = models.BooleanField(default=False)
    verified_at = models.DateTimeField(null=True, blank=True)
    verification_notes = models.TextField(null=True, blank=True)
    
    company = models.OneToOneField(
        'Company',
        on_delete=models.CASCADE,
        related_name='employer_profile',
        null=True,
        blank=True
    )
    
    class Meta:
        verbose_name = "Company Employer Profile"
        verbose_name_plural = "Company Employer Profiles"
    
    def __str__(self):
        return f"{self.company_name} ({self.company_registration_number})"


class AdminProfile(TimeStampedModel):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='admin_profile')
    department = models.CharField(max_length=100, blank=True)
    phone_number = models.CharField(max_length=20, blank=True)
    
    class Meta:
        verbose_name = "Admin Profile"
        verbose_name_plural = "Admin Profiles"
    
    def __str__(self):
        return f"{self.user.get_full_name()} - Admin"

class Company(TimeStampedModel):
    name = models.CharField(max_length=255)
    registration_number = models.CharField(max_length=100, unique=True)
    company_size = models.CharField(max_length=10)
    industry = models.CharField(max_length=100, blank=True)
    phone_number = models.CharField(max_length=20)
    country = models.CharField(max_length=100)
    city = models.CharField(max_length=100)
    address = models.CharField(max_length=255, blank=True)
    website = models.URLField(blank=True)
    
    admin_user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name='managed_company'
    )
    
    is_verified = models.BooleanField(default=False)
    verified_at = models.DateTimeField(null=True, blank=True)
    verified_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='verified_companies'
    )
    
    registration_certificate = models.FileField(upload_to='companies/certificates/')
    tax_id_document = models.FileField(upload_to='companies/tax/', null=True, blank=True)

    stamp_image = models.ImageField(
        upload_to='companies/stamps/',
        null=True,
        blank=True,
        help_text="Company stamp/seal, applied automatically to signed B2B agreement PDFs.",
    )

    logo = models.ImageField(
        upload_to='companies/logos/',
        null=True,
        blank=True,
        validators=[FileExtensionValidator(['jpg', 'jpeg', 'png', 'webp'])],
        help_text="Company logo shown on the Company Profile page.",
    )

    roles = models.JSONField(
        default=list,
        blank=True,
        help_text="Free-text self-described business roles/categories (e.g. 'Agency', "
                   "'Staffing') shown as badges on the Company Profile page. Not related "
                   "to Candidate.job_role or the interview role_code taxonomy.",
    )

    class Meta:
        verbose_name = "Company"
        verbose_name_plural = "Companies"
        indexes = [
            models.Index(fields=['registration_number']),
            models.Index(fields=['name']),
        ]
    
    def __str__(self):
        return f"{self.name} ({self.registration_number})"
    
    def get_team_members(self):
        return User.objects.filter(company=self, role='B2B_TEAM_MEMBER')
    
    def get_team_member_count(self):
        return self.get_team_members().count()


class TeamMemberProfile(TimeStampedModel):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='team_member_profile')
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name='member_profiles')
    
    job_title = models.CharField(max_length=100)
    department = models.CharField(max_length=100, blank=True)
    
    phone_number = models.CharField(max_length=20)
    
    permissions = models.JSONField(default=list)
    
    invitation_accepted_at = models.DateTimeField(null=True, blank=True)
    invited_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name='team_member_invites'
    )
    
    class Meta:
        verbose_name = "Team Member Profile"
        verbose_name_plural = "Team Member Profiles"
    
    def __str__(self):
        return f"{self.user.get_full_name()} - {self.company.name}"
    
    def has_permission(self, permission):
        return permission in self.permissions
    
    def get_permissions_display(self):
        perm_dict = dict(CompanyTeamPermissions.CHOICES)
        return [perm_dict.get(p, p) for p in self.permissions]


class TeamInvitation(TimeStampedModel):
    STATUS_CHOICES = [
        ('PENDING', 'Pending'),
        ('ACCEPTED', 'Accepted'),
        ('EXPIRED', 'Expired'),
        ('CANCELLED', 'Cancelled'),
    ]
    
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name='invitations')
    invited_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name='sent_invitations')
    
    email = models.EmailField()
    first_name = models.CharField(max_length=150)
    last_name = models.CharField(max_length=150)
    job_title = models.CharField(max_length=100)
    
    permissions = models.JSONField(default=list)
    
    token = models.CharField(max_length=255, unique=True)
    
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='PENDING')
    accepted_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField()
    
    class Meta:
        verbose_name = "Team Invitation"
        verbose_name_plural = "Team Invitations"
        indexes = [
            models.Index(fields=['token']),
            models.Index(fields=['email', 'status']),
        ]
    
    def __str__(self):
        return f"Invitation for {self.email} to {self.company.name}"
    
    def is_valid(self):
        return (
            self.status == 'PENDING' and
            timezone.now() <= self.expires_at
        )

@receiver(post_save, sender=User)
def create_user_profile(sender, instance, created, **kwargs):
    if created:
        if instance.role in [Roles.ADMIN, Roles.SUPERADMIN]:
            AdminProfile.objects.get_or_create(
                user=instance,
                defaults={
                    'department': '',
                    'phone_number': ''
                }
            )