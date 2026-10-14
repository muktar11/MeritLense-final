"""Company Profile redesign (14 Oct 2026): extra company fields, the
Business Address(es) list, and the Reviewer role's read-only
view_results permission."""
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from api.accounts.models import BusinessAddress, Company, CompanyEmployerProfile, TeamMemberProfile, User
from api.candidates.models import Candidate
from api.core.constants import CompanyTeamPermissions, EvaluationType, InterviewEvaluationTier, Roles
from api.evaluations.models import Evaluation
from api.interviews.models import InterviewConfiguration
from api.sessions.models import InterviewSession

P = CompanyTeamPermissions


class CompanyFixtureMixin:
    def make_company(self, suffix):
        owner = User.objects.create_user(
            email=f"owner-{suffix}@example.com", password="Password123!",
            first_name="Owner", last_name=suffix, role=Roles.B2B, is_verified=True,
        )
        company = Company.objects.create(
            name=f"Co {suffix}", registration_number=f"REG-{suffix}", company_size="1-10",
            phone_number="+971400000000", country="United Arab Emirates", city="Dubai",
            address="Office 1", admin_user=owner, business_license_verified=True,
        )
        CompanyEmployerProfile.objects.create(
            user=owner, company=company, company_name=company.name,
            company_registration_number=company.registration_number, company_size="1-10",
            phone_number=company.phone_number, country=company.country, city=company.city,
            resachetified_license=SimpleUploadedFile("license.pdf", b"%PDF-1.4", content_type="application/pdf"),
        )
        return owner, company

    def make_member(self, company, permissions, suffix):
        user = User.objects.create_user(
            email=f"member-{suffix}@example.com", password="Password123!",
            first_name="Member", last_name=suffix, role=Roles.B2B_TEAM_MEMBER, is_verified=True,
        )
        TeamMemberProfile.objects.create(
            user=user, company=company, job_title="reviewer",
            phone_number="+971400000001", permissions=permissions,
        )
        return user

    def make_evaluation(self, company, created_by, suffix):
        candidate = Candidate.objects.create(
            first_name="Cand", last_name=suffix, email=f"cand-{suffix}@example.com",
            passport_id=f"PP-{suffix}", job_role="NA", core_skills="care", preferred_language="EN",
            passport_document="candidates/documents/passport/test.pdf",
            created_by=created_by, company=company,
        )
        if created_by.role == Roles.B2B_TEAM_MEMBER:
            candidate.shared_with.add(created_by)
        config = InterviewConfiguration.objects.create(
            role_name="Housekeeper", role_code="domestic_worker", language="EN",
            evaluation_tier=InterviewEvaluationTier.FULL, duration_minutes=45, total_questions=1,
            allow_retries=True, max_retries=1, rubric_version="v2.0", question_set_version="v1.2",
        )
        session = InterviewSession.objects.create(
            candidate=candidate, organization=company, config=config,
            role_name=config.role_name, role_code=config.role_code, ui_language="EN",
            candidate_language="EN", tts_language_code="en-US", stt_language_code="en-US",
            total_questions=1, evaluation_tier=InterviewEvaluationTier.FULL,
            rubric_version="v2.0", question_set_version="v1.2",
            expires_at=InterviewSession.build_expiry(30), created_by=created_by,
        )
        evaluation = Evaluation.objects.create(
            session=session, candidate=candidate, evaluation_type=EvaluationType.INTERVIEW,
            scheduled_date=timezone.now() + timezone.timedelta(days=1), duration_minutes=45,
            created_by=created_by, company=company,
        )
        return session, evaluation


class CompanyProfileFieldsTests(CompanyFixtureMixin, TestCase):
    def setUp(self):
        self.client = APIClient()
        self.owner, self.company = self.make_company("fields")
        self.client.force_authenticate(self.owner)

    def test_owner_can_set_legal_name_type_and_description(self):
        response = self.client.patch(reverse("company-profile"), {
            "legal_name": "Fields Co L.L.C.", "company_type": "LLC", "description": "We care.",
        }, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["legal_name"], "Fields Co L.L.C.")
        self.assertEqual(response.data["company_type"], "LLC")
        self.assertEqual(response.data["description"], "We care.")

    def test_unknown_company_type_is_rejected(self):
        response = self.client.patch(reverse("company-profile"), {"company_type": "MADE_UP"}, format="json")
        self.assertEqual(response.status_code, 400)


