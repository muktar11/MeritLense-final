from rest_framework.exceptions import PermissionDenied

class PermissionService:

    def can_access(self, user, action, obj=None):
        if not user.is_authenticated:
            raise PermissionDenied("Authentication required.")

        # Placeholder for role + tenant + subscription checks
        return True
