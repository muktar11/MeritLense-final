from .models import Subscription
from api.core.constants import Roles


class SubscriptionUsageMixin:
    
    def check_subscription_limit(self, user, feature, increment=1):
        if user.role in [Roles.ADMIN, Roles.SUPERADMIN]:
            return True
        
        if hasattr(user, 'company_profile'):
            subscription = Subscription.objects.filter(
                company=user.company_profile.company,
                status__in=['ACTIVE', 'TRIALING']
            ).first()
        else:
            subscription = Subscription.objects.filter(
                user=user,
                status__in=['ACTIVE', 'TRIALING']
            ).first()
        
        if not subscription:
            return False
        
        return subscription.check_usage_limit(feature, increment)
    
    def increment_subscription_usage(self, user, feature, increment=1):
        if user.role in [Roles.ADMIN, Roles.SUPERADMIN]:
            return True
        
        if hasattr(user, 'company_profile'):
            subscription = Subscription.objects.filter(
                company=user.company_profile.company,
                status__in=['ACTIVE', 'TRIALING']
            ).first()
        else:
            subscription = Subscription.objects.filter(
                user=user,
                status__in=['ACTIVE', 'TRIALING']
            ).first()
        
        if subscription:
            return subscription.increment_usage(feature, increment)
        return False
    
    def decrement_subscription_usage(self, user, feature, decrement=1):
        if user.role in [Roles.ADMIN, Roles.SUPERADMIN]:
            return True
        
        if hasattr(user, 'company_profile'):
            subscription = Subscription.objects.filter(
                company=user.company_profile.company,
                status__in=['ACTIVE', 'TRIALING']
            ).first()
        else:
            subscription = Subscription.objects.filter(
                user=user,
                status__in=['ACTIVE', 'TRIALING']
            ).first()
        
        if subscription:
            return subscription.decrement_usage(feature, decrement)
        return False