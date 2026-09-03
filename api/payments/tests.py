from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient, APITestCase

from api.accounts.models import Company, User
from api.core.constants import Roles
from api.payments.entitlement_services import ADDON_POINTS_CATALOG, EntitlementService
from api.payments.models import AddonRequest, BalanceTransaction, Customer, Invoice, PackageBalance, Payment, Price, ProcessedStripeEvent, Subscription
from api.payments.refund_services import CONFIRMED_BILLING_ERROR, PLATFORM_ERROR, RefundEligibilityService, RefundService
from api.payments.serializers import CreateSubscriptionSerializer
from api.payments.services import StripeService


def make_price(**overrides):
    defaults = dict(
        name="Growth Package",
        stripe_price_id=f"price_{timezone.now().timestamp()}",
        stripe_product_id="prod_test",
        target_user_type="B2B",
        unit_amount=Decimal("2000.00"),
        currency="eur",
        interval="MONTHLY",
        interval_count=1,
        billing_type="RECURRING",
    )
    defaults.update(overrides)
    return Price.objects.create(**defaults)


def make_company(admin_user, **overrides):
    defaults = dict(
        name="Test Co",
        registration_number=f"REG-{admin_user.id}-{timezone.now().timestamp()}",
        company_size="1-10",
        phone_number="+15550000000",
        country="United States",
        city="San Francisco",
        admin_user=admin_user,
        registration_certificate=SimpleUploadedFile("cert.pdf", b"cert", content_type="application/pdf"),
    )
    defaults.update(overrides)
    return Company.objects.create(**defaults)


def make_subscription(user, price, **overrides):
    customer, _ = Customer.objects.get_or_create(
        user=user,
        defaults={"stripe_customer_id": f"cus_{user.id}", "email": user.email},
    )
    defaults = dict(
        user=user,
        customer=customer,
        stripe_subscription_id=f"sub_{user.id}_{timezone.now().timestamp()}",
        stripe_price=price,
        status="ACTIVE",
        current_period_start=timezone.now(),
        current_period_end=timezone.now() + timezone.timedelta(days=30),
        quantity=1,
    )
    defaults.update(overrides)
    return Subscription.objects.create(**defaults)


class HandleInvoicePaidTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="b2b-owner@example.com",
            password="Password123!",
            first_name="B2B",
            last_name="Owner",
            role=Roles.B2B,
            is_verified=True,
        )
        self.price = make_price()
        self.subscription = make_subscription(self.user, self.price, status="INCOMPLETE")
        self.service = StripeService()

    def test_creates_invoice_with_user_and_customer_from_subscription(self):
        invoice_data = {
            "id": "in_test_1",
            "number": "INV-001",
            "amount_due": 200000,
            "amount_paid": 200000,
            "amount_remaining": 0,
            "currency": "eur",
            "subscription": self.subscription.stripe_subscription_id,
            "invoice_pdf": "",
            "hosted_invoice_url": "",
        }

        invoice = self.service.handle_invoice_paid(invoice_data)

        self.assertIsNotNone(invoice)
        self.assertEqual(invoice.user, self.user)
        self.assertEqual(invoice.customer, self.subscription.customer)
        self.assertEqual(invoice.subscription, self.subscription)
        self.assertEqual(invoice.status, "PAID")
        self.assertEqual(invoice.amount_paid, Decimal("2000.00"))

        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.status, "ACTIVE")

    def test_renewal_invoice_creates_a_second_real_invoice_row(self):
        """The bug this fixes: previously only the first invoice (created
        synchronously in create_subscription) ever produced a real record -
        every renewal silently failed to save at all (Invoice.user/customer
        are required, and the old code never set them)."""
        first = {
            "id": "in_first",
            "number": "INV-001",
            "amount_due": 200000,
            "amount_paid": 200000,
            "amount_remaining": 0,
            "currency": "eur",
            "subscription": self.subscription.stripe_subscription_id,
        }
        second = {**first, "id": "in_second", "number": "INV-002"}

        self.service.handle_invoice_paid(first)
        self.service.handle_invoice_paid(second)

        self.assertEqual(Invoice.objects.filter(subscription=self.subscription).count(), 2)

    def test_skips_gracefully_when_subscription_unresolvable(self):
        invoice_data = {
            "id": "in_orphan",
            "number": "INV-999",
            "amount_due": 5000,
            "amount_paid": 5000,
            "amount_remaining": 0,
            "currency": "eur",
            "subscription": "sub_does_not_exist",
        }

        result = self.service.handle_invoice_paid(invoice_data)

        self.assertIsNone(result)
        self.assertEqual(Invoice.objects.count(), 0)

    def test_invoice_paid_resets_b2b_balance_to_full_even_if_partially_consumed(self):
        """No rollover: a renewal always resets to the plan's fixed amount,
        regardless of what was left over from the prior period."""
        company = make_company(self.user)
        self.price.slot_grant = 200
        self.price.points_grant = 2000
        self.price.save(update_fields=["slot_grant", "points_grant"])
        self.subscription.company = company
        self.subscription.save(update_fields=["company"])

        first_invoice = {
            "id": "in_first",
            "number": "INV-001",
            "amount_due": 200000,
            "amount_paid": 200000,
            "amount_remaining": 0,
            "currency": "eur",
            "subscription": self.subscription.stripe_subscription_id,
        }
        self.service.handle_invoice_paid(first_invoice)

        balance = PackageBalance.objects.get(owner_company=company, balance_type=PackageBalance.SLOTS)
        self.assertEqual(balance.current_balance, 200)
        balance.current_balance = 3
        balance.save(update_fields=["current_balance"])

        second_invoice = {**first_invoice, "id": "in_second", "number": "INV-002"}
        self.service.handle_invoice_paid(second_invoice)

        balance.refresh_from_db()
        self.assertEqual(balance.current_balance, 200)
        self.assertEqual(BalanceTransaction.objects.filter(balance=balance, transaction_type=BalanceTransaction.RESET).count(), 2)

    def test_invoice_paid_is_a_no_op_for_starter_enterprise_price_with_no_grant(self):
        company = make_company(self.user)
        self.subscription.company = company
        self.subscription.save(update_fields=["company"])
        # self.price defaults to slot_grant/points_grant = None (unset)

        self.service.handle_invoice_paid({
            "id": "in_pilot",
            "number": "INV-PILOT",
            "amount_due": 0,
            "amount_paid": 0,
            "amount_remaining": 0,
            "currency": "eur",
            "subscription": self.subscription.stripe_subscription_id,
        })

        self.assertEqual(PackageBalance.objects.filter(owner_company=company).count(), 0)


