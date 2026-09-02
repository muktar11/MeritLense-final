from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient, APITestCase

from api.accounts.models import Company, User
from api.core.constants import Roles
from api.payments.entitlement_services import ADDON_POINTS_CATALOG, EntitlementService
from api.payments.models import BalanceTransaction, Customer, Invoice, PackageBalance, Payment, Price, Subscription
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

    def test_spend_points_deducts_addon_cost(self):
        PackageBalance.objects.create(owner_user=self.b2c_user, balance_type=PackageBalance.POINTS, fixed_amount=50, current_balance=50)

        balance = EntitlementService.spend_points(user=self.b2c_user, addon_code="practical_simulation_test")

        self.assertEqual(balance.current_balance, 50 - ADDON_POINTS_CATALOG["practical_simulation_test"])

    def test_spend_points_rejects_unknown_addon_code(self):
        with self.assertRaises(ValueError):
            EntitlementService.spend_points(user=self.b2c_user, addon_code="not_a_real_addon")


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

        response = self.client.post(
            f"/api/v1/payments/subscriptions/{self.subscription.public_id}/change_plan",
            {"price_id": str(self.business.id), "prorate": True},
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.data)
        mock_stripe.Invoice.create.assert_called_once()
        balance = PackageBalance.objects.get(owner_company=self.company, balance_type=PackageBalance.SLOTS)
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
