"""Every customer email goes out in the shared MeritLense layout."""
from types import SimpleNamespace as NS
from unittest import mock

from django.core import mail
from django.core.mail import send_mail
from django.test import TestCase, override_settings

from api.accounts.utils import send_document_request_email, send_verification_email
from api.contracts.otp_service import OTPService
from api.core.constants import Roles
from api.core.email_template import CONFIDENTIALITY_NOTICE, LOGO_URL
from api.payments.short_links import email_button_html


def _html(message):
    return next(alt[0] for alt in message.alternatives if alt[1] == "text/html")


@override_settings(EMAIL_HOST="localhost", EMAIL_BACKEND="api.core.email_links.ShortLinkEmailBackend",
                   EMAIL_INNER_BACKEND="django.core.mail.backends.locmem.EmailBackend",
                   SHORT_LINK_BASE_URL="https://api.meritlense.com", FRONTEND_URL="https://meritlense.com")
class EmailTemplateTests(TestCase):
    def test_plain_text_email_gets_the_branded_html_and_keeps_its_text(self):
        body = ("Hello Sara,\n\nYour evaluation has been scheduled.\n\nEvaluation Details:\n- Type: Interview\n"
                "- Duration: 45 minutes\n\nStart your assessment here:\nhttps://meritlense.com/en/interview?x=1\n\n"
                "Best regards,\nMeritLense Team")
        send_mail("Evaluation Scheduled: Interview", body, "noreply@meritlense.com", ["sara@example.com"])

        message = mail.outbox[0]
        html = _html(message)
        self.assertIn(f'src="{LOGO_URL}"', html)
        self.assertIn("Assessment update", html)
        self.assertIn(">Evaluation Scheduled: Interview</h1>", html)
        self.assertIn("Hello Sara,", html)
        self.assertIn(">Duration</td>", html)
        self.assertIn(">45 minutes</td>", html)
        self.assertIn(">Start assessment</a>", html)
        self.assertIn("info@meritlense.com", html)
        self.assertEqual(html.count("Best regards,"), 1)  # the layout's own signature only
        self.assertIn("Start your assessment here:", message.body)  # the text version is unchanged

    def test_arabic_email_is_right_to_left_without_letter_spacing(self):
        send_mail("تم إلغاء التقييم: مقابلة", "مرحبًا سارة،\n\nتم إلغاء التقييم المحدد لك.\n\nمع أطيب التحيات،\nفريق MeritLense",
                  "noreply@meritlense.com", ["sara@example.com"])

        html = _html(mail.outbox[0])
        self.assertIn('<html lang="ar" dir="rtl">', html)
        self.assertIn("تحديث التقييم", html)
        self.assertNotIn("letter-spacing:1.5px", html)  # spacing breaks Arabic letter joins
        self.assertEqual(html.count("مع أطيب التحيات"), 1)

    def test_a_meeting_link_row_becomes_the_join_button(self):
        send_mail("Evaluation Scheduled", "Hello Sara,\n\n- Type: Interview\n\nMeeting Link: https://meet.google.com/abc",
                  "noreply@meritlense.com", ["sara@example.com"])

        html = _html(mail.outbox[0])
        self.assertIn(">Join meeting</a>", html)
        self.assertNotIn(">Meeting Link</td>", html)

    def test_sentences_with_a_colon_stay_text_and_a_lead_in_link_becomes_the_button(self):
        send_mail("Evaluation Completed", "Hello Sara,\n\nGood news: your request was approved.\n\n"
                  "Certificate Status: Issued\nYou can download your certificate here: https://meritlense.com/en/c/1",
                  "noreply@meritlense.com", ["sara@example.com"])

        html = _html(mail.outbox[0])
        self.assertIn("Good news: your request was approved.</p>", html)
        self.assertIn(">Certificate Status</td>", html)
        self.assertIn(">Download certificate</a>", html)

    def test_verification_code_is_plain_selectable_text_with_its_expiry(self):
        user = NS(first_name="Ahmed", email="ahmed@example.com", role=Roles.B2C, email_verification_code="38193",
                  individual_profile=NS(preferred_language="en"))
        send_verification_email(user)

        message = mail.outbox[0]
        html = _html(message)
        self.assertIn(">38193</p>", html)
        self.assertIn("user-select:all", html)
        self.assertNotIn("<button", html)
        self.assertIn("Expires in 24 hours.", html)
        self.assertIn("Do not share this code.", html)
        self.assertIn(">Verify your email</h1>", html)
        self.assertIn("Your verification code is: 38193", message.body)

    @override_settings(AGREEMENT_OTP_VALIDITY_MINUTES=10)
    def test_signing_code_email_states_the_configured_expiry(self):
        self.assertTrue(OTPService().send("ahmed@example.com", "481920", language="ar"))

        html = _html(mail.outbox[0])
        self.assertIn(">481920</p>", html)
        self.assertIn("خلال 10 دقيقة", html)
        self.assertIn('dir="rtl"', html)

    def test_button_emails_take_the_subject_as_title_and_drop_their_own_sign_off(self):
        html_message = email_button_html(greeting="Hello Ahmed,", paragraphs=["Package: Essential · Amount: 120 EUR"],
                                         button_label="View invoice", button_url="https://meritlense.com/en/x",
                                         footer_lines=["Best regards,", "MeritLense Team"])
        from api.accounts.utils import safe_send_mail
        safe_send_mail("Your MeritLense invoice INV-1", "Hello Ahmed", ["ahmed@example.com"], html_message=html_message)

        html = _html(mail.outbox[0])
        self.assertIn(">Your MeritLense invoice INV-1</h1>", html)
        self.assertIn("Billing &amp; payments", html)
        self.assertIn(">Essential</td>", html)
        self.assertEqual(html.count("Best regards,"), 1)
        self.assertNotIn("<!--ml:", html)

    def test_confidentiality_notice_is_hidden_until_set_then_inserted_verbatim(self):
        send_mail("Info", "Hello,\n\nText.", "noreply@meritlense.com", ["a@example.com"])
        self.assertNotIn("Confidentiality notice", _html(mail.outbox[0]))

        notice = "This email is confidential & intended only for the addressee."
        with mock.patch.dict(CONFIDENTIALITY_NOTICE, {"en": notice}):
            send_mail("Info", "Hello,\n\nText.", "noreply@meritlense.com", ["a@example.com"])
        html = _html(mail.outbox[1])
        self.assertIn("Confidentiality notice", html)
        self.assertIn("This email is confidential &amp; intended only for the addressee.", html)

    def test_document_request_email_asks_to_upload_under_account_documents(self):
        for lang, eyebrow, title, line in (
            ("en", "Account documents", "Please upload Commercial registration certificate",
             "Please upload Commercial registration certificate for Gulf Hospitality LLC through the Required "
             "Documents section of your profile. Thank you for your cooperation."),
            ("ar", "مستندات الحساب", "يرجى رفع Commercial registration certificate",
             "يرجى رفع Commercial registration certificate الخاص بـ Gulf Hospitality LLC من خلال قسم المستندات "
             "المطلوبة في ملفك الشخصي. شكرًا لتعاونك."),
        ):
            mail.outbox = []
            user = NS(first_name="Ahmed", email="ahmed@example.com",
                      company_profile=NS(company_name="Gulf Hospitality LLC", preferred_language=lang))
            send_document_request_email(user, "Commercial registration certificate")

            message = mail.outbox[0]
            html = _html(message)
            self.assertEqual(message.subject, title)
            self.assertIn(f">{eyebrow}</p>", html)
            self.assertIn(f">{title}</h1>", html)
            self.assertIn(line, html)
            self.assertIn(line, message.body)
            self.assertNotIn("Results", html)
            self.assertNotIn("النتائج", html)

    def test_a_document_named_certificate_is_not_filed_under_results(self):
        send_mail("Additional document requested: Training certificate", "Hello,\n\nPlease upload it.",
                  "noreply@meritlense.com", ["a@example.com"])
        self.assertIn(">Account documents</p>", _html(mail.outbox[0]))

    def test_a_branding_failure_never_blocks_the_email(self):
        with mock.patch("api.core.email_template.render_text_email", side_effect=RuntimeError("boom")):
            send_mail("Info", "Hello", "noreply@meritlense.com", ["a@example.com"])
        self.assertEqual(mail.outbox[0].body, "Hello")