class GrantB2COneTimePackageTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="b2c-buyer@example.com",
            password="Password123!",
            first_name="B2C",
            last_name="Buyer",
            role=Roles.B2C,
            is_verified=True,
        )
        self.customer = Customer.objects.create(user=self.user, stripe_customer_id="cus_b2c", email=self.user.email)
        self.price = make_price(
            name="Basic",
            target_user_type="B2C",
            unit_amount=Decimal("50.00"),
            billing_type="ONE_TIME",
            slot_grant=3,
            points_grant=50,
        )
        self.payment = Payment.objects.create(
            user=self.user,
            customer=self.customer,
            stripe_payment_intent_id="pi_test_1",
            amount=Decimal("50.00"),
            status="SUCCEEDED",
        )
        self.service = StripeService()

    def test_grants_slots_and_points_matching_the_price(self):
        payment_intent = {"id": "pi_test_1", "metadata": {"price_id": str(self.price.id)}}

        self.service._grant_one_time_package(self.payment, payment_intent)

        slots = PackageBalance.objects.get(owner_user=self.user, balance_type=PackageBalance.SLOTS)
        points = PackageBalance.objects.get(owner_user=self.user, balance_type=PackageBalance.POINTS)
        self.assertEqual(slots.current_balance, 3)
        self.assertEqual(points.current_balance, 50)
        self.assertEqual(slots.source_payment, self.payment)

    def test_idempotent_on_webhook_redelivery(self):
        payment_intent = {"id": "pi_test_1", "metadata": {"price_id": str(self.price.id)}}

        self.service._grant_one_time_package(self.payment, payment_intent)
        self.payment.refresh_from_db()
        self.service._grant_one_time_package(self.payment, payment_intent)

        self.assertEqual(PackageBalance.objects.filter(owner_user=self.user).count(), 2)


