import shutil
import tempfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from api.accounts.models import Company, CompanyEmployerProfile, User
from api.candidates.models import Candidate
from api.contracts.models import Agreement
from api.core.constants import (
    AgreementStatus,
    AgreementType,
    CompanySize,
    Languages,
    Roles,
    SubscriptionStatus,
    candidateJobRoles,
)
from api.payments.models import Customer, Price, Subscription


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
        # Mirrors a real production subscription: real-looking Stripe IDs
        # that, on QA, would otherwise round-trip to a real live Stripe
        # object if left untouched.
        price = Price.objects.create(
            name="Real Plan",
            stripe_price_id="price_real_prod_123",
            stripe_product_id="prod_real_prod_123",
            target_user_type="B2C",
            unit_amount=2999,
        )
        self.real_customer = Customer.objects.create(
            user=self.candidate_owner,
            stripe_customer_id="cus_REALPRODCUSTOMER123",
            email="owner@example.com",
            name="Real Owner",
            phone="+15551234567",
        )
        self.real_subscription = Subscription.objects.create(
            user=self.candidate_owner,
            customer=self.real_customer,
            stripe_subscription_id="sub_REALPRODSUBSCRIPTION123",
            stripe_price=price,
            status=SubscriptionStatus.ACTIVE,
            current_period_start=timezone.now(),
            current_period_end=timezone.now() + timezone.timedelta(days=30),
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
        self.assertEqual(User.objects.filter(email="qa-b2c@meritlense.com").count(), 1)
        self.assertEqual(
            Subscription.objects.filter(
                user__email="qa-b2c@meritlense.com", status=SubscriptionStatus.ACTIVE
            ).count(),
            1,
        )
        self.assertEqual(
            Agreement.objects.filter(
                user__email="qa-b2c@meritlense.com",
                agreement_type=AgreementType.B2C_AGREEMENT,
                status=AgreementStatus.SIGNED,
            ).count(),
            1,
        )

    def test_anonymize_replaces_real_stripe_ids_so_no_live_call_hits_a_real_object(self):
        call_command("anonymize_qa_data", password="QaTestPass123!", admin_email="qa-admin@meritlense.com")

        self.real_customer.refresh_from_db()
        self.assertNotEqual(self.real_customer.stripe_customer_id, "cus_REALPRODCUSTOMER123")
        self.assertNotEqual(self.real_customer.email, "owner@example.com")
        self.assertFalse(self.real_customer.default_payment_method_id)

        self.real_subscription.refresh_from_db()
        self.assertNotEqual(self.real_subscription.stripe_subscription_id, "sub_REALPRODSUBSCRIPTION123")

    def test_anonymize_guarantees_a_known_qa_b2c_paid_account(self):
        call_command("anonymize_qa_data", password="QaTestPass123!", admin_email="qa-admin@meritlense.com")

        b2c_user = User.objects.get(email="qa-b2c@meritlense.com")
        self.assertEqual(b2c_user.role, Roles.B2C)
        self.assertTrue(b2c_user.check_password("QaTestPass123!"))

        subscription = Subscription.objects.get(user=b2c_user, status=SubscriptionStatus.ACTIVE)
        # Never existed on Stripe at all - not a real object that got
        # anonymized, so there's nothing real a live API call could reach.
        self.assertTrue(subscription.stripe_subscription_id.startswith("sub_qa_test_b2c"))
        self.assertEqual(subscription.customer.stripe_customer_id, "cus_qa_test_b2c")

        # The frontend's AgreementGuard redirects to /sign-agreements for
        # any B2C account without a SIGNED B2C_AGREEMENT on file, regardless
        # of subscription status - the account otherwise looks "unactivated".
        agreement = Agreement.objects.get(
            user=b2c_user, agreement_type=AgreementType.B2C_AGREEMENT, status=AgreementStatus.SIGNED
        )
        self.assertIsNotNone(agreement.accepted_at)
