"""Every outgoing email carries short links instead of long URLs."""
from unittest import mock

from django.core import mail
from django.core.mail import EmailMultiAlternatives, send_mail
from django.test import TestCase, override_settings

from api.payments.models import PaymentShortLink

BASE = "https://api.meritlense.com"
RESET = "https://meritlense.com/en/reset-password?token=abc123def456ghi789&uid=42"


@override_settings(EMAIL_BACKEND="api.core.email_links.ShortLinkEmailBackend",
                   EMAIL_INNER_BACKEND="django.core.mail.backends.locmem.EmailBackend",
                   SHORT_LINK_BASE_URL=BASE, FRONTEND_URL="https://meritlense.com")
class ShortLinkEmailTests(TestCase):
    def test_plain_text_links_become_short_links_that_redirect(self):
        send_mail("Reset", f"Open {RESET}. Thanks", "noreply@meritlense.com", ["a@example.com"])
        link = PaymentShortLink.objects.get(target_url=RESET)
        self.assertEqual(mail.outbox[0].body, f"Open {BASE}/l/{link.code}. Thanks")
        response = self.client.get(f"/l/{link.code}")
        self.assertEqual((response.status_code, response["Location"]), (302, RESET))
        link.refresh_from_db()
        self.assertEqual(link.click_count, 1)

    def test_other_sites_short_and_already_short_links_are_left_alone(self):
        body = ("See https://example.com/a/very/long/path/that/is/not/ours?x=1 and https://meritlense.com "
                f"and {BASE}/p/abcdefgh")
        send_mail("Info", body, "noreply@meritlense.com", ["a@example.com"])
        self.assertEqual(mail.outbox[0].body, body)
        self.assertFalse(PaymentShortLink.objects.exists())

    def test_html_links_and_text_are_shortened_but_images_are_not(self):
        logo = "https://api.meritlense.com/static/brand/meritlense-logo-full-lockup.png"
        escaped = RESET.replace("&", "&amp;")
        message = EmailMultiAlternatives("Reset", f"Open {RESET}", "noreply@meritlense.com", ["a@example.com"])
        message.attach_alternative(f'<img src="{logo}"><a href="{escaped}">{escaped}</a>', "text/html")
        message.send()

        code = PaymentShortLink.objects.get(target_url=RESET).code
        sent = mail.outbox[0]
        html = sent.alternatives[0][0]
        self.assertIn(f'<img src="{logo}">', html)
        self.assertIn(f'<a href="{BASE}/l/{code}">{BASE}/l/{code}</a>', html)
        self.assertEqual(sent.body, f"Open {BASE}/l/{code}")
        self.assertEqual(PaymentShortLink.objects.count(), 1)  # same URL, one short link

    def test_the_same_link_is_reused_across_emails(self):
        for _ in range(2):
            send_mail("Reset", RESET, "noreply@meritlense.com", ["a@example.com"])
        self.assertEqual(mail.outbox[0].body, mail.outbox[1].body)
        self.assertEqual(PaymentShortLink.objects.count(), 1)

    def test_a_shortening_failure_never_blocks_the_email(self):
        with mock.patch("api.payments.short_links.short_url", side_effect=RuntimeError("db down")):
            send_mail("Reset", f"Open {RESET}", "noreply@meritlense.com", ["a@example.com"])
        self.assertEqual(mail.outbox[0].body, f"Open {RESET}")