class EntitlementServiceTests(TestCase):
    class _FakeCandidate:
        def __init__(self, created_by):
            self.created_by = created_by

    class _FakeSession:
        def __init__(self, *, organization_id=None, organization=None, created_by=None, public_id="sess-1"):
            self.organization_id = organization_id
            self.organization = organization
            self.candidate = EntitlementServiceTests._FakeCandidate(created_by)
            self.public_id = public_id

    def setUp(self):
        self.b2c_user = User.objects.create_user(
            email="b2c@example.com", password="Password123!", first_name="B2C", last_name="User",
            role=Roles.B2C, is_verified=True,
        )
        self.b2b_owner = User.objects.create_user(
            email="b2b@example.com", password="Password123!", first_name="B2B", last_name="Owner",
            role=Roles.B2B, is_verified=True,
        )
        self.company = make_company(self.b2b_owner)

    def test_b2c_consume_blocks_when_no_balance_exists(self):
        session = self._FakeSession(created_by=self.b2c_user)
        with self.assertRaises(ValueError):
            EntitlementService.consume_slot(session)

    def test_b2c_consume_decrements_oldest_purchase_first(self):
        older = PackageBalance.objects.create(owner_user=self.b2c_user, balance_type=PackageBalance.SLOTS, fixed_amount=3, current_balance=1)
        PackageBalance.objects.create(owner_user=self.b2c_user, balance_type=PackageBalance.SLOTS, fixed_amount=20, current_balance=20)

        session = self._FakeSession(created_by=self.b2c_user)
        EntitlementService.consume_slot(session)

        older.refresh_from_db()
        self.assertEqual(older.current_balance, 0)
        newer = PackageBalance.objects.exclude(pk=older.pk).get(owner_user=self.b2c_user)
        self.assertEqual(newer.current_balance, 20)

    def test_b2c_consume_raises_once_all_balances_exhausted(self):
        PackageBalance.objects.create(owner_user=self.b2c_user, balance_type=PackageBalance.SLOTS, fixed_amount=1, current_balance=1)
        session = self._FakeSession(created_by=self.b2c_user)

        EntitlementService.consume_slot(session)
        with self.assertRaises(ValueError):
            EntitlementService.consume_slot(session)

    def test_b2b_consume_decrements_company_wide_balance(self):
        price = make_price(target_user_type="B2B", slot_grant=200, points_grant=2000)
        make_subscription(self.b2b_owner, price, company=self.company, status="ACTIVE")

        session = self._FakeSession(organization_id=self.company.id, organization=self.company)
        EntitlementService.consume_slot(session)

        balance = PackageBalance.objects.get(owner_company=self.company, balance_type=PackageBalance.SLOTS)
        self.assertEqual(balance.current_balance, 199)
        self.assertEqual(BalanceTransaction.objects.filter(balance=balance, transaction_type=BalanceTransaction.CONSUME).count(), 1)

    def test_b2b_consume_is_unrestricted_when_price_has_no_slot_grant(self):
        price = make_price(target_user_type="B2B", slot_grant=None, points_grant=None)
        make_subscription(self.b2b_owner, price, company=self.company, status="ACTIVE")

        session = self._FakeSession(organization_id=self.company.id, organization=self.company)
        EntitlementService.consume_slot(session)  # should not raise

        self.assertEqual(PackageBalance.objects.filter(owner_company=self.company).count(), 0)

    def test_b2b_consume_blocks_when_subscription_is_suspended(self):
        price = make_price(target_user_type="B2B", slot_grant=200, points_grant=2000)
        subscription = make_subscription(self.b2b_owner, price, company=self.company, status="CANCELED")
        PackageBalance.objects.create(
            owner_company=self.company, balance_type=PackageBalance.SLOTS,
            source_subscription=subscription, fixed_amount=200, current_balance=50,
        )

        session = self._FakeSession(organization_id=self.company.id, organization=self.company)
        with self.assertRaises(ValueError):
            EntitlementService.consume_slot(session)

        balance = PackageBalance.objects.get(owner_company=self.company, balance_type=PackageBalance.SLOTS)
        self.assertEqual(balance.current_balance, 50)

    def test_b2b_consume_allowed_during_past_due_grace_period(self):
        price = make_price(target_user_type="B2B", slot_grant=200, points_grant=2000)
        subscription = make_subscription(self.b2b_owner, price, company=self.company, status="PAST_DUE")
        PackageBalance.objects.create(
            owner_company=self.company, balance_type=PackageBalance.SLOTS,
            source_subscription=subscription, fixed_amount=200, current_balance=50,
        )

        session = self._FakeSession(organization_id=self.company.id, organization=self.company)
        EntitlementService.consume_slot(session)  # should not raise - grace period is still usable

        balance = PackageBalance.objects.get(owner_company=self.company, balance_type=PackageBalance.SLOTS)
        self.assertEqual(balance.current_balance, 49)

    def test_no_renewal_grant_while_subscription_is_past_due(self):
        price = make_price(target_user_type="B2B", slot_grant=200, points_grant=2000)
        subscription = make_subscription(self.b2b_owner, price, company=self.company, status="ACTIVE")
        PackageBalance.objects.create(
            owner_company=self.company, balance_type=PackageBalance.SLOTS,
            source_subscription=subscription, fixed_amount=200, current_balance=17,
        )

        StripeService().handle_subscription_updated({"id": subscription.stripe_subscription_id, "status": "past_due"})

        balance = PackageBalance.objects.get(owner_company=self.company, balance_type=PackageBalance.SLOTS)
        self.assertEqual(balance.current_balance, 17)

    def test_reactivation_after_suspension_restores_access_to_remaining_balance(self):
        price = make_price(target_user_type="B2B", slot_grant=200, points_grant=2000)
        subscription = make_subscription(self.b2b_owner, price, company=self.company, status="CANCELED")
        PackageBalance.objects.create(
            owner_company=self.company, balance_type=PackageBalance.SLOTS,
            source_subscription=subscription, fixed_amount=200, current_balance=50,
        )
        session = self._FakeSession(organization_id=self.company.id, organization=self.company)

        with self.assertRaises(ValueError):
            EntitlementService.consume_slot(session)

        StripeService().handle_subscription_updated({"id": subscription.stripe_subscription_id, "status": "active"})
        EntitlementService.consume_slot(session)  # should not raise now

        balance = PackageBalance.objects.get(owner_company=self.company, balance_type=PackageBalance.SLOTS)
        self.assertEqual(balance.current_balance, 49)  # decremented from the 50 that was already there, not reset

    def test_spend_points_deducts_addon_cost(self):
        PackageBalance.objects.create(owner_user=self.b2c_user, balance_type=PackageBalance.POINTS, fixed_amount=50, current_balance=50)

        balance = EntitlementService.spend_points(user=self.b2c_user, addon_code="practical_simulation_test")

        self.assertEqual(balance.current_balance, 50 - ADDON_POINTS_CATALOG["practical_simulation_test"])

    def test_spend_points_rejects_unknown_addon_code(self):
        with self.assertRaises(ValueError):
            EntitlementService.spend_points(user=self.b2c_user, addon_code="not_a_real_addon")


