import re

from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APITestCase

from api.accounts.models import Company, CompanyEmployerProfile, User
from api.contracts.constants import CURRENT_VERSIONS
from api.contracts.models import Agreement
from api.contracts.pdf_service import render_preview_html
from api.core.constants import AgreementMethod, AgreementStatus, AgreementType, Roles


class FakeCompany:
    name = "Acme Staffing LLC"
    registration_number = "REG-12345"
    country = "AE"
    address = "123 Example Road, Tallinn"


class FakeIndividualProfile:
    nationality = "PH"


class FakeUser:
    individual_profile = FakeIndividualProfile()
    email = "jane@example.com"

    def get_full_name(self):
        return "Jane Doe"


class AgreementTemplateContentTests(TestCase):
    """Agreement previews must match the supplied current legal documents."""

    def test_b2b_agreement_has_no_placeholder_notice(self):
        html = render_preview_html(
            AgreementType.B2B_AGREEMENT, CURRENT_VERSIONS[AgreementType.B2B_AGREEMENT],
            company=FakeCompany(), user=None,
        )
        self.assertNotIn("PLACEHOLDER", html)
        self.assertIn("Acme Staffing LLC", html)
        self.assertIn("REG-12345", html)
        self.assertIn("123 Example Road, Tallinn", html)
        self.assertIn("Republic of Estonia", html)

    def test_b2c_agreement_has_no_placeholder_notice(self):
        html = render_preview_html(
            AgreementType.B2C_AGREEMENT, CURRENT_VERSIONS[AgreementType.B2C_AGREEMENT],
            company=None, user=FakeUser(),
        )
        self.assertNotIn("PLACEHOLDER", html)
        self.assertIn("Jane Doe", html)
        self.assertIn("Statutory 14-day withdrawal right", html)
        self.assertIn("Jane Doe", html)

    def test_b2b_and_b2c_versions_match_provided_documents(self):
        self.assertEqual(CURRENT_VERSIONS[AgreementType.B2B_AGREEMENT], "v1.6")
        self.assertEqual(CURRENT_VERSIONS[AgreementType.B2C_AGREEMENT], "v1.6")

    def test_privacy_terms_version_matches_provided_policy(self):
        self.assertEqual(CURRENT_VERSIONS[AgreementType.PRIVACY_TERMS], "v1.6")

    def test_b2b_agreement_includes_the_final_privacy_policy_clause(self):
        html = render_preview_html(
            AgreementType.B2B_AGREEMENT, CURRENT_VERSIONS[AgreementType.B2B_AGREEMENT],
            company=FakeCompany(), user=None,
        )
        self.assertIn("referenced for transparency and information purposes", html)
        self.assertIn("Acme Staffing LLC", html)

    def test_b2b_agreement_arabic_preview_renders_rtl_with_real_content(self):
        """b2b_agreement_ar.html didn't exist until now - Arabic-locale B2B
        signers were silently served the English template despite the
        frontend already requesting lang=ar (see TEMPLATE_BY_TYPE_AR)."""
        html = render_preview_html(
            AgreementType.B2B_AGREEMENT, CURRENT_VERSIONS[AgreementType.B2B_AGREEMENT],
            company=FakeCompany(), user=None, language="ar",
        )
        self.assertIn('dir="rtl"', html)
        self.assertNotIn("PLACEHOLDER", html)
        self.assertIn("اتفاقية خدمات للشركات والوكالات", html)
        self.assertIn("Acme Staffing LLC", html)
        self.assertIn("Assessment Slots", html)

    def test_dpa_has_no_placeholder_notice(self):
        html = render_preview_html(
            AgreementType.DPA, CURRENT_VERSIONS[AgreementType.DPA],
            company=FakeCompany(), user=None,
        )
        self.assertNotIn("PLACEHOLDER", html)
        self.assertIn("Acme Staffing LLC", html)
        self.assertIn("REG-12345", html)
        self.assertIn("Republic of Estonia", html)
        self.assertIn("Annex D — Retention Principles", html)

    def test_dpa_version_matches_provided_document(self):
        self.assertEqual(CURRENT_VERSIONS[AgreementType.DPA], "v1.7")

    def test_dpa_arabic_preview_renders_rtl_with_real_content(self):
        html = render_preview_html(
            AgreementType.DPA, CURRENT_VERSIONS[AgreementType.DPA],
            company=FakeCompany(), user=None, language="ar",
        )
        self.assertIn('dir="rtl"', html)
        self.assertNotIn("PLACEHOLDER", html)
        self.assertIn("اتفاقية معالجة البيانات", html)
        self.assertIn("Acme Staffing LLC", html)

    def test_dpa_annexes_match_the_final_provider_and_retention_disclosures(self):
        html = render_preview_html(
            AgreementType.DPA, CURRENT_VERSIONS[AgreementType.DPA],
            company=FakeCompany(), user=None,
        )
        self.assertNotIn("[To be completed]", html)
        self.assertNotIn("[To be confirmed]", html)
        self.assertNotIn("[Data Location to be confirmed]", html)
        self.assertNotIn("[Retention / Deletion Policy to be confirmed]", html)
        self.assertIn("Microsoft Azure", html)
        self.assertIn("OpenAI", html)
        self.assertIn("Payment provider", html)
        self.assertIn("not candidate assessment data unless technically necessary", html)
        self.assertIn("Standard Contractual Clauses", html)
        self.assertIn("current 90-day deletion control is enabled", html)

    def test_dpa_arabic_annex_b_subprocessors_are_confirmed_not_placeholder(self):
        html = render_preview_html(
            AgreementType.DPA, CURRENT_VERSIONS[AgreementType.DPA],
            company=FakeCompany(), user=None, language="ar",
        )
        self.assertNotIn("قيد الإكمال", html)
        self.assertNotIn("قيد التأكيد", html)
        self.assertIn("Microsoft Azure", html)
        self.assertIn("مقدم خدمات الدفع", html)
        self.assertIn("البنود التعاقدية القياسية", html)

    def test_dpa_annexes_describe_transfer_safeguards_without_blanket_claims(self):
        html = render_preview_html(
            AgreementType.DPA, CURRENT_VERSIONS[AgreementType.DPA],
            company=FakeCompany(), user=None,
        )
        self.assertIn("lawful Chapter V GDPR transfer basis applicable to that transfer", html)
        self.assertIn("not required to duplicate SCCs in this DPA", html)


