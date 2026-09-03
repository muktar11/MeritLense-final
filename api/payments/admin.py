from django.contrib import admin

from .models import AddonRequest, BalanceTransaction, DealRecord, PackageBalance, Payment, Subscription


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "company", "stripe_price", "status", "current_period_start", "current_period_end", "cancel_at_period_end")
    list_filter = ("status", "cancel_at_period_end")
    search_fields = ("stripe_subscription_id", "user__email", "company__name")
    raw_id_fields = ("user", "company", "customer", "stripe_price")


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "company", "amount", "currency", "status", "paid_at", "refunded_at")
    list_filter = ("status", "currency")
    search_fields = ("stripe_payment_intent_id", "user__email", "receipt_number")
    raw_id_fields = ("user", "company", "customer", "subscription", "stripe_payment_method")


@admin.register(PackageBalance)
class PackageBalanceAdmin(admin.ModelAdmin):
    list_display = ("id", "owner_user", "owner_company", "balance_type", "current_balance", "fixed_amount", "updated_at")
    list_filter = ("balance_type",)
    search_fields = ("owner_user__email", "owner_company__name")
    raw_id_fields = ("owner_user", "owner_company", "source_subscription", "source_payment")


@admin.register(BalanceTransaction)
class BalanceTransactionAdmin(admin.ModelAdmin):
    list_display = ("id", "balance", "transaction_type", "amount", "balance_after", "reference", "actor", "created_at")
    list_filter = ("transaction_type",)
    search_fields = ("reference", "balance__owner_user__email", "balance__owner_company__name")
    raw_id_fields = ("balance", "actor")


@admin.register(AddonRequest)
class AddonRequestAdmin(admin.ModelAdmin):
    list_display = ("id", "owner_user", "owner_company", "addon_code", "points_cost", "status", "resolved_at", "created_at")
    list_filter = ("status", "addon_code")
    search_fields = ("owner_user__email", "owner_company__name", "public_id")
    raw_id_fields = ("owner_user", "owner_company", "actor")


@admin.register(DealRecord)
class DealRecordAdmin(admin.ModelAdmin):
    list_display = ("id", "company", "deal_type", "price", "slot_grant", "points_grant", "unit_amount", "rollover_allowed", "is_active")
    list_filter = ("deal_type", "is_active", "rollover_allowed")
    search_fields = ("company__name", "addendum_reference", "public_id")
    raw_id_fields = ("company", "price", "created_by")