class AddonReservationTests(TestCase):
    """Points spent on add-ons go through a real Reserve -> Consume/Release
    lifecycle (Package Architecture Sign-Off, Section 5), not an immediate
    irreversible deduction."""

    def setUp(self):
        self.b2c_user = User.objects.create_user(
            email="addon-reservation@example.com", password="Password123!", first_name="Addon", last_name="User",
            role=Roles.B2C, is_verified=True,
        )

    def test_reserve_points_deducts_immediately_and_creates_reserved_addon_request(self):
        PackageBalance.objects.create(owner_user=self.b2c_user, balance_type=PackageBalance.POINTS, fixed_amount=50, current_balance=50)

        addon_request = EntitlementService.reserve_points(user=self.b2c_user, addon_code="practical_simulation_test")

        self.assertEqual(addon_request.status, AddonRequest.RESERVED)
        balance = PackageBalance.objects.get(owner_user=self.b2c_user, balance_type=PackageBalance.POINTS)
        self.assertEqual(balance.current_balance, 50 - ADDON_POINTS_CATALOG["practical_simulation_test"])
        reference = f"addon-reservation:{addon_request.public_id}"
        self.assertTrue(BalanceTransaction.objects.filter(reference=reference, transaction_type=BalanceTransaction.RESERVE).exists())

    def test_confirm_addon_marks_consumed_without_further_balance_change(self):
        PackageBalance.objects.create(owner_user=self.b2c_user, balance_type=PackageBalance.POINTS, fixed_amount=50, current_balance=50)
        addon_request = EntitlementService.reserve_points(user=self.b2c_user, addon_code="practical_simulation_test")
        balance_after_reserve = PackageBalance.objects.get(owner_user=self.b2c_user, balance_type=PackageBalance.POINTS).current_balance

        EntitlementService.confirm_addon(addon_request=addon_request)

        addon_request.refresh_from_db()
        self.assertEqual(addon_request.status, AddonRequest.CONSUMED)
        self.assertIsNotNone(addon_request.resolved_at)
        balance = PackageBalance.objects.get(owner_user=self.b2c_user, balance_type=PackageBalance.POINTS)
        self.assertEqual(balance.current_balance, balance_after_reserve)

    def test_release_points_credits_balance_back_and_marks_released(self):
        PackageBalance.objects.create(owner_user=self.b2c_user, balance_type=PackageBalance.POINTS, fixed_amount=50, current_balance=50)
        addon_request = EntitlementService.reserve_points(user=self.b2c_user, addon_code="practical_simulation_test")

        EntitlementService.release_points(addon_request=addon_request)

        addon_request.refresh_from_db()
        self.assertEqual(addon_request.status, AddonRequest.RELEASED)
        balance = PackageBalance.objects.get(owner_user=self.b2c_user, balance_type=PackageBalance.POINTS)
        self.assertEqual(balance.current_balance, 50)
        reference = f"addon-reservation:{addon_request.public_id}"
        self.assertTrue(BalanceTransaction.objects.filter(reference=reference, transaction_type=BalanceTransaction.RELEASE).exists())

    def test_release_is_idempotent(self):
        PackageBalance.objects.create(owner_user=self.b2c_user, balance_type=PackageBalance.POINTS, fixed_amount=50, current_balance=50)
        addon_request = EntitlementService.reserve_points(user=self.b2c_user, addon_code="practical_simulation_test")

        EntitlementService.release_points(addon_request=addon_request)
        EntitlementService.release_points(addon_request=addon_request)

        balance = PackageBalance.objects.get(owner_user=self.b2c_user, balance_type=PackageBalance.POINTS)
        self.assertEqual(balance.current_balance, 50)

    def test_cannot_release_a_confirmed_addon_request(self):
        PackageBalance.objects.create(owner_user=self.b2c_user, balance_type=PackageBalance.POINTS, fixed_amount=50, current_balance=50)
        addon_request = EntitlementService.reserve_points(user=self.b2c_user, addon_code="practical_simulation_test")
        EntitlementService.confirm_addon(addon_request=addon_request)

        with self.assertRaises(ValueError):
            EntitlementService.release_points(addon_request=addon_request)

        balance = PackageBalance.objects.get(owner_user=self.b2c_user, balance_type=PackageBalance.POINTS)
        self.assertEqual(balance.current_balance, 50 - ADDON_POINTS_CATALOG["practical_simulation_test"])

    def test_release_reverses_b2c_multi_row_fifo_reservation(self):
        older = PackageBalance.objects.create(owner_user=self.b2c_user, balance_type=PackageBalance.POINTS, fixed_amount=10, current_balance=10)
        newer = PackageBalance.objects.create(owner_user=self.b2c_user, balance_type=PackageBalance.POINTS, fixed_amount=40, current_balance=40)

        addon_request = EntitlementService.reserve_points(user=self.b2c_user, addon_code="practical_simulation_test")  # costs 30

        older.refresh_from_db()
        newer.refresh_from_db()
        self.assertEqual(older.current_balance, 0)
        self.assertEqual(newer.current_balance, 20)

        EntitlementService.release_points(addon_request=addon_request)

        older.refresh_from_db()
        newer.refresh_from_db()
        self.assertEqual(older.current_balance, 10)
        self.assertEqual(newer.current_balance, 40)


class RefundEligibilityTests(TestCase):
    """Refund Eligibility Policy: B2C is refundable only with zero Slots/
    Points consumed on that specific purchase; B2B's current billing
    period is never refundable by default."""

    def setUp(self):
        self.b2c_user = User.objects.create_user(
            email="refund-b2c@example.com", password="Password123!", first_name="Refund", last_name="User",
            role=Roles.B2C, is_verified=True,
        )
        self.customer = Customer.objects.create(user=self.b2c_user, stripe_customer_id="cus_refund_b2c", email=self.b2c_user.email)
        self.payment = Payment.objects.create(
            user=self.b2c_user, customer=self.customer, stripe_payment_intent_id="pi_refund_test",
            amount=Decimal("50.00"), status="SUCCEEDED",
        )

    def test_b2c_eligible_when_nothing_consumed(self):
        PackageBalance.objects.create(owner_user=self.b2c_user, balance_type=PackageBalance.SLOTS, source_payment=self.payment, fixed_amount=3, current_balance=3)

        eligible, reason = RefundEligibilityService.check(self.payment)

        self.assertTrue(eligible)
        self.assertEqual(reason, "ELIGIBLE")

    def test_b2c_ineligible_when_slots_consumed(self):
        PackageBalance.objects.create(owner_user=self.b2c_user, balance_type=PackageBalance.SLOTS, source_payment=self.payment, fixed_amount=3, current_balance=2)

        eligible, reason = RefundEligibilityService.check(self.payment)

        self.assertFalse(eligible)
        self.assertEqual(reason, "ALREADY_CONSUMED")

    def test_b2b_current_period_not_refundable_by_default(self):
        owner = User.objects.create_user(
            email="refund-b2b@example.com", password="Password123!", first_name="Refund", last_name="B2B",
            role=Roles.B2B, is_verified=True,
        )
        company = make_company(owner)
        price = make_price(target_user_type="B2B", slot_grant=200, points_grant=2000)
        subscription = make_subscription(owner, price, company=company, status="ACTIVE")
        b2b_payment = Payment.objects.create(
            user=owner, customer=subscription.customer, subscription=subscription, stripe_payment_intent_id="pi_refund_b2b",
            amount=Decimal("2000.00"), status="SUCCEEDED",
        )

        eligible, reason = RefundEligibilityService.check(b2b_payment)

        self.assertFalse(eligible)
        self.assertEqual(reason, "B2B_CURRENT_PERIOD_NOT_REFUNDABLE")


