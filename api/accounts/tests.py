import shutil
import tempfile
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from api.accounts.models import Company, CompanyEmployerProfile, IndividualEmployerProfile, User
from api.core.constants import AdminPermissions, CompanySize, JobRoles, Languages, Nationalities, Roles, SubscriptionStatus
from api.payments.models import Customer, Subscription


def make_file(name="document.pdf", content=b"test-file", content_type="application/pdf"):
    return SimpleUploadedFile(name, content, content_type=content_type)


class AccountsWeek2Tests(APITestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._media_dir = tempfile.mkdtemp(prefix="accounts-tests-")
        cls._override = override_settings(MEDIA_ROOT=cls._media_dir)
        cls._override.enable()

    @classmethod
    def tearDownClass(cls):
        cls._override.disable()
        shutil.rmtree(cls._media_dir, ignore_errors=True)
        super().tearDownClass()

    def create_verified_b2c_user(self, email="verified@example.com", password="Password123!"):
        user = User.objects.create_user(
            email=email,
            password=password,
            first_name="Verified",
            last_name="User",
            role=Roles.B2C,
            is_verified=True,
        )
        IndividualEmployerProfile.objects.create(
            user=user,
            passport_id=f"PASS-{user.id}",
            phone_number="+15550000001",
            job_role=JobRoles.SOFTWARE_ENGINEER,
            nationality=Nationalities.US,
            preferred_language=Languages.ENGLISH,
            id_document=make_file(f"id-{user.id}.pdf"),
            resume_document=make_file(f"resume-{user.id}.pdf"),
        )
        return user

    def create_verified_b2b_owner(self, email="owner@example.com", password="Password123!"):
        user = User.objects.create_user(
            email=email,
            password=password,
            first_name="Company",
            last_name="Owner",
            role=Roles.B2B,
            is_verified=True,
        )
        profile = CompanyEmployerProfile.objects.create(
            user=user,
            company_name="Test Company",
            company_registration_number=f"REG-{user.id}",
            company_size=CompanySize.SMALL,
            country="United States",
            city="San Francisco",
            preferred_language=Languages.ENGLISH,
            phone_number="+15550000002",
            registration_certificate=make_file(f"cert-{user.id}.pdf"),
            resachetified_license=make_file(f"license-{user.id}.pdf"),
        )
        company = Company.objects.create(
            name=profile.company_name,
            registration_number=profile.company_registration_number,
            company_size=profile.company_size,
            phone_number=profile.phone_number,
            country=profile.country,
            city=profile.city,
            admin_user=user,
            registration_certificate=make_file(f"company-cert-{user.id}.pdf"),
        )
        profile.company = company
        profile.save(update_fields=["company"])
        return user, company

    def authenticate(self, email, password):
        login_response = self.client.post(
            "/api/v1/auth/login",
            {"email": email, "password": password},
            format="json",
        )
        self.assertEqual(login_response.status_code, status.HTTP_200_OK, login_response.data)
        self.client.credentials(
            HTTP_AUTHORIZATION=f"Bearer {login_response.data['access']}"
        )
        return login_response

    def test_b2c_registration_requires_verification_before_login_and_supports_refresh(self):
        registration_payload = {
            "email": "new-b2c@example.com",
            "first_name": "New",
            "last_name": "Candidate",
            "password": "Password123!",
            "confirm_password": "Password123!",
            "passport_id": "REG-1001",
            "job_role": JobRoles.SOFTWARE_ENGINEER,
            "nationality": Nationalities.US,
            "preferred_language": Languages.ENGLISH,
            "phone_number": "+15551112222",
            "date_of_birth": "1993-04-05",
            "address": "123 Main Street",
            "id_document": make_file("registration-id.pdf"),
            "resume_document": make_file("registration-resume.pdf"),
        }

        response = self.client.post(
            "/api/v1/auth/register/b2c",
            registration_payload,
            format="multipart",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)

        user = User.objects.get(email=registration_payload["email"])
        self.assertEqual(user.role, Roles.B2C)
        self.assertFalse(user.is_verified)
        self.assertTrue(user.email_verification_code)
        self.assertTrue(
            IndividualEmployerProfile.objects.filter(user=user, passport_id="REG-1001").exists()
        )

        login_before_verification = self.client.post(
            "/api/v1/auth/login",
            {"email": user.email, "password": registration_payload["password"]},
            format="json",
        )
        self.assertEqual(login_before_verification.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertIn("verification", str(login_before_verification.data).lower())

        verify_response = self.client.post(
            "/api/v1/auth/verify-email",
            {"email": user.email, "code": user.email_verification_code},
            format="json",
        )
        self.assertEqual(verify_response.status_code, status.HTTP_200_OK, verify_response.data)

        login_response = self.client.post(
            "/api/v1/auth/login",
            {"email": user.email, "password": registration_payload["password"]},
            format="json",
        )
        self.assertEqual(login_response.status_code, status.HTTP_200_OK, login_response.data)
        self.assertEqual(login_response.data["role"], Roles.B2C)
        self.assertTrue(login_response.data["is_verified"])
        self.assertIn("access", login_response.data)
        self.assertIn("refresh", login_response.data)

        refresh_response = self.client.post(
            "/api/v1/auth/refresh",
            {"refresh": login_response.data["refresh"]},
            format="json",
        )
        self.assertEqual(refresh_response.status_code, status.HTTP_200_OK, refresh_response.data)
        self.assertIn("access", refresh_response.data)

    def test_b2c_registration_rejects_duplicate_passport_id_without_creating_user(self):
        existing_user = User.objects.create_user(
            email="existing-b2c@example.com",
            password="Password123!",
            first_name="Existing",
            last_name="User",
            role=Roles.B2C,
            is_verified=False,
        )
        IndividualEmployerProfile.objects.create(
            user=existing_user,
            passport_id="DUPLICATE-PASS-ID",
            phone_number="+15550000001",
            job_role=JobRoles.SOFTWARE_ENGINEER,
            nationality=Nationalities.US,
            preferred_language=Languages.ENGLISH,
            id_document=make_file("existing-id.pdf"),
            resume_document=make_file("existing-resume.pdf"),
        )

        response = self.client.post(
            "/api/v1/auth/register/b2c",
            {
                "email": "new-b2c-duplicate@example.com",
                "first_name": "New",
                "last_name": "Candidate",
                "password": "Password123!",
                "confirm_password": "Password123!",
                "passport_id": "DUPLICATE-PASS-ID",
                "job_role": JobRoles.SOFTWARE_ENGINEER,
                "nationality": Nationalities.US,
                "preferred_language": Languages.ENGLISH,
                "phone_number": "+15551112222",
                "id_document": make_file("duplicate-id.pdf"),
                "resume_document": make_file("duplicate-resume.pdf"),
            },
            format="multipart",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, response.data)
        self.assertIn("passport_id", response.data)
        self.assertFalse(User.objects.filter(email="new-b2c-duplicate@example.com").exists())

    def test_b2b_registration_rejects_duplicate_company_registration_number_without_creating_user(self):
        existing_user = User.objects.create_user(
            email="existing-b2b@example.com",
            password="Password123!",
            first_name="Existing",
            last_name="Admin",
            role=Roles.B2B,
            is_verified=False,
        )
        existing_profile = CompanyEmployerProfile.objects.create(
            user=existing_user,
            company_name="Existing Company",
            company_registration_number="COMP-REG-001",
            company_size="1-10",
            industry="Tech",
            phone_number="+15550000002",
            country="Ethiopia",
            city="Addis Ababa",
            address="Bole",
            website="https://existing.example.com",
            preferred_language=Languages.ENGLISH,
            registration_certificate=make_file("existing-company-cert.pdf"),
            resachetified_license=make_file("existing-company-license.pdf"),
        )
        company = Company.objects.create(
            name=existing_profile.company_name,
            registration_number=existing_profile.company_registration_number,
            company_size=existing_profile.company_size,
            industry=existing_profile.industry,
            phone_number=existing_profile.phone_number,
            country=existing_profile.country,
            city=existing_profile.city,
            address=existing_profile.address,
            website=existing_profile.website,
            admin_user=existing_user,
            registration_certificate=existing_profile.registration_certificate,
            is_verified=False,
        )
        existing_profile.company = company
        existing_profile.save()

        response = self.client.post(
            "/api/v1/auth/register/b2b",
            {
                "email": "new-b2b-duplicate@example.com",
                "first_name": "New",
                "last_name": "Company",
                "password": "Password123!",
                "confirm_password": "Password123!",
                "company_name": "New Company",
                "company_registration_number": "COMP-REG-001",
                "company_size": "1-10",
                "country": "Ethiopia",
                "city": "Addis Ababa",
                "preferred_language": Languages.ENGLISH,
                "phone_number": "+15553334444",
                "website": "https://new.example.com",
                "industry": "Services",
                "address": "Kazanchis",
                "registration_certificate": make_file("new-company-cert.pdf"),
                "resachetified_license": make_file("new-company-license.pdf"),
            },
            format="multipart",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, response.data)
        self.assertIn("company_registration_number", response.data)
        self.assertFalse(User.objects.filter(email="new-b2b-duplicate@example.com").exists())

    def test_b2b_registration_resend_verification_and_login_flow(self):
        registration_payload = {
            "email": "company-admin@example.com",
            "first_name": "Company",
            "last_name": "Admin",
            "password": "Password123!",
            "confirm_password": "Password123!",
            "company_name": "Acme Staffing",
            "company_registration_number": "ACME-REG-100",
            "company_size": "1-10",
            "country": "Ethiopia",
            "city": "Addis Ababa",
            "preferred_language": Languages.ENGLISH,
            "phone_number": "+251911223344",
            "website": "https://acme.example.com",
            "industry": "Recruitment",
            "address": "Bole",
            "registration_certificate": make_file("acme-cert.pdf"),
            "resachetified_license": make_file("acme-license.pdf"),
            "tax_id_document": make_file("acme-tax.pdf"),
        }

        response = self.client.post(
            "/api/v1/auth/register/b2b",
            registration_payload,
            format="multipart",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)

        user = User.objects.get(email=registration_payload["email"])
        profile = CompanyEmployerProfile.objects.get(user=user)
        company = Company.objects.get(admin_user=user)
        self.assertEqual(user.role, Roles.B2B)
        self.assertFalse(user.is_verified)
        self.assertEqual(profile.company, company)
        self.assertEqual(company.registration_number, registration_payload["company_registration_number"])

        original_code = user.email_verification_code
        resend_response = self.client.post(
            "/api/v1/auth/resend-verification",
            {"email": user.email},
            format="json",
        )
        self.assertEqual(resend_response.status_code, status.HTTP_200_OK, resend_response.data)

        user.refresh_from_db()
        self.assertTrue(user.email_verification_code)
        self.assertNotEqual(user.email_verification_code, original_code)

        verify_response = self.client.post(
            "/api/v1/auth/verify-email",
            {"email": user.email, "code": user.email_verification_code},
            format="json",
        )
        self.assertEqual(verify_response.status_code, status.HTTP_200_OK, verify_response.data)

        login_response = self.client.post(
            "/api/v1/auth/login",
            {"email": user.email, "password": registration_payload["password"]},
            format="json",
        )
        self.assertEqual(login_response.status_code, status.HTTP_200_OK, login_response.data)
        self.assertEqual(login_response.data["role"], Roles.B2B)
        self.assertTrue(login_response.data["is_verified"])
        self.assertIn("access", login_response.data)
        self.assertIn("refresh", login_response.data)

    def test_resend_verification_unknown_email_returns_generic_success_message(self):
        response = self.client.post(
            "/api/v1/auth/resend-verification",
            {"email": "missing@example.com"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertIn("message", response.data)

    def test_b2b_profile_me_patch_and_document_upload_work_after_login(self):
        user = User.objects.create_user(
            email="b2b-profile@example.com",
            password="Password123!",
            first_name="B2B",
            last_name="Owner",
            role=Roles.B2B,
            is_verified=True,
        )
        company = Company.objects.create(
            name="Profile Co",
            registration_number="PROFILE-CO-1",
            company_size="1-10",
            industry="Technology",
            phone_number="+15550001111",
            country="Ethiopia",
            city="Addis Ababa",
            address="Kazanchis",
            website="https://profile.example.com",
            admin_user=user,
            registration_certificate=make_file("profile-company-cert.pdf"),
            is_verified=False,
        )
        CompanyEmployerProfile.objects.create(
            user=user,
            company_name=company.name,
            company_registration_number=company.registration_number,
            company_size=company.company_size,
            industry=company.industry,
            phone_number=company.phone_number,
            country=company.country,
            city=company.city,
            address=company.address,
            website=company.website,
            preferred_language=Languages.ENGLISH,
            registration_certificate=make_file("profile-employer-cert.pdf"),
            resachetified_license=make_file("profile-license.pdf"),
            company=company,
        )

        self.authenticate(user.email, "Password123!")

        get_response = self.client.get("/api/v1/auth/me")
        self.assertEqual(get_response.status_code, status.HTTP_200_OK, get_response.data)
        self.assertEqual(get_response.data["email"], user.email)

        patch_response = self.client.patch(
            "/api/v1/auth/me",
            {
                "company_name": "Profile Co Updated",
                "phone_number": "+15550002222",
                "city": "Hawassa",
                "website": "https://updated.example.com",
            },
            format="json",
        )
        self.assertEqual(patch_response.status_code, status.HTTP_200_OK, patch_response.data)
        self.assertEqual(patch_response.data["company_name"], "Profile Co Updated")
        self.assertEqual(patch_response.data["city"], "Hawassa")

        user.refresh_from_db()
        user.company_profile.refresh_from_db()
        self.assertEqual(user.company_profile.company_name, "Profile Co Updated")
        self.assertEqual(user.company_profile.phone_number, "+15550002222")
        self.assertEqual(user.company_profile.city, "Hawassa")

        upload_response = self.client.post(
            "/api/v1/auth/documents/upload",
            {
                "document_type": "tax",
                "document": make_file("updated-tax.pdf"),
            },
            format="multipart",
        )
        self.assertEqual(upload_response.status_code, status.HTTP_200_OK, upload_response.data)
        self.assertEqual(upload_response.data["message"], "tax uploaded successfully")

        user.company_profile.refresh_from_db()
        self.assertTrue(bool(user.company_profile.tax_id_document))

    def test_password_reset_validate_reset_and_change_password_flow(self):
        user = self.create_verified_b2c_user()

        forgot_response = self.client.post(
            "/api/v1/auth/forgot-password",
            {"email": user.email},
            format="json",
        )
        self.assertEqual(forgot_response.status_code, status.HTTP_200_OK, forgot_response.data)

        user.refresh_from_db()
        self.assertTrue(user.password_reset_token)

        validate_response = self.client.post(
            "/api/v1/auth/validate-reset-token",
            {"token": user.password_reset_token},
            format="json",
        )
        self.assertEqual(validate_response.status_code, status.HTTP_200_OK, validate_response.data)
        self.assertTrue(validate_response.data["valid"])

        reset_response = self.client.post(
            "/api/v1/auth/reset-password",
            {
                "token": user.password_reset_token,
                "password": "NewPassword123!",
                "confirm_password": "NewPassword123!",
            },
            format="json",
        )
        self.assertEqual(reset_response.status_code, status.HTTP_200_OK, reset_response.data)

        invalidated_response = self.client.post(
            "/api/v1/auth/validate-reset-token",
            {"token": user.password_reset_token},
            format="json",
        )
        self.assertEqual(invalidated_response.status_code, status.HTTP_400_BAD_REQUEST)

        login_after_reset = self.client.post(
            "/api/v1/auth/login",
            {"email": user.email, "password": "NewPassword123!"},
            format="json",
        )
        self.assertEqual(login_after_reset.status_code, status.HTTP_200_OK, login_after_reset.data)

        self.client.credentials(
            HTTP_AUTHORIZATION=f"Bearer {login_after_reset.data['access']}"
        )
        change_response = self.client.post(
            "/api/v1/auth/change-password",
            {
                "current_password": "NewPassword123!",
                "new_password": "NewestPassword123!",
                "confirm_new_password": "NewestPassword123!",
            },
            format="json",
        )
        self.assertEqual(change_response.status_code, status.HTTP_200_OK, change_response.data)

        relogin_response = self.client.post(
            "/api/v1/auth/login",
            {"email": user.email, "password": "NewestPassword123!"},
            format="json",
        )
        self.assertEqual(relogin_response.status_code, status.HTTP_200_OK, relogin_response.data)

    def test_profile_me_get_and_patch_updates_user_and_profile_fields(self):
        user = self.create_verified_b2c_user(email="profile@example.com")
        self.authenticate(user.email, "Password123!")

        get_response = self.client.get("/api/v1/auth/me")
        self.assertEqual(get_response.status_code, status.HTTP_200_OK, get_response.data)
        self.assertEqual(get_response.data["email"], user.email)

        patch_response = self.client.patch(
            "/api/v1/auth/me",
            {
                "first_name": "Updated",
                "last_name": "Name",
                "phone_number": "+15559990000",
                "address": "Updated Address",
            },
            format="json",
        )
        self.assertEqual(patch_response.status_code, status.HTTP_200_OK, patch_response.data)

        user.refresh_from_db()
        user.individual_profile.refresh_from_db()
        self.assertEqual(user.first_name, "Updated")
        self.assertEqual(user.last_name, "Name")
        self.assertEqual(user.individual_profile.phone_number, "+15559990000")
        self.assertEqual(user.individual_profile.address, "Updated Address")

    def test_document_verification_admin_permission_uses_admin_permissions_json(self):
        target_user = self.create_verified_b2c_user(email="pending-docs@example.com")
        target_user.documents_verification_status = "PENDING"
        target_user.save(update_fields=["documents_verification_status"])

        admin_without_permission = User.objects.create_user(
            email="limited-admin@example.com",
            password="Password123!",
            first_name="Limited",
            last_name="Admin",
            role=Roles.ADMIN,
            is_verified=True,
            is_staff=True,
            admin_permissions=[],
        )
        limited_login = self.authenticate(admin_without_permission.email, "Password123!")
        self.assertEqual(limited_login.data["role"], Roles.ADMIN)

        denied_response = self.client.get("/api/v1/auth/admin/employers/pending-verification")
        self.assertEqual(denied_response.status_code, status.HTTP_403_FORBIDDEN)

        self.client.credentials()

        admin_with_permission = User.objects.create_user(
            email="review-admin@example.com",
            password="Password123!",
            first_name="Review",
            last_name="Admin",
            role=Roles.ADMIN,
            is_verified=True,
            is_staff=True,
            admin_permissions=[AdminPermissions.DOCUMENT_VERIFICATION],
        )
        self.authenticate(admin_with_permission.email, "Password123!")

        allowed_response = self.client.get("/api/v1/auth/admin/employers/pending-verification")
        self.assertEqual(allowed_response.status_code, status.HTTP_200_OK, allowed_response.data)

    def test_profile_picture_upload_replace_and_remove(self):
        user = self.create_verified_b2c_user()
        self.authenticate(user.email, "Password123!")

        # 1x1 transparent PNG - ImageField validates real decodable image
        # bytes via Pillow, not just an extension, so a fake payload would
        # fail even with a ".png" filename.
        png_bytes = (
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
            b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
            b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
        )

        get_response = self.client.get("/api/v1/auth/me")
        self.assertIsNone(get_response.data["profile_picture"])

        upload_response = self.client.post(
            "/api/v1/auth/me/profile-picture",
            {"profile_picture": make_file("avatar.png", png_bytes, "image/png")},
            format="multipart",
        )
        self.assertEqual(upload_response.status_code, status.HTTP_200_OK, upload_response.data)
        self.assertIn("avatar", upload_response.data["profile_picture"])

        user.refresh_from_db()
        self.assertTrue(bool(user.profile_picture))
        first_name = user.profile_picture.name

        get_after_upload = self.client.get("/api/v1/auth/me")
        self.assertIsNotNone(get_after_upload.data["profile_picture"])

        # Uploading again replaces the old file rather than accumulating one
        # per upload.
        second_upload = self.client.post(
            "/api/v1/auth/me/profile-picture",
            {"profile_picture": make_file("avatar2.png", png_bytes, "image/png")},
            format="multipart",
        )
        self.assertEqual(second_upload.status_code, status.HTTP_200_OK, second_upload.data)
        user.refresh_from_db()
        self.assertNotEqual(user.profile_picture.name, first_name)

        delete_response = self.client.delete("/api/v1/auth/me/profile-picture")
        self.assertEqual(delete_response.status_code, status.HTTP_200_OK, delete_response.data)
        user.refresh_from_db()
        self.assertFalse(bool(user.profile_picture))

    def test_profile_picture_rejects_unsupported_extension(self):
        user = self.create_verified_b2c_user()
        self.authenticate(user.email, "Password123!")

        response = self.client.post(
            "/api/v1/auth/me/profile-picture",
            {"profile_picture": make_file("resume.pdf", b"not-an-image", "application/pdf")},
            format="multipart",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, response.data)

        user.refresh_from_db()
        self.assertFalse(bool(user.profile_picture))

    def test_company_logo_upload_by_owner(self):
        user, company = self.create_verified_b2b_owner()
        self.authenticate(user.email, "Password123!")

        png_bytes = (
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
            b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
            b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
        )

        get_response = self.client.get("/api/v1/auth/companies/profile")
        self.assertEqual(get_response.status_code, status.HTTP_200_OK, get_response.data)
        self.assertIsNone(get_response.data["logo"])

        upload_response = self.client.patch(
            "/api/v1/auth/companies/profile",
            {"logo": make_file("logo.png", png_bytes, "image/png")},
            format="multipart",
        )
        self.assertEqual(upload_response.status_code, status.HTTP_200_OK, upload_response.data)
        self.assertIn("logo", upload_response.data["logo"])

        company.refresh_from_db()
        self.assertTrue(bool(company.logo))

        get_after_upload = self.client.get("/api/v1/auth/companies/profile")
        self.assertIsNotNone(get_after_upload.data["logo"])

    def test_company_logo_upload_rejected_for_team_member(self):
        _owner, company = self.create_verified_b2b_owner()
        team_member = User.objects.create_user(
            email="teammate@example.com",
            password="Password123!",
            first_name="Team",
            last_name="Member",
            role=Roles.B2B_TEAM_MEMBER,
            is_verified=True,
            company=company,
        )
        self.authenticate(team_member.email, "Password123!")

        response = self.client.patch(
            "/api/v1/auth/companies/profile",
            {"logo": make_file("logo.png", b"fake-bytes", "image/png")},
            format="multipart",
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN, response.data)

        company.refresh_from_db()
        self.assertFalse(bool(company.logo))

    def test_delete_account_rejects_wrong_password_without_changing_anything(self):
        user = self.create_verified_b2c_user()
        self.authenticate(user.email, "Password123!")

        response = self.client.post(
            "/api/v1/auth/me/delete",
            {"password": "WrongPassword!"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, response.data)

        user.refresh_from_db()
        self.assertTrue(user.is_active)

    def test_delete_account_rejects_non_b2c_role(self):
        user = User.objects.create_user(
            email="admin-delete@example.com",
            password="Password123!",
            first_name="Admin",
            last_name="User",
            role=Roles.ADMIN,
            is_verified=True,
            is_staff=True,
        )
        self.authenticate(user.email, "Password123!")

        response = self.client.post(
            "/api/v1/auth/me/delete",
            {"password": "Password123!"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, response.data)

        user.refresh_from_db()
        self.assertTrue(user.is_active)

    @patch("stripe.Subscription.modify")
    def test_delete_account_cancels_subscription_deactivates_user_and_blocks_reuse(self, mock_modify):
        user = self.create_verified_b2c_user()

        customer = Customer.objects.create(
            user=user,
            stripe_customer_id=f"cus_{user.id}",
            email=user.email,
        )
        subscription = Subscription.objects.create(
            user=user,
            customer=customer,
            stripe_subscription_id=f"sub_{user.id}",
            status=SubscriptionStatus.ACTIVE,
            current_period_start=timezone.now(),
            current_period_end=timezone.now() + timezone.timedelta(days=30),
        )

        login = self.authenticate(user.email, "Password123!")
        access_token = login.data["access"]

        response = self.client.post(
            "/api/v1/auth/me/delete",
            {"password": "Password123!"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        mock_modify.assert_called_once()

        user.refresh_from_db()
        self.assertFalse(user.is_active)

        user.individual_profile.refresh_from_db()
        self.assertTrue(user.individual_profile.is_deleted)

        subscription.refresh_from_db()
        self.assertEqual(subscription.status, SubscriptionStatus.CANCELED)

        # Login is blocked going forward...
        login_after_delete = self.client.post(
            "/api/v1/auth/login",
            {"email": user.email, "password": "Password123!"},
            format="json",
        )
        self.assertEqual(login_after_delete.status_code, status.HTTP_401_UNAUTHORIZED)

        # ...and the access token issued before deletion stops working too,
        # since SimpleJWT re-checks is_active from the DB on every request.
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {access_token}")
        stale_token_response = self.client.get("/api/v1/auth/me")
        self.assertEqual(stale_token_response.status_code, status.HTTP_401_UNAUTHORIZED)
