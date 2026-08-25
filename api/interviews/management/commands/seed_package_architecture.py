from django.core.management.base import BaseCommand
from django.db import transaction

from api.core.constants import CoverageLevel, InterviewEvaluationTier, PackageAudience
from api.interviews.models import PackageSessionConfig, RolePackageCoverage

try:
    from api.payments.models import Price
except Exception:  # pragma: no cover - optional during partial environments
    Price = None


PACKAGE_CONFIGS = [
    {
        "package_code": "basic",
        "package_name": "Basic",
        "audience": PackageAudience.B2C,
        "evaluation_tier": InterviewEvaluationTier.SCREENING,
        "min_questions": 5,
        "max_questions": 8,
        "default_question_count": 8,
        "duration_minutes": 15,
        "task_observation_enabled": False,
        "readiness_indicator_enabled": False,
        "certificate_enabled": False,
        "basic_report_enabled": True,
        "analytics_enabled": False,
        "api_access_enabled": False,
        "video_introduction_enabled": False,
        "behavioral_indicators_enabled": False,
        "points_balance": 50,
        "monthly_fee_display": "€50 one-time",
    },
    {
        "package_code": "essential",
        "package_name": "Essential",
        "audience": PackageAudience.B2C,
        "evaluation_tier": InterviewEvaluationTier.SCREENING,
        "min_questions": 8,
        "max_questions": 10,
        "default_question_count": 10,
        "duration_minutes": 20,
        "task_observation_enabled": False,
        "readiness_indicator_enabled": False,
        "certificate_enabled": False,
        "basic_report_enabled": True,
        "analytics_enabled": False,
        "api_access_enabled": False,
        "video_introduction_enabled": False,
        "behavioral_indicators_enabled": False,
        "points_balance": 80,
        "monthly_fee_display": "€80 one-time",
    },
    {
        "package_code": "advanced",
        "package_name": "Advanced",
        "audience": PackageAudience.B2C,
        "evaluation_tier": InterviewEvaluationTier.FULL,
        "min_questions": 10,
        "max_questions": 12,
        "default_question_count": 12,
        "duration_minutes": 30,
        "task_observation_enabled": True,
        "readiness_indicator_enabled": True,
        "certificate_enabled": True,
        "basic_report_enabled": True,
        "analytics_enabled": False,
        "api_access_enabled": False,
        "video_introduction_enabled": False,
        "behavioral_indicators_enabled": False,
        "points_balance": 150,
        "monthly_fee_display": "€150 one-time",
    },
    {
        "package_code": "premium",
        "package_name": "Premium",
        "audience": PackageAudience.B2C,
        "evaluation_tier": InterviewEvaluationTier.FULL,
        "min_questions": 12,
        "max_questions": 15,
        "default_question_count": 15,
        "duration_minutes": 30,
        "task_observation_enabled": True,
        "readiness_indicator_enabled": True,
        "certificate_enabled": True,
        "basic_report_enabled": True,
        "analytics_enabled": False,
        "api_access_enabled": False,
        "video_introduction_enabled": True,
        "behavioral_indicators_enabled": True,
        "points_balance": 250,
        "monthly_fee_display": "€250 one-time",
    },
    {
        "package_code": "starter",
        "package_name": "Starter",
        "audience": PackageAudience.B2B,
        "evaluation_tier": InterviewEvaluationTier.SCREENING,
        "min_questions": 5,
        "max_questions": 8,
        "default_question_count": 8,
        "duration_minutes": 15,
        "task_observation_enabled": False,
        "readiness_indicator_enabled": False,
        "certificate_enabled": False,
        "basic_report_enabled": True,
        "analytics_enabled": False,
        "api_access_enabled": False,
        "video_introduction_enabled": False,
        "behavioral_indicators_enabled": False,
        "points_balance": None,
        "monthly_fee_display": "Pilot pricing",
    },
    {
        "package_code": "growth",
        "package_name": "Growth",
        "audience": PackageAudience.B2B,
        "evaluation_tier": InterviewEvaluationTier.FULL,
        "min_questions": 10,
        "max_questions": 12,
        "default_question_count": 12,
        "duration_minutes": 25,
        "task_observation_enabled": True,
        "readiness_indicator_enabled": True,
        "certificate_enabled": True,
        "basic_report_enabled": True,
        "analytics_enabled": True,
        "api_access_enabled": False,
        "video_introduction_enabled": True,
        "behavioral_indicators_enabled": False,
        "points_balance": 2000,
        "monthly_fee_display": "€2,000 / mo",
    },
    {
        "package_code": "business",
        "package_name": "Business",
        "audience": PackageAudience.B2B,
        "evaluation_tier": InterviewEvaluationTier.FULL,
        "min_questions": 12,
        "max_questions": 15,
        "default_question_count": 15,
        "duration_minutes": 30,
        "task_observation_enabled": True,
        "readiness_indicator_enabled": True,
        "certificate_enabled": True,
        "basic_report_enabled": True,
        "analytics_enabled": True,
        "api_access_enabled": False,
        "video_introduction_enabled": True,
        "behavioral_indicators_enabled": True,
        "points_balance": 3500,
        "monthly_fee_display": "€3,500 / mo",
    },
    {
        "package_code": "enterprise",
        "package_name": "Enterprise",
        "audience": PackageAudience.B2B,
        "evaluation_tier": InterviewEvaluationTier.FULL,
        "min_questions": 12,
        "max_questions": 15,
        "default_question_count": 15,
        "duration_minutes": 30,
        "task_observation_enabled": True,
        "readiness_indicator_enabled": True,
        "certificate_enabled": True,
        "basic_report_enabled": True,
        "analytics_enabled": True,
        "api_access_enabled": True,
        "video_introduction_enabled": True,
        "behavioral_indicators_enabled": True,
        "points_balance": None,
        "monthly_fee_display": "Custom",
    },
]


