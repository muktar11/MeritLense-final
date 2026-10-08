"""The read-only historical Arabic impact inventory (scripts/arabic_impact_inventory.py,
deliverable F / D-07) must classify sessions correctly and never write."""
import csv
import os
import tempfile
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.test import TransactionTestCase
from django.utils import timezone

from api.accounts.models import Company, User
from api.candidates.models import Candidate
from api.core.constants import EvaluationType, InterviewEvaluationTier, Roles
from api.evaluations.models import Evaluation, ResponseEvaluationResult, ScoringRule, ScoringRuleSet
from api.interviews.models import InterviewConfiguration
from api.questions.models import QuestionTemplate
from api.sessions.models import CandidateResponse, InterviewSession, SessionQuestion

SCRIPT = Path(settings.BASE_DIR) / "scripts" / "arabic_impact_inventory.py"


class ArabicImpactInventoryTests(TransactionTestCase):
    def setUp(self):
        self.owner = User.objects.create_user(email="inv@example.com", password="x", first_name="I", last_name="V",
                                              role=Roles.B2B, is_verified=True)
        self.company = Company.objects.create(name="Inventory Co", registration_number="INV-1", company_size="11-50",
                                              phone_number="+1", country="X", city="Y", admin_user=self.owner)
        self.config = InterviewConfiguration.objects.create(
            role_name="Housekeeper", role_code="domestic_worker", language="AR",
            evaluation_tier=InterviewEvaluationTier.SCREENING, duration_minutes=30, total_questions=7,
            allow_retries=True, max_retries=1, rubric_version="GOV1.2-FINAL", question_set_version="GOV1.2-FINAL",
        )
        self.seq = 0

    def _template(self, steps):
        self.seq += 1
        return QuestionTemplate.objects.create(
            role_name="Housekeeper", role_code="domestic_worker", question_code=f"HK-TSK-{self.seq:03d}",
            language="AR", question_version="GOV1.2-FINAL", domain="x", skill="Task Execution",
            question_text="سؤال", expected_steps=steps,
        )

    def _evaluation(self, *, language="AR", template=None, rule_version=None, answered=True):
        self.seq += 1
        candidate = Candidate.objects.create(
            first_name="C", last_name=str(self.seq), email=f"inv-{self.seq}@example.com", passport_id=f"INV{self.seq}",
            job_role="HK", core_skills="x", preferred_language=language,
            passport_document="candidates/documents/passport/test.pdf", created_by=self.owner, company=self.company,
        )
        session = InterviewSession.objects.create(
            candidate=candidate, organization=self.company, config=self.config, role_name="Housekeeper",
            role_code="domestic_worker", ui_language="EN", candidate_language=language, tts_language_code="ar-SA",
            stt_language_code="ar-SA", total_questions=1, evaluation_tier=InterviewEvaluationTier.SCREENING,
            rubric_version="GOV1.2-FINAL", question_set_version="GOV1.2-FINAL",
            expires_at=InterviewSession.build_expiry(30), created_by=self.owner,
        )
        evaluation = Evaluation.objects.create(
            session=session, candidate=candidate, evaluation_type=EvaluationType.INTERVIEW, status="COMPLETED",
            scheduled_date=timezone.now(), duration_minutes=30, created_by=self.owner, company=self.company,
            evaluation_tier=InterviewEvaluationTier.SCREENING, score=Decimal("0"),
        )
        if not answered:
            return evaluation
        template = template or self._template(["خطوة"])
        question = SessionQuestion.objects.create(session=session, question_template=template, question_text="سؤال",
                                                  question_order=1)
        response = CandidateResponse.objects.create(session=session, question=question, transcript="إجابة")
        if rule_version:
            rule_set = ScoringRuleSet.objects.create(
                name=f"rs-{self.seq}", version=rule_version, role_code="domestic_worker", role_name="Housekeeper",
                evaluation_tier=InterviewEvaluationTier.SCREENING, is_active=True,
            )
            rule = ScoringRule.objects.create(rule_set=rule_set, question_template=template, max_score=5, pass_threshold=3)
            ResponseEvaluationResult.objects.create(
                evaluation=evaluation, session=session, candidate=candidate, response=response, question=question,
                rule_set=rule_set, rule=rule, score=0, max_score=5, matched_indicators=[],
            )
        return evaluation

    def _run(self):
        out = tempfile.NamedTemporaryFile(suffix=".csv", delete=False).name
        os.environ["INVENTORY_OUT"] = out
        try:
            exec(compile(SCRIPT.read_text(encoding="utf-8"), str(SCRIPT), "exec"), {"__name__": "inventory"})
            with open(out, encoding="utf-8") as fh:
                return {row["evaluation_id"]: row for row in csv.DictReader(fh)}
        finally:
            os.environ.pop("INVENTORY_OUT", None)

    def test_classifies_arabic_sessions_and_writes_nothing(self):
        affected = self._evaluation(template=self._template(["أ؛ ب؛ ج"]), rule_version="governance-v1.2-corrected")
        earlier = self._evaluation(template=self._template(["أ؛ ب؛ ج"]), rule_version="v1.0")
        repaired = self._evaluation(template=self._template(["أ", "ب", "ج"]), rule_version="governance-v1.2-corrected")
        unscored = self._evaluation()
        unanswered = self._evaluation(answered=False)
        english = self._evaluation(language="EN")

        counts_before = (Evaluation.objects.count(), ResponseEvaluationResult.objects.count(),
                         QuestionTemplate.objects.count(), list(Evaluation.objects.values_list("score", "readiness_status")))
        rows = self._run()
        counts_after = (Evaluation.objects.count(), ResponseEvaluationResult.objects.count(),
                        QuestionTemplate.objects.count(), list(Evaluation.objects.values_list("score", "readiness_status")))

        self.assertEqual(counts_before, counts_after)
        self.assertTrue(rows[str(affected.public_id)]["impact_class"].startswith("A "))
        self.assertTrue(rows[str(earlier.public_id)]["impact_class"].startswith("B "))
        self.assertTrue(rows[str(repaired.public_id)]["impact_class"].startswith("E "))
        self.assertTrue(rows[str(unscored.public_id)]["impact_class"].startswith("C "))
        self.assertTrue(rows[str(unanswered.public_id)]["impact_class"].startswith("D "))
        self.assertNotIn(str(english.public_id), rows)
        # No personal data exported.
        exported = " ".join(" ".join(r.values()) for r in rows.values())
        self.assertNotIn("inv-", exported)
        self.assertNotIn("INV", exported.replace("INV-1", ""))
