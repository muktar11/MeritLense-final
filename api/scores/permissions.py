from rest_framework.permissions import BasePermission
from api.core.constants import Roles


def _user_company(user):
    if user.role == Roles.B2B_TEAM_MEMBER:
        profile = getattr(user, 'team_member_profile', None)
        return profile.company if profile else None
    if user.role == Roles.B2B:
        profile = getattr(user, 'company_profile', None)
        return profile.company if profile else None
    return getattr(user, 'managed_company', None)


class CanManageScores(BasePermission):
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
        
        if hasattr(obj, 'candidate') and obj.candidate.created_by == user:
            return True
        
        if hasattr(user, 'managed_company') and obj.company == user.managed_company:
            return True
        
        company = _user_company(user)
        candidate = getattr(obj, 'candidate', None)
        return bool(company and (obj.company == company or (candidate and candidate.company == company)))


class CanViewScores(BasePermission):
    def has_permission(self, request, view):
        return request.user.is_authenticated
    
    def has_object_permission(self, request, view, obj):
        user = request.user
        
        if user.role in [Roles.ADMIN, Roles.SUPERADMIN]:
            return True
        
        if obj.created_by == user:
            return True
        
        if hasattr(obj, 'candidate') and obj.candidate.created_by == user:
            return True
        
        company = _user_company(user)
        candidate = getattr(obj, 'candidate', None)
        return bool(company and (obj.company == company or (candidate and candidate.company == company)))