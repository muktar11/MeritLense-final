import logging
from decimal import Decimal

import stripe
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from api.core.constants import AuditLogAction, AuditLogCategory, PackageRequestBilling, PackageRequestPaymentMethod, PackageRequestStatus
from api.core.public_ids import get_by_identifier
from .models import DealRecord, Invoice, PackageRequest, Price, Subscription

logger = logging.getLogger(__name__)

# A one-time-paid deal has no Stripe subscription to renew from, so it's
# modeled internally as a long-lived "recurring-shaped" Subscription (see
# activate_after_payment) - this is how long it stays valid before needing
# a new request/payment.
ONE_TIME_DEAL_VALIDITY_DAYS = 365


class PackageRequestError(Exception):
    pass


class PackageRequestService:
    """Submit -> review -> pay -> activate lifecycle for a company's
    Starter/Enterprise ask.

    Approval does not grant anything by itself: it generates a Stripe
    Payment Link for the SuperAdmin's agreed amount and emails it to the
    requester. The real DealRecord - and the Price/Subscription pair that
    makes it consumable (see EntitlementService._resolve_grant, which
    requires company.subscriptions.stripe_price.deal_record) - is only
    created by activate_after_payment, called from the
    payment_intent.succeeded / customer.subscription.created webhook
    handlers once Stripe confirms payment.
    """

    @classmethod
    def submit(cls, *, company, requested_by, deal_type, requested_slot_grant=None, requested_points_grant=None, message=""):
        from api.accounts.utils import notify_superadmins
        from api.audit.services import AuditLogService

        package_request = PackageRequest.objects.create(
            company=company,
            requested_by=requested_by,
            deal_type=deal_type,
            requested_slot_grant=requested_slot_grant,
            requested_points_grant=requested_points_grant,
            message=message,
        )

        AuditLogService.log(
            user=requested_by,
            action=AuditLogAction.PACKAGE_REQUEST_SUBMITTED,
            category=AuditLogCategory.SUBSCRIPTION,
            description=f"Package request submitted: {company.name} ({deal_type})",
            resource=package_request,
            data={"deal_type": deal_type, "requested_slot_grant": requested_slot_grant, "requested_points_grant": requested_points_grant},
        )

        notify_superadmins(
            subject=f"New {package_request.get_deal_type_display()} request from {company.name}",
            message=(
                f"{requested_by.get_full_name()} ({requested_by.email}) at {company.name} has requested a "
                f"{package_request.get_deal_type_display()} package.\n\n"
                f"Requested slots: {requested_slot_grant if requested_slot_grant is not None else 'not specified'}\n"
                f"Requested points: {requested_points_grant if requested_points_grant is not None else 'not specified'}\n\n"
                f"Message from requester:\n{message or '(none)'}\n\n"
                "Review it in the SuperAdmin dashboard."
            ),
        )
        return package_request

    @classmethod
    @transaction.atomic
    def approve(cls, *, package_request, actor, slot_grant, points_grant, unit_amount, billing_type, currency="eur", rollover_allowed=False, addendum_reference="", decision_reason=""):
        from api.accounts.utils import send_package_request_approved_email
        from api.audit.services import AuditLogService
        from .invoice_services import generate_invoice_pdf
        from .services import StripeService, _generate_one_time_invoice_number

        if billing_type not in (PackageRequestBilling.ONE_TIME, PackageRequestBilling.RECURRING):
            raise PackageRequestError("billing_type must be ONE_TIME or RECURRING.")

        package_request = PackageRequest.objects.select_for_update().get(pk=package_request.pk)
        if package_request.status != PackageRequestStatus.PENDING:
            raise PackageRequestError(f"This request has already been {package_request.status.lower()}.")

        stripe.api_key = settings.STRIPE_SECRET_KEY
        try:
            product = stripe.Product.create(
                name=f"{package_request.company.name} - {package_request.get_deal_type_display()}",
                metadata={"package_request_id": str(package_request.public_id)},
            )
            price_params = {
                "product": product.id,
                "unit_amount": int(unit_amount * 100),
                "currency": currency,
            }
            if billing_type == PackageRequestBilling.RECURRING:
                price_params["recurring"] = {"interval": "month"}
            stripe_price = stripe.Price.create(**price_params)

            payment_link_params = {
                "line_items": [{"price": stripe_price.id, "quantity": 1}],
                "metadata": {"package_request_id": str(package_request.public_id)},
                "after_completion": {
                    "type": "hosted_confirmation",
                    "hosted_confirmation": {
                        "custom_message": "Thank you - your MeritLense package will be activated shortly."
                    },
                },
            }
            if billing_type == PackageRequestBilling.RECURRING:
                payment_link_params["subscription_data"] = {"metadata": {"package_request_id": str(package_request.public_id)}}
            else:
                payment_link_params["payment_intent_data"] = {"metadata": {"package_request_id": str(package_request.public_id)}}
            payment_link = stripe.PaymentLink.create(**payment_link_params)
        except stripe.error.StripeError as exc:
            logger.exception("Stripe error creating payment link for package request %s", package_request.public_id)
            raise PackageRequestError(f"Failed to create the Stripe payment link: {exc.user_message or str(exc)}") from exc

        package_request.status = PackageRequestStatus.APPROVED
        package_request.decision_reason = decision_reason
        package_request.reviewed_by = actor
        package_request.reviewed_at = timezone.now()
        package_request.billing_type = billing_type
        package_request.approved_slot_grant = slot_grant
        package_request.approved_points_grant = points_grant
        package_request.unit_amount = unit_amount
        package_request.currency = currency
        package_request.rollover_allowed = rollover_allowed
        package_request.addendum_reference = addendum_reference
        package_request.stripe_product_id = product.id
        package_request.stripe_price_id = stripe_price.id
        package_request.stripe_payment_link_id = payment_link.id
        package_request.stripe_payment_link_url = payment_link.url

        # An unpaid Invoice from the moment of approval - the "Pay Online"
        # link on it (_build_snapshot.pay_online_url) is this same Payment
        # Link, and Bank Transfer is always shown alongside it (see
        # invoice.html's unpaid branch). Never a real Stripe Invoice object
        # (no subscription/billing cycle exists yet for a brand-new custom
        # deal), same synthetic-id approach as one-time B2C purchases.
        customer = StripeService().get_or_create_customer(package_request.requested_by)
        invoice = Invoice.objects.create(
            user=package_request.requested_by,
            customer=customer,
            stripe_invoice_id=f"pkgreq_{package_request.public_id}",
            number=_generate_one_time_invoice_number(),
            status="OPEN",
            amount_due=unit_amount,
            amount_paid=Decimal("0.00"),
            amount_remaining=unit_amount,
            currency=currency,
            hosted_invoice_url=payment_link.url,
        )
        try:
            generate_invoice_pdf(invoice)
        except Exception:
            logger.exception("Failed to generate invoice PDF for package request %s", package_request.public_id)
        package_request.invoice = invoice

        package_request.save(update_fields=[
            "status", "decision_reason", "reviewed_by", "reviewed_at", "billing_type",
            "approved_slot_grant", "approved_points_grant", "unit_amount", "currency",
            "rollover_allowed", "addendum_reference", "stripe_product_id", "stripe_price_id",
            "stripe_payment_link_id", "stripe_payment_link_url", "invoice", "updated_at",
        ])

        AuditLogService.log(
            user=actor,
            action=AuditLogAction.PACKAGE_REQUEST_APPROVED,
            category=AuditLogCategory.SUBSCRIPTION,
            description=f"Package request approved: {package_request.company.name} ({package_request.deal_type}) - payment link sent",
            resource=package_request,
            data={"slot_grant": slot_grant, "points_grant": points_grant, "unit_amount": str(unit_amount), "billing_type": billing_type, "invoice_number": invoice.number},
        )

        send_package_request_approved_email(package_request.requested_by, package_request)
        return package_request

    @classmethod
    def _activate(
        cls, package_request, *, payment_method, confirmed_by=None,
        stripe_subscription_id=None, current_period_end=None, payment_date=None,
    ):
        """Shared activation core for both payment paths: creates the real
        Price/Subscription/DealRecord, flips the request's Invoice to PAID
        (€0.00 due, same invoice number), and regenerates its PDF - the
        _build_snapshot.is_paid gate then renders the paid-invoice design
        (Payment Confirmation box, no Pay Online/Bank Transfer) the next
        time it's generated, unchanged.

        Called by activate_after_payment (Stripe webhook, no human actor,
        may carry a real stripe_subscription_id/current_period_end) and
        confirm_bank_transfer (a real SuperAdmin/Ops actor, never a Stripe
        subscription). Returns (package_request, deal_record, error) -
        error is None on success, else "already_paid" or "not_approved"
        (deal_record is None whenever error is set) - callers decide
        whether that's a silent no-op (webhook, idempotent-by-design) or a
        real error to surface (manual action)."""
        from .invoice_services import generate_invoice_pdf
        from .services import StripeService

        with transaction.atomic():
            package_request = PackageRequest.objects.select_for_update().get(pk=package_request.pk)

            if package_request.deal_record_id or package_request.status == PackageRequestStatus.PAID:
                return package_request, None, "already_paid"

            if package_request.status != PackageRequestStatus.APPROVED:
                return package_request, None, "not_approved"

            local_price = Price.objects.create(
                name=f"{package_request.company.name} - {package_request.get_deal_type_display()}",
                stripe_price_id=package_request.stripe_price_id,
                stripe_product_id=package_request.stripe_product_id,
                target_user_type="B2B",
                unit_amount=package_request.unit_amount,
                currency=package_request.currency,
                interval="MONTHLY",
                # Always modeled as RECURRING locally regardless of how Stripe
                # actually billed it - _consume_b2b only recognizes an active
                # RECURRING subscription (there's no B2B one-time consumption
                # path), so this is what makes a one-time-paid deal usable.
                billing_type="RECURRING",
                slot_grant=package_request.approved_slot_grant,
                points_grant=package_request.approved_points_grant,
                is_active=True,
                metadata={
                    "package_request_id": str(package_request.public_id),
                    "stripe_price_id": package_request.stripe_price_id,
                    "source_billing_type": package_request.billing_type,
                },
            )

            deal_record = DealRecord.objects.create(
                company=package_request.company,
                price=local_price,
                deal_type=package_request.deal_type,
                slot_grant=package_request.approved_slot_grant,
                points_grant=package_request.approved_points_grant,
                unit_amount=package_request.unit_amount,
                currency=package_request.currency,
                rollover_allowed=package_request.rollover_allowed,
                addendum_reference=package_request.addendum_reference,
                confirmation_note=f"Auto-created and activated from package request {package_request.public_id} after payment confirmation.",
                created_by=package_request.reviewed_by,
            )

            now = timezone.now()
            if package_request.billing_type == PackageRequestBilling.RECURRING and stripe_subscription_id and current_period_end:
                period_start, period_end = now, current_period_end
                real_stripe_subscription_id = stripe_subscription_id
            else:
                period_start = now
                period_end = now + timezone.timedelta(days=ONE_TIME_DEAL_VALIDITY_DAYS)
                real_stripe_subscription_id = f"package_request_{package_request.public_id}"

            customer = StripeService().get_or_create_customer(package_request.requested_by)
            if customer is None:
                logger.error(
                    "_activate: could not resolve a Stripe customer for user %s - "
                    "DealRecord %s created but no Subscription attached, entitlements will not resolve",
                    package_request.requested_by.id, deal_record.id,
                )
            else:
                Subscription.objects.create(
                    user=package_request.requested_by,
                    company=package_request.company,
                    customer=customer,
                    stripe_subscription_id=real_stripe_subscription_id,
                    stripe_price=local_price,
                    status="ACTIVE",
                    current_period_start=period_start,
                    current_period_end=period_end,
                    current_usage={},
                    metadata={"package_request_id": str(package_request.public_id), "source": "package_request"},
                )

            package_request.status = PackageRequestStatus.PAID
            package_request.paid_at = now
            package_request.deal_record = deal_record
            package_request.payment_method = payment_method
            update_fields = ["status", "paid_at", "deal_record", "payment_method", "updated_at"]
            if confirmed_by is not None:
                package_request.confirmed_by = confirmed_by
                package_request.confirmed_at = now
                update_fields += ["confirmed_by", "confirmed_at"]
            package_request.save(update_fields=update_fields)

            if package_request.invoice_id:
                invoice = package_request.invoice
                invoice.status = "PAID"
                invoice.amount_paid = invoice.amount_due
                invoice.amount_remaining = Decimal("0.00")
                invoice.paid_at = payment_date or now
                invoice.save(update_fields=["status", "amount_paid", "amount_remaining", "paid_at", "updated_at"])
                try:
                    generate_invoice_pdf(invoice)
                except Exception:
                    logger.exception("Failed to regenerate paid invoice PDF for package request %s", package_request.public_id)

        return package_request, deal_record, None

    @classmethod
    def activate_after_payment(cls, package_request_ref, *, stripe_subscription_id=None, current_period_end=None):
        """Called from the payments webhook once Stripe confirms payment for
        an approved request's Payment Link (Pay Online). Idempotent -
        webhooks can be delivered more than once."""
        from api.accounts.utils import send_package_request_payment_confirmed_email
        from api.audit.services import AuditLogService

        try:
            package_request = get_by_identifier(PackageRequest.objects.all(), package_request_ref)
        except PackageRequest.DoesNotExist:
            logger.warning("activate_after_payment: no PackageRequest found for %r", package_request_ref)
            return None

        package_request, deal_record, error = cls._activate(
            package_request, payment_method=PackageRequestPaymentMethod.STRIPE,
            stripe_subscription_id=stripe_subscription_id, current_period_end=current_period_end,
        )
        if error == "already_paid":
            logger.info("activate_after_payment: package request %s already activated, skipping", package_request.public_id)
            return package_request
        if error == "not_approved":
            logger.warning(
                "activate_after_payment: package request %s is %s, not APPROVED - ignoring payment webhook",
                package_request.public_id, package_request.status,
            )
            return None

        AuditLogService.log_system(
            action=AuditLogAction.PACKAGE_REQUEST_PAID,
            category=AuditLogCategory.SUBSCRIPTION,
            description=f"Package request paid and activated: {package_request.company.name} ({package_request.deal_type})",
            resource=package_request,
            data={"deal_record_id": deal_record.id, "unit_amount": str(package_request.unit_amount)},
        )

        send_package_request_payment_confirmed_email(package_request.requested_by, package_request, deal_record)
        return package_request

    @classmethod
    def confirm_bank_transfer(cls, *, package_request, actor, payment_date=None, note=""):
        """A SuperAdmin/Ops manual alternative to Pay Online's automatic
        Stripe-webhook confirmation (activate_after_payment), for a company
        that paid an approved request by bank transfer instead. Manual only
        by design - there is no automatic bank-transfer reconciliation.
        Activates the same way (real DealRecord/Subscription, same invoice
        flipped to PAID/€0.00 due, regenerated and re-emailed) but records
        who confirmed it, when, and that the method was BANK_TRANSFER."""
        from api.accounts.utils import send_package_request_payment_confirmed_email
        from api.audit.services import AuditLogService

        package_request, deal_record, error = cls._activate(
            package_request, payment_method=PackageRequestPaymentMethod.BANK_TRANSFER,
            confirmed_by=actor, payment_date=payment_date,
        )
        if error == "already_paid":
            raise PackageRequestError("This request has already been paid and activated.")
        if error == "not_approved":
            raise PackageRequestError(
                f"This request is {package_request.status.lower()}, not approved - nothing to confirm payment for."
            )

        AuditLogService.log(
            user=actor,
            action=AuditLogAction.PACKAGE_REQUEST_BANK_TRANSFER_CONFIRMED,
            category=AuditLogCategory.SUBSCRIPTION,
            description=f"Bank transfer payment confirmed: {package_request.company.name} ({package_request.deal_type})",
            resource=package_request,
            data={"deal_record_id": deal_record.id, "unit_amount": str(package_request.unit_amount), "note": note},
        )

        send_package_request_payment_confirmed_email(package_request.requested_by, package_request, deal_record)
        return package_request

    @classmethod
    def deny(cls, *, package_request, actor, decision_reason):
        from api.accounts.utils import send_package_request_denied_email
        from api.audit.services import AuditLogService

        if not decision_reason:
            raise PackageRequestError("A reason is required when denying a package request.")

        with transaction.atomic():
            package_request = PackageRequest.objects.select_for_update().get(pk=package_request.pk)
            if package_request.status != PackageRequestStatus.PENDING:
                raise PackageRequestError(f"This request has already been {package_request.status.lower()}.")

            package_request.status = PackageRequestStatus.DENIED
            package_request.decision_reason = decision_reason
            package_request.reviewed_by = actor
            package_request.reviewed_at = timezone.now()
            package_request.save(update_fields=["status", "decision_reason", "reviewed_by", "reviewed_at", "updated_at"])

        AuditLogService.log(
            user=actor,
            action=AuditLogAction.PACKAGE_REQUEST_DENIED,
            category=AuditLogCategory.SUBSCRIPTION,
            description=f"Package request denied: {package_request.company.name} ({package_request.deal_type})",
            resource=package_request,
            data={"decision_reason": decision_reason},
        )

        send_package_request_denied_email(package_request.requested_by, package_request, decision_reason)
        return package_request
