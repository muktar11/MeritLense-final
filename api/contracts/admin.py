from django.contrib import admin

from .models import Agreement, AgreementEvent, CookieConsent


@admin.register(Agreement)
class AgreementAdmin(admin.ModelAdmin):
    list_display = ("agreement_id", "agreement_type", "version", "status", "user", "candidate", "signed_at")
    list_filter = ("agreement_type", "status", "method", "version")
    search_fields = ("agreement_id", "signatory_name", "user__email", "candidate__email")
    readonly_fields = ("agreement_id", "public_id", "created_at", "updated_at")


@admin.register(AgreementEvent)
class AgreementEventAdmin(admin.ModelAdmin):
    list_display = ("agreement", "event_type", "created_at")
    list_filter = ("event_type",)
    search_fields = ("agreement__agreement_id", "description")
    readonly_fields = ("public_id", "created_at", "updated_at")


@admin.register(CookieConsent)
class CookieConsentAdmin(admin.ModelAdmin):
    list_display = ("user", "visitor_key", "expires_at", "created_at")
    search_fields = ("user__email", "visitor_key")
    readonly_fields = ("public_id", "created_at", "updated_at")
