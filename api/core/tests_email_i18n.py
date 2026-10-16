"""Employer emails go out in Arabic for Arabic users (16 Oct 2026)."""
from types import SimpleNamespace

from django.core import mail
from django.test import TestCase, override_settings
from django.utils import timezone

from api.accounts.models import Company, CompanyEmployerProfile, IndividualEmployerProfile, TeamInvitation, User
from api.accounts.utils import (
    send_account_approved_email,
    send_password_reset_email,
    send_team_invitation_email,
    send_verification_email,
)
from api.candidates.models import Candidate
from api.contracts.otp_service import OTPService
from api.core.constants import EvaluationType, Roles
from api.core.email_i18n import email_language, format_date, user_language
from api.evaluations.models import Evaluation
from api.evaluations.utils import send_evaluation_cancelled_email
from api.payments.notifications import maybe_send_low_balance_warning

EMAIL = dict(EMAIL_HOST="smtp.test", EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")


def has_arabic(text):
    return any("؀" <= ch <= "ۿ" for ch in text)


@override_settings(**EMAIL)
class EmailLanguageTests(TestCase):
    def setUp(self):
        mail.outbox.clear()

    def b2c(self, language, suffix):
        user = User.objects.create_user(
            email=f"b2c-{suffix}@example.com", password="Password123!", first_name="Amal",
            last_name="B2C", role=Roles.B2C, is_verified=True,
        )
        IndividualEmployerProfile.objects.create(
            user=user, passport_id=f"PP-{suffix}", phone_number="+971500000000",
            job_role="PM", nationality="US", preferred_language=language,
        )
        return user

    def b2b(self, language, suffix):
        owner = User.objects.create_user(
            email=f"b2b-{suffix}@example.com", password="Password123!", first_name="Omar",
            last_name="Owner", role=Roles.B2B, is_verified=True,
        )
        company = Company.objects.create(
            name=f"Co {suffix}", registration_number=f"REG-{suffix}", company_size="1-10",
            phone_number="+971400000000", country="United Arab Emirates", city="Dubai", admin_user=owner,
        )
        CompanyEmployerProfile.objects.create(
            user=owner, company=company, company_name=company.name, company_registration_number=company.registration_number,
            company_size="1-10", phone_number=company.phone_number, country=company.country, city=company.city,
            preferred_language=language,
        )
        return owner, company

    def test_language_comes_from_profile_or_request(self):
        ar_user = self.b2c("AR", "lang-ar")
        en_user = self.b2c("EN", "lang-en")
        self.assertEqual(user_language(ar_user), "ar")
        self.assertEqual(user_language(en_user), "en")
        request = SimpleNamespace(GET={"locale": "ar"}, data={})
        self.assertEqual(email_language(en_user, request), "ar")
        self.assertEqual(user_language(None), "en")

    def test_arabic_dates(self):
        value = timezone.datetime(2026, 10, 16, 15, 30)
        self.assertEqual(format_date(value, "ar"), "الجمعة، 16 أكتوبر 2026")
        self.assertEqual(format_date(value, "en"), "Friday, October 16, 2026")

    def test_password_reset_in_arabic_links_to_arabic_site(self):
        user = self.b2c("AR", "reset-ar")
        send_password_reset_email(user, None)
        msg = mail.outbox[-1]
        self.assertTrue(has_arabic(msg.subject) and has_arabic(msg.body))
        self.assertIn("/ar/auth/reset-password?token=", msg.body)

    def test_password_reset_stays_english_for_english_users(self):
        user = self.b2c("EN", "reset-en")
        send_password_reset_email(user, None)
        msg = mail.outbox[-1]
        self.assertEqual(msg.subject, "Reset Your Individual Employer Password")
        self.assertIn("/en/auth/reset-password?token=", msg.body)

    def test_company_verification_in_arabic(self):
        owner, _ = self.b2b("AR", "verify-ar")
        owner.email_verification_code = "12345"
        send_verification_email(owner)
        msg = mail.outbox[-1]
        self.assertEqual(msg.subject, "تأكيد حساب شركتك")
        self.assertIn("12345", msg.body)
        self.assertIn("الرخصة التجارية", msg.body)

    def test_account_approved_in_arabic(self):
        send_account_approved_email(self.b2c("AR", "approved-ar"))
        self.assertTrue(has_arabic(mail.outbox[-1].subject))

    def test_team_invitation_follows_company_language(self):
        owner, company = self.b2b("AR", "invite-ar")
        invitation = TeamInvitation.objects.create(
            company=company, invited_by=owner, email="new.member@example.com", first_name="Sara",
            last_name="Member", job_title="Reviewer", permissions=["view_results"],
            expires_at=timezone.now() + timezone.timedelta(days=7),
        )
        send_team_invitation_email(invitation, None)
        msg = mail.outbox[-1]
        self.assertIn("دعوة للانضمام", msg.subject)
        self.assertIn("الاطلاع على جميع نتائج الشركة", msg.body)
        self.assertIn("/ar/auth/accept-invitation?token=", msg.body)

    def test_low_balance_warning_in_arabic(self):
        owner, company = self.b2b("AR", "slots-ar")
        maybe_send_low_balance_warning(created_by=owner, company=company, remaining=1, limit=10)
        msg = mail.outbox[-1]
        self.assertIn("رصيد خانات التقييم منخفض", msg.subject)
        self.assertIn("1 من 10", msg.body)

    def test_candidate_email_uses_candidate_language(self):
        owner, company = self.b2b("EN", "eval-ar")
        candidate = Candidate.objects.create(
            first_name="Hala", last_name="Candidate", email="hala@example.com", passport_id="PP-EVAL-AR",
            job_role="NA", core_skills="care", preferred_language="AR",
            passport_document="candidates/documents/passport/test.pdf", created_by=owner, company=company,
        )
        evaluation = Evaluation.objects.create(
            candidate=candidate, evaluation_type=EvaluationType.INTERVIEW,
            scheduled_date=timezone.now() + timezone.timedelta(days=2), duration_minutes=45,
            created_by=owner, company=company, cancellation_reason="Role filled",
        )
        send_evaluation_cancelled_email(evaluation)
        msg = mail.outbox[-1]
        self.assertEqual(msg.subject, "تم إلغاء التقييم: مقابلة")
        self.assertIn("السبب: Role filled", msg.body)

    def test_signing_code_in_arabic(self):
        self.assertTrue(OTPService().send("signer@example.com", "654321", language="ar"))
        msg = mail.outbox[-1]
        self.assertEqual(msg.subject, "رمز التوقيع الخاص بك في MeritLense")
        self.assertIn("654321", msg.body)
        self.assertTrue(OTPService().send("signer@example.com", "111111"))
        self.assertEqual(mail.outbox[-1].subject, "Your MeritLense signing code")