class AgreementPublicPreviewEndpointTests(APITestCase):
    """B2C/B2B Agreement templates are linked from the marketing site
    footer, so they must render for a signed-out visitor - see
    agreement_public_preview and PUBLIC_PREVIEW_TYPES."""

    def test_b2c_public_preview_accessible_without_auth(self):
        response = self.client.get("/api/v1/agreements/public-preview/B2C_AGREEMENT")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertIn("Statutory 14-day withdrawal right", response.data["html"])
        self.assertEqual(response.data["version"], CURRENT_VERSIONS[AgreementType.B2C_AGREEMENT])

    def test_b2b_public_preview_accessible_without_auth(self):
        response = self.client.get("/api/v1/agreements/public-preview/B2B_AGREEMENT")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertIn("Republic of Estonia", response.data["html"])
        self.assertEqual(response.data["version"], CURRENT_VERSIONS[AgreementType.B2B_AGREEMENT])

    def test_b2c_public_preview_arabic(self):
        response = self.client.get("/api/v1/agreements/public-preview/B2C_AGREEMENT?lang=ar")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertIn('dir="rtl"', response.data["html"])

    def test_dpa_has_no_public_preview(self):
        response = self.client.get("/api/v1/agreements/public-preview/DPA")
        self.assertEqual(response.status_code, 400)

    def test_candidate_consent_has_no_public_preview(self):
        response = self.client.get("/api/v1/agreements/public-preview/CANDIDATE_CONSENT")
        self.assertEqual(response.status_code, 400)


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
        business_license_verified=True,
    )
    defaults.update(overrides)
    company = Company.objects.create(**defaults)
    CompanyEmployerProfile.objects.create(
        user=admin_user, company_name=company.name, company_registration_number=f"{company.registration_number}-profile",
        company_size=company.company_size, phone_number=company.phone_number, country=company.country, city=company.city,
        registration_certificate=SimpleUploadedFile("profile-cert.pdf", b"cert", content_type="application/pdf"),
        resachetified_license=SimpleUploadedFile("license.pdf", b"license", content_type="application/pdf"),
        company=company,
    )
    return company