B2B_ROLE_COVERAGE = {
    "domestic_worker": "Screening,Full,Full,Full",
    "child_caregiver": "Screening,Full,Full,Full",
    "elderly_caregiver": "Screening,Full,Full,Full",
    "special_needs_caregiver": "Screening,Partial,Full,Full",
    "nursing_assistant": "Screening,Partial,Full,Full",
    "home_care_assistant": "Screening,Partial,Full,Full",
    "elderly_medical_support": "Screening,Partial,Full,Full",
    "basic_patient_support": "Screening,Full,Full,Full",
    "hotel_housekeeper": "Screening,Full,Full,Full",
    "front_desk_agent": "Screening,Partial,Full,Full",
    "restaurant_staff": "Screening,Full,Full,Full",
    "security_guard": "Screening,Full,Full,Full",
    "event_security": "Screening,Full,Full,Full",
    "commercial_cleaner": "Screening,Full,Full,Full",
    "industrial_cleaner": "Screening,Partial,Full,Full",
    "warehouse_staff": "Screening,Full,Full,Full",
    "driver": "Screening,Full,Full,Full",
    "general_labor": "Screening,Full,Full,Full",
    "skilled_trades": "Screening,Partial,Full,Full",
    "farm_worker": "Screening,Full,Full,Full",
    "livestock_support": "Screening,Full,Full,Full",
}


B2C_ROLE_COVERAGE = {
    "domestic_worker": "Screening,Screening,Full,Full",
    "child_caregiver": "Screening,Screening,Full,Full",
    "elderly_caregiver": "Screening,Screening,Full,Full",
    "special_needs_caregiver": "Screening,Screening,Partial,Full",
    "nursing_assistant": "Screening,Screening,Partial,Full",
    "home_care_assistant": "Screening,Screening,Full,Full",
    "elderly_medical_support": "Screening,Screening,Partial,Full",
    "basic_patient_support": "Screening,Screening,Full,Full",
    "hotel_housekeeper": "Screening,Screening,Full,Full",
    "front_desk_agent": "Screening,Partial,Partial,Full",
    "restaurant_staff": "Screening,Screening,Full,Full",
    "security_guard": "Screening,Screening,Full,Full",
    "event_security": "Screening,Screening,Full,Full",
    "commercial_cleaner": "Screening,Screening,Full,Full",
    "industrial_cleaner": "Screening,Screening,Partial,Full",
    "warehouse_staff": "Screening,Screening,Full,Full",
    "driver": "Screening,Screening,Full,Full",
    "general_labor": "Screening,Screening,Full,Full",
    "skilled_trades": "Screening,Screening,Partial,Full",
    "farm_worker": "Screening,Screening,Full,Full",
    "livestock_support": "Screening,Screening,Full,Full",
}