class RefundServiceTests(TestCase):
    def setUp(self):
        self.b2c_user = User.objects.create_user(
            email="refund-service-b2c@example.com", password="Password123!", first_name="Refund", last_name="Service",
            role=Roles.B2C, is_verified=True,
        )
        self.customer = Customer.objects.create(user=self.b2c_user, stripe_customer_id="cus_refund_service", email=self.b2c_user.email)
        self.payment = Payment.objects.create(
            user=self.b2c_user, customer=self.customer, stripe_payment_intent_id="pi_refund_service_test",
            amount=Decimal("50.00"), status="SUCCEEDED",
        )
        self.balance = PackageBalance.objects.create(
            owner_user=self.b2c_user, balance_type=PackageBalance.SLOTS, source_payment=self.payment, fixed_amount=3, current_balance=3,
        )

    @patch("api.payments.refund_services.stripe")
    def test_successful_refund_revokes_unused_balance_and_marks_payment_refunded(self, mock_stripe):
        RefundService.refund_payment(payment=self.payment, actor=self.b2c_user)

        mock_stripe.Refund.create.assert_called_once_with(payment_intent="pi_refund_service_test")
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, "REFUNDED")
        self.assertIsNotNone(self.payment.refunded_at)
        self.balance.refresh_from_db()
        self.assertEqual(self.balance.current_balance, 0)
        self.assertTrue(
            BalanceTransaction.objects.filter(
                balance=self.balance, transaction_type=BalanceTransaction.REFUND,
                reference=f"refund:{self.payment.stripe_payment_intent_id}",
            ).exists()
        )

    @patch("api.payments.refund_services.stripe")
    def test_refund_requires_override_reason_code_when_ineligible(self, mock_stripe):
        self.balance.current_balance = 2
        self.balance.save(update_fields=["current_balance"])

        with self.assertRaises(ValueError):
            RefundService.refund_payment(payment=self.payment, actor=self.b2c_user)

        mock_stripe.Refund.create.assert_not_called()

    @patch("api.payments.refund_services.stripe")
    def test_refund_rejects_invalid_override_reason_code(self, mock_stripe):
        self.balance.current_balance = 2
        self.balance.save(update_fields=["current_balance"])

        with self.assertRaises(ValueError):
            RefundService.refund_payment(payment=self.payment, actor=self.b2c_user, override_reason_code="NOT_A_REAL_CODE")

        mock_stripe.Refund.create.assert_not_called()

    @patch("api.payments.refund_services.stripe")
    def test_refund_never_restores_already_consumed_entitlement_even_with_override(self, mock_stripe):
        self.balance.current_balance = 1  # 2 of 3 slots already consumed
        self.balance.save(update_fields=["current_balance"])

        RefundService.refund_payment(payment=self.payment, actor=self.b2c_user, override_reason_code=PLATFORM_ERROR)

        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, "REFUNDED")
        self.balance.refresh_from_db()
        self.assertEqual(self.balance.current_balance, 0)  # never restored above what was left unused

    @patch("api.payments.refund_services.stripe")
    def test_cannot_refund_already_refunded_payment(self, mock_stripe):
        RefundService.refund_payment(payment=self.payment, actor=self.b2c_user)
        mock_stripe.Refund.create.reset_mock()

        with self.assertRaises(ValueError):
            RefundService.refund_payment(payment=self.payment, actor=self.b2c_user)

        mock_stripe.Refund.create.assert_not_called()


class ChargeRefundedWebhookTests(TestCase):
    """Covers a refund issued directly from the Stripe Dashboard (not
    through our admin action) - the webhook must still sync the local
    entitlement state, and must not double-process a redelivered event."""

    def setUp(self):
        self.b2c_user = User.objects.create_user(
            email="refund-webhook-b2c@example.com", password="Password123!", first_name="Refund", last_name="Webhook",
            role=Roles.B2C, is_verified=True,
        )
        self.customer = Customer.objects.create(user=self.b2c_user, stripe_customer_id="cus_refund_webhook", email=self.b2c_user.email)
        self.payment = Payment.objects.create(
            user=self.b2c_user, customer=self.customer, stripe_payment_intent_id="pi_refund_webhook_test",
            amount=Decimal("50.00"), status="SUCCEEDED",
        )
        self.balance = PackageBalance.objects.create(
            owner_user=self.b2c_user, balance_type=PackageBalance.SLOTS, source_payment=self.payment, fixed_amount=3, current_balance=3,
        )
        self.service = StripeService()

    def test_charge_refunded_webhook_syncs_entitlement_and_is_idempotent(self):
        charge_data = {"payment_intent": "pi_refund_webhook_test"}

        self.service.handle_charge_refunded(charge_data)

        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, "REFUNDED")
        self.balance.refresh_from_db()
        self.assertEqual(self.balance.current_balance, 0)

        # Redelivery, or the admin path already having processed this
        # payment - must not double-process.
        self.service.handle_charge_refunded(charge_data)
        self.balance.refresh_from_db()
        self.assertEqual(self.balance.current_balance, 0)


class AdminPaymentRefundEndpointTests(APITestCase):
    def setUp(self):
        self.superadmin = User.objects.create_user(
            email="refund-endpoint-superadmin@example.com", password="Password123!",
            first_name="Refund", last_name="Super", role=Roles.SUPERADMIN, is_verified=True, is_staff=True,
        )
        login = self.client.post(
            "/api/v1/auth/login", {"email": self.superadmin.email, "password": "Password123!"}, format="json",
        )
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {login.data['access']}")

        self.b2c_user = User.objects.create_user(
            email="refund-endpoint-b2c@example.com", password="Password123!", first_name="Refund", last_name="Endpoint",
            role=Roles.B2C, is_verified=True,
        )
        self.customer = Customer.objects.create(user=self.b2c_user, stripe_customer_id="cus_refund_endpoint", email=self.b2c_user.email)
        self.payment = Payment.objects.create(
            user=self.b2c_user, customer=self.customer, stripe_payment_intent_id="pi_refund_endpoint_test",
            amount=Decimal("50.00"), status="SUCCEEDED",
        )
        PackageBalance.objects.create(
            owner_user=self.b2c_user, balance_type=PackageBalance.SLOTS, source_payment=self.payment, fixed_amount=3, current_balance=3,
        )

    @patch("api.payments.refund_services.stripe")
    def test_admin_can_refund_an_eligible_payment(self, mock_stripe):
        response = self.client.post(f"/api/v1/payments/admin/payments/{self.payment.id}/refund", {}, format="json")

        self.assertEqual(response.status_code, 200, response.data)
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, "REFUNDED")


