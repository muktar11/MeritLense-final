from django.test import TestCase

from api.core.permisssions import RequireSetScores
from api.scores.views import CandidateScoreViewSet, ScoreSetViewSet


class ScorePermissionWiringTests(TestCase):
    """Commercial team-permissions feature: setting a candidate's score is
    one of the four actions a B2B_TEAM_MEMBER needs explicit set_scores
    permission for (api.core.permisssions.RequireSetScores - see
    api.core.tests.HasTeamMemberPermissionTests for the permission class's
    own behavior). These confirm the two score-mutating views actually
    wire it in for the mutating actions, not just viewing."""

    def test_score_set_mutating_actions_require_set_scores(self):
        view = ScoreSetViewSet()
        for action in ("create", "update", "partial_update", "destroy"):
            view.action = action
            classes = view.get_permissions()
            self.assertTrue(
                any(isinstance(p, RequireSetScores) for p in classes),
                f"ScoreSetViewSet.{action} should require RequireSetScores",
            )

    def test_score_set_viewing_actions_do_not_require_set_scores(self):
        view = ScoreSetViewSet()
        for action in ("list", "retrieve"):
            view.action = action
            classes = view.get_permissions()
            self.assertFalse(
                any(isinstance(p, RequireSetScores) for p in classes),
                f"ScoreSetViewSet.{action} should stay open to every team member",
            )

    def test_candidate_score_mutating_actions_require_set_scores(self):
        view = CandidateScoreViewSet()
        for action in ("create", "update", "partial_update", "destroy"):
            view.action = action
            classes = view.get_permissions()
            self.assertTrue(
                any(isinstance(p, RequireSetScores) for p in classes),
                f"CandidateScoreViewSet.{action} should require RequireSetScores",
            )

    def test_candidate_score_viewing_actions_do_not_require_set_scores(self):
        view = CandidateScoreViewSet()
        for action in ("list", "retrieve"):
            view.action = action
            classes = view.get_permissions()
            self.assertFalse(
                any(isinstance(p, RequireSetScores) for p in classes),
                f"CandidateScoreViewSet.{action} should stay open to every team member",
            )
