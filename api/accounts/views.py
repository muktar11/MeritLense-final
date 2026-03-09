from datetime import timedelta
import secrets
from django.db.models import Q
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.permissions import AllowAny
from django.utils import timezone
from rest_framework.permissions import IsAuthenticated
from api.audit.services import AuditLogService
from api.core.constants import AuditLogAction, AuditLogCategory, AuditLogSeverity, CompanyTeamPermissions, DocumentStatus, Roles
from api.core.permisssions import CanVerifyDocuments, IsAdminOrSuperAdmin, IsB2BTeamMember, IsB2BUser, IsEmployer, IsOwnerOrAdmin, IsSuperAdmin
from .models import Company, TeamInvitation, TeamMemberProfile, User, IndividualEmployerProfile, CompanyEmployerProfile, AdminProfile
from .serializers import (
    AcceptInvitationSerializer,
    AdminProfileSerializer,
    AdminUserSerializer,
    B2CRegistrationSerializer, 
    B2BRegistrationSerializer,
    ChangePasswordSerializer,
    CompanyProfileSerializer,
    CompanySerializer,
    CreateAdminSerializer,
    DocumentVerificationSerializer,
    EmailVerificationSerializer,
    EmployerListSerializer,
    ForgotPasswordSerializer,
    IndividualProfileSerializer,
    InviteTeamMemberSerializer,
    ProfileSerializer,
    ResendVerificationSerializer,
    ResetPasswordSerializer,
    TeamInvitationSerializer,
    TeamMemberProfileSerializer,
    TeamMemberUpdateSerializer,
    UserStatusUpdateSerializer
)
from .utils import send_password_reset_email, send_verification_email, send_team_invitation_email
import random


class StandardResultsSetPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = 'page_size'
    max_page_size = 100
class B2CRegistrationView(APIView):
    permission_classes = [AllowAny]
    
    def post(self, request):
        serializer = B2CRegistrationSerializer(data=request.data, context={'request': request})
        
        if serializer.is_valid():
            user = serializer.save()
            
            profile_data = serializer.context.get('profile_data', {})
            
            profile = IndividualEmployerProfile.objects.create(
                user=user,
                passport_id=profile_data.get('passport_id'),
                job_role=profile_data.get('job_role'),
                nationality=profile_data.get('nationality'),
                preferred_language=profile_data.get('preferred_language'),
                phone_number=profile_data.get('phone_number'),
                date_of_birth=profile_data.get('date_of_birth'),
                address=profile_data.get('address'),
                id_document=profile_data.get('id_document'),
                resume_document=profile_data.get('resume_document')
            )
            
            send_verification_email(user, request)
            
            AuditLogService.log(
                user=user,
                action=AuditLogAction.USER_CREATED,
                category=AuditLogCategory.USER,
                description=f"New B2C user registered: {user.email}",
                resource=user,
                data={'registration_type': 'B2C'},
                request=request
            )
            
            return Response({
                'message': 'Registration successful. Please check your email for the 5-digit verification code.',
                'email': user.email
            }, status=status.HTTP_201_CREATED)
        
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class B2BRegistrationView(APIView):
    permission_classes = [AllowAny]
    
    def post(self, request):
        serializer = B2BRegistrationSerializer(data=request.data, context={'request': request})
        
        if serializer.is_valid():
            user = serializer.save()
            
            profile_data = serializer.context.get('profile_data', {})
            
            profile = CompanyEmployerProfile.objects.create(
                user=user,
                company_name=profile_data.get('company_name'),
                company_registration_number=profile_data.get('company_registration_number'),
                company_size=profile_data.get('company_size'),
                country=profile_data.get('country'),
                city=profile_data.get('city'),
                preferred_language=profile_data.get('preferred_language'),
                phone_number=profile_data.get('phone_number'),
                website=profile_data.get('website'),
                industry=profile_data.get('industry'),
                address=profile_data.get('address'),
                registration_certificate=profile_data.get('registration_certificate'),
                resachetified_license=profile_data.get('resachetified_license'),
                tax_id_document=profile_data.get('tax_id_document')
            )
            
            company = Company.objects.create(
                name=profile.company_name,
                registration_number=profile.company_registration_number,
                company_size=profile.company_size,
                industry=profile.industry,
                phone_number=profile.phone_number,
                country=profile.country,
                city=profile.city,
                address=profile.address,
                website=profile.website,
                admin_user=user,
                registration_certificate=profile.registration_certificate,
                tax_id_document=profile.tax_id_document,
                is_verified=False
            )
            
            profile.company = company
            profile.save()
            
            send_verification_email(user, request)
            
            AuditLogService.log(
                user=user,
                action=AuditLogAction.COMPANY_CREATED,
                category=AuditLogCategory.COMPANY,
                description=f"New company registered: {company.name}",
                resource=company,
                data={'company_name': company.name, 'registration_number': company.registration_number},
                request=request
            )
            
            AuditLogService.log(
                user=user,
                action=AuditLogAction.USER_CREATED,
                category=AuditLogCategory.USER,
                description=f"New B2B user registered: {user.email}",
                resource=user,
                data={'registration_type': 'B2B', 'company': company.name},
                request=request
            )
            
            return Response({
                'message': 'Registration successful. Please check your email for the 5-digit verification code.',
                'email': user.email
            }, status=status.HTTP_201_CREATED)
        
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class EmailVerificationView(APIView):
    permission_classes = [AllowAny]
    
    def post(self, request):
        serializer = EmailVerificationSerializer(data=request.data)
        
        if serializer.is_valid():
            email = serializer.validated_data['email']
            code = serializer.validated_data['code']
            
            try:
                user = User.objects.get(
                    email=email, 
                    email_verification_code=code,
                    is_verified=False
                )
                
                user.is_verified = True
                user.email_verified_at = timezone.now()
                user.email_verification_code = None
                user.save()
                
                AuditLogService.log(
                    user=user,
                    action=AuditLogAction.USER_VERIFIED,
                    category=AuditLogCategory.SECURITY,
                    description=f"Email verified for user: {user.email}",
                    resource=user,
                    request=request
                )
                
                return Response({
                    'message': 'Email verified successfully. You can now log in.',
                    'role': user.role
                }, status=status.HTTP_200_OK)
                
            except User.DoesNotExist:
                
                AuditLogService.log_system(
                    action=AuditLogAction.LOGIN_FAILED,
                    category=AuditLogCategory.SECURITY,
                    description=f"Failed email verification attempt for {email}",
                    severity=AuditLogSeverity.WARNING,
                    data={'email': email}
                )
                
                return Response({
                    'error': 'Invalid email or verification code.'
                }, status=status.HTTP_400_BAD_REQUEST)
                
        
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class ResendVerificationView(APIView):
    permission_classes = [AllowAny]
    
    def post(self, request):
        serializer = ResendVerificationSerializer(data=request.data)
        
        if serializer.is_valid():
            email = serializer.validated_data['email']
            
            try:
                user = User.objects.get(email=email, is_verified=False)
                
                new_code = ''.join([str(random.randint(0, 9)) for _ in range(5)])
                user.email_verification_code = new_code
                user.save()
                
                send_verification_email(user, request)
                
                return Response({
                    'message': 'Verification code sent successfully.'
                }, status=status.HTTP_200_OK)
                
            except User.DoesNotExist:
                return Response({
                    'message': 'If the email exists and is unverified, a verification code will be sent.'
                }, status=status.HTTP_200_OK)
        
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

