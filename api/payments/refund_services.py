from django.conf import settings
from django.db import transaction
from django.utils import timezone
import stripe

from api.audit.services import AuditLogService
from api.core.constants import AuditLogAction, AuditLogCategory, PaymentStatus
from .models import BalanceTransaction, PackageBalance

PLATFORM_ERROR = "PLATFORM_ERROR"
CONFIRMED_BILLING_ERROR = "CONFIRMED_BILLING_ERROR"
OVERRIDE_REASON_CODES = {PLATFORM_ERROR, CONFIRMED_BILLING_ERROR}


class RefundEligibilityService:
    """Refund Eligibility Policy: B2C is refundable only with zero Slots/
    Points consumed on that specific purchase (no partial refunds, no time
    window, each purchase independent); B2B/Enterprise's current billing
    period is never refundable by default (cancellation != refund) since
    their balances are pooled per-company, not tied to one payment."""

    @classmethod
    def check(cls, payment):
        """Returns (eligible: bool, reason_code: str)."""
        if payment.refunded_at:
            return False, "ALREADY_REFUNDED"
        if payment.status != PaymentStatus.SUCCEEDED:
            return False, "NOT_SUCCEEDED"
        if payment.subscription_id:
            return False, "B2B_CURRENT_PERIOD_NOT_REFUNDABLE"
        for balance in PackageBalance.objects.filter(source_payment=payment):
            if balance.current_balance < balance.fixed_amount:
                return False, "ALREADY_CONSUMED"
        return True, "ELIGIBLE"


class RefundService:
    @classmethod
    def refund_payment(cls, *, payment, actor, override_reason_code=None):
        """Manual/admin-only refund (Package Architecture Sign-Off, Section
        6 + Refund Eligibility Policy). Checks eligibility before the
        refund reaches Stripe; an override_reason_code can unblock an
        otherwise-ineligible refund, but never changes how much entitlement
        gets revoked - that's always capped to whatever's still unused."""
        eligible, reason = RefundEligibilityService.check(payment)
        if reason == "ALREADY_REFUNDED":
            raise ValueError("This payment has already been refunded")
        if not eligible and override_reason_code not in OVERRIDE_REASON_CODES:
            raise ValueError(f"Not eligible for refund ({reason}) - an override_reason_code is required")

        stripe.api_key = settings.STRIPE_SECRET_KEY
        stripe.Refund.create(payment_intent=payment.stripe_payment_intent_id)

        with transaction.atomic():
            payment.status = PaymentStatus.REFUNDED
            payment.refunded_at = timezone.now()
            payment.save(update_fields=["status", "refunded_at", "updated_at"])
            cls.revoke_unused_entitlement(payment)

        AuditLogService.log(
            user=actor,
            action=AuditLogAction.PAYMENT_REFUNDED,
            category=AuditLogCategory.PAYMENT,
            description=f"Payment refunded: {payment.stripe_payment_intent_id}",
            resource=payment,
            data={
                'eligibility_reason': reason,
                'override_reason_code': override_reason_code,
                'amount': str(payment.amount),
            },
        )
        return payment

    @classmethod
    def revoke_unused_entitlement(cls, payment):
        """Revokes whatever's left of a refunded one-time purchase - never
        negative, never touches what's already consumed. B2B/recurring
        payments aren't tied to a single PackageBalance (pooled,
        period-based) - nothing to revoke here for those. Called from both
        the admin refund action and the charge.refunded webhook, so it must
        be idempotent - naturally is, since it only touches rows that still
        have something left (current_balance__gt=0)."""
        for balance in PackageBalance.objects.select_for_update().filter(source_payment=payment, current_balance__gt=0):
            credit = balance.current_balance
            balance.current_balance = 0
            balance.save(update_fields=["current_balance", "updated_at"])
            BalanceTransaction.objects.create(
                balance=balance,
                transaction_type=BalanceTransaction.REFUND,
                amount=-credit,
                balance_after=0,
                reference=f"refund:{payment.stripe_payment_intent_id}",
            )