class BusinessAddressTests(CompanyFixtureMixin, TestCase):
    def setUp(self):
        self.client = APIClient()
        self.owner, self.company = self.make_company("addr")
        self.client.force_authenticate(self.owner)
        self.url = reverse("company-addresses")

    def test_new_company_gets_its_address_as_main_office(self):
        main = self.company.business_addresses.get(address_type=BusinessAddress.TYPE_MAIN)
        self.assertEqual((main.country, main.city, main.street), ("United Arab Emirates", "Dubai", "Office 1"))

    def _main(self):
        main = self.company.business_addresses.get(address_type=BusinessAddress.TYPE_MAIN)
        response = self.client.patch(reverse("company-address-detail", args=[main.public_id]), {
            "label": "Main Office (UAE)", "country": "United Arab Emirates", "city": "Dubai",
            "street": "Office 1203, Concord Tower",
        }, format="json")
        return response

    def test_main_address_is_mirrored_onto_company_and_owner_profile(self):
        self.assertEqual(self._main().status_code, 200)
        self.company.refresh_from_db()
        self.assertEqual(self.company.address, "Office 1203, Concord Tower")
        self.assertEqual(self.owner.company_profile.__class__.objects.get(pk=self.owner.company_profile.pk).address,
                         "Office 1203, Concord Tower")

    def test_only_one_main_office(self):
        self._main()
        response = self.client.post(self.url, {"address_type": "MAIN", "country": "Saudi Arabia"}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("address_type", response.data)

    def test_add_edit_and_remove_a_branch(self):
        self._main()
        created = self.client.post(self.url, {
            "label": "Branch (KSA)", "address_type": "BRANCH", "country": "Saudi Arabia",
            "city": "Riyadh", "street": "King Fahad Road",
        }, format="json")
        self.assertEqual(created.status_code, 201, created.data)
        detail = reverse("company-address-detail", args=[created.data["id"]])
        edited = self.client.patch(detail, {"address_type": "TRADING"}, format="json")
        self.assertEqual(edited.data["address_type"], "TRADING")
        profile = self.client.get(reverse("company-profile"))
        self.assertEqual(len(profile.data["business_addresses"]), 2)
        self.assertEqual(self.client.delete(detail).status_code, 204)
        self.assertEqual(self.company.business_addresses.count(), 1)

    def test_main_office_cannot_be_removed(self):
        main = self._main().data
        response = self.client.delete(reverse("company-address-detail", args=[main["id"]]))
        self.assertEqual(response.status_code, 400)

    def test_country_is_required(self):
        response = self.client.post(self.url, {"address_type": "BRANCH", "country": " "}, format="json")
        self.assertEqual(response.status_code, 400)

    def test_another_company_cannot_touch_the_address(self):
        branch = self.client.post(self.url, {"address_type": "BRANCH", "country": "Oman"}, format="json").data
        other_owner, _ = self.make_company("other")
        self.client.force_authenticate(other_owner)
        detail = reverse("company-address-detail", args=[branch["id"]])
        self.assertEqual(self.client.patch(detail, {"city": "X"}, format="json").status_code, 404)
        self.assertEqual(self.client.delete(detail).status_code, 404)

    def test_team_member_cannot_manage_addresses(self):
        member = self.make_member(self.company, P.ALL + [P.VIEW_RESULTS], "addr-admin")
        self.client.force_authenticate(member)
        response = self.client.post(self.url, {"address_type": "BRANCH", "country": "Oman"}, format="json")
        self.assertEqual(response.status_code, 403)

    def test_legacy_address_edit_moves_the_main_office(self):
        self._main()
        self.client.patch(reverse("company-profile"), {"city": "Abu Dhabi"}, format="json")
        main = self.company.business_addresses.get(address_type=BusinessAddress.TYPE_MAIN)
        self.assertEqual(main.city, "Abu Dhabi")


class ViewResultsPermissionTests(CompanyFixtureMixin, TestCase):
    """Reviewer (view_results only) reads every result in the company but
    can change nothing; without it a member still sees only their own."""

    def setUp(self):
        self.client = APIClient()
        self.owner, self.company = self.make_company("vr")
        self.evaluator = self.make_member(self.company, [P.ADD_CANDIDATES, P.SET_EVALUATION], "vr-eval")
        self.reviewer = self.make_member(self.company, [P.VIEW_RESULTS], "vr-review")
        self.own_session, self.own_eval = self.make_evaluation(self.company, self.evaluator, "vr-own")
        self.owner_session, self.owner_eval = self.make_evaluation(self.company, self.owner, "vr-owner")
        _, other_company = self.make_company("vr-other")
        self.foreign_session, self.foreign_eval = self.make_evaluation(other_company, other_company.admin_user, "vr-foreign")

    def _ids(self, response):
        rows = response.data["results"] if isinstance(response.data, dict) and "results" in response.data else response.data
        return {str(row["id"]) for row in rows}

    def test_reviewer_lists_every_company_evaluation_but_no_other_company(self):
        self.client.force_authenticate(self.reviewer)
        response = self.client.get(reverse("evaluation-list"))
        self.assertEqual(response.status_code, 200, response.data)
        ids = self._ids(response)
        self.assertIn(str(self.own_eval.public_id), ids)
        self.assertIn(str(self.owner_eval.public_id), ids)
        self.assertNotIn(str(self.foreign_eval.public_id), ids)

    def test_reviewer_can_open_any_company_evaluation_and_session(self):
        self.client.force_authenticate(self.reviewer)
        detail = self.client.get(reverse("evaluation-detail", args=[self.owner_eval.public_id]))
        self.assertEqual(detail.status_code, 200, detail.data)
        session = self.client.get(reverse("interview-session-detail", args=[self.owner_session.public_id]))
        self.assertEqual(session.status_code, 200, session.data)
        foreign = self.client.get(reverse("interview-session-detail", args=[self.foreign_session.public_id]))
        self.assertEqual(foreign.status_code, 403)

    def test_reviewer_cannot_change_anything(self):
        self.client.force_authenticate(self.reviewer)
        cancel = self.client.post(reverse("evaluation-cancel", args=[self.owner_eval.public_id]), {}, format="json")
        self.assertEqual(cancel.status_code, 403)
        session_cancel = self.client.post(
            reverse("interview-session-cancel", args=[self.owner_session.public_id]), {}, format="json",
        )
        self.assertEqual(session_cancel.status_code, 403)
        self.assertEqual(self.client.post(reverse("candidate-list"), {}, format="json").status_code, 403)

    def test_reviewer_cannot_drive_a_live_interview(self):
        self.client.force_authenticate(self.reviewer)
        response = self.client.get(reverse("interview-session-next-question", args=[self.owner_session.public_id]))
        self.assertEqual(response.status_code, 403)

    def test_member_without_view_results_still_sees_only_their_own(self):
        self.client.force_authenticate(self.evaluator)
        ids = self._ids(self.client.get(reverse("evaluation-list")))
        self.assertEqual(ids, {str(self.own_eval.public_id)})

    def test_admin_role_writes_stay_on_own_candidates(self):
        admin_member = self.make_member(self.company, P.ALL + [P.VIEW_RESULTS], "vr-admin")
        self.client.force_authenticate(admin_member)
        self.assertEqual(
            self.client.get(reverse("evaluation-detail", args=[self.owner_eval.public_id])).status_code, 200,
        )
        cancel = self.client.post(reverse("evaluation-cancel", args=[self.owner_eval.public_id]), {}, format="json")
        self.assertEqual(cancel.status_code, 404)

    def test_view_results_is_not_full_access(self):
        from types import SimpleNamespace

        from api.core.permisssions import RequireFullTeamAccess

        request = SimpleNamespace(user=self.reviewer, method="GET")
        self.assertFalse(RequireFullTeamAccess().has_permission(request, None))

    def test_invite_accepts_view_results(self):
        self.client.force_authenticate(self.owner)
        response = self.client.post(reverse("team-invite"), {
            "email": "new-reviewer@example.com", "first_name": "New", "last_name": "Reviewer",
            "job_title": "reviewer", "phone_number": "+971500000000", "permissions": [P.VIEW_RESULTS],
        }, format="json")
        self.assertIn(response.status_code, (200, 201), response.data)