class ForgotPasswordView(APIView):
    permission_classes = [AllowAny]
    
    def post(self, request):
        serializer = ForgotPasswordSerializer(data=request.data, context={'request': request})
        
        if serializer.is_valid():
            email = serializer.validated_data['email']
            
            user = serializer.context.get('user')
            
            if user:
                send_password_reset_email(user, request)
                AuditLogService.log(
                    user=user,
                    action=AuditLogAction.PASSWORD_RESET,
                    category=AuditLogCategory.SECURITY,
                    description=f"Password reset requested for user: {user.email}",
                    resource=user,
                    request=request
                )
            
            return Response({
                'message': 'If an account exists with this email, you will receive a password reset link.'
            }, status=status.HTTP_200_OK)
        
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class ResetPasswordView(APIView):
    permission_classes = [AllowAny]
    
    def post(self, request):
        serializer = ResetPasswordSerializer(data=request.data)
        
        if serializer.is_valid():
            token = serializer.validated_data['token']
            new_password = serializer.validated_data['password']
            
            try:
                user = User.objects.get(
                    password_reset_token=token,
                    is_active=True
                )
                
                if not user.is_password_reset_token_valid():
                    return Response({
                        'error': 'Password reset link has expired. Please request a new one.'
                    }, status=status.HTTP_400_BAD_REQUEST)
                
                user.set_password(new_password)
                
                user.password_reset_token = None
                user.password_reset_token_created_at = None
                
                user.last_login = None
                
                user.save()
                
                AuditLogService.log(
                    user=user,
                    action=AuditLogAction.PASSWORD_CHANGE,
                    category=AuditLogCategory.SECURITY,
                    description=f"Password reset completed for user: {user.email}",
                    resource=user,
                    request=request
                )
                
                return Response({
                    'message': 'Password has been reset successfully. You can now log in with your new password.'
                }, status=status.HTTP_200_OK)
                
            except User.DoesNotExist:
                return Response({
                    'error': 'Invalid or expired password reset token.'
                }, status=status.HTTP_400_BAD_REQUEST)
        
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

class ChangePasswordView(APIView):
    permission_classes = [IsAuthenticated]
    
    def post(self, request):
        serializer = ChangePasswordSerializer(
            data=request.data,
            context={'request': request}
        )
        
        if serializer.is_valid():
            user = request.user
            new_password = serializer.validated_data['new_password']
            
            user.set_password(new_password)
            
            
            user.save()
            
            AuditLogService.log(
                user=user,
                action=AuditLogAction.PASSWORD_CHANGE,
                category=AuditLogCategory.SECURITY,
                description=f"Password changed for user: {user.email}",
                resource=user,
                request=request
            )
            
            return Response({
                'message': 'Password changed successfully. Please log in again with your new password.'
            }, status=status.HTTP_200_OK)
        
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

