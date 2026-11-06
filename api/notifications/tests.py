from django.conf import settings
from django.core import mail
from django.test import override_settings
from rest_framework.test import APITestCase

from api.accounts.models import User
from api.accounts.utils import safe_send_mail
from api.core.constants import Roles
from api.notifications.models import Notification
from api.notifications.actions import ACTION_HEADER, resolve_action
from api.notifications.services import IN_APP_HEADER

# The production email path (branding + short links + in-app copy) on top
# of the in-memory test backend.
REAL_EMAIL_PATH = override_settings(
    EMAIL_HOST="smtp.test",
    EMAIL_BACKEND="api.core.email_links.ShortLinkEmailBackend",
    EMAIL_INNER_BACKEND="django.core.mail.backends.locmem.EmailBackend",
)


@REAL_EMAIL_PATH
class EmailToInAppNotificationTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="Notify.Me@example.com", password="Password123!",
            first_name="Nora", last_name="Notify", role=Roles.B2C, is_verified=True,
        )

    def test_email_to_user_creates_notification(self):
        safe_send_mail("Evaluation Scheduled: Full", "\n        Hello Nora,\n\n        Your evaluation is booked.\n        ",
                       ["notify.me@example.com"])
        self.assertEqual(len(mail.outbox), 1)
        n = Notification.objects.get(user=self.user)
        self.assertEqual(n.title, "Evaluation Scheduled: Full")
        self.assertEqual(n.body, "Hello Nora,\n\nYour evaluation is booked.")
        self.assertIsNone(n.read_at)

    def test_plain_send_mail_also_creates_notification(self):
        from django.core.mail import send_mail
        send_mail("Low Assessment Slot balance", "Top up soon.", settings.DEFAULT_FROM_EMAIL, [self.user.email])
        self.assertTrue(Notification.objects.filter(user=self.user, title="Low Assessment Slot balance").exists())

    def test_security_email_stays_out_of_app(self):
        safe_send_mail("Your MeritLense signing code", "Your code is 123456", [self.user.email], in_app=False)
        self.assertEqual(len(mail.outbox), 1)
        self.assertFalse(Notification.objects.exists())
        self.assertNotIn(IN_APP_HEADER, mail.outbox[0].extra_headers)

    def test_signing_code_and_reset_emails_stay_out_of_app(self):
        from api.accounts.utils import send_password_reset_email, send_verification_email
        from api.contracts.otp_service import OTPService
        self.user.email_verification_code = "12345"
        send_verification_email(self.user)
        send_password_reset_email(self.user, None)
        OTPService().send(self.user.email, "654321")
        self.assertEqual(len(mail.outbox), 3)
        self.assertFalse(Notification.objects.exists())

    def test_non_user_recipient_gets_no_notification(self):
        safe_send_mail("Your interview link", "Join here", ["candidate@example.com"])
        self.assertFalse(Notification.objects.exists())


