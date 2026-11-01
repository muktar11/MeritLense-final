from django.conf import settings
from django.core import mail
from django.test import override_settings
from rest_framework.test import APITestCase

from api.accounts.models import User
from api.accounts.utils import safe_send_mail
from api.core.constants import Roles
from api.notifications.models import Notification
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