class ValidateResetTokenView(APIView):
    permission_classes = [AllowAny]
    
    def post(self, request):
        token = request.data.get('token')
        
        if not token:
            return Response({
                'valid': False,
                'error': 'Token is required.'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        try:
            user = User.objects.get(
                password_reset_token=token,
                is_active=True
            )
            
            if user.is_password_reset_token_valid():
                return Response({
                    'valid': True,
                    'message': 'Token is valid.'
                }, status=status.HTTP_200_OK)
            else:
                return Response({
                    'valid': False,
                    'error': 'Token has expired.'
                }, status=status.HTTP_400_BAD_REQUEST)
                
        except User.DoesNotExist:
            return Response({
                'valid': False,
                'error': 'Invalid token.'
            }, status=status.HTTP_400_BAD_REQUEST)
class ProfileDetailView(APIView):
    permission_classes = [IsAuthenticated, IsOwnerOrAdmin]
    
    def get(self, request):
        serializer = ProfileSerializer(request.user)
        return Response(serializer.data)
    
    def patch(self, request):
        user = request.user
        
        if user.role == Roles.B2C:
            profile = get_object_or_404(IndividualEmployerProfile, user=user)
            serializer = IndividualProfileSerializer(profile, data=request.data, partial=True)
        elif user.role == Roles.B2B:
            profile = get_object_or_404(CompanyEmployerProfile, user=user)
            serializer = CompanyProfileSerializer(profile, data=request.data, partial=True)
        elif user.role in [Roles.ADMIN, Roles.SUPERADMIN]:
            try:
                profile = AdminProfile.objects.get(user=user)
            except AdminProfile.DoesNotExist:
                profile = AdminProfile.objects.create(
                    user=user,
                    department='',
                    phone_number=''
                )
            serializer = AdminProfileSerializer(profile, data=request.data, partial=True)
        elif user.role == Roles.B2B_TEAM_MEMBER: 
            profile = get_object_or_404(TeamMemberProfile, user=user)
            allowed_fields = ['job_title', 'department', 'phone_number']
            update_data = {k: v for k, v in request.data.items() if k in allowed_fields}
            serializer = TeamMemberProfileSerializer(profile, data=update_data, partial=True)  
        else:
            return Response({'error': 'Invalid user role'}, status=status.HTTP_400_BAD_REQUEST)
        
        if serializer.is_valid():
            serializer.save()
            AuditLogService.log(
                user=user,
                action=AuditLogAction.USER_UPDATED,
                category=AuditLogCategory.USER,
                description=f"Profile updated for user: {user.email}",
                resource=user,
                data={'changes': request.data},
                request=request
            )
            return Response(serializer.data)
        
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

class ProfileDocumentUploadView(APIView):
    permission_classes = [IsAuthenticated, IsEmployer]
    
    def post(self, request):
        user = request.user
        document_type = request.data.get('document_type')
        document_file = request.FILES.get('document')
        
        if not document_file:
            return Response({'error': 'No document provided'}, status=status.HTTP_400_BAD_REQUEST)
        
        if user.role == Roles.B2C:
            profile = get_object_or_404(IndividualEmployerProfile, user=user)
            
            if document_type == 'id':
                profile.id_document = document_file
            elif document_type == 'resume':
                profile.resume_document = document_file
            elif document_type == 'additional':
                profile.additional_documents = document_file
            else:
                return Response({'error': 'Invalid document type'}, status=status.HTTP_400_BAD_REQUEST)
            
            profile.save()
            serializer = IndividualProfileSerializer(profile)
            
        elif user.role == Roles.B2B:
            profile = get_object_or_404(CompanyEmployerProfile, user=user)
            
            if document_type == 'registration':
                profile.registration_certificate = document_file
            elif document_type == 'license':
                profile.resachetified_license = document_file
            elif document_type == 'tax':
                profile.tax_id_document = document_file
            elif document_type == 'additional':
                profile.additional_documents = document_file
            else:
                return Response({'error': 'Invalid document type'}, status=status.HTTP_400_BAD_REQUEST)
            
            profile.save()
            serializer = CompanyProfileSerializer(profile)
        else:
            return Response({'error': 'Invalid user role'}, status=status.HTTP_400_BAD_REQUEST)
        
        AuditLogService.log(
                user=user,
                action=AuditLogAction.USER_UPDATED,
                category=AuditLogCategory.USER,
                description=f"Profile updated for user: {user.email}",
                resource=user,
                data={'changes': request.data},
                request=request
            )
        return Response({
            'message': f'{document_type} uploaded successfully',
            'profile': serializer.data
        }, status=status.HTTP_200_OK)
        

class AdminStatsView(APIView):
    permission_classes = [IsAuthenticated, IsAdminOrSuperAdmin]
    
    def get(self, request):
        total_users = User.objects.count()
        total_b2c = User.objects.filter(role=Roles.B2C).count()
        total_b2b = User.objects.filter(role=Roles.B2B).count()
        
        pending_email_verification = User.objects.filter(is_verified=False).count()
        pending_document_verification = (
            IndividualEmployerProfile.objects.filter(documents_verified=False).count() +
            CompanyEmployerProfile.objects.filter(documents_verified=False).count()
        )
        
        week_ago = timezone.now() - timedelta(days=7)
        recent_users = User.objects.filter(created_at__gte=week_ago).count()
        
        return Response({
            'total_users': total_users,
            'total_b2c': total_b2c,
            'total_b2b': total_b2b,
            'pending_email_verification': pending_email_verification,
            'pending_document_verification': pending_document_verification,
            'recent_registrations': recent_users
        })


class AdminUserListView(APIView):
    permission_classes = [IsAuthenticated, IsSuperAdmin]
    pagination_class = StandardResultsSetPagination
    
    def get(self, request):
        admins = User.objects.filter(role__in=[Roles.SUPERADMIN, Roles.ADMIN])
        
        role = request.query_params.get('role')
        if role:
            admins = admins.filter(role=role)
        
        is_active = request.query_params.get('is_active')
        if is_active is not None:
            admins = admins.filter(is_active=is_active.lower() == 'true')
        
        search = request.query_params.get('search')
        if search:
            admins = admins.filter(
                Q(email__icontains=search) |
                Q(first_name__icontains=search) |
                Q(last_name__icontains=search)
            )
        
        paginator = StandardResultsSetPagination()
        paginated_admins = paginator.paginate_queryset(admins, request)
        serializer = AdminUserSerializer(paginated_admins, many=True)
        
        return paginator.get_paginated_response(serializer.data)


class CreateAdminView(APIView):
    permission_classes = [IsAuthenticated, IsSuperAdmin]
    
    def post(self, request):
        serializer = CreateAdminSerializer(data=request.data)
        
        if serializer.is_valid():
            admin_user = serializer.save()
            
            AuditLogService.log(
                user=request.user,
                action=AuditLogAction.USER_CREATED,
                category=AuditLogCategory.USER,
                description=f"Admin user created: {admin_user.email}",
                resource=admin_user,
                data={'created_by': request.user.email},
                request=request
            )
            
            return Response({
                'message': 'Admin user created successfully',
                'admin': AdminUserSerializer(admin_user).data
            }, status=status.HTTP_201_CREATED)
        
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

class AdminUserDetailView(APIView):
    permission_classes = [IsAuthenticated, IsSuperAdmin]
    
    def get_object(self, user_id):
        try:
            return User.objects.get(id=user_id, role__in=[Roles.SUPERADMIN, Roles.ADMIN])
        except User.DoesNotExist:
            return None
    
    def get(self, request, user_id):
        user = self.get_object(user_id)
        if not user:
            return Response({'error': 'Admin user not found'}, status=status.HTTP_404_NOT_FOUND)
        
        serializer = AdminUserSerializer(user)
        return Response(serializer.data)
    
    def patch(self, request, user_id):
        user = self.get_object(user_id)
        if not user:
            return Response({'error': 'Admin user not found'}, status=status.HTTP_404_NOT_FOUND)
        
        allowed_fields = ['first_name', 'last_name', 'is_active', 'admin_permissions']
        update_data = {k: v for k, v in request.data.items() if k in allowed_fields}
        
        serializer = AdminUserSerializer(user, data=update_data, partial=True)
        if serializer.is_valid():
            serializer.save()
            
            AuditLogService.log(
                user=request.user,
                action=AuditLogAction.USER_UPDATED,
                category=AuditLogCategory.USER,
                description=f"Admin user updated: {user.email}",
                resource=user,
                data={'changes': update_data, 'updated_by': request.user.email},
                request=request
            )
            return Response(serializer.data)
        
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    
    def delete(self, request, user_id):
        user = self.get_object(user_id)
        if not user:
            return Response({'error': 'Admin user not found'}, status=status.HTTP_404_NOT_FOUND)
        
        if user.id == request.user.id:
            return Response({'error': 'Cannot delete your own account'}, status=status.HTTP_400_BAD_REQUEST)
        
        user.delete()
        AuditLogService.log(
            user=request.user,
            action=AuditLogAction.USER_DELETED,
            category=AuditLogCategory.USER,
            description=f"Admin user deleted: {user_id}",
            data={'deleted_user': user_id, 'deleted_by': request.user.email},
            request=request
        )
        return Response({'message': 'Admin user deleted successfully'}, status=status.HTTP_200_OK)


class EmployerListView(APIView):
    permission_classes = [IsAuthenticated, IsAdminOrSuperAdmin]
    pagination_class = StandardResultsSetPagination
    
    def get(self, request):
        employers = User.objects.filter(role__in=[Roles.B2B, Roles.B2C, Roles.B2B_TEAM_MEMBER])
        
        role = request.query_params.get('role')
        if role:
            employers = employers.filter(role=role)
        
        verification_status = request.query_params.get('verification_status')
        if verification_status:
            employers = employers.filter(documents_verification_status=verification_status)
        
        is_verified = request.query_params.get('is_verified')
        if is_verified is not None:
            employers = employers.filter(is_verified=is_verified.lower() == 'true')
        
        documents_verified = request.query_params.get('documents_verified')
        if documents_verified is not None:
            employers = employers.filter(documents_verified=documents_verified.lower() == 'true')
        
        search = request.query_params.get('search')
        if search:
            employers = employers.filter(
                Q(email__icontains=search) |
                Q(first_name__icontains=search) |
                Q(last_name__icontains=search)
            )
        
        date_from = request.query_params.get('date_from')
        if date_from:
            employers = employers.filter(created_at__gte=date_from)
        
        date_to = request.query_params.get('date_to')
        if date_to:
            employers = employers.filter(created_at__lte=date_to)
        
        ordering = request.query_params.get('ordering', '-created_at')
        employers = employers.order_by(ordering)
        
        paginator = StandardResultsSetPagination()
        paginated_employers = paginator.paginate_queryset(employers, request)
        serializer = EmployerListSerializer(paginated_employers, many=True)
        
        return paginator.get_paginated_response(serializer.data)


class EmployerDetailView(APIView):
    permission_classes = [IsAuthenticated, IsAdminOrSuperAdmin]
    
    def get_object(self, user_id):
        try:
            return User.objects.get(id=user_id, role__in=[Roles.B2B, Roles.B2C])
        except User.DoesNotExist:
            return None
    
    def get(self, request, user_id):
        user = self.get_object(user_id)
        if not user:
            return Response({'error': 'Employer not found'}, status=status.HTTP_404_NOT_FOUND)
        
        serializer = EmployerListSerializer(user)
        return Response(serializer.data)
    
    def patch(self, request, user_id):
        user = self.get_object(user_id)
        if not user:
            return Response({'error': 'Employer not found'}, status=status.HTTP_404_NOT_FOUND)
        
        serializer = UserStatusUpdateSerializer(data=request.data)
        if serializer.is_valid():
            if 'is_active' in serializer.validated_data:
                user.is_active = serializer.validated_data['is_active']
            
            if 'is_verified' in serializer.validated_data:
                user.is_verified = serializer.validated_data['is_verified']
            
            if 'rejection_reason' in serializer.validated_data:
                user.rejection_reason = serializer.validated_data['rejection_reason']
            
            user.save()
            
            AuditLogService.log(
                user=request.user,
                action=AuditLogAction.USER_UPDATED,
                category=AuditLogCategory.USER,
                description=f"Employer status updated: {user.email}",
                resource=user,
                data={'changes': serializer.validated_data, 'updated_by': request.user.email},
                request=request
            )
            
            return Response({
                'message': 'User status updated successfully',
                'user': EmployerListSerializer(user).data
            })
        
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class PendingVerificationListView(APIView):
    permission_classes = [IsAuthenticated, CanVerifyDocuments]
    
    def get(self, request):
        pending_users = User.objects.filter(
            documents_verification_status=DocumentStatus.PENDING,
            role__in=[Roles.B2B, Roles.B2C]
        ).order_by('created_at')
        
        result = []
        for user in pending_users:
            profile = user.get_profile()
            if profile:
                user_data = EmployerListSerializer(user).data
                
                if user.role == Roles.B2C:
                    user_data['documents'] = {
                        'id_document': request.build_absolute_uri(profile.id_document.url) if profile.id_document else None,
                        'resume_document': request.build_absolute_uri(profile.resume_document.url) if profile.resume_document else None,
                    }
                elif user.role == Roles.B2B:
                    user_data['documents'] = {
                        'registration_certificate': request.build_absolute_uri(profile.registration_certificate.url) if profile.registration_certificate else None,
                        'resachetified_license': request.build_absolute_uri(profile.resachetified_license.url) if profile.resachetified_license else None,
                        'tax_id_document': request.build_absolute_uri(profile.tax_id_document.url) if profile.tax_id_document else None,
                    }
                
                result.append(user_data)
        
        return Response({
            'count': len(result),
            'results': result
        })


class VerifyDocumentsView(APIView):
    permission_classes = [IsAuthenticated, CanVerifyDocuments]
    
    def post(self, request):
        serializer = DocumentVerificationSerializer(data=request.data)
        
        if serializer.is_valid():
            try:
                user = User.objects.get(
                    id=serializer.validated_data['user_id'],
                    role__in=[Roles.B2B, Roles.B2C]
                )
            except User.DoesNotExist:
                return Response({'error': 'User not found'}, status=status.HTTP_404_NOT_FOUND)
            
            profile = user.get_profile()
            if not profile:
                return Response({'error': 'User profile not found'}, status=status.HTTP_404_NOT_FOUND)
            
            status_value = serializer.validated_data['status']
            
            if status_value == 'APPROVED':
                user.documents_verified = True
                user.documents_verification_status = DocumentStatus.APPROVED
                user.documents_verified_at = timezone.now()
                profile.documents_verified = True
                profile.verified_at = timezone.now()
                profile.verification_notes = serializer.validated_data.get('verification_notes', '')
                
                message = 'Documents approved successfully'
                
                AuditLogService.log(
                    user=request.user,
                    action=AuditLogAction.DOCUMENT_VERIFIED,
                    category=AuditLogCategory.DOCUMENT,
                    description=f"Documents approved for {user.get_full_name()} ({user.email})",
                    resource=user,
                    data={
                        'verifier': request.user.email,
                        'verification_notes': serializer.validated_data.get('verification_notes', ''),
                        'user_role': user.role
                    },
                    request=request
                )
                
            else:
                user.documents_verified = False
                user.documents_verification_status = DocumentStatus.REJECTED
                user.documents_verified_at = None
                user.rejection_reason = serializer.validated_data.get('rejection_reason', '')
                profile.documents_verified = False
                profile.verified_at = None
                profile.verification_notes = serializer.validated_data.get('verification_notes', '')
                
                message = 'Documents rejected'
                
                AuditLogService.log(
                    user=request.user,
                    action=AuditLogAction.DOCUMENT_REJECTED,
                    category=AuditLogCategory.DOCUMENT,
                    description=f"Documents rejected for {user.get_full_name()} ({user.email})",
                    resource=user,
                    data={
                        'verifier': request.user.email,
                        'rejection_reason': serializer.validated_data.get('rejection_reason', ''),
                        'verification_notes': serializer.validated_data.get('verification_notes', ''),
                        'user_role': user.role
                    },
                    request=request
                )
            
            user.save()
            profile.save()
            
            return Response({
                'message': message,
                'user': EmployerListSerializer(user).data
            })
        
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class RejectDocumentsView(APIView):
    permission_classes = [IsAuthenticated, CanVerifyDocuments]
    
    def post(self, request):
        user_id = request.data.get('user_id')
        rejection_reason = request.data.get('rejection_reason', '')
        verification_notes = request.data.get('verification_notes', '')
        
        if not user_id:
            return Response({'error': 'user_id is required'}, status=status.HTTP_400_BAD_REQUEST)
        
        if not rejection_reason:
            return Response({'error': 'rejection_reason is required'}, status=status.HTTP_400_BAD_REQUEST)
        
        try:
            user = User.objects.get(
                id=user_id,
                role__in=[Roles.B2B, Roles.B2C]
            )
        except User.DoesNotExist:
            return Response({'error': 'User not found'}, status=status.HTTP_404_NOT_FOUND)
        
        profile = user.get_profile()
        if not profile:
            return Response({'error': 'User profile not found'}, status=status.HTTP_404_NOT_FOUND)
        
        user.documents_verified = False
        user.documents_verification_status = DocumentStatus.REJECTED
        user.documents_verified_at = None
        user.rejection_reason = rejection_reason
        user.save()
        
        profile.documents_verified = False
        profile.verified_at = None
        profile.verification_notes = verification_notes
        profile.save()
        
        AuditLogService.log(
            user=request.user,
            action=AuditLogAction.DOCUMENT_REJECTED,
            category=AuditLogCategory.DOCUMENT,
            description=f"Documents rejected for {user.get_full_name()} ({user.email})",
            resource=user,
            data={
                'verifier': request.user.email,
                'rejection_reason': rejection_reason,
                'verification_notes': verification_notes,
                'user_role': user.role
            },
            request=request
        )
        
        return Response({
            'message': 'Documents rejected successfully',
            'user': EmployerListSerializer(user).data
        }, status=status.HTTP_200_OK)
class AdminDashboardStatsView(APIView):
    permission_classes = [IsAuthenticated, IsAdminOrSuperAdmin]
    
    def get(self, request):
        total_users = User.objects.count()
        total_admins = User.objects.filter(role__in=[Roles.SUPERADMIN, Roles.ADMIN]).count()
        total_b2c = User.objects.filter(role=Roles.B2C).count()
        total_b2b = User.objects.filter(role=Roles.B2B).count()
        
        pending_email = User.objects.filter(is_verified=False).count()
        pending_documents = User.objects.filter(
            documents_verification_status=DocumentStatus.PENDING
        ).count()
        
        approved_documents = User.objects.filter(
            documents_verification_status=DocumentStatus.APPROVED
        ).count()
        
        rejected_documents = User.objects.filter(
            documents_verification_status=DocumentStatus.REJECTED
        ).count()
        
        recent_users = User.objects.order_by('-created_at')[:10]
        recent_users_data = EmployerListSerializer(recent_users, many=True).data
        
        return Response({
            'user_stats': {
                'total_users': total_users,
                'total_admins': total_admins,
                'total_b2c': total_b2c,
                'total_b2b': total_b2b,
            },
            'verification_stats': {
                'pending_email': pending_email,
                'pending_documents': pending_documents,
                'approved_documents': approved_documents,
                'rejected_documents': rejected_documents,
            },
            'recent_users': recent_users_data
        })

class CompanyProfileView(APIView):
    permission_classes = [IsAuthenticated, IsB2BUser]
    
    def get(self, request):
        try:
            if hasattr(request.user, 'managed_company'):
                company = request.user.managed_company
                serializer = CompanySerializer(company)
                return Response(serializer.data)
            
            if hasattr(request.user, 'company_profile') and request.user.company_profile.company:
                company = request.user.company_profile.company
                serializer = CompanySerializer(company)
                return Response(serializer.data)
            
            return Response(
                {'error': 'No company found for this user'},
                status=status.HTTP_404_NOT_FOUND
            )
            
        except Exception as e:
            return Response(
                {'error': str(e)},
                status=status.HTTP_400_BAD_REQUEST
            )
    
    def patch(self, request):
        try:
            if hasattr(request.user, 'managed_company'):
                company = request.user.managed_company
            elif hasattr(request.user, 'company_profile') and request.user.company_profile.company:
                company = request.user.company_profile.company
            else:
                return Response(
                    {'error': 'No company found for this user'},
                    status=status.HTTP_404_NOT_FOUND
                )
            
            serializer = CompanySerializer(company, data=request.data, partial=True)
            
            if serializer.is_valid():
                serializer.save()
                
                if hasattr(request.user, 'company_profile'):
                    profile = request.user.company_profile
                    if 'name' in request.data:
                        profile.company_name = request.data['name']
                    if 'phone_number' in request.data:
                        profile.phone_number = request.data['phone_number']
                    if 'country' in request.data:
                        profile.country = request.data['country']
                    if 'city' in request.data:
                        profile.city = request.data['city']
                    if 'address' in request.data:
                        profile.address = request.data['address']
                    if 'website' in request.data:
                        profile.website = request.data['website']
                    if 'industry' in request.data:
                        profile.industry = request.data['industry']
                    profile.save()
                
                return Response(serializer.data)
            
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
            
        except Exception as e:
            return Response(
                {'error': str(e)},
                status=status.HTTP_400_BAD_REQUEST
            )

class MyCompanyInfoView(APIView):
    permission_classes = [IsAuthenticated, IsB2BTeamMember]
    
    def get(self, request):
        try:
            profile = request.user.team_member_profile
            company = profile.company
            
            from api.payments.models import Subscription
            active_subscription = Subscription.objects.filter(
                company=company,
                status__in=['ACTIVE', 'TRIALING']
            ).first()
            
            data = {
                'id': company.id,
                'name': company.name,
                'registration_number': company.registration_number,
                'company_size': company.company_size,
                'industry': company.industry,
                'country': company.country,
                'city': company.city,
                'address': company.address,
                'phone_number': company.phone_number,
                'website': company.website,
                'is_verified': company.is_verified,
                'subscription': {
                    'status': active_subscription.status if active_subscription else None,
                    'plan': active_subscription.stripe_price.name if active_subscription and active_subscription.stripe_price else None,
                } if active_subscription else None
            }
            return Response(data)
            
        except (TeamMemberProfile.DoesNotExist, AttributeError):
            return Response(
                {'error': 'Team member profile not found'},
                status=status.HTTP_404_NOT_FOUND
            )
class TeamMemberListView(APIView):
    permission_classes = [IsAuthenticated, IsB2BUser]
    
    def get(self, request):
        try:
            if not hasattr(request.user, 'company_profile'):
                return Response(
                    {'error': 'No company profile found'},
                    status=status.HTTP_404_NOT_FOUND
                )
            
            company = request.user.company_profile.company
            team_members = TeamMemberProfile.objects.filter(
                company=company
            ).select_related('user').order_by('-created_at')
            
            serializer = TeamMemberProfileSerializer(team_members, many=True)
            return Response(serializer.data)
            
        except Exception as e:
            return Response(
                {'error': str(e)},
                status=status.HTTP_400_BAD_REQUEST
            )


class InviteTeamMemberView(APIView):
    permission_classes = [IsAuthenticated, IsB2BUser]
    
    def post(self, request):
        serializer = InviteTeamMemberSerializer(data=request.data)
        
        if serializer.is_valid():
            try:
                if not hasattr(request.user, 'company_profile'):
                    return Response(
                        {'error': 'No company profile found'},
                        status=status.HTTP_404_NOT_FOUND
                    )
                
                company = request.user.company_profile.company
                
                token = secrets.token_urlsafe(32)
                
                expires_at = timezone.now() + timedelta(days=7)
                
                invitation = TeamInvitation.objects.create(
                    company=company,
                    invited_by=request.user,
                    email=serializer.validated_data['email'],
                    first_name=serializer.validated_data['first_name'],
                    last_name=serializer.validated_data['last_name'],
                    job_title=serializer.validated_data['job_title'],
                    permissions=serializer.validated_data.get('permissions', ['view_candidates']),
                    token=token,
                    expires_at=expires_at
                )
                
                try:
                    
                    send_team_invitation_email(invitation, request)
                    
                except Exception as email_error:
                    print(f"Email sending failed: {str(email_error)}")
                
                AuditLogService.log(
                    user=request.user,
                    action=AuditLogAction.TEAM_MEMBER_INVITED,
                    category=AuditLogCategory.TEAM,
                    description=f"Team member invited: {invitation.email} to {company.name}",
                    resource=invitation,
                    data={
                        'invited_email': invitation.email,
                        'job_title': invitation.job_title,
                        'permissions': invitation.permissions
                    },
                    request=request
                )
                
                return Response({
                    'message': 'Invitation sent successfully',
                    'invitation': TeamInvitationSerializer(invitation).data
                }, status=status.HTTP_201_CREATED)
                
            except Exception as e:
                return Response(
                    {'error': str(e)},
                    status=status.HTTP_400_BAD_REQUEST
                )
        
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class PendingInvitationsView(APIView):
    permission_classes = [IsAuthenticated, IsB2BUser]
    
    def get(self, request):
        try:
            if not hasattr(request.user, 'company_profile'):
                return Response(
                    {'error': 'No company profile found'},
                    status=status.HTTP_404_NOT_FOUND
                )
            
            company = request.user.company_profile.company
            invitations = TeamInvitation.objects.filter(
                company=company,
                status='PENDING'
            ).order_by('-created_at')
            
            serializer = TeamInvitationSerializer(invitations, many=True)
            return Response(serializer.data)
            
        except Exception as e:
            return Response(
                {'error': str(e)},
                status=status.HTTP_400_BAD_REQUEST
            )


class CancelInvitationView(APIView):
    permission_classes = [IsAuthenticated, IsB2BUser]
    
    def post(self, request, invitation_id):
        try:
            if not hasattr(request.user, 'company_profile'):
                return Response(
                    {'error': 'No company profile found'},
                    status=status.HTTP_404_NOT_FOUND
                )
            
            invitation = TeamInvitation.objects.get(
                id=invitation_id,
                company=request.user.company_profile.company,
                status='PENDING'
            )
            
            invitation.status = 'CANCELLED'
            invitation.save()
            
            AuditLogService.log(
                user=request.user,
                action=AuditLogAction.TEAM_MEMBER_REMOVED,
                category=AuditLogCategory.TEAM,
                description=f"Team invitation cancelled for {invitation.email}",
                resource=invitation,
                data={'cancelled_by': request.user.email},
                request=request
            )
            
            return Response({'message': 'Invitation cancelled successfully'})
            
        except TeamInvitation.DoesNotExist:
            return Response(
                {'error': 'Invitation not found or cannot be cancelled'},
                status=status.HTTP_404_NOT_FOUND
            )
        except Exception as e:
            return Response(
                {'error': str(e)},
                status=status.HTTP_400_BAD_REQUEST
            )


class AcceptInvitationView(APIView):
    permission_classes = []
    
    def post(self, request):
        serializer = AcceptInvitationSerializer(data=request.data)
        
        if serializer.is_valid():
            token = serializer.validated_data['token']
            
            try:
                invitation = TeamInvitation.objects.get(
                    token=token,
                    status='PENDING'
                )
                
                if not invitation.is_valid():
                    return Response(
                        {'error': 'Invitation has expired'},
                        status=status.HTTP_400_BAD_REQUEST
                    )
                
                user = User.objects.create_user(
                    email=invitation.email,
                    password=serializer.validated_data['password'],
                    first_name=invitation.first_name,
                    last_name=invitation.last_name,
                    role=Roles.B2B_TEAM_MEMBER,
                    is_verified=True,
                    company=invitation.company
                )
                
                team_profile = TeamMemberProfile.objects.create(
                    user=user,
                    company=invitation.company,
                    job_title=invitation.job_title,
                    permissions=invitation.permissions,
                    invitation_accepted_at=timezone.now(),
                    invited_by=invitation.invited_by
                )
                
                invitation.status = 'ACCEPTED'
                invitation.accepted_at = timezone.now()
                invitation.save()
                
                AuditLogService.log(
                    user=user,
                    action=AuditLogAction.TEAM_MEMBER_JOINED,
                    category=AuditLogCategory.TEAM,
                    description=f"Team member joined: {user.email} joined {invitation.company.name}",
                    resource=user,
                    data={
                        'company': invitation.company.name,
                        'job_title': invitation.job_title,
                        'invited_by': invitation.invited_by.email if invitation.invited_by else None
                    },
                    request=request
                )
                
                return Response({
                    'message': 'Invitation accepted successfully. You can now log in.',
                    'email': user.email
                }, status=status.HTTP_201_CREATED)
                
            except TeamInvitation.DoesNotExist:
                return Response(
                    {'error': 'Invalid invitation token'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            except Exception as e:
                return Response(
                    {'error': str(e)},
                    status=status.HTTP_400_BAD_REQUEST
                )
        
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class TeamMemberDetailView(APIView):
    permission_classes = [IsAuthenticated, IsB2BUser]
    
    def get_object(self, member_id, user):
        try:
            if not hasattr(user, 'company_profile'):
                return None
            
            company = user.company_profile.company
            return TeamMemberProfile.objects.get(
                id=member_id,
                company=company
            )
        except TeamMemberProfile.DoesNotExist:
            return None
    
    def get(self, request, member_id):
        member = self.get_object(member_id, request.user)
        if not member:
            return Response(
                {'error': 'Team member not found'},
                status=status.HTTP_404_NOT_FOUND
            )
        
        serializer = TeamMemberProfileSerializer(member)
        return Response(serializer.data)
    
    def patch(self, request, member_id):
        member = self.get_object(member_id, request.user)
        if not member:
            return Response(
                {'error': 'Team member not found'},
                status=status.HTTP_404_NOT_FOUND
            )
        
        serializer = TeamMemberUpdateSerializer(member, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            AuditLogService.log(
                user=request.user,
                action=AuditLogAction.TEAM_MEMBER_UPDATED,
                category=AuditLogCategory.TEAM,
                description=f"Team member updated: {member.user.email}",
                resource=member,
                data={'changes': request.data, 'updated_by': request.user.email},
                request=request
            )
            return Response(TeamMemberProfileSerializer(member).data)
        
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    
    def delete(self, request, member_id):
        member = self.get_object(member_id, request.user)
        if not member:
            return Response(
                {'error': 'Team member not found'},
                status=status.HTTP_404_NOT_FOUND
            )
        
        user = member.user
        user.is_active = False
        user.save()
        
        member.delete()
        
        AuditLogService.log(
            user=request.user,
            action=AuditLogAction.TEAM_MEMBER_REMOVED,
            category=AuditLogCategory.TEAM,
            description=f"Team member removed: {user.email}",
            data={'removed_by': request.user.email},
            request=request
        )
        
        return Response({'message': 'Team member removed successfully'})

class TeamMemberPermissionsView(APIView):
    permission_classes = [IsAuthenticated, IsB2BUser]
    
    def get(self, request):
        permissions = [
            {'key': p[0], 'label': p[1]}
            for p in CompanyTeamPermissions.CHOICES
        ]
        return Response(permissions)
