"""Consolidated bilingual LIVE-AI acceptance test (item 7, decision of 9 Oct 2026).

Real pipeline on a throwaway database with the approved v1.2 bank and the
whole-bank indicator registry, using the REAL OpenAI interpretation call (the
key is read from the environment only - never written anywhere). Budget guard
stops the run before estimated spend reaches USD 4 (approved cap USD 5).

Part A: the 144 QA-13 cases (9 questions x 8 scenarios x EN/AR).
Part B: complete 21-question Nursing Assistant assessments in EN and AR through
report and certificate, plus an unsafe Arabic assessment that must be held,
then released by a reviewer.

Usage (backend repo root, branch fix/launch-readiness):
  OPENAI_API_KEY=... OPENAI_INTERPRETATION_MODEL=... python live_acceptance.py <qa13.xlsx> <out.json>
"""
import json
import os
import sys
import time
from collections import Counter, defaultdict

sys.path.insert(0, os.getcwd())
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "meritlense.settings")
os.environ["INDICATOR_ID_EXTRACTION_ENABLED"] = "True"
import django  # noqa: E402

django.setup()

from django.conf import settings  # noqa: E402
from django.core.management import call_command  # noqa: E402
from django.db import connection  # noqa: E402
from django.test.utils import setup_test_environment  # noqa: E402
from django.utils import timezone  # noqa: E402

# USD per 1M tokens (input, output). Unknown models are priced as gpt-4o (conservative).
PRICES = {"gpt-4o-mini": (0.15, 0.60), "gpt-4.1-mini": (0.40, 1.60), "gpt-4.1-nano": (0.10, 0.40),
          "gpt-4.1": (2.00, 8.00), "gpt-4o": (2.50, 10.00)}
BUDGET_STOP = 4.00
SPENT = {"in": 0, "out": 0, "usd": 0.0, "calls": 0}


class BudgetExceeded(Exception):
    pass


class AIRequestFailed(Exception):
    pass


FAILED_REQUESTS = []


def price_for(model):
    for name in sorted(PRICES, key=len, reverse=True):
        if (model or "").startswith(name):
            return PRICES[name]
    return PRICES["gpt-4o"]


def track(interpretation):
    usage = (interpretation.metadata or {}).get("usage") or {}
    pin, pout = price_for(interpretation.model)
    i, o = int(usage.get("prompt_tokens") or 0), int(usage.get("completion_tokens") or 0)
    SPENT["in"] += i
    SPENT["out"] += o
    SPENT["calls"] += 1
    SPENT["usd"] += i / 1e6 * pin + o / 1e6 * pout
    if SPENT["usd"] >= BUDGET_STOP:
        raise BudgetExceeded(f"Estimated spend ${SPENT['usd']:.2f} reached the ${BUDGET_STOP} stop.")


class DryRunProvider:
    """HARNESS_DRY=1: exercises the harness itself without spending anything."""

    def interpret(self, *, prompt):
        p = json.loads(prompt)
        obs = [{"indicator_id": i["id"], "polarity": "affirmative", "attribution": "self", "quote": "x",
                "source_language": "en", "uncertain": False}
               for i in p["question"].get("indicators") or [] if i["type"] == "must_include"]
        body = {"answer_relevance": "high", "mentioned_steps": [], "missing_steps": [], "safety_risks": [],
                "compliance_risks": [], "language_quality": "clear", "extraction_confidence": 0.95,
                "confidence_notes": [], "uncertainty_notes": [], "transcript_issues": [], "key_evidence_phrases": [],
                "observations": obs}
        return {"provider": "DRY", "model": "dry-run", "raw_content": json.dumps(body),
                "metadata": {"usage": {"prompt_tokens": 0, "completion_tokens": 0}}}


