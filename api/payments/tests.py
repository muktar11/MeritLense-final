from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import stripe
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase
from django.utils import timezone
from rest_framework.test import APIClient, APITestCase

from api.accounts.models import Company, User
from api.audit.models import AuditLog
from api.candidates.models import Candidate
from api.core.constants import InterviewEvaluationTier, InterviewSessionStatus, Roles
from api.interviews.models import InterviewConfiguration
from api.payments.entitlement_services import ADDON_POINTS_CATALOG, EntitlementService
from api.payments.models import AddonRequest, BalanceTransaction, Customer, DealRecord, Invoice, PackageBalance, Payment, Price, ProcessedStripeEvent, SlotReservation, Subscription
from api.payments.serializers import DealRecordSerializer
from api.payments.refund_services import CONFIRMED_BILLING_ERROR, PLATFORM_ERROR, RefundEligibilityService, RefundService
from api.payments.serializers import CreateSubscriptionSerializer
from api.payments.services import PaymentIntentInitializationError, StripeService
from api.sessions.models import InterviewSession
from api.sessions.services import InterviewSessionService


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


class CreatePaymentIntentInitializationTests(SimpleTestCase):
    def setUp(self):
        self.user = SimpleNamespace(
            id=123,
            email="candidate@example.com",
            get_full_name=lambda: "Candidate",
        )
        self.service = StripeService()

    @patch.object(StripeService, "get_or_create_customer", return_value=None)
    def test_customer_creation_failure_returns_actionable_error(self, _get_customer):
        with self.assertRaisesRegex(PaymentIntentInitializationError, "Stripe customer"):
            self.service.create_payment_intent(self.user, amount=Decimal("10.00"))

    @patch.object(
        StripeService,
        "get_or_create_customer",
        return_value=SimpleNamespace(id=1, stripe_customer_id="cus_test"),
    )
    def test_non_positive_amount_is_rejected(self, _get_customer):
        with self.assertRaisesRegex(PaymentIntentInitializationError, "positive price"):
            self.service.create_payment_intent(self.user, amount=Decimal("0.00"))

    @patch.object(
        StripeService,
        "get_or_create_customer",
        return_value=SimpleNamespace(id=1, stripe_customer_id="cus_test"),
    )
    @patch("api.payments.services.stripe.PaymentIntent.create")
    def test_stripe_failure_is_logged_and_reported(self, create_intent, _get_customer):
        create_intent.side_effect = stripe.error.APIConnectionError("Stripe unavailable")

        with self.assertLogs("api.payments.services", level="ERROR"):
            with self.assertRaisesRegex(PaymentIntentInitializationError, "Stripe unavailable"):
                self.service.create_payment_intent(self.user, amount=Decimal("10.00"))

    @patch("api.payments.services.PaymentMethod.objects.filter")
    @patch("api.payments.services.Customer.objects.update_or_create")
    @patch("api.payments.services.Customer.objects.get")
    @patch("api.payments.services.stripe.Customer.create")
    @patch("api.payments.services.stripe.Customer.retrieve")
    def test_replaces_customer_missing_from_configured_stripe_account(
        self, retrieve, create, get_customer, update_or_create, payment_methods
    ):
        stored_customer = SimpleNamespace(
            id=1,
            stripe_customer_id="cus_test",
            email="candidate@example.com",
            name="Candidate",
            metadata={},
            default_payment_method_id="pm_test",
            save=MagicMock(),
        )
        get_customer.return_value = stored_customer

        def update_customer(user, defaults):
            for key, value in defaults.items():
                setattr(stored_customer, key, value)
            return stored_customer, False

        update_or_create.side_effect = update_customer
        retrieve.side_effect = stripe.error.InvalidRequestError(
            "No such customer",
            "customer",
            code="resource_missing",
        )
        create.return_value = SimpleNamespace(id="cus_live")
        update_or_create.return_value = (stored_customer, False)

        customer = self.service.get_or_create_customer(self.user)

        self.assertIs(customer, stored_customer)
        self.assertEqual(customer.stripe_customer_id, "cus_live")
        self.assertEqual(customer.default_payment_method_id, "")
        self.assertEqual(customer.metadata["previous_stripe_customer_ids"], ["cus_test"])
        customer.save.assert_called_once()
        payment_methods.return_value.update.assert_called_once_with(
            is_active=False,
            is_default=False,
        )


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

    def test_due_date_is_populated_from_the_stripe_payload(self):
        """Regression: timezone.utc doesn't exist on this Django version
        (only datetime.timezone.utc does) - the original fix crashed
        handle_invoice_paid's whole try block silently (caught by the
        method's own outer except) the first time a real due_date was
        ever passed, discovered by actually simulating a live webhook
        against production rather than by this test suite, since no
        existing fixture included a due_date key at all."""
        invoice_data = {
            "id": "in_test_due_date",
            "number": "INV-DUE-1",
            "amount_due": 200000,
            "amount_paid": 200000,
            "amount_remaining": 0,
            "currency": "eur",
            "subscription": self.subscription.stripe_subscription_id,
            "due_date": 1893456000,  # 2030-01-01T00:00:00Z
            "invoice_pdf": "",
            "hosted_invoice_url": "",
        }

        invoice = self.service.handle_invoice_paid(invoice_data)

        self.assertIsNotNone(invoice)
        self.assertEqual(invoice.due_date.year, 2030)

    def test_local_pdf_is_generated_automatically_for_a_new_paid_invoice(self):
        invoice_data = {
            "id": "in_test_auto_pdf",
            "number": "INV-AUTO-PDF-1",
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
        self.assertTrue(invoice.local_pdf_file)
        self.assertTrue(invoice.pdf_hash)

    def test_creates_invoice_when_subscription_is_nested_under_parent(self):
        """Confirmed live: a newer Stripe API version stopped sending a
        top-level 'subscription' field on invoice webhook payloads
        entirely, nesting it under parent.subscription_details.subscription
        instead - every real B2B renewal was silently skipped as a result."""
        invoice_data = {
            "id": "in_test_nested",
            "number": "INV-002",
            "amount_due": 200000,
            "amount_paid": 200000,
            "amount_remaining": 0,
            "currency": "eur",
            "parent": {"subscription_details": {"subscription": self.subscription.stripe_subscription_id}},
            "invoice_pdf": "",
            "hosted_invoice_url": "",
        }

        invoice = self.service.handle_invoice_paid(invoice_data)

        self.assertIsNotNone(invoice)
        self.assertEqual(invoice.subscription, self.subscription)
        self.assertEqual(invoice.status, "PAID")

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

    def test_invoice_paid_emails_the_customer_and_activation_notice(self):
        """Business requirement: automatic email alerts for package
        activation and invoice generation - both fire from this one
        webhook handler since an INCOMPLETE subscription becomes ACTIVE
        and its first real Invoice row is created in the same call."""
        mail.outbox = []
        invoice_data = {
            "id": "in_activation_test",
            "number": "INV-ACT-1",
            "amount_due": 200000,
            "amount_paid": 200000,
            "amount_remaining": 0,
            "currency": "eur",
            "subscription": self.subscription.stripe_subscription_id,
            "invoice_pdf": "https://stripe.example/inv.pdf",
            "hosted_invoice_url": "",
        }

        self.service.handle_invoice_paid(invoice_data)

        activation_emails = [m for m in mail.outbox if "active" in m.subject.lower()]
        self.assertEqual(len(activation_emails), 1)
        self.assertIn(self.user.email, activation_emails[0].to)

        invoice_emails = [m for m in mail.outbox if "invoice" in m.subject.lower()]
        self.assertEqual(len(invoice_emails), 1)
        self.assertIn(self.user.email, invoice_emails[0].to)
        self.assertIn("https://stripe.example/inv.pdf", invoice_emails[0].body)

    def test_renewal_invoice_does_not_resend_activation_email(self):
        """Only the INCOMPLETE->ACTIVE transition (first invoice) should
        trigger the activation email - a renewal on an already-active
        subscription must not re-send it, only the invoice email."""
        first = {
            "id": "in_act_first", "number": "INV-ACT-1", "amount_due": 200000,
            "amount_paid": 200000, "amount_remaining": 0, "currency": "eur",
            "subscription": self.subscription.stripe_subscription_id,
        }
        self.service.handle_invoice_paid(first)

        mail.outbox = []
        second = {**first, "id": "in_act_second", "number": "INV-ACT-2"}
        self.service.handle_invoice_paid(second)

        activation_emails = [m for m in mail.outbox if "active" in m.subject.lower()]
        self.assertEqual(len(activation_emails), 0)

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

    def test_manual_billing_reason_invoice_does_not_reset_balance(self):
        """Confirmed live: change_plan()'s upgrade-proration invoice (billing_reason
        'manual') still generates a real invoice.payment_succeeded webhook - it must
        not re-reset a balance apply_upgrade_grant() already additively topped up."""
        company = make_company(self.user)
        self.price.slot_grant = 500
        self.price.points_grant = 3500
        self.price.save(update_fields=["slot_grant", "points_grant"])
        self.subscription.company = company
        self.subscription.save(update_fields=["company"])
        balance = PackageBalance.objects.create(
            owner_company=company, balance_type=PackageBalance.SLOTS,
            source_subscription=self.subscription, fixed_amount=500, current_balance=530,
        )

        invoice = self.service.handle_invoice_paid({
            "id": "in_manual_proration",
            "number": "INV-PRORATION",
            "amount_due": 150000,
            "amount_paid": 150000,
            "amount_remaining": 0,
            "currency": "eur",
            "subscription": self.subscription.stripe_subscription_id,
            "billing_reason": "manual",
        })

        self.assertIsNotNone(invoice)
        self.assertEqual(invoice.status, "PAID")
        balance.refresh_from_db()
        self.assertEqual(balance.current_balance, 530)

    def test_grant_resolution_prefers_deal_record_over_price(self):
        """self.price defaults to slot_grant/points_grant = None (unset) -
        a linked, active DealRecord must still grant its own terms."""
        company = make_company(self.user)
        self.subscription.company = company
        self.subscription.save(update_fields=["company"])
        DealRecord.objects.create(
            company=company, price=self.price, deal_type=DealRecord.ENTERPRISE,
            slot_grant=50, points_grant=None, unit_amount=Decimal("5000.00"),
        )

        self.service.handle_invoice_paid({
            "id": "in_deal", "number": "INV-DEAL", "amount_due": 500000, "amount_paid": 500000,
            "amount_remaining": 0, "currency": "eur", "subscription": self.subscription.stripe_subscription_id,
        })

        balance = PackageBalance.objects.get(owner_company=company, balance_type=PackageBalance.SLOTS)
        self.assertEqual(balance.current_balance, 50)

    def test_renewal_rolls_over_unused_balance_when_deal_allows_it(self):
        company = make_company(self.user)
        self.subscription.company = company
        self.subscription.save(update_fields=["company"])
        DealRecord.objects.create(
            company=company, price=self.price, deal_type=DealRecord.ENTERPRISE,
            slot_grant=50, unit_amount=Decimal("5000.00"), rollover_allowed=True,
        )

        self.service.handle_invoice_paid({
            "id": "in_first", "number": "INV-001", "amount_due": 500000, "amount_paid": 500000,
            "amount_remaining": 0, "currency": "eur", "subscription": self.subscription.stripe_subscription_id,
        })
        balance = PackageBalance.objects.get(owner_company=company, balance_type=PackageBalance.SLOTS)
        balance.current_balance = 30  # 20 consumed since the first grant
        balance.save(update_fields=["current_balance"])

        self.service.handle_invoice_paid({
            "id": "in_second", "number": "INV-002", "amount_due": 500000, "amount_paid": 500000,
            "amount_remaining": 0, "currency": "eur", "subscription": self.subscription.stripe_subscription_id,
        })

        balance.refresh_from_db()
        self.assertEqual(balance.current_balance, 80)  # 30 unused + 50 new grant, not overwritten

    def test_renewal_does_not_rollover_by_default(self):
        company = make_company(self.user)
        self.subscription.company = company
        self.subscription.save(update_fields=["company"])
        DealRecord.objects.create(
            company=company, price=self.price, deal_type=DealRecord.ENTERPRISE,
            slot_grant=50, unit_amount=Decimal("5000.00"),  # rollover_allowed defaults to False
        )

        self.service.handle_invoice_paid({
            "id": "in_first", "number": "INV-001", "amount_due": 500000, "amount_paid": 500000,
            "amount_remaining": 0, "currency": "eur", "subscription": self.subscription.stripe_subscription_id,
        })
        balance = PackageBalance.objects.get(owner_company=company, balance_type=PackageBalance.SLOTS)
        balance.current_balance = 30
        balance.save(update_fields=["current_balance"])

        self.service.handle_invoice_paid({
            "id": "in_second", "number": "INV-002", "amount_due": 500000, "amount_paid": 500000,
            "amount_remaining": 0, "currency": "eur", "subscription": self.subscription.stripe_subscription_id,
        })

        balance.refresh_from_db()
        self.assertEqual(balance.current_balance, 50)  # hard reset, matches today's default behavior


class DealRecordSerializerTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            email="deal-owner@example.com", password="Password123!", first_name="Deal", last_name="Owner",
            role=Roles.B2B, is_verified=True,
        )
        self.company = make_company(self.owner)

    def _free_trial_data(self, **overrides):
        data = dict(company=self.company.id, deal_type=DealRecord.FREE_TRIAL, slot_grant=2, unit_amount="0.00")
        data.update(overrides)
        return data

    def test_free_trial_requires_exactly_two_slots_and_zero_price(self):
        self.assertFalse(DealRecordSerializer(data=self._free_trial_data(slot_grant=3)).is_valid())
        self.assertFalse(DealRecordSerializer(data=self._free_trial_data(unit_amount="10.00")).is_valid())
        self.assertTrue(DealRecordSerializer(data=self._free_trial_data()).is_valid())

    def test_free_trial_rejects_duplicate_for_same_company(self):
        DealRecord.objects.create(company=self.company, deal_type=DealRecord.FREE_TRIAL, slot_grant=2, unit_amount=Decimal("0.00"))

        serializer = DealRecordSerializer(data=self._free_trial_data())
        self.assertFalse(serializer.is_valid())