ROLE_NAMES = {
    "domestic_worker": "Domestic Worker (Housekeeper)",
    "child_caregiver": "Child Caregiver",
    "elderly_caregiver": "Elderly Caregiver",
    "special_needs_caregiver": "Special Needs Caregiver",
    "nursing_assistant": "Nursing Assistant",
    "home_care_assistant": "Home Care Assistant",
    "elderly_medical_support": "Elderly Medical Support",
    "basic_patient_support": "Basic Patient Support",
    "hotel_housekeeper": "Hotel Housekeeper",
    "front_desk_agent": "Front Desk & Guest Service",
    "restaurant_staff": "Restaurant Staff",
    "security_guard": "Security Guard",
    "event_security": "Event Security",
    "commercial_cleaner": "Commercial Cleaner",
    "industrial_cleaner": "Industrial Cleaner",
    "warehouse_staff": "Warehouse Staff",
    "driver": "Driver",
    "general_labor": "General Labor",
    "skilled_trades": "Skilled Trades & Maintenance",
    "farm_worker": "Farm Worker",
    "livestock_support": "Livestock Support",
}


PACKAGE_ORDER = {
    PackageAudience.B2B: ["starter", "growth", "business", "enterprise"],
    PackageAudience.B2C: ["basic", "essential", "advanced", "premium"],
}


# Candidate Assessment Slots per package (MeritLense Package Architecture v1.2,
# Section 6). None = no automated enforcement (per-agreement/custom tiers).
SLOT_GRANTS = {
    "basic": 3,
    "essential": 5,
    "advanced": 10,
    "premium": 20,
    "starter": None,
    "growth": 200,
    "business": 500,
    "enterprise": None,
}


class Command(BaseCommand):
    help = "Seed package session configs and role coverage mappings from MeritLense Package Architecture v1.2."

    @transaction.atomic
    def handle(self, *args, **options):
        for config in PACKAGE_CONFIGS:
            PackageSessionConfig.objects.update_or_create(
                package_code=config["package_code"],
                defaults={**config, "is_active": True},
            )

        for audience, coverage_map in (
            (PackageAudience.B2B, B2B_ROLE_COVERAGE),
            (PackageAudience.B2C, B2C_ROLE_COVERAGE),
        ):
            for role_code, levels_csv in coverage_map.items():
                role_name = ROLE_NAMES[role_code]
                for package_code, coverage_level in zip(PACKAGE_ORDER[audience], levels_csv.split(","), strict=True):
                    package = PackageSessionConfig.objects.get(package_code=package_code)
                    normalized_level = coverage_level.strip().upper()
                    RolePackageCoverage.objects.update_or_create(
                        role_code=role_code,
                        package_code=package_code,
                        defaults={
                            "role_name": role_name,
                            "package_name": package.package_name,
                            "audience": audience,
                            "coverage_level": normalized_level,
                            "evaluation_tier": self._coverage_to_tier(normalized_level),
                            "readiness_indicator_enabled": normalized_level != CoverageLevel.SCREENING,
                            "certificate_enabled": normalized_level != CoverageLevel.SCREENING,
                            "video_introduction_enabled": package.video_introduction_enabled and normalized_level == CoverageLevel.FULL,
                            "behavioral_indicators_enabled": package.behavioral_indicators_enabled and normalized_level == CoverageLevel.FULL,
                            "is_active": True,
                        },
                    )

        self._sync_price_metadata()
        self._sync_price_entitlements()
        self.stdout.write(self.style.SUCCESS("Seeded package architecture v1.2"))

    def _coverage_to_tier(self, coverage_level):
        if coverage_level == CoverageLevel.SCREENING:
            return InterviewEvaluationTier.SCREENING
        return InterviewEvaluationTier.FULL

    def _sync_price_metadata(self):
        if Price is None:
            return

        for price in Price.objects.all():
            package_code = self._infer_package_code_from_price_name(price.name)
            if not package_code:
                continue
            metadata = price.metadata or {}
            features = price.features or {}
            metadata["package_code"] = package_code
            if "personality_report" in features and "behavioral_indicators_report" not in features:
                features["behavioral_indicators_report"] = features.pop("personality_report")
            if "motivation_report" in features and "role_readiness_indicators" not in features:
                features["role_readiness_indicators"] = features.pop("motivation_report")
            price.metadata = metadata
            price.features = features
            price.save(update_fields=["metadata", "features", "updated_at"])

    def _sync_price_entitlements(self):
        if Price is None:
            return

        points_grants = {config["package_code"]: config["points_balance"] for config in PACKAGE_CONFIGS}

        for price in Price.objects.all():
            package_code = self._infer_package_code_from_price_name(price.name)
            if not package_code:
                continue
            price.slot_grant = SLOT_GRANTS.get(package_code)
            price.points_grant = points_grants.get(package_code)
            price.save(update_fields=["slot_grant", "points_grant", "updated_at"])

    def _infer_package_code_from_price_name(self, price_name):
        normalized = (price_name or "").strip().lower()
        for package in PACKAGE_CONFIGS:
            if package["package_name"].lower() in normalized:
                return package["package_code"]
        return ""
