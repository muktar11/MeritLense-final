from types import SimpleNamespace

from django.test import TestCase

from api.accounts.models import Company, TeamMemberProfile, User
from api.core.constants import CompanyTeamPermissions, Countries, Roles
from api.core.permisssions import (
    RequireAddCandidates,
    RequireCandidateAccess,
    RequireFullTeamAccess,
    RequireSetEvaluation,
    RequireSetPayment,
    RequireSetScores,
)


class CountriesConstantTests(TestCase):
    def test_all_codes_are_unique_two_letter_strings(self):
        codes = [code for code, _ in Countries.CHOICES]
        self.assertEqual(len(codes), len(set(codes)), "Countries.CHOICES has duplicate codes")
        for code in codes:
            self.assertEqual(len(code), 2, f"{code!r} is not a 2-letter ISO 3166-1 alpha-2 code")
            self.assertTrue(code.isupper(), f"{code!r} should be uppercase")

    def test_has_roughly_the_full_iso_3166_1_country_count(self):
        # ISO 3166-1 currently lists ~195 countries - a loose range check
        # to catch a transcription error that dropped/duplicated a large
        # chunk of the list, without being brittle about the exact count.
        self.assertGreater(len(Countries.CHOICES), 190)
        self.assertLess(len(Countries.CHOICES), 210)


class HasTeamMemberPermissionTests(TestCase):
    """A B2B_TEAM_MEMBER's access is gated by the permissions selected for
    them at invite time (TeamMemberProfile.permissions); every other role
    must pass through unaffected, since these classes are meant to be
    appended to a view's existing permission_classes, not replace them."""

    def setUp(self):
        self.admin = User.objects.create_user(
            email="perm-admin@example.com", password="Password123!",
            first_name="Perm", last_name="Admin", role=Roles.B2B, is_verified=True,
        )
        self.company = Company.objects.create(
            name="Perm Co", registration_number="PERM-1", company_size="1-10",
            phone_number="+10000000000", country="US", city="NYC", admin_user=self.admin,
        )
        self.b2c_user = User.objects.create_user(
            email="perm-b2c@example.com", password="Password123!",
            first_name="Perm", last_name="B2C", role=Roles.B2C, is_verified=True,
        )

    def _team_member(self, permissions, suffix):
        user = User.objects.create_user(
            email=f"perm-tm-{suffix}@example.com", password="Password123!",
            first_name="Perm", last_name="Staff", role=Roles.B2B_TEAM_MEMBER, is_verified=True,
        )
        TeamMemberProfile.objects.create(
            user=user, company=self.company, job_title="Staff",
            phone_number="+10000000001", permissions=permissions,
        )
        return user

    @staticmethod
    def _request_for(user):
        return SimpleNamespace(user=user)

    def test_b2b_admin_always_passes_regardless_of_permission(self):
        self.assertTrue(RequireAddCandidates().has_permission(self._request_for(self.admin), None))

    def test_non_b2b_role_passes_through_unaffected(self):
        self.assertTrue(RequireSetEvaluation().has_permission(self._request_for(self.b2c_user), None))

    def test_team_member_without_permission_is_denied(self):
        user = self._team_member([], "none")
        self.assertFalse(RequireAddCandidates().has_permission(self._request_for(user), None))

    def test_team_member_with_permission_is_allowed(self):
        user = self._team_member([CompanyTeamPermissions.ADD_CANDIDATES], "add-candidates")
        self.assertTrue(RequireAddCandidates().has_permission(self._request_for(user), None))

    def test_candidate_area_access_requires_a_relevant_permission(self):
        no_access = self._team_member([], "candidate-no-access")
        evaluation_only = self._team_member(
            [CompanyTeamPermissions.SET_EVALUATION], "candidate-evaluation-access"
        )
        self.assertFalse(RequireCandidateAccess().has_permission(self._request_for(no_access), None))
        self.assertTrue(
            RequireCandidateAccess().has_permission(self._request_for(evaluation_only), None)
        )

    def test_full_team_access_requires_every_permission(self):
        partial_user = self._team_member(
            [CompanyTeamPermissions.ADD_CANDIDATES], "partial-overview"
        )
        full_user = self._team_member(CompanyTeamPermissions.ALL, "full-overview")
        self.assertFalse(
            RequireFullTeamAccess().has_permission(self._request_for(partial_user), None)
        )
        self.assertTrue(
            RequireFullTeamAccess().has_permission(self._request_for(full_user), None)
        )

    def test_team_member_permissions_are_independent(self):
        user = self._team_member([CompanyTeamPermissions.SET_SCORES], "scores-only")
        request = self._request_for(user)
        self.assertFalse(RequireAddCandidates().has_permission(request, None))
        self.assertFalse(RequireSetEvaluation().has_permission(request, None))
        self.assertTrue(RequireSetScores().has_permission(request, None))
        self.assertFalse(RequireSetPayment().has_permission(request, None))

    def test_team_member_with_all_four_passes_every_check(self):
        user = self._team_member(CompanyTeamPermissions.ALL, "all")
        request = self._request_for(user)
        for permission_cls in (RequireAddCandidates, RequireSetEvaluation, RequireSetScores, RequireSetPayment):
            self.assertTrue(permission_cls().has_permission(request, None))
