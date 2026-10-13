"""Competency weighting comparison (management decision 4, 13 Oct 2026).

Scores IDENTICAL answers twice through the real scoring pipeline - once with
the current method (overall = total points / total possible points, so a
competency's weight is its question count) and once with equal competency
weighting (overall = mean of competency percentages) - and compares overall
scores, readiness decisions, holds and certificate eligibility.

Runs on a throwaway database with the controlled extractor (no AI calls):
the comparison is about aggregation, which happens after interpretation.

Usage (backend repo root):
    python scripts/question_bank/compare_weighting.py <out.json>
"""
import json
import os
import random
import sys
from collections import Counter, defaultdict
from decimal import Decimal
from unittest import mock

sys.path.insert(0, os.getcwd())
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "meritlense.settings")
import django  # noqa: E402

django.setup()

from django.core.management import call_command  # noqa: E402
from django.db import connection  # noqa: E402
from django.test.utils import setup_test_environment  # noqa: E402
from django.utils import timezone  # noqa: E402

PACKAGE = "api/interviews/fixtures/question_bank/governance_v2_0.json"
COMPETENCIES = ("safety_awareness", "task_execution", "hygiene_standards", "communication_ability", "behavior_integrity")
RANDOM_PROFILES = 4  # per role, seeded


def profiles_for(role_profile, rng):
    """Answer profiles: question_code -> mode, built from the competency."""
    comps = [c for c in COMPETENCIES if role_profile["allocation"]["FULL"].get(c)]
    out = {"all_complete": {c: 1.0 for c in comps}}
    for comp in comps:
        out[f"fails_{comp}"] = {c: (0.0 if c == comp else 1.0) for c in comps}
    for i in range(RANDOM_PROFILES):
        out[f"mixed_{i + 1}"] = {c: rng.choice((0.0, 0.25, 0.5, 0.75, 1.0)) for c in comps}
    return out


