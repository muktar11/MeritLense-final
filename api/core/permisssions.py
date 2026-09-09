from rest_framework.permissions import BasePermission, SAFE_METHODS
from api.core.constants import Roles


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
    """B2B company admins and their team members may only create or modify
    core resources (candidates, evaluations) once their company has passed
    admin document review (Company.is_verified). Not applied to B2C, which
    has no Company and its own separate email-verification gate.

    Read-only requests (list/retrieve/GET) are always allowed so a pending
    company can still see its own dashboard while awaiting approval - this
    only blocks write actions.
    """
    message = "Your company's registration is still pending admin approval."

    def has_permission(self, request, view):
        if request.method in SAFE_METHODS:
            return True
        user = request.user
        if not user or not user.is_authenticated or user.role not in (Roles.B2B, Roles.B2B_TEAM_MEMBER):
            return True
        company = get_user_company(user)
        return bool(company and company.is_verified)


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
