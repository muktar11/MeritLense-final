"""Registration consent (improvement deck, slides 2-3): the Terms & Privacy
checkbox is required and recorded; B2C no longer asks for Job Title; cookie
choices are recorded."""
import uuid
from unittest import mock

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from api.accounts.models import IndividualEmployerProfile, User
from api.audit.models import AuditLog
from api.contracts.constants import CURRENT_VERSIONS
from api.contracts.models import Agreement, CookieConsent
from api.core.constants import AgreementMethod, AgreementStatus, AgreementType

B2C = {
    "email": "consent-b2c@example.com", "first_name": "Consent", "last_name": "Person",
    "password": "Password123!", "confirm_password": "Password123!", "passport_id": "CONSENT-1",
    "nationality": "US", "preferred_language": "EN", "phone_number": "+15550001111",
}
B2B = {
    "email": "consent-b2b@example.com", "first_name": "Consent", "last_name": "Company",
    "password": "Password123!", "confirm_password": "Password123!", "company_name": "Consent Co",
    "company_registration_number": "CONSENT-B2B-1", "company_size": "11-50", "country": "AE",
    "city": "Dubai", "preferred_language": "EN", "phone_number": "+971500000000",
}


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
@mock.patch("api.accounts.views.notify_superadmins")
class RegistrationConsentTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def test_b2c_requires_the_checkbox_and_no_longer_needs_job_title(self, _notify):
        response = self.client.post("/api/v1/auth/register/b2c", B2C, format="multipart")
        self.assertEqual(response.status_code, 400)
        self.assertIn("terms_accepted", response.data)
        response = self.client.post("/api/v1/auth/register/b2c", {**B2C, "terms_accepted": False}, format="multipart")
        self.assertEqual(response.status_code, 400)
        self.assertFalse(User.objects.filter(email=B2C["email"]).exists())

        response = self.client.post("/api/v1/auth/register/b2c",
                                    {**B2C, "terms_accepted": True, "terms_language": "ar"}, format="multipart")
        self.assertEqual(response.status_code, 201, response.data)
        user = User.objects.get(email=B2C["email"])
        self.assertEqual(IndividualEmployerProfile.objects.get(user=user).job_role, "")
        agreement = Agreement.objects.get(user=user, agreement_type=AgreementType.PRIVACY_TERMS)
        self.assertEqual(agreement.status, AgreementStatus.SIGNED)
        self.assertEqual(agreement.method, AgreementMethod.CHECKBOX)
        self.assertEqual(agreement.version, CURRENT_VERSIONS[AgreementType.PRIVACY_TERMS])
        self.assertEqual(agreement.language, "ar")
        self.assertIsNotNone(agreement.accepted_at)
        log = AuditLog.objects.filter(description__contains="registration checkbox").latest("created_at")
        self.assertIn("18 years or older", log.data["statement"])

    def test_other_nationality_and_country_need_the_typed_value(self, _notify):
        base = {**B2C, "terms_accepted": True, "nationality": "OT", "country_of_residence": "OT"}
        response = self.client.post("/api/v1/auth/register/b2c", base, format="multipart")
        self.assertEqual(response.status_code, 400)
        self.assertIn("nationality_other", response.data)
        self.assertIn("country_of_residence_other", response.data)
        response = self.client.post("/api/v1/auth/register/b2c", {
            **base, "nationality_other": "Stateless", "country_of_residence_other": "Somaliland",
            "target_market": "AE", "target_city": "Dubai"}, format="multipart")
        self.assertEqual(response.status_code, 201, response.data)
        profile = IndividualEmployerProfile.objects.get(user__email=B2C["email"])
        self.assertEqual((profile.nationality, profile.nationality_other), ("OT", "Stateless"))
        self.assertEqual(profile.country_of_residence_other, "Somaliland")
        self.assertEqual((profile.target_market, profile.target_city), ("AE", "Dubai"))

    def test_every_country_is_a_valid_nationality(self, _notify):
        response = self.client.post("/api/v1/auth/register/b2c",
                                    {**B2C, "terms_accepted": True, "nationality": "ET"}, format="multipart")
        self.assertEqual(response.status_code, 201, response.data)

    def test_b2b_requires_the_checkbox_and_records_it(self, _notify):
        self.assertEqual(self.client.post("/api/v1/auth/register/b2b", B2B, format="multipart").status_code, 400)
        response = self.client.post("/api/v1/auth/register/b2b", {**B2B, "terms_accepted": True}, format="multipart")
        self.assertEqual(response.status_code, 201, response.data)
        user = User.objects.get(email=B2B["email"])
        agreement = Agreement.objects.get(user=user, agreement_type=AgreementType.PRIVACY_TERMS)
        self.assertEqual(agreement.language, "en")
        log = AuditLog.objects.filter(description__contains="registration checkbox").latest("created_at")
        self.assertIn("I have read and agree", log.data["statement"])


