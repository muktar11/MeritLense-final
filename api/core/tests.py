import shutil
import tempfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings

from api.accounts.models import Company, CompanyEmployerProfile, User
from api.candidates.models import Candidate
from api.core.constants import CompanySize, Languages, Roles, candidateJobRoles


def make_file(name="document.pdf", content=b"test-file", content_type="application/pdf"):
    return SimpleUploadedFile(name, content, content_type=content_type)


@override_settings()
class AnonymizeQaDataCommandTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._media_dir = tempfile.mkdtemp(prefix="anonymize-qa-tests-")
        cls._override = override_settings(MEDIA_ROOT=cls._media_dir)
        cls._override.enable()

    @classmethod
    def tearDownClass(cls):
        cls._override.disable()
        shutil.rmtree(cls._media_dir, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        self.candidate_owner = User.objects.create_user(
            email="owner@example.com",
            password="Password123!",
            first_name="Real",
            last_name="Owner",
            role=Roles.B2C,
            is_verified=True,
        )
        self.candidate = Candidate.objects.create(
            first_name="Jane",
            last_name="Doe",
            email="jane.doe@example.com",
            passport_id="REAL-PASSPORT-1",
            job_role=candidateJobRoles.NANNY,
            core_skills="communication, patience",
            preferred_language=Languages.ENGLISH,
            passport_document=make_file(),
            profile_photo=make_file("photo.jpg", content_type="image/jpeg"),
            created_by=self.candidate_owner,
        )
        self.company_admin = User.objects.create_user(
            email="admin@realcompany.com",
            password="Password123!",
            first_name="Company",
            last_name="Admin",
            role=Roles.B2B,
            is_verified=True,
        )
        self.company = Company.objects.create(
            name="Real Company Inc",
            registration_number="REAL-REG-123",
            company_size=CompanySize.SMALL,
            industry="Technology",
            phone_number="+15550000000",
            country="US",
            city="New York",
            address="1 Company Way",
            admin_user=self.company_admin,
            registration_certificate=make_file("cert.pdf"),
        )
        CompanyEmployerProfile.objects.create(
            user=self.company_admin,
            company_name="Real Company Inc",
            company_registration_number="REAL-REG-123",
            company_size=CompanySize.SMALL,
            industry="Technology",
            phone_number="+15550000000",
            country="US",
            city="New York",
            address="1 Company Way",
            preferred_language=Languages.ENGLISH,
            registration_certificate=make_file("profile-cert.pdf"),
            resachetified_license=make_file("license.pdf"),
            company=self.company,
        )

    def test_anonymize_replaces_pii_and_clears_documents(self):
        call_command("anonymize_qa_data", password="QaTestPass123!", admin_email="qa-admin@meritlense.com")

        self.candidate.refresh_from_db()
        self.assertNotEqual(self.candidate.email, "jane.doe@example.com")
        self.assertTrue(self.candidate.email.endswith("@qa.meritlense.test"))
        self.assertNotEqual(self.candidate.passport_id, "REAL-PASSPORT-1")
        self.assertFalse(self.candidate.passport_document)
        self.assertFalse(self.candidate.profile_photo)
        # verification_photo is replaced with a synthetic placeholder, not
        # cleared - the identity-verification precheck step needs *some*
        # readable reference image to load, real or not.
        self.assertTrue(self.candidate.verification_photo)

        self.company.refresh_from_db()
        self.assertNotEqual(self.company.registration_number, "REAL-REG-123")
        self.assertFalse(self.company.registration_certificate)

        self.company_admin.refresh_from_db()
        self.assertNotEqual(self.company_admin.email, "admin@realcompany.com")
        self.assertTrue(self.company_admin.check_password("QaTestPass123!"))

    def test_anonymize_guarantees_a_known_qa_admin_login(self):
        self.candidate_owner.is_superuser = True
        self.candidate_owner.is_staff = True
        self.candidate_owner.save(update_fields=["is_superuser", "is_staff"])

        call_command("anonymize_qa_data", password="QaTestPass123!", admin_email="qa-admin@meritlense.com")

        admin = User.objects.get(email="qa-admin@meritlense.com")
        self.assertTrue(admin.is_superuser)
        self.assertTrue(admin.check_password("QaTestPass123!"))

    def test_anonymize_is_idempotent(self):
        call_command("anonymize_qa_data", password="QaTestPass123!", admin_email="qa-admin@meritlense.com")
        call_command("anonymize_qa_data", password="QaTestPass123!", admin_email="qa-admin@meritlense.com")

        self.assertEqual(Candidate.objects.count(), 1)
        self.assertEqual(User.objects.filter(email="qa-admin@meritlense.com").count(), 1)
