from rest_framework.permissions import BasePermission
from api.core.constants import Roles


class CanManageCandidate(BasePermission):
    def has_permission(self, request, view):
        if not request.user.is_authenticated:
            return False
        
        if view.action == 'create':
            return request.user.role in [Roles.B2C, Roles.B2B, Roles.B2B_TEAM_MEMBER]
        
        return True
    
    def has_object_permission(self, request, view, obj):
        user = request.user
        
        if user.role in [Roles.ADMIN, Roles.SUPERADMIN]:
            return True
        
        if obj.created_by == user:
            return True
        
        if hasattr(user, 'managed_company') and obj.company == user.managed_company:
            return True
        
        if user.role == Roles.B2B_TEAM_MEMBER:
            return obj.created_by == user
        
        return False


class CanViewCandidate(BasePermission):
    def has_permission(self, request, view):
        return request.user.is_authenticated
    
    def has_object_permission(self, request, view, obj):
        user = request.user
        
        if user.role in [Roles.ADMIN, Roles.SUPERADMIN]:
            return True
        
        if obj.created_by == user:
            return True
        
        if hasattr(user, 'company_profile') and obj.company == user.company_profile.company:
            return True
        
        if user.role == Roles.B2B_TEAM_MEMBER and user in obj.shared_with.all():
            return True
        
        return False
