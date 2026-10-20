from django.contrib import admin

from .models import ServiceIncident


@admin.register(ServiceIncident)
class ServiceIncidentAdmin(admin.ModelAdmin):
    list_display = ("created_at", "cause", "status", "summary", "owner", "deadline", "occurrence_count")
    list_filter = ("status", "cause")
    search_fields = ("summary", "details", "public_id")
    raw_id_fields = ("session", "evaluation", "company", "account_user", "owner", "resolved_by")
    readonly_fields = ("public_id", "created_at", "updated_at", "notified_at", "last_seen_at", "occurrence_count")
