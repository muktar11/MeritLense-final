from django.contrib import admin
from api.dashboard.models import AdminAlertConfiguration


@admin.register(AdminAlertConfiguration)
class AdminAlertConfigurationAdmin(admin.ModelAdmin):
    list_display = ("singleton_key", "updated_at")
