import shutil
import tempfile
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from rest_framework.test import APIClient, APITestCase

from api.accounts.models import Company, CompanyEmployerProfile, TeamMemberProfile, User
from api.contracts.models import Agreement
from api.core.constants import AgreementType, AgreementStatus, Roles


def make_document(name="document.pdf"):
    return SimpleUploadedFile(name, b"%PDF-1.4 test", content_type="application/pdf")


class AgreementSigningPermissionTests(APITestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._media_dir = tempfile.mkdtemp(prefix="contract-tests-")
        cls._override = override_settings(MEDIA_ROOT=cls._media_dir)
        cls._override.enable()

    @classmethod
    def tearDownClass(cls):
        cls._override.disable()
        shutil.rmtree(cls._media_dir, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        self.client = APIClient()
        self.owner = User.objects.create_user(
            email="owner@example.com",
            password="testpass123",
            first_name="Owner",
            last_name="User",
            role=Roles.B2B,
            is_verified=True,
        )
        self.team_member = User.objects.create_user(
            email="team@example.com",
            password="testpass123",
            first_name="Team",
            last_name="Member",
            role=Roles.B2B_TEAM_MEMBER,
            is_verified=True,
        )

        self.company = Company.objects.create(
            name="MeritLense Co",
            registration_number="ML-AGR-001",
            company_size="11-50",
            industry="Tech",
            phone_number="+251900000000",
            country="ET",
            city="Addis Ababa",
            address="Bole",
            website="https://example.com",
            admin_user=self.owner,
            registration_certificate=make_document("company-registration.pdf"),
            tax_id_document=make_document("company-tax.pdf"),
        )
        CompanyEmployerProfile.objects.create(
            user=self.owner,
            company_name=self.company.name,
            company_registration_number=self.company.registration_number,
            company_size=self.company.company_size,
            industry=self.company.industry,
            phone_number=self.company.phone_number,
            country=self.company.country,
            city=self.company.city,
            address=self.company.address,
            website=self.company.website,
            preferred_language="EN",
            registration_certificate=make_document("profile-registration.pdf"),
            resachetified_license=make_document("license.pdf"),
            tax_id_document=make_document("profile-tax.pdf"),
            company=self.company,
        )
        TeamMemberProfile.objects.create(
            user=self.team_member,
            company=self.company,
            job_title="Coordinator",
            department="Ops",
            phone_number="+251911111111",
            permissions=[],
            invited_by=self.owner,
        )

    def test_team_member_cannot_initiate_company_agreement_signing(self):
        self.client.force_authenticate(self.team_member)

        response = self.client.post(
            "/api/v1/agreements/sign/initiate",
            {
                "agreement_types": [AgreementType.B2B_AGREEMENT, AgreementType.DPA],
                "signatory_name": "Team Member",
                "authorized_signatory_confirmed": True,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 403, response.data)
        self.assertEqual(Agreement.objects.count(), 0)

    @patch("api.contracts.views.OTPService.send", return_value=True)
    @patch("api.contracts.views.OTPService.issue")
    def test_company_owner_can_initiate_company_agreement_signing(self, issue_mock, send_mock):
        self.client.force_authenticate(self.owner)
        issue_mock.return_value = ("123456", "hashed-code", self.owner.created_at)

        response = self.client.post(
            "/api/v1/agreements/sign/initiate",
            {
                "agreement_types": [AgreementType.B2B_AGREEMENT, AgreementType.DPA],
                "signatory_name": "Owner User",
                "authorized_signatory_confirmed": True,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(Agreement.objects.count(), 2)
        self.assertEqual(
            set(
                Agreement.objects.values_list("agreement_type", flat=True)
            ),
            {AgreementType.B2B_AGREEMENT, AgreementType.DPA},
        )
        self.assertTrue(
            all(
                agreement.company_id == self.company.id and agreement.status == AgreementStatus.PENDING
                for agreement in Agreement.objects.all()
            )
        )
        send_mock.assert_called_once()
