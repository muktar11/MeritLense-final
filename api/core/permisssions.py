from rest_framework.permissions import BasePermission, SAFE_METHODS
from api.core.constants import CompanyTeamPermissions, Roles


def get_user_company(user):
    """Resolve the Company for a B2B company admin or B2B_TEAM_MEMBER, else
    None (B2C and Candidate accounts have no Company)."""
    company_profile = getattr(user, 'company_profile', None)
    if company_profile is not None:
        return company_profile.company
    team_member_profile = getattr(user, 'team_member_profile', None)
    if team_member_profile is not None:
        return team_member_profile.company
    return None


class IsSuperAdmin(BasePermission):
    def has_permission(self, request, view):
        return bool(
            request.user and 
            request.user.is_authenticated and 
            request.user.role == Roles.SUPERADMIN
        )


class IsAdminOrSuperAdmin(BasePermission):
    def has_permission(self, request, view):
        return bool(
            request.user and 
            request.user.is_authenticated and 
            request.user.role in [Roles.ADMIN, Roles.SUPERADMIN]
        )


class IsB2CUser(BasePermission):
    def has_permission(self, request, view):
        return bool(
            request.user and 
            request.user.is_authenticated and 
            request.user.role == Roles.B2C and
            request.user.is_verified
        )


class IsB2BUser(BasePermission):
    def has_permission(self, request, view):
        return bool(
            request.user and 
            request.user.is_authenticated and 
            request.user.role == Roles.B2B and
            request.user.is_verified
        )


class IsB2BTeamMember(BasePermission):
    def has_permission(self, request, view):
        return bool(
            request.user and 
            request.user.is_authenticated and 
            request.user.role == Roles.B2B_TEAM_MEMBER and
            request.user.is_verified
        )


class IsB2BUserOrTeamMember(BasePermission):
    def has_permission(self, request, view):
        return bool(
            request.user and 
            request.user.is_authenticated and 
            request.user.role in [Roles.B2B, Roles.B2B_TEAM_MEMBER] and
            request.user.is_verified
        )


class IsEmployer(BasePermission):
    def has_permission(self, request, view):
        return bool(
            request.user and 
            request.user.is_authenticated and 
            request.user.role in [Roles.B2C, Roles.B2B, Roles.B2B_TEAM_MEMBER] and
            request.user.is_verified
        )


class HasTeamMemberPermission(BasePermission):
    """A narrow, additive restriction - not a general access gate. Only
    ever restricts a Roles.B2B_TEAM_MEMBER (checked against their
    invite-time TeamMemberProfile.permissions, set by the company admin
    via Invite Team Member); every other role (B2B admin, B2C, Admin/
    SuperAdmin, etc.) passes through unaffected, since this is meant to
    be appended to a view's existing permission_classes - which already
    decide who the view serves at all - not replace them. Subclass and
    set `permission_code` to one of CompanyTeamPermissions' four values.

    Usage: permission_classes = [IsAuthenticated, IsCompanyApproved, RequireSetEvaluation]
    """
    permission_code = None

    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated:
            return False
        if user.role != Roles.B2B_TEAM_MEMBER:
            return True
        profile = getattr(user, 'team_member_profile', None)
        return bool(profile and profile.has_permission(self.permission_code))


class RequireAddCandidates(HasTeamMemberPermission):
    permission_code = CompanyTeamPermissions.ADD_CANDIDATES


class RequireCandidateAccess(HasTeamMemberPermission):
    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated:
            return False
        if user.role != Roles.B2B_TEAM_MEMBER:
            return True
        profile = getattr(user, 'team_member_profile', None)
        return bool(
            profile
            and any(
                profile.has_permission(permission)
                for permission in (
                    CompanyTeamPermissions.ADD_CANDIDATES,
                    CompanyTeamPermissions.SET_EVALUATION,
                    CompanyTeamPermissions.SET_SCORES,
                )
            )
        )


class RequireSetEvaluation(HasTeamMemberPermission):
    permission_code = CompanyTeamPermissions.SET_EVALUATION


class RequireSetScores(HasTeamMemberPermission):
    permission_code = CompanyTeamPermissions.SET_SCORES


class RequireSetPayment(HasTeamMemberPermission):
    permission_code = CompanyTeamPermissions.SET_PAYMENT


class RequireFullTeamAccess(HasTeamMemberPermission):
    """Require all company-team permissions before showing the B2B overview."""

    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated:
            return False
        if user.role != Roles.B2B_TEAM_MEMBER:
            return True
        profile = getattr(user, 'team_member_profile', None)
        return bool(
            profile
            and all(profile.has_permission(permission) for permission in CompanyTeamPermissions.ALL)
        )


class IsOwnerOrAdmin(BasePermission):
    def has_object_permission(self, request, view, obj):
        if request.method in SAFE_METHODS:
            return True
        
        if request.user.role in [Roles.ADMIN, Roles.SUPERADMIN]:
            return True
        
        if hasattr(obj, 'user'):
            return obj.user == request.user
        elif hasattr(obj, 'id'):
            return obj == request.user
        
        return False


class CanVerifyDocuments(BasePermission):
    def has_permission(self, request, view):
        if not request.user.is_authenticated:
            return False
        
        if request.user.role == Roles.SUPERADMIN:
            return True
        
        if request.user.role == Roles.ADMIN:
            return request.user.has_admin_permission('can_verify_documents')
        
        return False


class CanManageUsers(BasePermission):
    def has_permission(self, request, view):
        if not request.user.is_authenticated:
            return False
        
        if request.user.role == Roles.SUPERADMIN:
            return True
        
        if request.user.role == Roles.ADMIN:
            return request.user.has_admin_permission('can_manage_users')
        
        return False


class IsCompanyApproved(BasePermission):
    """Secondary write guard for core company resources.

    The shared JWT authenticator applies the full company-license lockout to
    B2B routes; this permission remains as a resource-level safeguard and is
    not applied to B2C users.
    """
    message = "Your company's trade license is still pending admin approval."

    def has_permission(self, request, view):
        if request.method in SAFE_METHODS:
            return True
        user = request.user
        if not user or not user.is_authenticated or user.role not in (Roles.B2B, Roles.B2B_TEAM_MEMBER):
            return True
        company = get_user_company(user)
        company_profile = getattr(company, "employer_profile", None) if company else None
        return bool(
            company
            and company.business_license_verified
            and company_profile
            and company_profile.resachetified_license
        )


class IsCompanyAdmin(BasePermission):
    def has_permission(self, request, view):
        return bool(
            request.user and 
            request.user.is_authenticated and 
            request.user.role == Roles.B2B
        )
    
    def has_object_permission(self, request, view, obj):
        if hasattr(obj, 'company') and hasattr(request.user, 'company_profile'):
            return obj.company == request.user.company_profile.company
        return False
