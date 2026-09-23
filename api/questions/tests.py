from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from api.core.constants import InterviewEvaluationTier, QuestionDifficulty, QuestionLifecycleStatus
from api.questions.models import QuestionTemplate


class BackfillArabicExpectedStepsTests(TestCase):
    def _make_template(self, *, language, expected_steps, question_code="TST-001"):
        return QuestionTemplate.objects.create(
            role_name="Test Role",
            role_code="test_role",
            question_code=question_code,
            question_version="1.0",
            question_status=QuestionLifecycleStatus.ACTIVE,
            domain="Safety",
            skill_tag="safety_awareness",
            skill="Safety Awareness",
            sequence_number=1,
            difficulty=QuestionDifficulty.MEDIUM,
            question_text=f"Question text ({language})",
            question_type="safety",
            question_format="SCENARIO",
            language=language,
            scoring_type="0/3/5",
            difficulty_score=2,
            estimated_time_seconds=60,
            expected_answer_type="multi_step",
            evaluation_tier=InterviewEvaluationTier.FULL,
            rubric_version="v1",
            question_set_version="v1",
            critical_question=False,
            is_active=True,
            expected_steps=expected_steps,
        )

    def test_backfills_arabic_expected_steps_from_english_counterpart(self):
        self._make_template(language="EN", expected_steps=["pull over safely", "check tires"])
        ar_template = self._make_template(language="AR", expected_steps=[])

        call_command("backfill_arabic_expected_steps", stdout=StringIO())

        ar_template.refresh_from_db()
        self.assertEqual(len(ar_template.expected_steps), 2)
        self.assertTrue(all(ar_template.expected_steps))

    def test_does_not_touch_pairs_where_english_is_also_empty(self):
        self._make_template(language="EN", expected_steps=[], question_code="TST-002")
        ar_template = self._make_template(language="AR", expected_steps=[], question_code="TST-002")

        call_command("backfill_arabic_expected_steps", stdout=StringIO())

        ar_template.refresh_from_db()
        self.assertEqual(ar_template.expected_steps, [])

    def test_does_not_overwrite_arabic_steps_that_are_already_set(self):
        self._make_template(language="EN", expected_steps=["pull over safely"], question_code="TST-003")
        ar_template = self._make_template(
            language="AR", expected_steps=["توقف بأمان"], question_code="TST-003"
        )

        call_command("backfill_arabic_expected_steps", stdout=StringIO())

        ar_template.refresh_from_db()
        self.assertEqual(ar_template.expected_steps, ["توقف بأمان"])

    def test_dry_run_does_not_save_changes(self):
        self._make_template(language="EN", expected_steps=["pull over safely"], question_code="TST-004")
        ar_template = self._make_template(language="AR", expected_steps=[], question_code="TST-004")

        call_command("backfill_arabic_expected_steps", "--dry-run", stdout=StringIO())

        ar_template.refresh_from_db()
        self.assertEqual(ar_template.expected_steps, [])