class AdminDealRecordEndpointTests(APITestCase):
    def setUp(self):
        self.superadmin = User.objects.create_user(
            email="deal-endpoint-superadmin@example.com", password="Password123!",
            first_name="Deal", last_name="Super", role=Roles.SUPERADMIN, is_verified=True, is_staff=True,
        )
        login = self.client.post(
            "/api/v1/auth/login", {"email": self.superadmin.email, "password": "Password123!"}, format="json",
        )
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {login.data['access']}")

        self.owner = User.objects.create_user(
            email="deal-endpoint-owner@example.com", password="Password123!", first_name="Deal", last_name="Endpoint",
            role=Roles.B2B, is_verified=True,
        )
        self.company = make_company(self.owner)

    def test_superadmin_can_create_a_deal_record(self):
        response = self.client.post(
            "/api/v1/payments/admin/deal-records",
            {"company": self.company.id, "deal_type": DealRecord.ENTERPRISE, "slot_grant": 40, "unit_amount": "4000.00"},
            format="json",
        )

        self.assertEqual(response.status_code, 201, response.data)
        deal = DealRecord.objects.get(company=self.company)
        self.assertEqual(deal.created_by, self.superadmin)

    def test_non_superadmin_cannot_create_a_deal_record(self):
        self.client.credentials()
        login = self.client.post(
            "/api/v1/auth/login", {"email": self.owner.email, "password": "Password123!"}, format="json",
        )
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {login.data['access']}")

        response = self.client.post(
            "/api/v1/payments/admin/deal-records",
            {"company": self.company.id, "deal_type": DealRecord.ENTERPRISE, "slot_grant": 40, "unit_amount": "4000.00"},
            format="json",
        )

        self.assertEqual(response.status_code, 403)