class RetireAndReplacePriceTests(TestCase):
    """Editing a package's amount/currency/interval retires the old Stripe
    Price and mints a new local Price row (Stripe Prices are immutable) -
    slot_grant/points_grant must carry over, or every price edit would
    silently strip a package's entitlements back to unenforced."""

    def setUp(self):
        self.old_price = make_price(name="Growth", target_user_type="B2B", slot_grant=200, points_grant=2000)
        self.service = StripeService()

    @patch("api.payments.services.stripe")
    def test_carries_forward_slot_and_points_grant_when_not_overridden(self, mock_stripe):
        mock_stripe.Product.create.return_value = MagicMock(id="prod_new")
        mock_stripe.Price.create.return_value = MagicMock(id="price_new")

        new_price = self.service.retire_and_replace_price(self.old_price, {"unit_amount": Decimal("2500.00")})

        self.assertEqual(new_price.slot_grant, 200)
        self.assertEqual(new_price.points_grant, 2000)
        self.old_price.refresh_from_db()
        self.assertFalse(self.old_price.is_active)

    @patch("api.payments.services.stripe")
    def test_explicit_override_wins_over_old_price(self, mock_stripe):
        mock_stripe.Product.create.return_value = MagicMock(id="prod_new")
        mock_stripe.Price.create.return_value = MagicMock(id="price_new")

        new_price = self.service.retire_and_replace_price(
            self.old_price, {"unit_amount": Decimal("2500.00"), "slot_grant": 300}
        )

        self.assertEqual(new_price.slot_grant, 300)
        self.assertEqual(new_price.points_grant, 2000)


class SpendPointsEndpointTests(APITestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            email="points-spender@example.com", password="Password123!", first_name="Point", last_name="Spender",
            role=Roles.B2C, is_verified=True,
        )
        self.client.force_authenticate(self.user)
        PackageBalance.objects.create(owner_user=self.user, balance_type=PackageBalance.POINTS, fixed_amount=50, current_balance=50)

    def test_spend_valid_addon_deducts_points(self):
        response = self.client.post(
            "/api/v1/payments/points/spend",
            {"addon_code": "practical_simulation_test"},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["remaining_points"], 20)

    def test_spend_rejects_invalid_addon_code(self):
        response = self.client.post(
            "/api/v1/payments/points/spend",
            {"addon_code": "does_not_exist"},
            format="json",
        )
        self.assertEqual(response.status_code, 400)

    def test_spend_rejects_when_balance_insufficient(self):
        PackageBalance.objects.filter(owner_user=self.user).update(current_balance=5)

        response = self.client.post(
            "/api/v1/payments/points/spend",
            {"addon_code": "practical_simulation_test"},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("points remaining", response.data["detail"])


class AdminSubscriptionStatsAndSerializerTests(APITestCase):
    """Covers the admin Billing & Subscriptions page (/dashboard/admin/billing/)."""

    def setUp(self):
        self.superadmin = User.objects.create_user(
            email="billing-superadmin@example.com",
            password="Password123!",
            first_name="Billing",
            last_name="Super",
            role=Roles.SUPERADMIN,
            is_verified=True,
            is_staff=True,
        )
        login = self.client.post(
            "/api/v1/auth/login",
            {"email": self.superadmin.email, "password": "Password123!"},
            format="json",
        )
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {login.data['access']}")

        self.b2b_owner = User.objects.create_user(
            email="billing-b2b-owner@example.com",
            password="Password123!",
            first_name="B2B",
            last_name="Owner",
            role=Roles.B2B,
            is_verified=True,
        )
        self.company = make_company(self.b2b_owner, name="Acme Corp")

    def test_stats_counts_revenue_regardless_of_currency_case(self):
        """Regression test: stats() did an exact-match `currency == 'eur'`
        check, the same class of bug already fixed elsewhere this session -
        a Price stored as 'EUR' silently dropped out of monthly_revenue."""
        price_lower = make_price(name="Growth Lower", target_user_type="B2B", currency="eur", unit_amount=Decimal("2000.00"))
        price_upper = make_price(name="Growth Upper", target_user_type="B2B", currency="EUR", unit_amount=Decimal("3500.00"))
        make_subscription(self.b2b_owner, price_lower, company=self.company, status="ACTIVE")
        make_subscription(self.b2b_owner, price_upper, company=self.company, status="ACTIVE")

        response = self.client.get("/api/v1/payments/admin/subscriptions/stats")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["monthly_revenue"], 5500.0)

    def test_subscription_list_exposes_company_name(self):
        """Regression test: the admin billing page could only show a raw
        company id ("Company ID: 47") because the serializer never exposed
        a name at all."""
        price = make_price(target_user_type="B2B")
        make_subscription(self.b2b_owner, price, company=self.company, status="ACTIVE")

        response = self.client.get("/api/v1/payments/admin/subscriptions")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["results"][0]["company_name"], "Acme Corp")


class CreateSubscriptionSerializerPaymentMethodTests(TestCase):
    """payment_method_id is optional on the serializer (a genuine $0 plan
    has nothing to charge), but that must not let a paid plan be
    subscribed to for free by simply omitting it."""

    def setUp(self):
        self.user = User.objects.create_user(
            email="free-tier-subscriber@example.com", password="Password123!",
            first_name="Free", last_name="Tier", role=Roles.B2C, is_verified=True,
        )

    def _serializer(self, price, payment_method_id=None):
        data = {"price_id": str(price.pk)}
        if payment_method_id is not None:
            data["payment_method_id"] = payment_method_id
        return CreateSubscriptionSerializer(data=data, context={"request": MagicMock(user=self.user)})

    def test_paid_plan_without_payment_method_is_rejected(self):
        price = make_price(unit_amount=Decimal("2000.00"), target_user_type="BOTH")
        serializer = self._serializer(price)
        self.assertFalse(serializer.is_valid())
        self.assertIn("payment_method_id", serializer.errors)

    def test_paid_plan_with_payment_method_is_accepted(self):
        price = make_price(unit_amount=Decimal("2000.00"), target_user_type="BOTH")
        serializer = self._serializer(price, payment_method_id="pm_test123")
        self.assertTrue(serializer.is_valid(), serializer.errors)

    def test_free_plan_without_payment_method_is_accepted(self):
        price = make_price(unit_amount=Decimal("0.00"), target_user_type="BOTH")
        serializer = self._serializer(price)
        self.assertTrue(serializer.is_valid(), serializer.errors)


