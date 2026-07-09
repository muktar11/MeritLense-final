import shutil
import tempfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.utils import timezone
from drf_spectacular.generators import SchemaGenerator
from rest_framework import status
from rest_framework.test import APITestCase

from api.accounts.models import Company, CompanyEmployerProfile, TeamMemberProfile, User
from api.candidates.models import Candidate
from api.core.constants import CompanySize, CompanyTeamPermissions, Languages, Roles, SubscriptionStatus, candidateJobRoles
from api.payments.models import Customer, Price, Subscription


def make_file(name="document.pdf", content=b"candidate-file", content_type="application/pdf"):
    return SimpleUploadedFile(name, content, content_type=content_type)


class CandidatesWeek2Tests(APITestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._media_dir = tempfile.mkdtemp(prefix="candidate-tests-")
        cls._override = override_settings(MEDIA_ROOT=cls._media_dir)
        cls._override.enable()

    @classmethod
    def tearDownClass(cls):
        cls._override.disable()
        shutil.rmtree(cls._media_dir, ignore_errors=True)
        super().tearDownClass()

    def create_active_subscription(self, user, company=None, candidate_limit=50):
        suffix = f"{user.id}-{Subscription.objects.count()}"
        price = Price.objects.create(
            name=f"Candidate Test Plan {suffix}",
            stripe_price_id=f"price_candidate_test_{suffix}",
            stripe_product_id=f"prod_candidate_test_{suffix}",
            target_user_type="B2B" if company else "B2C",
            unit_amount=0,
            feature_limits={"candidate_limit": candidate_limit},
        )
        customer = Customer.objects.create(
            user=user,
            stripe_customer_id=f"cus_candidate_test_{suffix}",
            email=user.email,
            name=user.get_full_name(),
        )
        return Subscription.objects.create(
            user=user,
            company=company,
            customer=customer,
            stripe_subscription_id=f"sub_candidate_test_{suffix}",
            stripe_price=price,
            status=SubscriptionStatus.ACTIVE,
            current_period_start=timezone.now(),
            current_period_end=timezone.now() + timezone.timedelta(days=30),
            current_usage={"candidate_limit": 0},
        )

    def create_b2b_company(self, email, company_name, registration_number):
        user = User.objects.create_user(
            email=email,
            password="Password123!",
            first_name="Company",
            last_name="Admin",
            role=Roles.B2B,
            is_verified=True,
        )
        company = Company.objects.create(
            name=company_name,
            registration_number=registration_number,
            company_size=CompanySize.SMALL,
            industry="Technology",
            phone_number="+15550000000",
            country="US",
            city="New York",
            address="1 Company Way",
            website="https://example.com",
            admin_user=user,
            registration_certificate=make_file(f"{registration_number}-certificate.pdf"),
        )
        CompanyEmployerProfile.objects.create(
            user=user,
            company_name=company_name,
            company_registration_number=registration_number,
            company_size=CompanySize.SMALL,
            industry="Technology",
            phone_number="+15550000000",
            country="US",
            city="New York",
            address="1 Company Way",
            website="https://example.com",
            preferred_language=Languages.ENGLISH,
            registration_certificate=make_file(f"{registration_number}-profile-certificate.pdf"),
            resachetified_license=make_file(f"{registration_number}-license.pdf"),
            company=company,
        )
        self.create_active_subscription(user, company=company)
        return user, company

    def create_team_member(self, company, email):
        user = User.objects.create_user(
            email=email,
            password="Password123!",
            first_name="Team",
            last_name="Member",
            role=Roles.B2B_TEAM_MEMBER,
            is_verified=True,
            company=company,
        )
        profile = TeamMemberProfile.objects.create(
            user=user,
            company=company,
            job_title="Recruiter",
            department="Hiring",
            phone_number="+15557778888",
            permissions=[CompanyTeamPermissions.VIEW_CANDIDATES],
            invited_by=company.admin_user,
        )
        self.create_active_subscription(user)
        return user, profile

    def create_b2c_user(self, email):
        user = User.objects.create_user(
            email=email,
            password="Password123!",
            first_name="Solo",
            last_name="Employer",
            role=Roles.B2C,
            is_verified=True,
        )
        self.create_active_subscription(user)
        return user

    def authenticate(self, user, password="Password123!"):
        response = self.client.post(
            "/api/v1/auth/login",
            {"email": user.email, "password": password},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {response.data['access']}")
        return response

    def create_candidate(self, user, **overrides):
        defaults = {
            "first_name": "Jane",
            "last_name": "Candidate",
            "email": f"candidate-{User.objects.count()}@example.com",
            "passport_id": f"PASS-{Candidate.objects.count() + 1000}",
            "job_role": candidateJobRoles.NANNY,
            "core_skills": "communication, patience",
            "preferred_language": Languages.ENGLISH,
            "passport_document": make_file(f"passport-{Candidate.objects.count() + 1}.pdf"),
        }
        defaults.update(overrides)
        self.authenticate(user)
        response = self.client.post(
            "/api/v1/candidates/candidates",
            defaults,
            format="multipart",
        )
        self.client.credentials()
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        return Candidate.objects.get(email=defaults["email"])

    def test_b2b_candidate_creation_is_company_scoped_and_duplicate_email_is_rejected(self):
        company_admin, company = self.create_b2b_company(
            "company-admin@example.com", "Acme", "ACME-1"
        )
        teammate, _ = self.create_team_member(company, "teammate@example.com")
        other_admin, _ = self.create_b2b_company(
            "other-admin@example.com", "OtherCo", "OTHER-1"
        )

        self.authenticate(company_admin)
        create_response = self.client.post(
            "/api/v1/candidates/candidates",
            {
                "first_name": "Jane",
                "last_name": "Doe",
                "email": "shared-scope@example.com",
                "passport_id": "COMP-100",
                "job_role": candidateJobRoles.NANNY,
                "core_skills": "communication, patience",
                "preferred_language": Languages.ENGLISH,
                "passport_document": make_file("company-candidate.pdf"),
            },
            format="multipart",
        )
        self.assertEqual(create_response.status_code, status.HTTP_201_CREATED, create_response.data)

        self.client.credentials()
        self.authenticate(teammate)
        duplicate_response = self.client.post(
            "/api/v1/candidates/candidates",
            {
                "first_name": "John",
                "last_name": "Smith",
                "email": "shared-scope@example.com",
                "passport_id": "COMP-101",
                "job_role": candidateJobRoles.DRIVER,
                "core_skills": "driving",
                "preferred_language": Languages.ENGLISH,
                "passport_document": make_file("duplicate-company-candidate.pdf"),
            },
            format="multipart",
        )
        self.assertEqual(duplicate_response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("email", duplicate_response.data)

        self.client.credentials()
        self.authenticate(other_admin)
        other_company_response = self.client.post(
            "/api/v1/candidates/candidates",
            {
                "first_name": "Other",
                "last_name": "Company",
                "email": "shared-scope@example.com",
                "passport_id": "OTHER-101",
                "job_role": candidateJobRoles.DRIVER,
                "core_skills": "driving",
                "preferred_language": Languages.ENGLISH,
                "passport_document": make_file("other-company-candidate.pdf"),
            },
            format="multipart",
        )
        self.assertEqual(other_company_response.status_code, status.HTTP_201_CREATED, other_company_response.data)

    def test_b2c_candidate_list_and_detail_are_limited_to_owner(self):
        owner = self.create_b2c_user("owner@example.com")
        other_user = self.create_b2c_user("other@example.com")
        owner_candidate = self.create_candidate(owner, email="owner-candidate@example.com", passport_id="OWNER-1")
        other_candidate = self.create_candidate(other_user, email="other-candidate@example.com", passport_id="OWNER-2")

        self.authenticate(owner)
        list_response = self.client.get("/api/v1/candidates/candidates")
        self.assertEqual(list_response.status_code, status.HTTP_200_OK, list_response.data)
        self.assertEqual(len(list_response.data), 1)
        self.assertEqual(list_response.data[0]["id"], str(owner_candidate.public_id))

        forbidden_detail = self.client.get(
            f"/api/v1/candidates/candidates/{other_candidate.public_id}"
        )
        self.assertEqual(forbidden_detail.status_code, status.HTTP_404_NOT_FOUND)

    def test_share_and_unshare_control_team_member_access_and_edit_rights(self):
        company_admin, company = self.create_b2b_company(
            "sharing-admin@example.com", "ShareCo", "SHARE-1"
        )
        teammate, teammate_profile = self.create_team_member(company, "share-member@example.com")
        candidate = self.create_candidate(
            company_admin,
            email="share-target@example.com",
            passport_id="SHARE-100",
        )

        self.authenticate(company_admin)
        share_response = self.client.post(
            f"/api/v1/candidates/candidates/{candidate.public_id}/share",
            {"user_ids": [teammate_profile.id]},
            format="json",
        )
        self.assertEqual(share_response.status_code, status.HTTP_200_OK, share_response.data)

        self.client.credentials()
        self.authenticate(teammate)
        retrieve_response = self.client.get(
            f"/api/v1/candidates/candidates/{candidate.public_id}"
        )
        self.assertEqual(retrieve_response.status_code, status.HTTP_200_OK, retrieve_response.data)

        update_response = self.client.patch(
            f"/api/v1/candidates/candidates/{candidate.public_id}",
            {"first_name": "Blocked"},
            format="json",
        )
        self.assertEqual(update_response.status_code, status.HTTP_403_FORBIDDEN)

        self.client.credentials()
        self.authenticate(company_admin)
        unshare_response = self.client.post(
            f"/api/v1/candidates/candidates/{candidate.public_id}/unshare",
            {"user_ids": [teammate_profile.id]},
            format="json",
        )
        self.assertEqual(unshare_response.status_code, status.HTTP_200_OK, unshare_response.data)

        self.client.credentials()
        self.authenticate(teammate)
        missing_after_unshare = self.client.get(
            f"/api/v1/candidates/candidates/{candidate.public_id}"
        )
        self.assertEqual(missing_after_unshare.status_code, status.HTTP_404_NOT_FOUND)

    def test_share_rejects_team_members_from_other_companies(self):
        company_admin, company = self.create_b2b_company(
            "owner-admin@example.com", "OwnerCo", "OWNER-CO"
        )
        _, other_company = self.create_b2b_company(
            "outside-admin@example.com", "OutsideCo", "OUTSIDE-CO"
        )
        _, outside_profile = self.create_team_member(other_company, "outside-member@example.com")
        candidate = self.create_candidate(
            company_admin,
            email="cross-company@example.com",
            passport_id="CROSS-100",
        )

        self.authenticate(company_admin)
        share_response = self.client.post(
            f"/api/v1/candidates/candidates/{candidate.public_id}/share",
            {"user_ids": [outside_profile.id]},
            format="json",
        )
        self.assertEqual(share_response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("user_ids", share_response.data)

    def test_team_member_created_candidate_is_attached_to_company_and_visible_to_creator(self):
        company_admin, company = self.create_b2b_company(
            "creator-admin@example.com", "CreatorCo", "CREATOR-1"
        )
        teammate, _ = self.create_team_member(company, "creator-member@example.com")

        self.authenticate(teammate)
        create_response = self.client.post(
            "/api/v1/candidates/candidates",
            {
                "first_name": "Owned",
                "last_name": "ByTeamMember",
                "email": "team-owned@example.com",
                "passport_id": "TEAM-OWN-1",
                "job_role": candidateJobRoles.HOUSEKEEPER,
                "core_skills": "cleaning, organization",
                "preferred_language": Languages.ENGLISH,
                "passport_document": make_file("team-owned.pdf"),
            },
            format="multipart",
        )
        self.assertEqual(create_response.status_code, status.HTTP_201_CREATED, create_response.data)

        candidate = Candidate.objects.get(email="team-owned@example.com")
        self.assertEqual(candidate.company, company)
        self.assertEqual(candidate.created_by, teammate)
        self.assertIn(teammate, candidate.shared_with.all())

    def test_candidate_create_schema_uses_multipart_form_data(self):
        schema = SchemaGenerator().get_schema(request=None, public=True)
        operation = schema["paths"]["/api/v1/candidates/candidates"]["post"]
        content = operation["requestBody"]["content"]

        self.assertIn("multipart/form-data", content)
        self.assertNotIn("application/json", content)

    def test_candidate_detail_schema_uses_uuid_path_parameter(self):
        schema = SchemaGenerator().get_schema(request=None, public=True)
        operation = schema["paths"]["/api/v1/candidates/candidates/{id}"]["get"]
        parameter = next(param for param in operation["parameters"] if param["name"] == "id")

        self.assertEqual(parameter["schema"]["type"], "string")
        self.assertEqual(parameter["schema"]["pattern"], "^[0-9a-fA-F-]{36}$")
