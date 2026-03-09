from rest_framework.permissions import BasePermission
from api.core.constants import Roles


class CanManageEvaluation(BasePermission):
    """
    Permission to manage evaluations (create, update, delete)
    """
    def has_permission(self, request, view):
        # Must be authenticated
        if not request.user.is_authenticated:
            return False
        
        # Any B2C or B2B user can create evaluations
        if view.action == 'create':
            return request.user.role in [Roles.B2C, Roles.B2B, Roles.B2B_TEAM_MEMBER]
        
        # For other actions, we'll check object-level permissions
        return True
    
    def has_object_permission(self, request, view, obj):
        user = request.user
        
        # Admin and superadmin can do anything
        if user.role in [Roles.ADMIN, Roles.SUPERADMIN]:
            return True
        
        # Creator can manage their evaluations
        if obj.created_by == user:
            return True
        
        # B2B company admin can manage all evaluations in their company
        if hasattr(user, 'managed_company') and obj.company == user.managed_company:
            return True
        
        # B2B team members can manage evaluations for candidates they can access
        if user.role == Roles.B2B_TEAM_MEMBER:
            if user in obj.candidate.shared_with.all():
                return True
        
        return False


class CanViewEvaluation(BasePermission):
    """
    Permission to view evaluations
    """
    def has_permission(self, request, view):
        return request.user.is_authenticated
    
    def has_object_permission(self, request, view, obj):
        user = request.user
        
        # Admin can view anything
        if user.role in [Roles.ADMIN, Roles.SUPERADMIN]:
            return True
        
        # Creator can view their own
        if obj.created_by == user:
            return True
        
        # B2B company members can view company evaluations
        if hasattr(user, 'company_profile') and obj.company == user.company_profile.company:
            return True
        
        # B2B team members can view evaluations for candidates they can access
        if user.role == Roles.B2B_TEAM_MEMBER:
            if user in obj.candidate.shared_with.all():
                return True
        
        return False