class AdminAdjustBalanceTests(TestCase):
    def setUp(self):
        self.superadmin = User.objects.create_user(
            email="adjust-superadmin@example.com", password="Password123!",
            first_name="Adjust", last_name="Super", role=Roles.SUPERADMIN, is_verified=True,
        )
        self.owner = User.objects.create_user(
            email="adjust-owner@example.com", password="Password123!", first_name="Adjust", last_name="Owner",
            role=Roles.B2B, is_verified=True,
        )
        self.company = make_company(self.owner)
        self.balance = PackageBalance.objects.create(
            owner_company=self.company, balance_type=PackageBalance.SLOTS, fixed_amount=200, current_balance=30,
        )

    def test_admin_adjust_balance_credits_positive_delta(self):
        EntitlementService.admin_adjust_balance(balance=self.balance, delta=20, reason="Manual correction after billing-error refund", actor=self.superadmin)

        self.balance.refresh_from_db()
        self.assertEqual(self.balance.current_balance, 50)
        txn = BalanceTransaction.objects.get(balance=self.balance, transaction_type=BalanceTransaction.ADMIN_ADJUST)
        self.assertEqual(txn.amount, 20)
        self.assertEqual(txn.metadata, {"reason": "Manual correction after billing-error refund"})

    def test_admin_adjust_balance_debits_negative_delta(self):
        EntitlementService.admin_adjust_balance(balance=self.balance, delta=-10, reason="Correcting an over-grant", actor=self.superadmin)

        self.balance.refresh_from_db()
        self.assertEqual(self.balance.current_balance, 20)

    def test_admin_adjust_balance_rejects_negative_result(self):
        with self.assertRaises(ValueError):
            EntitlementService.admin_adjust_balance(balance=self.balance, delta=-999, reason="Too much", actor=self.superadmin)

        self.balance.refresh_from_db()
        self.assertEqual(self.balance.current_balance, 30)


