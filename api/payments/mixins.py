from .models import Subscription
from api.core.constants import Roles


class SubscriptionUsageMixin:

    def _get_active_subscription(self, user):
        company_profile = getattr(user, 'company_profile', None)
        team_member_profile = getattr(user, 'team_member_profile', None)
        company = (
            company_profile.company if company_profile
            else team_member_profile.company if team_member_profile
            else None
        )

        subscriptions = Subscription.objects.filter(status__in=['ACTIVE', 'TRIALING'])
        if company:
            return subscriptions.filter(company=company).first()
        return subscriptions.filter(user=user, company__isnull=True).first()

    def check_subscription_limit(self, user, feature, increment=1):
        if user.role in [Roles.ADMIN, Roles.SUPERADMIN]:
            return True

        subscription = self._get_active_subscription(user)
        if not subscription:
            return False

        return subscription.check_usage_limit(feature, increment)

    def increment_subscription_usage(self, user, feature, increment=1):
        if user.role in [Roles.ADMIN, Roles.SUPERADMIN]:
            return True

        subscription = self._get_active_subscription(user)
        if subscription:
            return subscription.increment_usage(feature, increment)
        return False

    def decrement_subscription_usage(self, user, feature, decrement=1):
        if user.role in [Roles.ADMIN, Roles.SUPERADMIN]:
            return True

        subscription = self._get_active_subscription(user)
        if subscription:
            return subscription.decrement_usage(feature, decrement)
        return False