class AdminAgreementEndpointTests(APITestCase):
    """Admin needs to see and download a company's signed B2B Agreement
    before approving/rejecting the account - previously a full gap, see
    admin_user_agreements and the admin bypass on agreement_download."""

    def setUp(self):
        self.superadmin = User.objects.create_user(
            email="agreement-endpoint-superadmin@example.com", password="Password123!",
            first_name="Agreement", last_name="Super", role=Roles.SUPERADMIN, is_verified=True, is_staff=True,
        )
        login = self.client.post(
            "/api/v1/auth/login", {"email": self.superadmin.email, "password": "Password123!"}, format="json",
        )
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {login.data['access']}")

        self.b2b_user = User.objects.create_user(
            email="agreement-endpoint-b2b@example.com", password="Password123!",
            first_name="Bizz", last_name="Owner", role=Roles.B2B, is_verified=True,
        )
        self.company = make_company(self.b2b_user)
        self.agreement = Agreement.objects.create(
            user=self.b2b_user, company=self.company, agreement_type=AgreementType.B2B_AGREEMENT,
            version="v1.4", method=AgreementMethod.OTP_SIGNATURE, status=AgreementStatus.SIGNED,
            signatory_name="Bizz Owner", accepted_at=timezone.now(), contract_id="MLB2B-TEST-1",
            signed_pdf=SimpleUploadedFile("agreement.pdf", b"%PDF-1.4 fake", content_type="application/pdf"),
        )

    def test_admin_can_list_a_users_agreements(self):
        response = self.client.get(f"/api/v1/agreements/admin/user/{self.b2b_user.id}")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['contract_id'], "MLB2B-TEST-1")

    def test_admin_can_download_another_users_signed_agreement(self):
        response = self.client.get(f"/api/v1/agreements/download/{self.agreement.id}")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertIn('url', response.data)

    def test_admin_listing_unknown_user_returns_404(self):
        response = self.client.get("/api/v1/agreements/admin/user/999999")
        self.assertEqual(response.status_code, 404)

    def test_stale_pending_agreement_must_be_reviewed_again(self):
        agreement = Agreement.objects.create(
            user=self.superadmin,
            agreement_type=AgreementType.B2C_AGREEMENT,
            version="v1.5",
            method=AgreementMethod.OTP_SIGNATURE,
            status=AgreementStatus.PENDING,
            signatory_name="Agreement Super",
            otp_reference="stale-terms-reference",
        )

        response = self.client.post(
            "/api/v1/agreements/sign/confirm",
            {"otp_reference": agreement.otp_reference, "code": "12345"},
            format="json",
        )

        self.assertEqual(response.status_code, 409, response.data)
        self.assertIn("terms have changed", response.data["error"])
        agreement.refresh_from_db()
        self.assertEqual(agreement.status, AgreementStatus.SUPERSEDED)
        self.assertFalse(agreement.signed_pdf)

    def test_non_admin_cannot_list_another_users_agreements(self):
        other_user = User.objects.create_user(
            email="agreement-endpoint-other@example.com", password="Password123!",
            first_name="Other", last_name="User", role=Roles.B2C, is_verified=True,
        )
        login = self.client.post(
            "/api/v1/auth/login", {"email": other_user.email, "password": "Password123!"}, format="json",
        )
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {login.data['access']}")
        response = self.client.get(f"/api/v1/agreements/admin/user/{self.b2b_user.id}")
        self.assertEqual(response.status_code, 403)

    def test_non_owner_non_admin_cannot_download(self):
        other_user = User.objects.create_user(
            email="agreement-endpoint-other2@example.com", password="Password123!",
            first_name="Other", last_name="Two", role=Roles.B2C, is_verified=True,
        )
        login = self.client.post(
            "/api/v1/auth/login", {"email": other_user.email, "password": "Password123!"}, format="json",
        )
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {login.data['access']}")
        response = self.client.get(f"/api/v1/agreements/download/{self.agreement.id}")
        self.assertEqual(response.status_code, 404)

    def test_signing_b2b_agreement_notifies_superadmins(self):
        """Business requirement: admins get an automatic alert once a B2B
        Agreement is signed, so they know to review it for approval."""
        signer = User.objects.create_user(
            email="agreement-endpoint-signer@example.com", password="Password123!",
            first_name="Signer", last_name="Co", role=Roles.B2B, is_verified=True,
        )
        make_company(
            signer, name="Signer Co", registration_number="REG-SIGNER-1",
            stamp_image=SimpleUploadedFile("stamp.png", b"fake-png-bytes", content_type="image/png"),
        )

        login = self.client.post(
            "/api/v1/auth/login", {"email": signer.email, "password": "Password123!"}, format="json",
        )
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {login.data['access']}")

        mail.outbox = []
        initiate = self.client.post(
            "/api/v1/agreements/sign/initiate",
            {
                "agreement_types": [AgreementType.B2B_AGREEMENT],
                "signatory_name": "Signer Co",
                "authorized_signatory_confirmed": True,
            },
            format="json",
        )
        self.assertEqual(initiate.status_code, 200, initiate.data)

        otp_email = next(m for m in mail.outbox if "signing code" in m.subject.lower())
        code = re.search(r"is:\s*(\d+)", otp_email.body).group(1)

        mail.outbox = []
        confirm = self.client.post(
            "/api/v1/agreements/sign/confirm",
            {"otp_reference": initiate.data["otp_reference"], "code": code},
            format="json",
        )
        self.assertEqual(confirm.status_code, 200, confirm.data)

        admin_alerts = [m for m in mail.outbox if self.superadmin.email in m.to]
        self.assertEqual(len(admin_alerts), 1)
        self.assertIn("Signer Co", admin_alerts[0].subject)