class AdminPackageBalanceEndpointTests(APITestCase):
    def setUp(self):
        self.superadmin = User.objects.create_user(
            email="adjust-endpoint-superadmin@example.com", password="Password123!",
            first_name="Adjust", last_name="Endpoint", role=Roles.SUPERADMIN, is_verified=True, is_staff=True,
        )
        login = self.client.post(
            "/api/v1/auth/login", {"email": self.superadmin.email, "password": "Password123!"}, format="json",
        )
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {login.data['access']}")

        self.owner = User.objects.create_user(
            email="adjust-endpoint-owner@example.com", password="Password123!", first_name="Adjust", last_name="Company",
            role=Roles.B2B, is_verified=True,
        )
        self.company = make_company(self.owner)
        self.balance = PackageBalance.objects.create(
            owner_company=self.company, balance_type=PackageBalance.SLOTS, fixed_amount=200, current_balance=30,
        )

    def test_superadmin_can_adjust_a_balance(self):
        response = self.client.post(
            f"/api/v1/payments/admin/package-balances/{self.balance.id}/adjust",
            {"delta": 15, "reason": "Goodwill credit"},
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.balance.refresh_from_db()
        self.assertEqual(self.balance.current_balance, 45)

    def test_non_superadmin_cannot_adjust_a_balance(self):
        self.client.credentials()
        login = self.client.post(
            "/api/v1/auth/login", {"email": self.owner.email, "password": "Password123!"}, format="json",
        )
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {login.data['access']}")

        response = self.client.post(
            f"/api/v1/payments/admin/package-balances/{self.balance.id}/adjust",
            {"delta": 15, "reason": "Goodwill credit"},
            format="json",
        )

        self.assertEqual(response.status_code, 403)


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

    def test_issues_a_paid_invoice_for_the_purchase(self):
        # A bare PaymentIntent purchase never fires Stripe's
        # invoice.payment_succeeded (handle_invoice_paid), so without this
        # the B2C billing tab's Invoices table stays permanently empty for
        # one-time package purchases even though the model/PDF/API already
        # support it end-to-end.
        payment_intent = {"id": "pi_test_1", "metadata": {"price_id": str(self.price.id)}}

        self.service._grant_one_time_package(self.payment, payment_intent)

        invoice = Invoice.objects.get(stripe_payment_intent=self.payment)
        self.assertEqual(invoice.status, "PAID")
        self.assertEqual(invoice.amount_paid, Decimal("50.00"))
        self.assertEqual(invoice.amount_due, Decimal("50.00"))
        self.assertEqual(invoice.amount_remaining, Decimal("0.00"))
        self.assertEqual(invoice.user, self.user)
        self.assertEqual(invoice.customer, self.customer)
        self.assertTrue(invoice.number.startswith("INV-"))
        self.assertTrue(invoice.local_pdf_file)

    def test_invoice_issuance_is_idempotent_on_webhook_redelivery(self):
        payment_intent = {"id": "pi_test_1", "metadata": {"price_id": str(self.price.id)}}

        self.service._grant_one_time_package(self.payment, payment_intent)
        self.payment.refresh_from_db()
        self.service._grant_one_time_package(self.payment, payment_intent)

        self.assertEqual(Invoice.objects.filter(stripe_payment_intent=self.payment).count(), 1)

    def test_sends_a_payment_confirmation_email_with_amount_and_invoice_link(self):
        # A one-time purchase previously got no clear "payment received"
        # email at all - _notify_invoice_generated's "a new invoice has
        # been generated for your subscription" wording doesn't read as a
        # payment confirmation and is wrong for a one-time purchase anyway.
        mail.outbox = []
        payment_intent = {"id": "pi_test_1", "metadata": {"price_id": str(self.price.id)}}

        self.service._grant_one_time_package(self.payment, payment_intent)

        confirmation_emails = [m for m in mail.outbox if "payment confirmed" in m.subject.lower()]
        self.assertEqual(len(confirmation_emails), 1)
        email = confirmation_emails[0]
        self.assertEqual(email.to, [self.user.email])
        self.assertIn("50.00", email.body)
        self.assertIn(self.price.name, email.body)
        self.assertIn("/dashboard/indivisual/profile", email.body)


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
            EntitlementService.consume_slot_legacy(session)

    def test_b2c_consume_decrements_oldest_purchase_first(self):
        older = PackageBalance.objects.create(owner_user=self.b2c_user, balance_type=PackageBalance.SLOTS, fixed_amount=3, current_balance=1)
        PackageBalance.objects.create(owner_user=self.b2c_user, balance_type=PackageBalance.SLOTS, fixed_amount=20, current_balance=20)

        session = self._FakeSession(created_by=self.b2c_user)
        EntitlementService.consume_slot_legacy(session)

        older.refresh_from_db()
        self.assertEqual(older.current_balance, 0)
        newer = PackageBalance.objects.exclude(pk=older.pk).get(owner_user=self.b2c_user)
        self.assertEqual(newer.current_balance, 20)

    def test_b2c_consume_raises_once_all_balances_exhausted(self):
        PackageBalance.objects.create(owner_user=self.b2c_user, balance_type=PackageBalance.SLOTS, fixed_amount=1, current_balance=1)
        session = self._FakeSession(created_by=self.b2c_user)

        EntitlementService.consume_slot_legacy(session)
        with self.assertRaises(ValueError):
            EntitlementService.consume_slot_legacy(session)

    def test_b2b_consume_decrements_company_wide_balance(self):
        price = make_price(target_user_type="B2B", slot_grant=200, points_grant=2000)
        make_subscription(self.b2b_owner, price, company=self.company, status="ACTIVE")

        session = self._FakeSession(organization_id=self.company.id, organization=self.company)
        EntitlementService.consume_slot_legacy(session)

        balance = PackageBalance.objects.get(owner_company=self.company, balance_type=PackageBalance.SLOTS)
        self.assertEqual(balance.current_balance, 199)
        self.assertEqual(BalanceTransaction.objects.filter(balance=balance, transaction_type=BalanceTransaction.CONSUME).count(), 1)

    def test_b2b_consume_is_unrestricted_when_price_has_no_slot_grant(self):
        price = make_price(target_user_type="B2B", slot_grant=None, points_grant=None)
        make_subscription(self.b2b_owner, price, company=self.company, status="ACTIVE")

        session = self._FakeSession(organization_id=self.company.id, organization=self.company)
        EntitlementService.consume_slot_legacy(session)  # should not raise

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
            EntitlementService.consume_slot_legacy(session)

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
        EntitlementService.consume_slot_legacy(session)  # should not raise - grace period is still usable

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
            EntitlementService.consume_slot_legacy(session)

        StripeService().handle_subscription_updated({"id": subscription.stripe_subscription_id, "status": "active"})
        EntitlementService.consume_slot_legacy(session)  # should not raise now

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
        # A real B2C one-time purchase always gets a bookkeeping Subscription
        # row too (see _grant_one_time_package) - confirmed live this was
        # wrongly classified as B2B (subscription_id set) without this.
        one_time_price = make_price(target_user_type="B2C", billing_type="ONE_TIME", slot_grant=3, points_grant=50)
        one_time_subscription = make_subscription(self.b2c_user, one_time_price, status="ACTIVE")
        self.payment = Payment.objects.create(
            user=self.b2c_user, customer=self.customer, subscription=one_time_subscription, stripe_payment_intent_id="pi_refund_test",
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
        one_time_price = make_price(target_user_type="B2C", billing_type="ONE_TIME", slot_grant=3, points_grant=50)
        one_time_subscription = make_subscription(self.b2c_user, one_time_price, status="ACTIVE")
        self.payment = Payment.objects.create(
            user=self.b2c_user, customer=self.customer, subscription=one_time_subscription, stripe_payment_intent_id="pi_refund_service_test",
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


class RefundEntitlementBackingTests(TestCase):
    """Entitlement Backing (Slot Reservation Lifecycle spec, Section 7):
    a Reservation drawn from a refunded purchase can't remain valid -
    the session it backs must be cancelled and its Slot released as part
    of the refund itself, not left dangling."""

    def setUp(self):
        self.b2c_user = User.objects.create_user(
            email="refund-backing-b2c@example.com", password="Password123!", first_name="Backing", last_name="User",
            role=Roles.B2C, is_verified=True,
        )
        self.customer = Customer.objects.create(user=self.b2c_user, stripe_customer_id="cus_refund_backing", email=self.b2c_user.email)
        one_time_price = make_price(target_user_type="B2C", billing_type="ONE_TIME", slot_grant=3, points_grant=50)
        one_time_subscription = make_subscription(self.b2c_user, one_time_price, status="ACTIVE")
        self.payment = Payment.objects.create(
            user=self.b2c_user, customer=self.customer, subscription=one_time_subscription, stripe_payment_intent_id="pi_refund_backing_test",
            amount=Decimal("50.00"), status="SUCCEEDED",
        )
        self.balance = PackageBalance.objects.create(
            owner_user=self.b2c_user, balance_type=PackageBalance.SLOTS, source_payment=self.payment, fixed_amount=3, current_balance=3,
        )
        self.candidate = Candidate.objects.create(
            first_name="Backing", last_name="Candidate", email="refund-backing-candidate@example.com",
            passport_id="PASS-BACK-001", job_role="NA", core_skills="care", preferred_language="EN",
            passport_document=SimpleUploadedFile("passport.pdf", b"%PDF-1.1", content_type="application/pdf"),
            created_by=self.b2c_user,
        )
        self.config = InterviewConfiguration.objects.create(
            role_name="Nanny", role_code="nanny", language="EN", evaluation_tier=InterviewEvaluationTier.FULL,
            duration_minutes=30, total_questions=1, allow_retries=True, max_retries=1,
            rubric_version="v1", question_set_version="v1",
        )

    @patch("api.payments.refund_services.stripe")
    def test_refund_cancels_the_session_and_releases_the_reservation(self, mock_stripe):
        session = InterviewSessionService.create_session(candidate=self.candidate, config=self.config, created_by=self.b2c_user)
        reservation = SlotReservation.objects.get(session=session)
        mail.outbox = []

        RefundService.refund_payment(payment=self.payment, actor=self.b2c_user, override_reason_code=PLATFORM_ERROR)

        session.refresh_from_db()
        reservation.refresh_from_db()
        self.balance.refresh_from_db()
        self.assertEqual(session.status, InterviewSessionStatus.CANCELLED)
        self.assertEqual(reservation.status, SlotReservation.RELEASED)
        self.assertEqual(self.balance.current_balance, 0)  # released back to 3, then the whole refund zeroes it
        self.assertTrue(any("cancelled" in msg.subject.lower() for msg in mail.outbox))

    @patch("api.payments.refund_services.stripe")
    def test_refund_does_not_touch_an_already_started_session(self, mock_stripe):
        session = InterviewSessionService.create_session(candidate=self.candidate, config=self.config, created_by=self.b2c_user)
        InterviewSessionService.start_session(session, actor=self.b2c_user)
        reservation = SlotReservation.objects.get(session=session)
        self.balance.refresh_from_db()

        RefundService.refund_payment(payment=self.payment, actor=self.b2c_user, override_reason_code=PLATFORM_ERROR)

        session.refresh_from_db()
        reservation.refresh_from_db()
        self.assertEqual(session.status, InterviewSessionStatus.IN_PROGRESS)
        self.assertEqual(reservation.status, SlotReservation.CONSUMED)

    @patch("api.payments.refund_services.stripe")
    def test_refund_without_any_reservation_is_unaffected(self, mock_stripe):
        # No session was ever scheduled against this balance - the
        # ordinary revoke-only path must still work exactly as before.
        RefundService.refund_payment(payment=self.payment, actor=self.b2c_user)

        self.balance.refresh_from_db()
        self.assertEqual(self.balance.current_balance, 0)


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
        one_time_price = make_price(target_user_type="B2C", billing_type="ONE_TIME", slot_grant=3, points_grant=50)
        one_time_subscription = make_subscription(self.b2c_user, one_time_price, status="ACTIVE")
        self.payment = Payment.objects.create(
            user=self.b2c_user, customer=self.customer, subscription=one_time_subscription, stripe_payment_intent_id="pi_refund_webhook_test",
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
        one_time_price = make_price(target_user_type="B2C", billing_type="ONE_TIME", slot_grant=3, points_grant=50)
        one_time_subscription = make_subscription(self.b2c_user, one_time_price, status="ACTIVE")
        self.payment = Payment.objects.create(
            user=self.b2c_user, customer=self.customer, subscription=one_time_subscription, stripe_payment_intent_id="pi_refund_endpoint_test",
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


class AdminInvoiceEndpointTests(APITestCase):
    def setUp(self):
        self.superadmin = User.objects.create_user(
            email="invoice-endpoint-superadmin@example.com", password="Password123!",
            first_name="Invoice", last_name="Super", role=Roles.SUPERADMIN, is_verified=True, is_staff=True,
        )
        login = self.client.post(
            "/api/v1/auth/login", {"email": self.superadmin.email, "password": "Password123!"}, format="json",
        )
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {login.data['access']}")

        self.b2c_user = User.objects.create_user(
            email="invoice-endpoint-b2c@example.com", password="Password123!",
            first_name="Jamie", last_name="Customer", role=Roles.B2C, is_verified=True,
        )
        self.customer = Customer.objects.create(user=self.b2c_user, stripe_customer_id="cus_invoice_endpoint", email=self.b2c_user.email)
        self.invoice = Invoice.objects.create(
            user=self.b2c_user, customer=self.customer, stripe_invoice_id="in_endpoint_test",
            number="INV-ENDPOINT-1", status="PAID", amount_due=Decimal("79.00"),
            amount_paid=Decimal("79.00"), amount_remaining=Decimal("0.00"), currency="eur",
            invoice_pdf="https://stripe.example/inv.pdf", paid_at=timezone.now(),
        )

    def test_admin_can_list_all_invoices(self):
        response = self.client.get("/api/v1/payments/admin/invoices")
        self.assertEqual(response.status_code, 200, response.data)
        results = response.data
        self.assertEqual(len(results), 1)

    def test_admin_can_search_invoices_by_customer_email(self):
        response = self.client.get("/api/v1/payments/admin/invoices", {"search": "invoice-endpoint-b2c"})
        results = response.data
        self.assertEqual(len(results), 1)

        response = self.client.get("/api/v1/payments/admin/invoices", {"search": "no-such-customer"})
        results = response.data
        self.assertEqual(len(results), 0)

    @patch("api.accounts.utils.safe_send_mail")
    def test_admin_can_send_invoice_to_customer(self, mock_send_mail):
        mock_send_mail.return_value = 1

        response = self.client.post(f"/api/v1/payments/admin/invoices/{self.invoice.id}/send", {}, format="json")

        self.assertEqual(response.status_code, 200, response.data)
        mock_send_mail.assert_called_once()
        recipients = mock_send_mail.call_args[0][2]
        self.assertEqual(recipients, [self.b2c_user.email])

    def test_sending_an_invoice_with_no_pdf_link_is_rejected(self):
        bare_invoice = Invoice.objects.create(
            user=self.b2c_user, customer=self.customer, stripe_invoice_id="in_no_pdf",
            number="INV-ENDPOINT-2", status="OPEN", amount_due=Decimal("10.00"),
            amount_paid=Decimal("0.00"), amount_remaining=Decimal("10.00"), currency="eur",
        )

        response = self.client.post(f"/api/v1/payments/admin/invoices/{bare_invoice.id}/send", {}, format="json")

        self.assertEqual(response.status_code, 400)

    def test_non_admin_cannot_access_admin_invoice_list(self):
        login = self.client.post(
            "/api/v1/auth/login", {"email": self.b2c_user.email, "password": "Password123!"}, format="json",
        )
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {login.data['access']}")

        response = self.client.get("/api/v1/payments/admin/invoices")

        self.assertEqual(response.status_code, 403)


class ReconcileSlotReservationsCommandTests(TestCase):
    """Safety-net scan (Slot Reservation Lifecycle spec, Section 11) - only
    ever logs findings, never auto-corrects anything."""

    def setUp(self):
        self.user = User.objects.create_user(
            email="reconcile-owner@example.com", password="Password123!", first_name="Owner", last_name="User",
            role=Roles.B2C, is_verified=True,
        )
        self.candidate = Candidate.objects.create(
            first_name="Rec", last_name="Candidate", email="reconcile-candidate@example.com",
            passport_id="PASS-REC-001", job_role="NA", core_skills="care", preferred_language="EN",
            passport_document=SimpleUploadedFile("passport.pdf", b"%PDF-1.1", content_type="application/pdf"),
            created_by=self.user,
        )
        self.config = InterviewConfiguration.objects.create(
            role_name="Nanny", role_code="nanny", language="EN", evaluation_tier=InterviewEvaluationTier.FULL,
            duration_minutes=30, total_questions=1, allow_retries=True, max_retries=1,
            rubric_version="v1", question_set_version="v1",
        )
        PackageBalance.objects.create(owner_user=self.user, balance_type=PackageBalance.SLOTS, fixed_amount=5, current_balance=5)

    def _make_session(self, *, status):
        return InterviewSession.objects.create(
            candidate=self.candidate, organization=self.candidate.company, config=self.config,
            role_name=self.config.role_name, ui_language="EN", candidate_language="EN",
            tts_language_code="en-US", stt_language_code="en-US", total_questions=1,
            status=status, expires_at=InterviewSession.build_expiry(30), created_by=self.user,
        )

    def test_no_findings_on_a_healthy_reservation(self):
        InterviewSessionService.create_session(candidate=self.candidate, config=self.config, created_by=self.user)

        call_command("reconcile_slot_reservations")

        self.assertEqual(AuditLog.objects.filter(user_role="SYSTEM").count(), 0)

    def test_flags_an_active_session_with_no_reservation_at_all(self):
        # Give SlotReservation a real row to anchor the "since" cutoff to
        # (created before the target session below), otherwise the target
        # would be treated as pre-rollout and correctly skipped.
        SlotReservation.objects.create(owner_user=self.user, session=self._make_session(status=InterviewSessionStatus.CANCELLED), candidate=self.candidate)
        session = self._make_session(status=InterviewSessionStatus.READY)

        call_command("reconcile_slot_reservations")

        findings = AuditLog.objects.filter(action="RESERVATION_ANOMALY_DETECTED")
        self.assertTrue(any(f.data.get("session_id") == str(session.public_id) for f in findings))

    def test_flags_a_reservation_still_reserved_on_a_cancelled_session(self):
        session = self._make_session(status=InterviewSessionStatus.CANCELLED)
        reservation = SlotReservation.objects.create(owner_user=self.user, session=session, candidate=self.candidate)

        call_command("reconcile_slot_reservations")

        findings = AuditLog.objects.filter(action="RESERVATION_ANOMALY_DETECTED")
        self.assertTrue(any(f.data.get("reservation_id") == str(reservation.public_id) for f in findings))
        # Never auto-corrects.
        reservation.refresh_from_db()
        self.assertEqual(reservation.status, SlotReservation.RESERVED)

    def test_flags_a_consumed_reservation_whose_session_never_started(self):
        session = self._make_session(status=InterviewSessionStatus.READY)
        reservation = SlotReservation.objects.create(
            owner_user=self.user, session=session, candidate=self.candidate, status=SlotReservation.CONSUMED,
        )

        call_command("reconcile_slot_reservations")

        findings = AuditLog.objects.filter(action="RESERVATION_ANOMALY_DETECTED")
        self.assertTrue(any(f.data.get("reservation_id") == str(reservation.public_id) for f in findings))

    def test_does_not_flag_pre_rollout_sessions_with_no_reservation(self):
        # No SlotReservation exists anywhere yet -> "since" cutoff is None
        # -> finding #1 (missing reservation) must not fire at all for a
        # session that legitimately predates the rollout.
        self._make_session(status=InterviewSessionStatus.READY)

        call_command("reconcile_slot_reservations")

        self.assertEqual(AuditLog.objects.filter(action="RESERVATION_ANOMALY_DETECTED").count(), 0)


class SlotBalanceSummaryTests(TestCase):
    """get_balance_summary's SLOTS entry (Slot Reservation Lifecycle spec,
    Section 8): Available/Reserved/Consumed/Pending Sessions must be
    separately visible, not collapsed into one ambiguous "remaining"
    figure."""

    def setUp(self):
        self.b2c_user = User.objects.create_user(
            email="slot-summary-b2c@example.com", password="Password123!", first_name="B2C", last_name="User",
            role=Roles.B2C, is_verified=True,
        )
        self.candidate = Candidate.objects.create(
            first_name="Sum", last_name="Candidate", email="slot-summary-candidate@example.com",
            passport_id="PASS-SUM-001", job_role="NA", core_skills="care", preferred_language="EN",
            passport_document=SimpleUploadedFile("passport.pdf", b"%PDF-1.1", content_type="application/pdf"),
            created_by=self.b2c_user,
        )
        self.config = InterviewConfiguration.objects.create(
            role_name="Nanny", role_code="nanny", language="EN", evaluation_tier=InterviewEvaluationTier.FULL,
            duration_minutes=30, total_questions=1, allow_retries=True, max_retries=1,
            rubric_version="v1", question_set_version="v1",
        )

    def test_b2c_reports_available_reserved_consumed_and_pending(self):
        PackageBalance.objects.create(owner_user=self.b2c_user, balance_type=PackageBalance.SLOTS, fixed_amount=10, current_balance=10)

        reserved_session = InterviewSessionService.create_session(candidate=self.candidate, config=self.config, created_by=self.b2c_user)
        consumed_session = InterviewSessionService.create_session(candidate=self.candidate, config=self.config, created_by=self.b2c_user)
        InterviewSessionService.start_session(consumed_session, actor=self.b2c_user)

        summary = EntitlementService.get_balance_summary("USER", self.b2c_user)[PackageBalance.SLOTS]

        self.assertEqual(summary["remaining"], 8)  # 10 - 1 reserved - 1 consumed
        self.assertEqual(summary["limit"], 10)
        self.assertEqual(summary["reserved"], 1)
        self.assertEqual(summary["consumed"], 1)
        self.assertEqual(summary["pending_sessions"], 1)

    def test_b2b_reports_available_reserved_consumed_and_pending(self):
        b2b_owner = User.objects.create_user(
            email="slot-summary-b2b@example.com", password="Password123!", first_name="B2B", last_name="Owner",
            role=Roles.B2B, is_verified=True,
        )
        company = make_company(b2b_owner)
        price = make_price(target_user_type="B2B", slot_grant=20, points_grant=200)
        make_subscription(b2b_owner, price, company=company, status="ACTIVE")
        b2b_candidate = Candidate.objects.create(
            first_name="B2B", last_name="Candidate", email="slot-summary-b2b-candidate@example.com",
            passport_id="PASS-SUM-B2B-001", job_role="NA", core_skills="care", preferred_language="EN",
            passport_document=SimpleUploadedFile("passport.pdf", b"%PDF-1.1", content_type="application/pdf"),
            created_by=b2b_owner, company=company,
        )

        InterviewSessionService.create_session(candidate=b2b_candidate, config=self.config, created_by=b2b_owner)

        summary = EntitlementService.get_balance_summary("COMPANY", company)[PackageBalance.SLOTS]

        self.assertEqual(summary["remaining"], 19)
        self.assertEqual(summary["limit"], 20)
        self.assertEqual(summary["reserved"], 1)
        self.assertEqual(summary["consumed"], 0)
        self.assertEqual(summary["pending_sessions"], 1)

    def test_consumed_resets_to_zero_right_after_a_b2b_period_reset(self):
        b2b_owner = User.objects.create_user(
            email="slot-summary-reset@example.com", password="Password123!", first_name="B2B", last_name="Owner",
            role=Roles.B2B, is_verified=True,
        )
        company = make_company(b2b_owner)
        price = make_price(target_user_type="B2B", slot_grant=20, points_grant=200)
        subscription = make_subscription(b2b_owner, price, company=company, status="ACTIVE")
        b2b_candidate = Candidate.objects.create(
            first_name="B2B", last_name="Reset", email="slot-summary-reset-candidate@example.com",
            passport_id="PASS-SUM-RST-001", job_role="NA", core_skills="care", preferred_language="EN",
            passport_document=SimpleUploadedFile("passport.pdf", b"%PDF-1.1", content_type="application/pdf"),
            created_by=b2b_owner, company=company,
        )
        session = InterviewSessionService.create_session(candidate=b2b_candidate, config=self.config, created_by=b2b_owner)
        InterviewSessionService.start_session(session, actor=b2b_owner)

        before = EntitlementService.get_balance_summary("COMPANY", company)[PackageBalance.SLOTS]
        self.assertEqual(before["consumed"], 1)

        EntitlementService.reset_b2b_balances(subscription)

        after = EntitlementService.get_balance_summary("COMPANY", company)[PackageBalance.SLOTS]
        self.assertEqual(after["consumed"], 0)
        self.assertEqual(after["remaining"], 20)

    def test_consumed_never_goes_negative_after_an_admin_credit_above_the_original_grant(self):
        # admin_adjust_balance only ever moves current_balance, never
        # fixed_amount ("a stable record of what was actually granted") -
        # so a positive correction can legitimately push current_balance
        # above fixed_amount. The derived Consumed figure must clamp at 0
        # instead of going negative in that case.
        balance = PackageBalance.objects.create(
            owner_user=self.b2c_user, balance_type=PackageBalance.SLOTS, fixed_amount=3, current_balance=3,
        )
        admin = User.objects.create_user(
            email="slot-summary-admin@example.com", password="Password123!", first_name="Super", last_name="Admin",
            role=Roles.SUPERADMIN, is_verified=True,
        )
        EntitlementService.admin_adjust_balance(balance=balance, delta=20, reason="support grant", actor=admin)

        summary = EntitlementService.get_balance_summary("USER", self.b2c_user)[PackageBalance.SLOTS]

        self.assertEqual(summary["remaining"], 23)
        self.assertEqual(summary["limit"], 3)
        self.assertEqual(summary["consumed"], 0)


class SlotReservationNotificationTests(TestCase):
    """Reservation-failure and low-balance emails (Slot Reservation
    Lifecycle spec, Section 3, #3) - sent to both the Scheduler and the
    Account/Billing Owner, deduplicated to one email when they're the
    same person."""

    def setUp(self):
        self.config = InterviewConfiguration.objects.create(
            role_name="Nanny", role_code="nanny", language="EN", evaluation_tier=InterviewEvaluationTier.FULL,
            duration_minutes=30, total_questions=1, allow_retries=True, max_retries=1,
            rubric_version="v1", question_set_version="v1",
        )

    def test_b2b_reservation_failure_emails_both_scheduler_and_admin_owner_once_each(self):
        admin_owner = User.objects.create_user(
            email="notify-b2b-admin@example.com", password="Password123!", first_name="Admin", last_name="Owner",
            role=Roles.B2B, is_verified=True,
        )
        company = make_company(admin_owner)
        scheduler = User.objects.create_user(
            email="notify-b2b-scheduler@example.com", password="Password123!", first_name="Team", last_name="Member",
            role=Roles.B2B_TEAM_MEMBER, is_verified=True,
        )
        candidate = Candidate.objects.create(
            first_name="Notify", last_name="Candidate", email="notify-b2b-candidate@example.com",
            passport_id="PASS-NOTIFY-001", job_role="NA", core_skills="care", preferred_language="EN",
            passport_document=SimpleUploadedFile("passport.pdf", b"%PDF-1.1", content_type="application/pdf"),
            created_by=scheduler, company=company,
        )
        # No active subscription/balance at all for this company -> blocked.
        mail.outbox = []

        with self.assertRaises(ValueError):
            InterviewSessionService.create_session(candidate=candidate, config=self.config, created_by=scheduler)

        self.assertEqual(len(mail.outbox), 2)
        recipients = {msg.to[0] for msg in mail.outbox}
        self.assertEqual(recipients, {admin_owner.email, scheduler.email})

    def test_low_balance_threshold_is_at_least_one(self):
        from api.payments.notifications import low_balance_threshold
        self.assertEqual(low_balance_threshold(0), 0)
        self.assertEqual(low_balance_threshold(None), 0)
        self.assertEqual(low_balance_threshold(5), 1)  # round(5*0.1)=1, floor is 1
        self.assertEqual(low_balance_threshold(200), 20)


class InvoicePdfBillingPartyTests(TestCase):
    """_billing_party_context reads CompanyEmployerProfile/IndividualEmployerProfile
    directly off Invoice.user - never through CompanyEmployerProfile.company,
    a separate, optional, nullable verified-Company record."""

    def setUp(self):
        self.customer_kwargs = {"stripe_customer_id": "cus_billing_party_test"}

    def _invoice_for(self, user):
        customer = Customer.objects.create(user=user, **self.customer_kwargs)
        return Invoice.objects.create(
            user=user, customer=customer, stripe_invoice_id=f"in_billing_{user.id}",
            number=f"INV-BP-{user.id}", status="PAID", amount_due=Decimal("100.00"),
            amount_paid=Decimal("100.00"), amount_remaining=Decimal("0.00"), currency="eur",
        )

    def test_b2b_billing_party_uses_company_profile_not_company(self):
        from api.accounts.models import CompanyEmployerProfile
        from api.core.constants import CompanySize
        from api.payments.invoice_services import _billing_party_context

        user = User.objects.create_user(
            email="b2b-billing-party@example.com", password="Password123!",
            first_name="Jordan", last_name="Owner", role=Roles.B2B, is_verified=True,
        )
        CompanyEmployerProfile.objects.create(
            user=user, company_name="Acme Corp", company_registration_number="REG-BP-1",
            company_size=CompanySize.CHOICES[0][0], phone_number="+10000000000",
            country="United Arab Emirates", city="Dubai", address="Concord Tower",
        )
        invoice = self._invoice_for(user)

        billing_party = _billing_party_context(invoice)

        self.assertEqual(billing_party["name"], "Acme Corp")
        self.assertEqual(billing_party["address"], "Concord Tower, Dubai, United Arab Emirates")
        self.assertIsNone(billing_party["tax_id"])
        self.assertEqual(billing_party["email"], user.email)

    def test_b2c_billing_party_uses_individual_profile_address(self):
        from api.accounts.models import IndividualEmployerProfile
        from api.core.constants import JobRoles, Nationalities
        from api.payments.invoice_services import _billing_party_context

        user = User.objects.create_user(
            email="b2c-billing-party@example.com", password="Password123!",
            first_name="Sam", last_name="Customer", role=Roles.B2C, is_verified=True,
        )
        IndividualEmployerProfile.objects.create(
            user=user, passport_id="PASS-BP-1", phone_number="+10000000000",
            address="123 Main St, Springfield",
            job_role=JobRoles.CHOICES[0][0], nationality=Nationalities.CHOICES[0][0],
            id_document=SimpleUploadedFile("id.pdf", b"%PDF-1.1", content_type="application/pdf"),
            resume_document=SimpleUploadedFile("resume.pdf", b"%PDF-1.1", content_type="application/pdf"),
        )
        invoice = self._invoice_for(user)

        billing_party = _billing_party_context(invoice)

        self.assertEqual(billing_party["name"], user.get_full_name())
        self.assertEqual(billing_party["address"], "123 Main St, Springfield")

    def test_blank_address_falls_back_to_none(self):
        from api.payments.invoice_services import _billing_party_context

        user = User.objects.create_user(
            email="no-profile-billing-party@example.com", password="Password123!",
            first_name="No", last_name="Profile", role=Roles.B2C, is_verified=True,
        )
        invoice = self._invoice_for(user)

        billing_party = _billing_party_context(invoice)

        self.assertEqual(billing_party["name"], user.get_full_name())
        self.assertIsNone(billing_party["address"])
        self.assertIsNone(billing_party["tax_id"])


class InvoiceAddressTranslationTests(TestCase):
    """The billing address (city/country/street names - descriptive text,
    not an identity) gets translated to Arabic on an Arabic invoice, same
    non-blocking contract as every other TranslationService call in this
    codebase: never raises, falls back to the original text on any
    failure. The company/individual NAME never gets translated - same
    treatment as "MeritLense" itself, which stays untranslated on every
    Arabic document in the app."""

    def setUp(self):
        self.user = User.objects.create_user(
            email="address-translation@example.com", password="Password123!",
            first_name="Addr", last_name="Test", role=Roles.B2C, is_verified=True,
        )
        customer = Customer.objects.create(user=self.user, stripe_customer_id="cus_addr_translation")
        self.invoice = Invoice.objects.create(
            user=self.user, customer=customer, stripe_invoice_id="in_addr_translation",
            number="INV-ADDR-1", status="PAID", amount_due=Decimal("100.00"),
            amount_paid=Decimal("100.00"), amount_remaining=Decimal("0.00"), currency="eur",
        )

    @patch("api.translation.services.TranslationService.translate")
    def test_address_is_translated_on_arabic_invoice(self, mock_translate):
        from api.accounts.models import IndividualEmployerProfile
        from api.core.constants import JobRoles, Nationalities
        from api.payments.invoice_services import _build_snapshot

        IndividualEmployerProfile.objects.create(
            user=self.user, passport_id="PASS-ADDR-1", phone_number="+10000000000",
            address="Concord Tower, Dubai, United Arab Emirates",
            job_role=JobRoles.CHOICES[0][0], nationality=Nationalities.CHOICES[0][0],
            preferred_language="AR",
            id_document=SimpleUploadedFile("id.pdf", b"%PDF-1.1", content_type="application/pdf"),
            resume_document=SimpleUploadedFile("resume.pdf", b"%PDF-1.1", content_type="application/pdf"),
        )
        mock_translate.return_value = {"translated_text": "برج كونكورد، دبي، الإمارات العربية المتحدة", "provider": "GOOGLE"}

        snapshot = _build_snapshot(self.invoice)

        mock_translate.assert_called_once_with(
            text="Concord Tower, Dubai, United Arab Emirates", source_language="en", target_language="ar",
        )
        self.assertEqual(snapshot["billing_party"]["address"], "برج كونكورد، دبي، الإمارات العربية المتحدة")
        self.assertFalse(snapshot["billing_party"]["address_is_latin"])

    @patch("api.translation.services.TranslationService.translate")
    def test_translation_failure_falls_back_to_original_address(self, mock_translate):
        from api.accounts.models import IndividualEmployerProfile
        from api.core.constants import JobRoles, Nationalities
        from api.payments.invoice_services import _build_snapshot

        IndividualEmployerProfile.objects.create(
            user=self.user, passport_id="PASS-ADDR-2", phone_number="+10000000000",
            address="Concord Tower, Dubai, United Arab Emirates",
            job_role=JobRoles.CHOICES[0][0], nationality=Nationalities.CHOICES[0][0],
            preferred_language="AR",
            id_document=SimpleUploadedFile("id.pdf", b"%PDF-1.1", content_type="application/pdf"),
            resume_document=SimpleUploadedFile("resume.pdf", b"%PDF-1.1", content_type="application/pdf"),
        )
        mock_translate.side_effect = RuntimeError("provider unavailable")

        snapshot = _build_snapshot(self.invoice)

        self.assertEqual(snapshot["billing_party"]["address"], "Concord Tower, Dubai, United Arab Emirates")
        self.assertTrue(snapshot["billing_party"]["address_is_latin"])

    @patch("api.translation.services.TranslationService.translate")
    def test_english_invoice_never_calls_translate(self, mock_translate):
        from api.accounts.models import IndividualEmployerProfile
        from api.core.constants import JobRoles, Nationalities
        from api.payments.invoice_services import _build_snapshot

        IndividualEmployerProfile.objects.create(
            user=self.user, passport_id="PASS-ADDR-3", phone_number="+10000000000",
            address="Concord Tower, Dubai, United Arab Emirates",
            job_role=JobRoles.CHOICES[0][0], nationality=Nationalities.CHOICES[0][0],
            preferred_language="EN",
            id_document=SimpleUploadedFile("id.pdf", b"%PDF-1.1", content_type="application/pdf"),
            resume_document=SimpleUploadedFile("resume.pdf", b"%PDF-1.1", content_type="application/pdf"),
        )

        snapshot = _build_snapshot(self.invoice)

        mock_translate.assert_not_called()
        self.assertEqual(snapshot["billing_party"]["address"], "Concord Tower, Dubai, United Arab Emirates")


class GenerateInvoicePdfsCommandTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="invoice-pdf-command@example.com", password="Password123!",
            first_name="Cmd", last_name="Test", role=Roles.B2C, is_verified=True,
        )
        self.customer = Customer.objects.create(user=self.user, stripe_customer_id="cus_command_test")
        self.invoice = Invoice.objects.create(
            user=self.user, customer=self.customer, stripe_invoice_id="in_command_test",
            number="INV-CMD-1", status="PAID", amount_due=Decimal("50.00"),
            amount_paid=Decimal("50.00"), amount_remaining=Decimal("0.00"), currency="eur",
        )

    def test_dry_run_does_not_write(self):
        call_command("generate_invoice_pdfs", "in_command_test", "--dry-run")

        self.invoice.refresh_from_db()
        self.assertFalse(self.invoice.local_pdf_file)

    def test_generates_pdf_for_named_invoice(self):
        call_command("generate_invoice_pdfs", "in_command_test")

        self.invoice.refresh_from_db()
        self.assertTrue(self.invoice.local_pdf_file)
        self.assertTrue(self.invoice.pdf_hash)
        self.assertEqual(self.invoice.pdf_render_snapshot["invoice_number"], "INV-CMD-1")

    def test_skips_already_generated_without_force(self):
        call_command("generate_invoice_pdfs", "in_command_test")
        self.invoice.refresh_from_db()
        first_hash = self.invoice.pdf_hash

        self.invoice.amount_due = Decimal("999.00")
        self.invoice.save(update_fields=["amount_due"])
        call_command("generate_invoice_pdfs", "in_command_test")

        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.pdf_hash, first_hash)

    def test_force_regenerates(self):
        call_command("generate_invoice_pdfs", "in_command_test")
        self.invoice.refresh_from_db()

        self.invoice.amount_due = Decimal("999.00")
        self.invoice.amount_remaining = Decimal("999.00")
        self.invoice.save(update_fields=["amount_due", "amount_remaining"])
        call_command("generate_invoice_pdfs", "in_command_test", "--force")

        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.pdf_render_snapshot["amount_due_display"], "999.00")

    def test_all_missing_only_targets_invoices_without_a_local_pdf(self):
        already_done = Invoice.objects.create(
            user=self.user, customer=self.customer, stripe_invoice_id="in_command_already_done",
            number="INV-CMD-2", status="PAID", amount_due=Decimal("10.00"),
            amount_paid=Decimal("10.00"), amount_remaining=Decimal("0.00"), currency="eur",
        )
        call_command("generate_invoice_pdfs", "in_command_already_done")

        call_command("generate_invoice_pdfs", "--all-missing")

        self.invoice.refresh_from_db()
        self.assertTrue(self.invoice.local_pdf_file)
