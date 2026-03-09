from rest_framework.permissions import BasePermission, SAFE_METHODS
from api.core.constants import Roles


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
            return request.user.has_perm('can_verify_documents')
        
        return False


class CanManageUsers(BasePermission):
    def has_permission(self, request, view):
        if not request.user.is_authenticated:
            return False
        
        if request.user.role == Roles.SUPERADMIN:
            return True
        
        if request.user.role == Roles.ADMIN:
            return request.user.has_perm('can_manage_users')
        
        return False


class IsCompanyAdmin(BasePermission):
    def has_permission(self, request, view):
        return bool(
            request.user and 
            request.user.is_authenticated and 
            request.user.role == Roles.B2B
        )
    
    def has_object_permission(self, request, view, obj):
        if hasattr(obj, 'company') and hasattr(request.user, 'company_profile'):
            return obj.company == request.user.company_profile
        return False