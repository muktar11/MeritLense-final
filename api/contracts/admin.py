from django.contrib import admin

from .models import Agreement


@admin.register(Agreement)
class AgreementAdmin(admin.ModelAdmin):
    list_display = ('agreement_type', 'version', 'user', 'company', 'method', 'status', 'accepted_at', 'contract_id')
    list_filter = ('agreement_type', 'method', 'status')
    search_fields = ('user__email', 'company__name', 'contract_id', 'signatory_name')
    readonly_fields = ('otp_code_hash', 'otp_expires_at', 'otp_attempts')