def main(qa13_path, out_path):
    if os.environ.get("HARNESS_DRY") == "1":
        from api.translation.services import ResponseInterpretationService
        ResponseInterpretationService.get_provider = classmethod(lambda cls: DryRunProvider())
    elif not settings.OPENAI_API_KEY:
        sys.exit("OPENAI_API_KEY is not set")
    setup_test_environment()
    connection.settings_dict.setdefault("TEST", {})["NAME"] = "test_meritlense_live_acceptance"
    old = connection.creation.create_test_db(verbosity=0, autoclobber=True, keepdb=False)
    try:
        results = run(qa13_path)
    finally:
        connection.creation.destroy_test_db(old, verbosity=0)
    results["spend"] = SPENT
    results["failed_requests"] = FAILED_REQUESTS
    results["model"] = settings.OPENAI_INTERPRETATION_MODEL
    json.dump(results, open(out_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)
    print(f"\nSpend: {SPENT['calls']} calls, {SPENT['in']} in / {SPENT['out']} out tokens, ~${SPENT['usd']:.3f}")


def run(qa13_path):
    import openpyxl

    from api.accounts.models import Company, CompanyEmployerProfile, User
    from api.candidates.models import Candidate
    from api.contracts.models import Agreement
    from api.core.constants import AgreementMethod, AgreementType, EvaluationType, InterviewEvaluationTier, Roles
    from api.evaluations.certificate_services import certificate_eligibility, generate_certificate
    from api.evaluations.human_review_services import HumanReviewService, is_hidden_from
    from api.evaluations.models import Evaluation, ResponseEvaluationResult
    from api.evaluations.scoring_services import Week6ScoringService
    from api.interviews.models import InterviewConfiguration
    from api.questions.models import QuestionTemplate
    from api.reports.services import EvaluationReportService
    from api.sessions.models import CandidateResponse, InterviewSession, SessionQuestion
    from api.sessions.services import QuestionGenerationService
    from api.translation.services import AIProcessingOrchestrationService

    call_command("import_governance_question_bank", verbosity=0)
    call_command("load_indicator_registry", verbosity=0)
    bank = json.load(open("api/interviews/fixtures/question_bank/governance_v1_2_corrected.json", encoding="utf-8"))
    by_code = {q["question_code"]: q for q in bank["questions"]}

    owner = User.objects.create_user(email="live@example.com", password="x", first_name="Live", last_name="Test",
                                     role=Roles.B2B, is_verified=True)
    company = Company.objects.create(name="Live Acceptance", registration_number="LIVE-1", company_size="11-50",
                                     phone_number="+1", country="X", city="Y", admin_user=owner)
    CompanyEmployerProfile.objects.create(user=owner, company_name=company.name, company=company,
                                          company_registration_number="LIVE-1", company_size="11-50")
    reviewer = User.objects.create_user(email="reviewer@example.com", password="x", first_name="QA", last_name="Reviewer",
                                        role=Roles.ADMIN, is_verified=True)
    seq = [0]

    def new_session(role_code, lang, tier, total):
        seq[0] += 1
        config = InterviewConfiguration.objects.get(role_code=role_code, language=lang, evaluation_tier=tier,
                                                   question_set_version="GOV1.2-FINAL")
        cand = Candidate.objects.create(first_name="Cand", last_name=str(seq[0]), email=f"live-{seq[0]}@example.com",
                                        passport_id=f"LIVE{seq[0]}", job_role="OT", core_skills="", preferred_language=lang,
                                        passport_document="candidates/documents/passport/test.pdf",
                                        created_by=owner, company=company)
        consent = Agreement.objects.create(user=owner, agreement_type=AgreementType.CANDIDATE_CONSENT, version="1",
                                           method=AgreementMethod.CHECKBOX, status="SIGNED", accepted_at=timezone.now())
        full = tier == InterviewEvaluationTier.FULL
        session = InterviewSession.objects.create(
            candidate=cand, organization=company, config=config, role_name=config.role_name, role_code=role_code,
            ui_language="EN", candidate_language=lang, tts_language_code="en-US",
            stt_language_code="en-US" if lang == "EN" else "ar-SA", evaluation_tier=tier, total_questions=total,
            readiness_indicator_enabled=full, certificate_enabled=full, rubric_version="GOV1.2-FINAL",
            question_set_version="GOV1.2-FINAL", identity_verified=True, candidate_consent_agreement=consent,
            status="COMPLETED", ended_at=timezone.now(), expires_at=InterviewSession.build_expiry(30), created_by=owner)
        evaluation = Evaluation.objects.create(
            session=session, candidate=cand, evaluation_type=EvaluationType.INTERVIEW, status="COMPLETED",
            scheduled_date=timezone.now(), duration_minutes=45, created_by=owner, company=company, evaluation_tier=tier,
            readiness_indicator_enabled=full, certificate_enabled=full)
        return session, evaluation

    def answer(session, question, text, lang):
        response = CandidateResponse.objects.create(session=session, question=question, transcript=text,
                                                    original_transcript=text, transcript_language=lang.lower())
        for attempt in range(6):
            AIProcessingOrchestrationService.interpret_response(response=response, force=attempt > 0)
            response.refresh_from_db()
            if response.interpretation_status == "COMPLETED":
                break
            meta = getattr(getattr(response, "ai_interpretation", None), "metadata", None) or {}
            print(f"   retry {attempt + 1}: {response.interpretation_error} {meta.get('error_metadata')}")
            time.sleep(min(60, 5 * 2 ** attempt))
        if response.interpretation_status != "COMPLETED":
            FAILED_REQUESTS.append(str(response.public_id))
            raise AIRequestFailed(f"Interpretation failed after retries: {response.interpretation_error}")
        track(response.ai_interpretation)
        AIProcessingOrchestrationService.prepare_evaluation_input(response=response)
        response.refresh_from_db()
        return response

    # -------------------------------------------------------------- Part A
    ws = openpyxl.load_workbook(qa13_path)["144 Corrected QA Cases"]
    rows = list(ws.iter_rows(values_only=True))
    head = rows[0]
    cases = [dict(zip(head, r)) for r in rows[1:] if r[0]]
    part_a = []
    for case in cases:
        code, lang, scenario = case["Question Code"], case["Language"], case["Scenario"]
        template = QuestionTemplate.objects.get(question_code=code, language=lang, question_version="GOV1.2-FINAL")
        session, evaluation = new_session(template.role_code, lang, InterviewEvaluationTier.SCREENING, 1)
        question = SessionQuestion.objects.create(session=session, question_template=template,
                                                  question_text=template.question_text, question_order=1,
                                                  skill_tag=template.skill_tag, skill=template.skill, domain=template.domain)
        try:
            response = answer(session, question, case["Natural answer corrected"], lang)
        except AIRequestFailed as exc:
            part_a.append({"case_id": case["Case ID"], "question_code": code, "language": lang, "scenario": scenario,
                           "expected": "-", "passed": False, "ai_request_failed": True, "required_points": 0,
                           "found": 0, "negative_evidence": [], "held": None, "review_reasons": [str(exc)],
                           "observations": [], "answer": case["Natural answer corrected"]})
            print(f"ERROR {case['Case ID']:44} AI request failed after retries")
            continue
        Week6ScoringService.run_for_evaluation(evaluation=evaluation)
        evaluation.refresh_from_db()
        result = ResponseEvaluationResult.objects.get(response=response)
        artifact = response.evaluation_input_artifact
        meta = artifact.metadata
        mi_total = len(meta.get("indicator_registry") and [i for i in meta["indicator_registry"] if "-MI-" in i["id"]] or [])
        accepted_mi = len(result.matched_indicators)
        neg = meta.get("negative_evidence_ids") or []
        held = evaluation.review_status == Evaluation.REVIEW_REQUIRED
        if scenario in ("COMPLETE", "SAFE_PARAPHRASE"):
            passed, expected = (accepted_mi == mi_total and not held), "all required points found, not held"
        elif scenario == "ONE_MI_OMITTED":
            passed, expected = (accepted_mi < mi_total and not neg), "at least one required point missing, no unsafe flag"
        elif scenario == "AFFIRMATIVE_UNSAFE":
            passed, expected = held, "held for human review"
        elif scenario in ("NEGATED_UNSAFE", "HYPOTHETICAL_UNSAFE", "QUOTED_UNSAFE"):
            passed, expected = (not neg and not held), "no unsafe act attributed, not held"
        else:  # UNCERTAIN_UNSAFE
            passed, expected = (not neg), "unsafe act not asserted (hold acceptable)"
        part_a.append({
            "case_id": case["Case ID"], "question_code": code, "language": lang, "scenario": scenario,
            "expected": expected, "passed": passed, "required_points": mi_total, "found": accepted_mi,
            "negative_evidence": neg, "held": held, "review_reasons": evaluation.review_reasons,
            "observations": [(o["indicator_id"], o["polarity"], o["attribution"], o["decision"])
                             for o in meta.get("indicator_observations") or []],
            "answer": case["Natural answer corrected"],
        })
        print(f"{'PASS' if passed else 'FAIL'} {case['Case ID']:44} found {accepted_mi}/{mi_total} neg={neg} held={held}"
              f"  ~${SPENT['usd']:.3f}")

    # -------------------------------------------------------------- Part B
    def full_assessment(lang, unsafe_on_first=False):
        session, evaluation = new_session("nursing_assistant", lang, InterviewEvaluationTier.FULL, 21)
        questions = QuestionGenerationService.generate_questions(session)
        for idx, q in enumerate(questions):
            src = by_code[q.question_template.question_code]
            if lang == "EN":
                points = [p.strip() for p in src["must_include_en"].replace(";", "|").split("|") if p.strip()]
                text = "I would " + ", then ".join(p[0].lower() + p[1:] for p in points) + "."
            else:
                import re
                points = [p.strip() for p in re.split(r"[;؛]", src["must_include_ar"]) if p.strip()]
                text = "سأقوم بما يلي: " + "، ثم ".join(points) + "."
            if unsafe_on_first and idx == 0:
                text += (" But if I am short of time I give the patient medication from my own supply without telling the nurse."
                         if lang == "EN" else " لكن إذا كان الوقت ضيقًا أعطي المريض دواءً من عندي دون إبلاغ الممرضة.")
            answer(session, q, text, lang)
        summary = Week6ScoringService.run_for_evaluation(evaluation=evaluation)
        evaluation.refresh_from_db()
        report = EvaluationReportService.generate_for_evaluation(evaluation=evaluation, actor=owner)
        evaluation.refresh_from_db()
        cert = generate_certificate(evaluation, summary) if evaluation.certificate_enabled else None
        evaluation.refresh_from_db()
        eligible, reason = certificate_eligibility(evaluation, summary)
        return {
            "language": lang, "unsafe_injected": unsafe_on_first, "questions": len(questions),
            "overall_percentage": float(summary.overall_percentage), "summary_status": summary.status,
            "readiness": evaluation.readiness_status, "review_status": evaluation.review_status,
            "review_reasons": evaluation.review_reasons,
            "report_number": report.report_number, "report_readiness": report.readiness_status,
            "report_score": float(report.overall_percentage), "report_pdf": bool(report.employer_pdf),
            "employer_can_see": not is_hidden_from(evaluation, owner),
            "certificate_issued": cert is not None and evaluation.certificate_status == "ISSUED",
            "certificate_decision": f"{'ELIGIBLE' if eligible else 'NOT ELIGIBLE'}: {reason}",
            "below_threshold": [b["competency_code"] for b in summary.below_threshold_competencies],
            "evaluation": evaluation,
        }

    part_b = []
    for lang in ("EN", "AR"):
        r = full_assessment(lang)
        r.pop("evaluation")
        part_b.append(r)
        print(f"FULL {lang}: {r['overall_percentage']}% readiness={r['readiness']} review={r['review_status']} "
              f"report={r['report_readiness']} cert={r['certificate_issued']} ({r['certificate_decision']}) ~${SPENT['usd']:.3f}")
    unsafe = full_assessment("AR", unsafe_on_first=True)
    evaluation = unsafe.pop("evaluation")
    held_before = unsafe["review_status"]
    released = None
    if evaluation.review_status == Evaluation.REVIEW_REQUIRED:
        evaluation, _ = HumanReviewService.approve(evaluation=evaluation, reviewer=reviewer, decision="NOT_READY",
                                                   notes="Answer 1 describes giving unprescribed medication.")
        evaluation.refresh_from_db()
        released = {"review_status": evaluation.review_status, "readiness": evaluation.readiness_status,
                    "employer_can_see": not is_hidden_from(evaluation, owner),
                    "certificate_status": evaluation.certificate_status}
    unsafe["after_review"] = released
    part_b.append(unsafe)
    print(f"FULL AR unsafe: held={held_before} readiness={unsafe['readiness']} cert={unsafe['certificate_issued']} "
          f"after review={released}")

    summary = defaultdict(Counter)
    for r in part_a:
        summary[r["scenario"]][("pass" if r["passed"] else "fail", r["language"])] += 1
    parity = []
    by_case = {(r["question_code"], r["scenario"], r["language"]): r for r in part_a}
    for (code, scenario, lang), r in by_case.items():
        if lang == "EN":
            other = by_case.get((code, scenario, "AR"))
            if other is not None:
                parity.append(r["passed"] == other["passed"] and r["held"] == other["held"]
                              and r["found"] == other["found"])
    print("\nPart A by scenario:", {k: dict(v) for k, v in summary.items()})
    print(f"Part A overall: {sum(r['passed'] for r in part_a)}/{len(part_a)} passed; "
          f"EN/AR parity {sum(parity)}/{len(parity)} pairs identical")
    return {"part_a": part_a, "part_b": part_b,
            "part_a_by_scenario": {k: {f"{a}-{b}": n for (a, b), n in v.items()} for k, v in summary.items()},
            "parity_identical_pairs": sum(parity), "parity_pairs": len(parity)}


if __name__ == "__main__":
    try:
        main(sys.argv[1], sys.argv[2])
    except BudgetExceeded as exc:
        print("STOPPED:", exc)