@REAL_EMAIL_PATH
@override_settings(FRONTEND_URL="https://meritlense.com")
class NotificationTaskLinkTests(APITestCase):
    """The "Go to task" button: the page each notification leads to."""

    def setUp(self):
        self.b2c = User.objects.create_user(
            email="b2c-task@example.com", password="Password123!",
            first_name="Bea", last_name="Cee", role=Roles.B2C, is_verified=True,
        )
        self.b2b = User.objects.create_user(
            email="b2b-task@example.com", password="Password123!",
            first_name="Bob", last_name="Bee", role=Roles.B2B, is_verified=True,
        )

    def _action(self, user):
        return Notification.objects.get(user=user).action_url

    def test_named_page_resolves_per_account_type(self):
        safe_send_mail("Your invoice", "Thanks.", [self.b2c.email, self.b2b.email], action="billing")
        self.assertEqual(self._action(self.b2c), "/dashboard/indivisual/payment?tab=billing")
        self.assertEqual(self._action(self.b2b), "/dashboard/business/payment?tab=billing")
        self.assertNotIn(ACTION_HEADER, mail.outbox[0].extra_headers)

    def test_exact_dashboard_path_is_kept(self):
        safe_send_mail("Interview scheduled", "Join soon.", [self.b2b.email],
                       action="/dashboard/business/live-call?sessionId=abc-123")
        self.assertEqual(self._action(self.b2b), "/dashboard/business/live-call?sessionId=abc-123")

    def test_paths_outside_the_dashboard_are_refused(self):
        for bad in ("https://evil.example/phish", "//evil.example", "/auth/reset-password?token=x", "nonsense"):
            self.assertEqual(resolve_action(bad, self.b2c), "", bad)

    def test_falls_back_to_a_dashboard_link_in_the_email(self):
        safe_send_mail("Package", "See https://meritlense.com/ar/dashboard/indivisual/payment?tab=billing.",
                       [self.b2c.email])
        self.assertEqual(self._action(self.b2c), "/dashboard/indivisual/payment?tab=billing")

    def test_login_link_leads_to_the_dashboard(self):
        safe_send_mail("Welcome", "Sign in at https://meritlense.com/en/auth/login", [self.b2b.email])
        self.assertEqual(self._action(self.b2b), "/dashboard/business")

    def test_other_sites_and_no_link_give_no_button(self):
        safe_send_mail("Hello", "Pay at https://checkout.stripe.com/c/pay/abc", [self.b2c.email])
        self.assertEqual(self._action(self.b2c), "")

    def test_low_slot_warning_leads_to_plans(self):
        from api.payments.notifications import maybe_send_low_balance_warning
        maybe_send_low_balance_warning(created_by=self.b2c, company=None, remaining=1, limit=10)
        self.assertEqual(self._action(self.b2c), "/dashboard/indivisual/payment")

    def test_document_request_leads_to_profile(self):
        from api.accounts.utils import send_document_request_email
        send_document_request_email(self.b2b, "Trade License")
        self.assertEqual(self._action(self.b2b), "/dashboard/business/company-profile")

    def test_api_returns_the_link(self):
        Notification.objects.create(user=self.b2c, title="t", body="b", action_url="/dashboard/indivisual/evaluations")
        Notification.objects.create(user=self.b2c, title="u", body="b")
        self.client.force_authenticate(self.b2c)
        results = self.client.get("/api/v1/notifications").data["results"]
        self.assertEqual({n["title"]: n["action_url"] for n in results},
                         {"t": "/dashboard/indivisual/evaluations", "u": None})


class NotificationApiTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="api-notify@example.com", password="Password123!",
            first_name="Api", last_name="User", role=Roles.B2C, is_verified=True,
        )
        self.other = User.objects.create_user(
            email="other-notify@example.com", password="Password123!",
            first_name="Other", last_name="User", role=Roles.B2C, is_verified=True,
        )
        self.first = Notification.objects.create(user=self.user, title="First", body="a")
        self.second = Notification.objects.create(user=self.user, title="Second", body="b")
        self.foreign = Notification.objects.create(user=self.other, title="Not yours", body="c")
        self.client.force_authenticate(self.user)

    def test_list_shows_only_own_newest_first_with_unread_count(self):
        response = self.client.get("/api/v1/notifications")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["unread_count"], 2)
        self.assertEqual([n["title"] for n in response.data["results"]], ["Second", "First"])

    def test_mark_one_and_all_read(self):
        response = self.client.post(f"/api/v1/notifications/{self.first.public_id}/read")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["read"])
        self.assertEqual(self.client.get("/api/v1/notifications").data["unread_count"], 1)

        self.client.post("/api/v1/notifications/read-all")
        self.assertEqual(self.client.get("/api/v1/notifications").data["unread_count"], 0)
        self.foreign.refresh_from_db()
        self.assertIsNone(self.foreign.read_at)

    def test_cannot_touch_someone_elses_notification(self):
        response = self.client.post(f"/api/v1/notifications/{self.foreign.public_id}/read")
        self.assertEqual(response.status_code, 404)

    def test_requires_login(self):
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get("/api/v1/notifications").status_code, 401)