def main(out_path):
    setup_test_environment()
    connection.settings_dict.setdefault("TEST", {})["NAME"] = "test_meritlense_weighting"
    old = connection.creation.create_test_db(verbosity=0, autoclobber=True, keepdb=False)
    try:
        results = run()
    finally:
        connection.creation.destroy_test_db(old, verbosity=0)
    json.dump(results, open(out_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)
    s = results["summary"]
    print(json.dumps(s, indent=1))


def run():
    from api.accounts.models import Company, CompanyEmployerProfile, User
    from api.candidates.models import Candidate
    from api.contracts.models import Agreement
    from api.core.constants import AgreementMethod, AgreementType, EvaluationType, InterviewEvaluationTier, Roles
    from api.evaluations.certificate_services import certificate_eligibility
    from api.evaluations.models import Evaluation, ScoringRuleSet
    from api.evaluations.scoring_services import Week6ScoringService
    from api.evaluations.tests_launch_safeguards import Extractor
    from api.interviews.models import InterviewConfiguration
    from api.sessions.models import CandidateResponse, InterviewSession, SessionQuestion
    from api.sessions.services import QuestionGenerationService
    from api.translation.services import AIProcessingOrchestrationService, ResponseInterpretationService

    package = json.load(open(PACKAGE, encoding="utf-8"))
    version = package["version"]
    competency_of = {q["question_code"]: q["competency_code"] for q in package["questions"]}
    call_command("import_governance_question_bank", verbosity=0)
    call_command("import_question_bank_draft", verbosity=0)
    call_command("activate_question_bank_version", version, verbosity=0)

    owner = User.objects.create_user(email="weighting@example.com", password="x", first_name="W", last_name="C",
                                     role=Roles.B2B, is_verified=True)
    company = Company.objects.create(name="Weighting", registration_number="WC-1", company_size="11-50",
                                     phone_number="+1", country="X", city="Y", admin_user=owner)
    CompanyEmployerProfile.objects.create(user=owner, company_name=company.name, company=company,
                                          company_registration_number="WC-1", company_size="11-50")

    class ProfileExtractor(Extractor):
        """Within a competency with share p, the first round(p * n) of its
        questions are answered completely and the rest not at all."""

        def __init__(self, plan):
            super().__init__("complete")
            self.plan = plan

        def interpret(self, *, prompt):
            ids = [i["id"] for i in json.loads(prompt)["question"].get("indicators") or []]
            code = ids[0].removeprefix("V2-").rsplit("-", 2)[0] if ids else ""
            self.mode, self.calls = self.plan.get(code, "complete"), 0
            return super().interpret(prompt=prompt)

    rng = random.Random(20261013)
    rows = []
    seq = 0
    for role_profile in package["role_profiles"]:
        role_code = package["role_code_map"][role_profile["role"]]
        config = InterviewConfiguration.objects.get(role_code=role_code, language="EN",
                                                    evaluation_tier=InterviewEvaluationTier.FULL)
        rule_set = ScoringRuleSet.objects.get(question_set_version=version, role_code=role_code,
                                              evaluation_tier=InterviewEvaluationTier.FULL, company=None)
        for name, shares in profiles_for(role_profile, rng).items():
            # The same 21 questions and the same answers, assessed twice -
            # once per method, each with its own (immutable) readiness record.
            templates, plan, outcome = None, None, {}
            for method in (ScoringRuleSet.AGGREGATION_RESPONSE_SUM, ScoringRuleSet.AGGREGATION_EQUAL_COMPETENCY):
                rule_set.aggregation_method = method
                rule_set.save(update_fields=["aggregation_method"])
                seq += 1
                cand = Candidate.objects.create(first_name="W", last_name=str(seq), email=f"w{seq}@example.com",
                                                passport_id=f"W{seq}", job_role="OT", core_skills="",
                                                preferred_language="EN",
                                                passport_document="candidates/documents/passport/t.pdf",
                                                created_by=owner, company=company)
                consent = Agreement.objects.create(user=owner, agreement_type=AgreementType.CANDIDATE_CONSENT,
                                                   version="1", method=AgreementMethod.CHECKBOX, status="SIGNED",
                                                   accepted_at=timezone.now())
                session = InterviewSession.objects.create(
                    candidate=cand, organization=company, config=config, role_name=config.role_name,
                    role_code=role_code, ui_language="EN", candidate_language="EN", tts_language_code="en-US",
                    stt_language_code="en-US", evaluation_tier=InterviewEvaluationTier.FULL,
                    readiness_indicator_enabled=True, certificate_enabled=True, rubric_version=version,
                    question_set_version=version, identity_verified=True, candidate_consent_agreement=consent,
                    status="COMPLETED", ended_at=timezone.now(), expires_at=InterviewSession.build_expiry(30),
                    created_by=owner)
                if templates is None:
                    questions = QuestionGenerationService.generate_questions(session)
                    templates = [q.question_template for q in questions]
                    by_comp = defaultdict(list)
                    for t in templates:
                        by_comp[competency_of[t.question_code]].append(t.question_code)
                    plan = {}
                    for comp, codes in by_comp.items():
                        keep = round(shares.get(comp, 1.0) * len(codes))
                        for i, code in enumerate(codes):
                            plan[code] = "complete" if i < keep else "none"
                else:
                    questions = [
                        SessionQuestion.objects.create(
                            session=session, question_template=t, question_text=t.question_text, domain=t.domain,
                            skill_tag=t.skill_tag, skill=t.skill, difficulty=t.difficulty, question_order=i + 1)
                        for i, t in enumerate(templates)
                    ]
                evaluation = Evaluation.objects.create(
                    session=session, candidate=cand, evaluation_type=EvaluationType.INTERVIEW, status="COMPLETED",
                    scheduled_date=timezone.now(), duration_minutes=45, created_by=owner, company=company,
                    evaluation_tier=InterviewEvaluationTier.FULL, readiness_indicator_enabled=True,
                    certificate_enabled=True)
                with mock.patch.object(ResponseInterpretationService, "get_provider",
                                       return_value=ProfileExtractor(plan)):
                    for q in questions:
                        r = CandidateResponse.objects.create(session=session, question=q, transcript="(answer)",
                                                             original_transcript="(answer)", transcript_language="en")
                        AIProcessingOrchestrationService.interpret_response(response=r)
                        r.refresh_from_db()
                        AIProcessingOrchestrationService.prepare_evaluation_input(response=r)
                summary = Week6ScoringService.run_for_evaluation(evaluation=evaluation)
                evaluation.refresh_from_db()
                outcome[method] = {
                    "overall": summary.overall_percentage,
                    "readiness": evaluation.readiness_status,
                    "review": evaluation.review_status,
                    "certificate": certificate_eligibility(evaluation, summary)[0],
                    "competencies": {c["competency_code"]: c["percentage"] for c in summary.competencies_summary},
                }
            rule_set.aggregation_method = ScoringRuleSet.AGGREGATION_EQUAL_COMPETENCY
            rule_set.save(update_fields=["aggregation_method"])
            cur, eq = outcome[ScoringRuleSet.AGGREGATION_RESPONSE_SUM], outcome[ScoringRuleSet.AGGREGATION_EQUAL_COMPETENCY]
            rows.append({
                "role": role_profile["role"], "profile": name, "allocation": role_profile["allocation"]["FULL"],
                "criticality": role_profile["criticality"], "current": cur, "equal": eq,
                "overall_delta": eq["overall"] - cur["overall"],
                "same_decision": (cur["readiness"], cur["review"], cur["certificate"]) ==
                                 (eq["readiness"], eq["review"], eq["certificate"]),
            })
            print(f"{role_profile['role']:24} {name:32} current {cur['overall']:>6} equal {eq['overall']:>6} "
                  f"{cur['readiness']:>16} / {eq['readiness']:<16}")
    return {"summary": summarise(rows), "rows": rows}


def summarise(rows):
    deltas = [abs(r["overall_delta"]) for r in rows]
    # Same competency profile, different role: does the overall score depend
    # on which role (i.e. on the matrix's question counts)?
    spread = defaultdict(lambda: defaultdict(list))
    for r in rows:
        if r["profile"].startswith("fails_"):
            spread[r["profile"]]["current"].append(r["current"]["overall"])
            spread[r["profile"]]["equal"].append(r["equal"]["overall"])
    by_profile = {p: {m: {"min": min(v), "max": max(v)} for m, v in d.items()} for p, d in spread.items()}

    # Ranking: candidates within the same role ordered differently?
    inversions = 0
    pairs = 0
    by_role = defaultdict(list)
    for r in rows:
        by_role[r["role"]].append(r)
    for role_rows in by_role.values():
        for i, a in enumerate(role_rows):
            for b in role_rows[i + 1:]:
                pairs += 1
                dc = a["current"]["overall"] - b["current"]["overall"]
                de = a["equal"]["overall"] - b["equal"]["overall"]
                if dc * de < 0:
                    inversions += 1
    return {
        "assessments": len(rows),
        "identical_readiness_review_certificate": sum(r["same_decision"] for r in rows),
        "overall_abs_delta": {"mean": round(float(sum(deltas)) / len(deltas), 2), "max": float(max(deltas)),
                              "unchanged": sum(1 for d in deltas if d == 0)},
        "same_failure_across_roles": {p: {m: {k: float(x) for k, x in v.items()} for m, v in d.items()}
                                      for p, d in sorted(by_profile.items())},
        "within_role_rank_inversions": {"inversions": inversions, "pairs": pairs},
        "readiness_counts": {
            "current": dict(Counter(r["current"]["readiness"] for r in rows)),
            "equal": dict(Counter(r["equal"]["readiness"] for r in rows)),
        },
    }


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    main(sys.argv[1])
