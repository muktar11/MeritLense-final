from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone
from unittest.mock import patch
from rest_framework import status
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.test import APIClient, APIRequestFactory
from rest_framework_simplejwt.tokens import AccessToken

from api.accounts.models import Company, CompanyDocumentRequest, CompanyEmployerProfile, TeamMemberProfile, User
from api.core.constants import CompanySize, Languages, Roles
from api.live_calls.auth import OptionalJWTAuthentication


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class B2BDocumentWorkflowTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            email="workflow-owner@example.com",
            password="Password123!",
            first_name="Workflow",
            last_name="Owner",
            role=Roles.B2B,
            is_verified=True,
        )
        self.company = Company.objects.create(
            name="Workflow Co",
            registration_number="WORKFLOW-001",
            company_size=CompanySize.SMALL,
            phone_number="+15550000001",
            country="United States",
            city="San Francisco",
            admin_user=self.owner,
        )
        self.profile = CompanyEmployerProfile.objects.create(
            user=self.owner,
            company_name=self.company.name,
            company_registration_number=self.company.registration_number,
            company_size=self.company.company_size,
            country=self.company.country,
            city=self.company.city,
            preferred_language=Languages.ENGLISH,
            phone_number=self.company.phone_number,
            company=self.company,
        )
        self.client = APIClient()
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {AccessToken.for_user(self.owner)}")

    def tearDown(self):
        if self.profile.resachetified_license:
            self.profile.resachetified_license.delete(save=False)
        for document_request in CompanyDocumentRequest.objects.filter(company=self.company):
            if document_request.document:
                document_request.document.delete(save=False)

    def test_unapproved_company_is_locked_except_profile_and_license_upload(self):
        blocked = self.client.get("/api/v1/auth/companies/team")
        self.assertNotIn(blocked.status_code, (status.HTTP_200_OK, status.HTTP_404_NOT_FOUND))

        profile = self.client.get("/api/v1/auth/me")
        self.assertEqual(profile.status_code, status.HTTP_200_OK, profile.data)
        self.assertFalse(profile.data["trade_license_uploaded"])
        self.assertFalse(profile.data["company_is_verified"])
        self.assertEqual(profile.data["documents_verification_status"], "PENDING")

        company_profile = self.client.get("/api/v1/auth/companies/profile")
        self.assertEqual(company_profile.status_code, status.HTTP_200_OK, company_profile.data)
        blocked_document_patch = self.client.patch(
            "/api/v1/auth/me",
            {"tax_id_document": SimpleUploadedFile("tax.pdf", b"tax")},
            format="multipart",
        )
        self.assertEqual(blocked_document_patch.status_code, status.HTTP_403_FORBIDDEN)

    def test_legacy_approval_without_license_is_not_reported_as_license_approved(self):
        self.company.is_verified = True
        self.company.save(update_fields=["is_verified"])

        profile_response = self.client.get("/api/v1/auth/me")

        self.assertEqual(profile_response.status_code, status.HTTP_200_OK, profile_response.data)
        self.assertFalse(profile_response.data["company_is_verified"])
        self.assertFalse(profile_response.data["trade_license_uploaded"])

        team_user = User.objects.create_user(
            email="legacy-status-staff@example.com",
            password="TestPassword123!",
            first_name="Legacy",
            last_name="Staff",
            role=Roles.B2B_TEAM_MEMBER,
            is_verified=True,
        )
        TeamMemberProfile.objects.create(
            user=team_user,
            company=self.company,
            job_title="Recruiter",
            phone_number="+15550000004",
            permissions=[],
        )
        team_client = APIClient()
        team_client.credentials(HTTP_AUTHORIZATION=f"Bearer {AccessToken.for_user(team_user)}")

        team_response = team_client.get("/api/v1/auth/me")

        self.assertEqual(team_response.status_code, status.HTTP_200_OK, team_response.data)
        self.assertFalse(team_response.data["company_is_verified"])
        self.assertFalse(team_response.data["trade_license_uploaded"])

        locked = self.client.get("/api/v1/auth/companies/team")
        self.assertNotIn(locked.status_code, (status.HTTP_200_OK, status.HTTP_404_NOT_FOUND))

    def test_optional_live_call_authentication_cannot_bypass_company_lockout(self):
        request = APIRequestFactory().post("/api/live-calls/session/join")
        request.META["HTTP_AUTHORIZATION"] = f"Bearer {AccessToken.for_user(self.owner)}"
        with self.assertRaises(AuthenticationFailed) as error:
            OptionalJWTAuthentication().authenticate(request)
        self.assertEqual(error.exception.get_codes(), "company_verification_required")

    @patch("api.accounts.views.notify_superadmins")
    @patch("api.accounts.views.send_verification_email")
    def test_b2b_signup_does_not_require_a_trade_license(self, send_verification_email, notify_superadmins):
        response = APIClient().post(
            "/api/v1/auth/register/b2b",
            {
                "email": "new-workflow-company@example.com",
                "first_name": "New",
                "last_name": "Company",
                "password": "Password123!",
                "confirm_password": "Password123!",
                "company_name": "New Workflow Company",
                "company_registration_number": "WORKFLOW-NEW-001",
                "company_size": CompanySize.SMALL,
                "country": "United States",
                "city": "San Francisco",
                "preferred_language": Languages.ENGLISH,
                "phone_number": "+15550000002",
                "registration_certificate": SimpleUploadedFile("registration.pdf", b"registration"),
            },
            format="multipart",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        owner = User.objects.get(email="new-workflow-company@example.com")
        self.assertFalse(owner.company_profile.resachetified_license)
        self.assertFalse(owner.managed_company.is_verified)
        send_verification_email.assert_called_once()
        notify_superadmins.assert_called_once()

    def test_license_upload_resets_approval_and_sends_receipt_email(self):
        self.company.is_verified = True
        self.company.save(update_fields=["is_verified"])
        self.owner.documents_verified = True
        self.owner.documents_verification_status = "APPROVED"
        self.owner.save(update_fields=["documents_verified", "documents_verification_status"])

        response = self.client.post(
            "/api/v1/auth/documents/upload",
            {
                "document_type": "license",
                "document": SimpleUploadedFile("trade-license.pdf", b"license"),
            },
            format="multipart",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.company.refresh_from_db()
        self.owner.refresh_from_db()
        self.profile.refresh_from_db()
        self.assertFalse(self.company.is_verified)
        self.assertFalse(self.owner.documents_verified)
        self.assertEqual(self.owner.documents_verification_status, "PENDING")
        self.assertTrue(self.profile.resachetified_license)
        self.assertTrue(any("received" in item.subject.lower() for item in mail.outbox))

    def test_requested_document_can_only_be_uploaded_once_while_pending(self):
        admin = User.objects.create_user(
            email="workflow-admin@example.com",
            password="Password123!",
            role=Roles.SUPERADMIN,
            is_verified=True,
            is_staff=True,
        )
        admin_client = APIClient()
        admin_client.force_authenticate(admin)
        create_response = admin_client.post(
            "/api/v1/auth/admin/employers/request-document",
            {"user_id": str(self.owner.public_id), "name": "Proof of address"},
            format="json",
        )
        self.assertEqual(create_response.status_code, status.HTTP_201_CREATED, create_response.data)
        document_request = CompanyDocumentRequest.objects.get(company=self.company)
        self.assertTrue(any("Proof of address" in item.subject for item in mail.outbox))

        listed = self.client.get("/api/v1/auth/companies/document-requests")
        self.assertEqual(listed.status_code, status.HTTP_200_OK, listed.data)
        self.assertEqual(listed.data["results"][0]["status"], CompanyDocumentRequest.PENDING)
        self.assertEqual(listed.data["results"][0]["name"], "Proof of address")
        self.assertIn("requested_at", listed.data["results"][0])

        upload_data = {
            "document": SimpleUploadedFile("address.pdf", b"proof"),
        }
        upload_response = self.client.post(
            f"/api/v1/auth/companies/document-requests/{document_request.id}/upload",
            upload_data,
            format="multipart",
        )
        self.assertEqual(upload_response.status_code, status.HTTP_200_OK, upload_response.data)
        document_request.refresh_from_db()
        self.assertEqual(document_request.status, CompanyDocumentRequest.UPLOADED)
        self.assertIsNotNone(document_request.uploaded_at)

        duplicate = self.client.post(
            f"/api/v1/auth/companies/document-requests/{document_request.id}/upload",
            {"document": SimpleUploadedFile("address-again.pdf", b"proof")},
            format="multipart",
        )
        self.assertEqual(duplicate.status_code, status.HTTP_400_BAD_REQUEST)

        admin_list = admin_client.get(
            f"/api/v1/auth/admin/employers/{self.owner.public_id}/document-requests"
        )
        self.assertEqual(admin_list.status_code, status.HTTP_200_OK, admin_list.data)
        self.assertTrue(admin_list.data["results"][0]["document"])

    def test_employer_list_contains_b2b_document_urls_and_requested_documents(self):
        admin = User.objects.create_user(
            email="workflow-list-admin@example.com",
            password="Password123!",
            role=Roles.SUPERADMIN,
            is_verified=True,
            is_staff=True,
        )
        requested = CompanyDocumentRequest.objects.create(
            company=self.company,
            requested_by=admin,
            document_name="Proof of address",
            status=CompanyDocumentRequest.UPLOADED,
            document="b2b/documents/requested/proof.pdf",
            uploaded_at=timezone.now(),
        )
        self.profile.registration_certificate = "b2b/documents/registration/cert.pdf"
        self.profile.resachetified_license = "b2b/documents/license/license.pdf"
        self.profile.save(update_fields=["registration_certificate", "resachetified_license"])

        admin_client = APIClient()
        admin_client.force_authenticate(admin)
        response = admin_client.get("/api/v1/auth/admin/employers")
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        owner_data = next(row for row in response.data["results"] if row["email"] == self.owner.email)
        self.assertEqual(
            owner_data["documents"]["resachetified_license"],
            "http://testserver/media/b2b/documents/license/license.pdf",
        )
        self.assertEqual(len(owner_data["requested_documents"]), 1)
        self.assertEqual(owner_data["requested_documents"][0]["id"], requested.id)
        self.assertEqual(owner_data["requested_documents"][0]["name"], "Proof of address")
        self.assertEqual(
            owner_data["requested_documents"][0]["document_url"],
            "http://testserver/media/b2b/documents/requested/proof.pdf",
        )
        b2c_user = User.objects.create_user(
            email="workflow-b2c@example.com",
            password="Password123!",
            role=Roles.B2C,
            is_verified=True,
        )
        b2c_response = admin_client.get("/api/v1/auth/admin/employers?role=B2C")
        b2c_data = next(row for row in b2c_response.data["results"] if row["email"] == b2c_user.email)
        self.assertNotIn("documents", b2c_data)
        self.assertNotIn("requested_documents", b2c_data)

    def test_unapproved_staff_can_use_only_profile_and_requested_document_workflow(self):
        staff = User.objects.create_user(
            email="workflow-staff@example.com",
            password="Password123!",
            first_name="Workflow",
            last_name="Staff",
            role=Roles.B2B_TEAM_MEMBER,
            is_verified=True,
        )
        TeamMemberProfile.objects.create(
            user=staff,
            company=self.company,
            job_title="Recruiter",
            phone_number="+15550000003",
            permissions=[],
        )
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {AccessToken.for_user(staff)}")
        profile = client.get("/api/v1/auth/me")
        self.assertEqual(profile.status_code, status.HTTP_200_OK, profile.data)
        self.assertFalse(profile.data["company_is_verified"])
        self.assertEqual(client.get("/api/v1/auth/companies/document-requests").status_code,
                         status.HTTP_200_OK)
        blocked = client.get("/api/v1/auth/companies/team")
        self.assertNotIn(blocked.status_code, (status.HTTP_200_OK, status.HTTP_404_NOT_FOUND))
        requested_document = CompanyDocumentRequest.objects.create(
            company=self.company,
            document_name="Staff-uploaded supporting file",
        )
        staff_upload = client.post(
            f"/api/v1/auth/companies/document-requests/{requested_document.id}/upload",
            {"document": SimpleUploadedFile("supporting-file.pdf", b"supporting file")},
            format="multipart",
        )
        self.assertEqual(staff_upload.status_code, status.HTTP_200_OK, staff_upload.data)
        requested_document.refresh_from_db()
        self.assertEqual(requested_document.status, CompanyDocumentRequest.UPLOADED)

    def test_admin_cannot_approve_company_without_trade_license(self):
        admin = User.objects.create_user(
            email="workflow-reviewer@example.com",
            password="Password123!",
            role=Roles.SUPERADMIN,
            is_verified=True,
            is_staff=True,
        )
        client = APIClient()
        client.force_authenticate(admin)
        response = client.post(
            "/api/v1/auth/admin/employers/verify-documents",
            {
                "user_id": str(self.owner.public_id),
                "status": "APPROVED",
                "verification_notes": "Reviewed",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, response.data)
        self.company.refresh_from_db()
        self.assertFalse(self.company.is_verified)