class AdminPackagePermanentDeleteTests(APITestCase):
    """Covers the admin Package Management page's permanent-delete action -
    only ever allowed for a package with zero subscription history, since
    Subscription.stripe_price is SET_NULL on delete (safe from a DB
    integrity standpoint) but would silently erase which plan a real
    subscriber was on."""

    def setUp(self):
        self.superadmin = User.objects.create_user(
            email="pkg-delete-superadmin@example.com", password="Password123!",
            first_name="Pkg", last_name="Super", role=Roles.SUPERADMIN, is_verified=True, is_staff=True,
        )
        login = self.client.post(
            "/api/v1/auth/login", {"email": self.superadmin.email, "password": "Password123!"}, format="json",
        )
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {login.data['access']}")

    def test_deletes_a_package_with_no_subscription_history(self):
        price = make_price(name="Unused Pilot", target_user_type="B2B")

        response = self.client.delete(f"/api/v1/payments/admin/prices/{price.id}?permanent=true")

        self.assertEqual(response.status_code, 204, response.data)
        self.assertFalse(Price.objects.filter(id=price.id).exists())

    def test_rejects_deleting_a_package_with_subscription_history(self):
        b2b_owner = User.objects.create_user(
            email="pkg-delete-b2b-owner@example.com", password="Password123!",
            first_name="B2B", last_name="Owner", role=Roles.B2B, is_verified=True,
        )
        price = make_price(name="Growth In Use", target_user_type="B2B")
        make_subscription(b2b_owner, price, status="ACTIVE")

        response = self.client.delete(f"/api/v1/payments/admin/prices/{price.id}?permanent=true")

        self.assertEqual(response.status_code, 400, response.data)
        self.assertTrue(Price.objects.filter(id=price.id).exists())

    def test_deactivate_without_permanent_flag_is_unaffected(self):
        """Regression guard: the existing soft-deactivate action must keep
        working exactly as before - it's the default when ?permanent isn't
        passed at all."""
        price = make_price(name="Still Deactivatable", target_user_type="B2B")

        response = self.client.delete(f"/api/v1/payments/admin/prices/{price.id}")

        self.assertEqual(response.status_code, 200, response.data)
        price.refresh_from_db()
        self.assertFalse(price.is_active)


class ChangePlanEntitlementTests(APITestCase):
    """change_plan() must not leave PackageBalance stale until an unrelated
    future renewal (see the memo comment above it in views.py). Per the
    Package Architecture memo: an upgrade gets its new entitlement
    immediately upon confirmed payment of the proration; a downgrade (or
    an upgrade nobody actually paid extra for) leaves the current balance
    untouched - "No automatic entitlement reduction... without an approved
    package-change rule" - until the next natural renewal applies it."""

    def setUp(self):
        self.superadmin = User.objects.create_user(
            email="change-plan-superadmin@example.com", password="Password123!",
            first_name="Change", last_name="Super", role=Roles.SUPERADMIN, is_verified=True, is_staff=True,
        )
        login = self.client.post(
            "/api/v1/auth/login", {"email": self.superadmin.email, "password": "Password123!"}, format="json",
        )
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {login.data['access']}")

        self.owner = User.objects.create_user(
            email="change-plan-owner@example.com", password="Password123!",
            first_name="B2B", last_name="Owner", role=Roles.B2B, is_verified=True,
        )
        self.company = make_company(self.owner)
        self.growth = make_price(name="growth package", target_user_type="BOTH", unit_amount=Decimal("2000.00"), slot_grant=200, points_grant=2000)
        self.business = make_price(name="business package", target_user_type="BOTH", unit_amount=Decimal("3500.00"), slot_grant=500, points_grant=3500)
        self.subscription = make_subscription(self.owner, self.growth, company=self.company, status="ACTIVE")

    def _mock_subscription_retrieve(self, mock_stripe):
        mock_stripe.Subscription.retrieve.return_value = {"items": {"data": [{"id": "si_test123"}]}}

    @patch("api.payments.views.stripe")
    def test_upgrade_grants_new_entitlement_immediately_on_confirmed_payment(self, mock_stripe):
        self._mock_subscription_retrieve(mock_stripe)
        mock_invoice = MagicMock()
        mock_invoice.finalize_invoice.return_value = mock_invoice
        mock_invoice.pay.return_value = MagicMock(status="paid")
        mock_stripe.Invoice.create.return_value = mock_invoice

        # Unused balance from the old (growth) plan must be added to, not
        # overwritten by, the new plan's full grant (Sign-Off Section 3).
        PackageBalance.objects.create(
            owner_company=self.company, balance_type=PackageBalance.SLOTS, fixed_amount=200, current_balance=30,
        )

        response = self.client.post(
            f"/api/v1/payments/subscriptions/{self.subscription.public_id}/change_plan",
            {"price_id": str(self.business.id), "prorate": True},
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.data)
        mock_stripe.Invoice.create.assert_called_once()
        balance = PackageBalance.objects.get(owner_company=self.company, balance_type=PackageBalance.SLOTS)
        self.assertEqual(balance.current_balance, 530)

    @patch("api.payments.views.stripe")
    def test_repeated_upgrade_in_same_billing_cycle_does_not_double_grant(self, mock_stripe):
        self._mock_subscription_retrieve(mock_stripe)
        mock_invoice = MagicMock()
        mock_invoice.finalize_invoice.return_value = mock_invoice
        mock_invoice.pay.return_value = MagicMock(status="paid")
        mock_stripe.Invoice.create.return_value = mock_invoice

        enterprise = make_price(name="enterprise-like package", target_user_type="BOTH", unit_amount=Decimal("5000.00"), slot_grant=1000, points_grant=5000)

        first = self.client.post(
            f"/api/v1/payments/subscriptions/{self.subscription.public_id}/change_plan",
            {"price_id": str(self.business.id), "prorate": True},
            format="json",
        )
        self.assertEqual(first.status_code, 200, first.data)
        balance = PackageBalance.objects.get(owner_company=self.company, balance_type=PackageBalance.SLOTS)
        self.assertEqual(balance.current_balance, 500)

        # Stripe does not move current_period_start/end for a mid-cycle plan
        # swap - only a real renewal does - so this second upgrade lands in
        # the same billing cycle as the first and must not grant again.
        second = self.client.post(
            f"/api/v1/payments/subscriptions/{self.subscription.public_id}/change_plan",
            {"price_id": str(enterprise.id), "prorate": True},
            format="json",
        )
        self.assertEqual(second.status_code, 200, second.data)
        balance.refresh_from_db()
        self.assertEqual(balance.current_balance, 500)

    @patch("api.payments.views.stripe")
    def test_downgrade_does_not_touch_existing_balance(self, mock_stripe):
        self._mock_subscription_retrieve(mock_stripe)
        self.subscription.stripe_price = self.business
        self.subscription.save(update_fields=["stripe_price"])
        PackageBalance.objects.create(
            owner_company=self.company, balance_type=PackageBalance.SLOTS, fixed_amount=500, current_balance=17,
        )

        response = self.client.post(
            f"/api/v1/payments/subscriptions/{self.subscription.public_id}/change_plan",
            {"price_id": str(self.growth.id), "prorate": True},
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.data)
        mock_stripe.Invoice.create.assert_not_called()
        balance = PackageBalance.objects.get(owner_company=self.company, balance_type=PackageBalance.SLOTS)
        self.assertEqual(balance.current_balance, 17)

    @patch("api.payments.views.stripe")
    def test_upgrade_without_prorate_does_not_immediately_grant(self, mock_stripe):
        self._mock_subscription_retrieve(mock_stripe)
        PackageBalance.objects.create(
            owner_company=self.company, balance_type=PackageBalance.SLOTS, fixed_amount=200, current_balance=200,
        )

        response = self.client.post(
            f"/api/v1/payments/subscriptions/{self.subscription.public_id}/change_plan",
            {"price_id": str(self.business.id), "prorate": False},
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.data)
        mock_stripe.Invoice.create.assert_not_called()
        balance = PackageBalance.objects.get(owner_company=self.company, balance_type=PackageBalance.SLOTS)
        self.assertEqual(balance.current_balance, 200)


