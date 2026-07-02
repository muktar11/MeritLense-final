from django.contrib import admin

from .models import InterviewConfiguration, InterviewRubric, PackageSessionConfig, RolePackageCoverage


@admin.register(InterviewConfiguration)
class InterviewConfigurationAdmin(admin.ModelAdmin):
    list_display = ("role_name", "role_code", "language", "evaluation_tier", "total_questions", "duration_minutes", "is_active")
    list_filter = ("language", "evaluation_tier", "is_active", "enable_translation", "enable_integrity_checks")
    search_fields = ("role_name", "role_code")


@admin.register(InterviewRubric)
class InterviewRubricAdmin(admin.ModelAdmin):
    list_display = ("role_name", "role_code", "skill_tag", "scoring_category", "max_score", "rubric_version", "is_active")
    list_filter = ("role_name", "role_code", "rubric_version", "is_active")
    search_fields = ("role_name", "role_code", "skill_tag", "scoring_category")


@admin.register(PackageSessionConfig)
class PackageSessionConfigAdmin(admin.ModelAdmin):
    list_display = ("package_name", "package_code", "audience", "evaluation_tier", "duration_minutes", "is_active")
    list_filter = ("audience", "evaluation_tier", "is_active")
    search_fields = ("package_name", "package_code")


@admin.register(RolePackageCoverage)
class RolePackageCoverageAdmin(admin.ModelAdmin):
    list_display = ("role_name", "role_code", "package_name", "coverage_level", "evaluation_tier", "is_active")
    list_filter = ("audience", "coverage_level", "evaluation_tier", "is_active")
    search_fields = ("role_name", "role_code", "package_name", "package_code")