class CookieConsentTests(TestCase):
    def test_choices_are_recorded_as_history(self):
        client = APIClient()
        consent_id = str(uuid.uuid4())
        url = "/api/v1/agreements/cookie-consent"
        response = client.post(url, {"consent_id": consent_id, "choice": "reject_optional", "language": "ar"},
                               format="json")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual((response.data["necessary"], response.data["analytics"], response.data["marketing"]),
                         (True, False, False))
        response = client.post(url, {"consent_id": consent_id, "choice": "custom", "analytics": True,
                                     "marketing": False}, format="json")
        self.assertEqual((response.data["analytics"], response.data["marketing"]), (True, False))
        response = client.post(url, {"consent_id": consent_id, "choice": "accept_all"}, format="json")
        self.assertEqual((response.data["analytics"], response.data["marketing"]), (True, True))
        rows = CookieConsent.objects.filter(consent_id=consent_id).order_by("created_at")
        self.assertEqual([r.choice for r in rows], ["reject_optional", "custom", "accept_all"])
        self.assertEqual(rows[0].language, "ar")
        self.assertTrue(all(r.necessary for r in rows))
        self.assertEqual(rows[0].policy_version, CURRENT_VERSIONS[AgreementType.PRIVACY_TERMS])

    def test_bad_input_is_refused(self):
        client = APIClient()
        url = "/api/v1/agreements/cookie-consent"
        self.assertEqual(client.post(url, {"consent_id": "nope", "choice": "accept_all"}, format="json").status_code, 400)
        self.assertEqual(client.post(url, {"consent_id": str(uuid.uuid4()), "choice": "maybe"}, format="json").status_code, 400)
        self.assertFalse(CookieConsent.objects.exists())


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
@mock.patch("api.accounts.views.notify_superadmins")
class AdminCreatedAccountTests(TestCase):
    """An admin creating an employer on someone's behalf can't tick the
    Terms box for them: not required, and no acceptance is recorded."""

    def setUp(self):
        from api.core.constants import Roles

        self.admin = User.objects.create_user(email="creator@example.com", password="x", first_name="A",
                                              last_name="D", role=Roles.SUPERADMIN, is_verified=True,
                                              is_staff=True, is_superuser=True)
        self.client = APIClient()
        self.client.force_authenticate(self.admin)

    def test_admin_creates_b2c_and_b2b_without_the_checkbox(self, _notify):
        for url, payload in (("/api/v1/auth/admin/employers/create/b2c", {**B2C, "email": "admin-b2c@example.com",
                                                                            "passport_id": "ADMIN-B2C-1"}),
                             ("/api/v1/auth/admin/employers/create/b2b", {**B2B, "email": "admin-b2b@example.com",
                                                                            "company_registration_number": "ADMIN-B2B-1"})):
            with self.subTest(url=url):
                response = self.client.post(url, payload, format="multipart")
                self.assertIn(response.status_code, (200, 201), response.data)
                user = User.objects.get(email=payload["email"])
                self.assertFalse(Agreement.objects.filter(user=user, agreement_type=AgreementType.PRIVACY_TERMS).exists())
