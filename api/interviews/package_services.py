from api.core.constants import CoverageLevel, InterviewEvaluationTier, Roles, SubscriptionStatus
from api.interviews.models import PackageSessionConfig, RolePackageCoverage


PACKAGE_NAME_TO_CODE = {
    "basic": "basic",
    "essential": "essential",
    "advanced": "advanced",
    "premium": "premium",
    "starter": "starter",
    "growth": "growth",
    "business": "business",
    "enterprise": "enterprise",
}


class PackageArchitectureService:
    @classmethod
    def resolve_session_package_context(cls, *, user, role_code, requested_package_code=None):
        package_code = requested_package_code or cls.infer_active_package_code(user)
        if not package_code:
            return None

        package = PackageSessionConfig.objects.filter(
            package_code=package_code,
            is_active=True,
        ).first()
        if package is None:
            return None

        coverage = RolePackageCoverage.objects.filter(
            role_code=role_code,
            package_code=package_code,
            is_active=True,
        ).first()
        if coverage is None:
            return None

        return {
            "package": package,
            "coverage": coverage,
            "evaluation_tier": cls.coverage_to_evaluation_tier(coverage.coverage_level),
            "readiness_indicator_enabled": package.readiness_indicator_enabled and coverage.coverage_level != CoverageLevel.SCREENING,
            "certificate_enabled": package.certificate_enabled and coverage.coverage_level != CoverageLevel.SCREENING,
            "task_observation_enabled": package.task_observation_enabled and coverage.coverage_level != CoverageLevel.SCREENING,
        }

    @classmethod
    def infer_active_package_code(cls, user):
        if not user or not getattr(user, "is_authenticated", False):
            return ""

        try:
            from api.payments.models import Subscription
        except Exception:
            return ""

        subscriptions = Subscription.objects.filter(
            status__in=[SubscriptionStatus.ACTIVE, SubscriptionStatus.TRIALING]
        ).select_related("stripe_price")

        if user.role == Roles.B2B_TEAM_MEMBER and hasattr(user, "team_member_profile"):
            subscriptions = subscriptions.filter(company=user.team_member_profile.company)
        elif user.role == Roles.B2B and hasattr(user, "company_profile"):
            subscriptions = subscriptions.filter(company=user.company_profile.company)
        else:
            subscriptions = subscriptions.filter(user=user)

        subscription = subscriptions.order_by("-created_at").first()
        if subscription is None or subscription.stripe_price is None:
            return ""

        metadata = subscription.stripe_price.metadata or {}
        package_code = metadata.get("package_code")
        if package_code:
            return str(package_code).strip().lower()

        return cls.normalize_package_name(subscription.stripe_price.name)

    @classmethod
    def normalize_package_name(cls, package_name):
        normalized = (package_name or "").strip().lower()
        for key, value in PACKAGE_NAME_TO_CODE.items():
            if key in normalized:
                return value
        return ""

    @classmethod
    def coverage_to_evaluation_tier(cls, coverage_level):
        if coverage_level == CoverageLevel.SCREENING:
            return InterviewEvaluationTier.SCREENING
        return InterviewEvaluationTier.FULL