class WebhookIdempotencyTests(APITestCase):
    """A redelivered Stripe webhook (retries, manual replays - Stripe
    explicitly documents this can happen) must not be reprocessed, since
    e.g. reset_b2b_balances() would otherwise wipe out consumption that
    happened between the first delivery and a later redelivery of the
    same event."""

    def _fake_event(self, event_id="evt_test123", event_type="invoice.payment_succeeded"):
        return {"id": event_id, "type": event_type, "data": {"object": {}}}

    @patch("api.payments.views.StripeService")
    @patch("api.payments.views.stripe.Webhook.construct_event")
    def test_duplicate_event_id_is_not_reprocessed(self, mock_construct_event, mock_service_cls):
        mock_construct_event.return_value = self._fake_event()
        mock_service_cls.return_value.handle_webhook_event.return_value = {"ok": True}

        first = self.client.post(
            "/api/v1/payments/webhook", data=b"{}", content_type="application/json",
            HTTP_STRIPE_SIGNATURE="sig",
        )
        second = self.client.post(
            "/api/v1/payments/webhook", data=b"{}", content_type="application/json",
            HTTP_STRIPE_SIGNATURE="sig",
        )

        self.assertEqual(first.status_code, 200, first.content)
        self.assertEqual(second.status_code, 200, second.content)
        self.assertTrue(second.json().get("duplicate"))
        self.assertEqual(mock_service_cls.return_value.handle_webhook_event.call_count, 1)
        self.assertEqual(ProcessedStripeEvent.objects.filter(stripe_event_id="evt_test123").count(), 1)

    @patch("api.payments.views.StripeService")
    @patch("api.payments.views.stripe.Webhook.construct_event")
    def test_different_event_ids_both_process(self, mock_construct_event, mock_service_cls):
        mock_construct_event.side_effect = [
            self._fake_event(event_id="evt_a"),
            self._fake_event(event_id="evt_b"),
        ]
        mock_service_cls.return_value.handle_webhook_event.return_value = {"ok": True}

        self.client.post("/api/v1/payments/webhook", data=b"{}", content_type="application/json", HTTP_STRIPE_SIGNATURE="sig")
        self.client.post("/api/v1/payments/webhook", data=b"{}", content_type="application/json", HTTP_STRIPE_SIGNATURE="sig")

        self.assertEqual(mock_service_cls.return_value.handle_webhook_event.call_count, 2)

    @patch("api.payments.views.StripeService")
    @patch("api.payments.views.stripe.Webhook.construct_event")
    def test_processing_failure_removes_marker_so_retry_can_reprocess(self, mock_construct_event, mock_service_cls):
        mock_construct_event.return_value = self._fake_event(event_id="evt_fail")
        mock_service_cls.return_value.handle_webhook_event.side_effect = RuntimeError("boom")

        response = self.client.post(
            "/api/v1/payments/webhook", data=b"{}", content_type="application/json",
            HTTP_STRIPE_SIGNATURE="sig",
        )

        self.assertEqual(response.status_code, 500)
        self.assertFalse(ProcessedStripeEvent.objects.filter(stripe_event_id="evt_fail").exists())
