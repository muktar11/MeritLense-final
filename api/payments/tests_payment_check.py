"""The billing tab's "Check payment" (debug-payment), 16 Oct 2026."""
from types import SimpleNamespace
from unittest.mock import patch

from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient
from django.test import TestCase

from api.accounts.models import User
from api.core.constants import Roles
from api.payments.models import Customer, Subscription
from api.payments.tests import make_price


class PaymentCheckTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="check-payment@example.com", password="Password123!", first_name="C", last_name="P",
            role=Roles.B2C, is_verified=True,
        )
        self.customer = Customer.objects.create(user=self.user, stripe_customer_id="cus_check", email=self.user.email)
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def subscription(self, stripe_id):
        return Subscription.objects.create(
            user=self.user, customer=self.customer, stripe_price=make_price(), status="ACTIVE",
            stripe_subscription_id=stripe_id, current_period_start=timezone.now(),
            current_period_end=timezone.now() + timezone.timedelta(days=30),
        )

    @patch("api.payments.views.stripe.PaymentIntent.retrieve")
    @patch("api.payments.views.stripe.Subscription.retrieve")
    def test_one_time_purchase_is_reported_by_its_payment(self, sub_retrieve, intent_retrieve):
        sub = self.subscription("one_time_pi_123")
        intent_retrieve.return_value = SimpleNamespace(status="succeeded", last_payment_error=None)
        response = self.client.get(reverse("debug-payment"))
        self.assertEqual(response.status_code, 200)
        row = response.json()[0]
        self.assertEqual(row["subscription_id"], str(sub.public_id))
        self.assertEqual(row["invoice_status"], "paid")
        self.assertEqual(row["payment_intent_status"], "succeeded")
        self.assertNotIn("check_failed", row)
        intent_retrieve.assert_called_once_with("pi_123")
        sub_retrieve.assert_not_called()

    @patch("api.payments.views.stripe.Subscription.retrieve", side_effect=Exception("No such subscription"))
    def test_a_stripe_error_marks_only_that_subscription(self, _):
        self.subscription("sub_missing")
        response = self.client.get(reverse("debug-payment"))
        self.assertEqual(response.status_code, 200)
        row = response.json()[0]
        self.assertTrue(row["from_records"])
        self.assertEqual(row["stripe_status"], "active")

    @patch("api.payments.views.stripe.PaymentIntent.retrieve", side_effect=Exception("No such payment_intent"))
    def test_one_time_purchase_unknown_to_stripe_reported_from_records(self, _):
        self.subscription("one_time_pi_gone")
        row = self.client.get(reverse("debug-payment")).json()[0]
        self.assertTrue(row["from_records"])
        self.assertNotIn("check_failed", row)

    @patch("api.payments.views.stripe.Subscription.retrieve")
    def test_package_request_purchase_reported_from_records(self, sub_retrieve):
        self.subscription("package_request_abc")
        row = self.client.get(reverse("debug-payment")).json()[0]
        self.assertTrue(row["from_records"])
        sub_retrieve.assert_not_called()