class CompanyStampRequiredForSigningTests(APITestCase):
    """The company stamp is baked into the signed PDF at sign time only and
    never regenerated (see pdf_service.STAMPED_TYPES) - a company that
    signs before uploading a stamp is permanently stuck with an unstamped
    document. Signing B2B_AGREEMENT/DPA must be blocked until a stamp
    exists."""

    def _login_b2b_user(self, **company_overrides):
        user = User.objects.create_user(
            email=f"stamp-gate-{company_overrides.get('registration_number', 'x')}@example.com",
            password="Password123!", first_name="Owner", last_name="Co", role=Roles.B2B, is_verified=True,
        )
        make_company(user, name="Stamp Test Co", **company_overrides)
        login = self.client.post(
            "/api/v1/auth/login", {"email": user.email, "password": "Password123!"}, format="json",
        )
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {login.data['access']}")
        return user

    def test_b2b_sign_blocked_without_company_stamp(self):
        self._login_b2b_user(registration_number="REG-STAMP-1")
        response = self.client.post(
            "/api/v1/agreements/sign/initiate",
            {
                "agreement_types": [AgreementType.B2B_AGREEMENT],
                "signatory_name": "Stamp Test Co",
                "authorized_signatory_confirmed": True,
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400, response.data)
        self.assertTrue(response.data.get("stamp_missing"))

    def test_dpa_sign_also_blocked_without_company_stamp(self):
        self._login_b2b_user(registration_number="REG-STAMP-2")
        response = self.client.post(
            "/api/v1/agreements/sign/initiate",
            {
                "agreement_types": [AgreementType.DPA],
                "signatory_name": "Stamp Test Co",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400, response.data)
        self.assertTrue(response.data.get("stamp_missing"))

    def test_b2b_sign_allowed_with_company_stamp(self):
        self._login_b2b_user(
            registration_number="REG-STAMP-3",
            stamp_image=SimpleUploadedFile("stamp.png", b"fake-png-bytes", content_type="image/png"),
        )
        response = self.client.post(
            "/api/v1/agreements/sign/initiate",
            {
                "agreement_types": [AgreementType.B2B_AGREEMENT],
                "signatory_name": "Stamp Test Co",
                "authorized_signatory_confirmed": True,
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
