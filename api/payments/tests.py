from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from api.accounts.models import User
from api.core.constants import Roles
from api.payments.models import Customer, Invoice, Payment, Price, Subscription
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
