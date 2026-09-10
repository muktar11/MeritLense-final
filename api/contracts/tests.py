import re

from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APITestCase

from api.accounts.models import Company, User
from api.contracts.constants import CURRENT_VERSIONS
from api.contracts.models import Agreement
from api.contracts.pdf_service import render_preview_html
from api.core.constants import AgreementMethod, AgreementStatus, AgreementType, Roles


class FakeCompany:
    name = "Acme Staffing LLC"
    registration_number = "REG-12345"
    country = "AE"


class FakeIndividualProfile:
    nationality = "PH"


class FakeUser:
    individual_profile = FakeIndividualProfile()
    email = "jane@example.com"

    def get_full_name(self):
        return "Jane Doe"


class AgreementTemplateContentTests(TestCase):
    """B2B/B2C agreement bodies must be the real legal text, not the
    placeholder that shipped before legal review - see CURRENT_VERSIONS
    and api/contracts/templates/contracts/*.html."""

    def test_b2b_agreement_has_no_placeholder_notice(self):
        html = render_preview_html(
            AgreementType.B2B_AGREEMENT, CURRENT_VERSIONS[AgreementType.B2B_AGREEMENT],
            company=FakeCompany(), user=None,
        )
        self.assertNotIn("PLACEHOLDER", html)
        self.assertIn("Acme Staffing LLC", html)
        self.assertIn("Republic of Estonia", html)

    def test_b2c_agreement_has_no_placeholder_notice(self):
        html = render_preview_html(
            AgreementType.B2C_AGREEMENT, CURRENT_VERSIONS[AgreementType.B2C_AGREEMENT],
            company=None, user=FakeUser(),
        )
        self.assertNotIn("PLACEHOLDER", html)
        self.assertIn("Jane Doe", html)
        self.assertIn("Refund Policy (B2C)", html)

    def test_b2b_and_b2c_versions_match_provided_documents(self):
        self.assertEqual(CURRENT_VERSIONS[AgreementType.B2B_AGREEMENT], "v1.4")
        self.assertEqual(CURRENT_VERSIONS[AgreementType.B2C_AGREEMENT], "v1.5")

    def test_dpa_still_flagged_as_placeholder_pending_real_content(self):
        # Not covered by this fix - no real DPA text was supplied. Left as a
        # canary so this doesn't silently stay stale once DPA text lands.
        html = render_preview_html(
            AgreementType.DPA, CURRENT_VERSIONS[AgreementType.DPA],
            company=FakeCompany(), user=None,
        )
        self.assertIn("PLACEHOLDER", html)


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
        make_company(signer, name="Signer Co", registration_number="REG-SIGNER-1")

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
