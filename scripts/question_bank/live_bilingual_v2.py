"""GOV2.0 bilingual LIVE-AI equivalence check (2nd management review, item 1/5).

Real pipeline on a throwaway database: GOV1.2 imported, GOV2.0-RC1 imported
and activated (the cutover path), and the REAL interpretation call. The key
is read from the environment only and never written anywhere. A budget guard
stops the run when estimated spend reaches LIVE_BUDGET_USD (default 3).

Part A - matched EN/AR answers: 3 questions per role (63), each answered as
  complete / partial / off-topic in English and Arabic. Compares the
  indicator IDs the AI credits and the resulting question scores.
Part B - unsafe answers: hand-written EN/AR pairs; the negative indicator
  must be detected (held for review) in both languages.
Part C - complete Full assessments in EN and AR for four roles through
  scoring, readiness, certificate eligibility and report.

Usage (backend repo root):
  OPENAI_API_KEY=... [OPENAI_INTERPRETATION_MODEL=gpt-4o-mini] [LIVE_BUDGET_USD=3] \\
      python scripts/question_bank/live_bilingual_v2.py <out.json>
  HARNESS_DRY=1 python scripts/question_bank/live_bilingual_v2.py <out.json>   # no AI, no spend
"""
import json
import math
import os
import sys
import time
from collections import defaultdict

sys.path.insert(0, os.getcwd())
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "meritlense.settings")
import django  # noqa: E402

django.setup()

from django.conf import settings  # noqa: E402
from django.core.management import call_command  # noqa: E402
from django.db import connection  # noqa: E402
from django.test.utils import setup_test_environment  # noqa: E402
from django.utils import timezone  # noqa: E402

PRICES = {"gpt-4o-mini": (0.15, 0.60), "gpt-4.1-mini": (0.40, 1.60), "gpt-4.1-nano": (0.10, 0.40),
          "gpt-4.1": (2.00, 8.00), "gpt-4o": (2.50, 10.00)}
BUDGET_STOP = float(os.environ.get("LIVE_BUDGET_USD", "3"))
SPENT = {"in": 0, "out": 0, "usd": 0.0, "calls": 0}
VERSION = "GOV2.0-RC1"
FULL_ROLES = ("driver", "nursing_assistant", "child_caregiver", "restaurant_staff")
OFF_TOPIC = {"EN": "I am not sure, I would just wait and see what happens.",
             "AR": "لست متأكدًا، سأنتظر فقط لأرى ما سيحدث."}
# Unsafe answer pairs: (question code, English answer, Arabic answer).
UNSAFE = [
    ("DRV-RLS-001", "The brakes feel soft but I am close to the destination, so I keep driving and report it later.",
     "الفرامل ضعيفة لكنني قريب من الوجهة، لذلك أواصل القيادة وأبلغ عن ذلك لاحقًا."),
    ("DRV-SAF-005", "I can smell fuel, but I keep driving to the next station to check it there.",
     "أشم رائحة وقود، لكنني أواصل القيادة حتى المحطة التالية لأفحصها هناك."),
    ("WS-SAF-009", "I would walk quickly under the raised forks because the forklift is not moving.",
     "سأمر بسرعة تحت الشوكات المرفوعة لأن الرافعة الشوكية لا تتحرك."),
    ("GL-SAF-010", "I would climb into the confined space quickly to get the tool, it only takes a minute.",
     "سأدخل المكان المحصور بسرعة لإحضار الأداة، فالأمر يستغرق دقيقة فقط."),
    ("FW-SAF-018", "I would reach in and pull out the stuck material while the machine is still running.",
     "سأدخل يدي وأسحب المادة العالقة بينما الآلة لا تزال تعمل."),
    ("ST-SAF-018", "If my colleague has gone home, I would remove his lock myself so I can finish the job.",
     "إذا غادر زميلي إلى المنزل، سأزيل قفله بنفسي حتى أتمكن من إنهاء العمل."),
]


class BudgetExceeded(Exception):
    pass


def price_for(model):
    for name in sorted(PRICES, key=len, reverse=True):
        if (model or "").startswith(name):
            return PRICES[name]
    return PRICES["gpt-4o"]


def track(interpretation):
    usage = (interpretation.metadata or {}).get("usage") or {}
    pin, pout = price_for(interpretation.model)
    i, o = int(usage.get("prompt_tokens") or 0), int(usage.get("completion_tokens") or 0)
    SPENT.update({"in": SPENT["in"] + i, "out": SPENT["out"] + o, "calls": SPENT["calls"] + 1,
                  "usd": SPENT["usd"] + i / 1e6 * pin + o / 1e6 * pout})
    if SPENT["usd"] >= BUDGET_STOP:
        raise BudgetExceeded(f"Estimated spend ${SPENT['usd']:.2f} reached the ${BUDGET_STOP} stop.")


class DryRunProvider:
    def interpret(self, *, prompt):
        p = json.loads(prompt)
        text = p.get("transcript") or p.get("candidate_transcript") or ""
        obs = [] if ("not sure" in text or "لست متأكدًا" in text) else [
            {"indicator_id": i["id"], "polarity": "affirmative", "attribution": "self", "quote": "x",
             "source_language": "en", "uncertain": False}
            for i in p["question"].get("indicators") or [] if i["type"] == "must_include"]
        body = {"answer_relevance": "high", "mentioned_steps": [], "missing_steps": [], "safety_risks": [],
                "compliance_risks": [], "language_quality": "clear", "extraction_confidence": 0.95,
                "confidence_notes": [], "uncertainty_notes": [], "transcript_issues": [], "key_evidence_phrases": [],
                "observations": obs}
        return {"provider": "DRY", "model": "dry-run", "raw_content": json.dumps(body),
                "metadata": {"usage": {"prompt_tokens": 0, "completion_tokens": 0}}}


def split_en(t):
    return [p.strip() for p in t.split(";") if p.strip()] if ";" in t else [p.strip() for p in t.split(",") if p.strip()]


def split_ar(t):
    import re
    return [p.strip() for p in re.split(r"[;؛]", t) if p.strip()]


def main(out_path):
    if os.environ.get("HARNESS_DRY") == "1":
        from api.translation.services import ResponseInterpretationService
        ResponseInterpretationService.get_provider = classmethod(lambda cls: DryRunProvider())
    elif not settings.OPENAI_API_KEY:
        sys.exit("OPENAI_API_KEY is not set")
    setup_test_environment()
    connection.settings_dict.setdefault("TEST", {})["NAME"] = "test_meritlense_live_v2"
    old = connection.creation.create_test_db(verbosity=0, autoclobber=True, keepdb=False)
    results = {}
    try:
        results = run()
    except BudgetExceeded as exc:
        results["stopped"] = str(exc)
    finally:
        connection.creation.destroy_test_db(old, verbosity=0)
    results.update({"spend": SPENT, "model": settings.OPENAI_INTERPRETATION_MODEL,
                    "dry_run": os.environ.get("HARNESS_DRY") == "1"})
    json.dump(results, open(out_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)
    print(f"Spend: {SPENT['calls']} calls, {SPENT['in']} in / {SPENT['out']} out tokens, ~${SPENT['usd']:.3f}")


def run():
    from api.accounts.models import Company, CompanyEmployerProfile, User
    from api.candidates.models import Candidate
    from api.contracts.models import Agreement
    from api.core.constants import AgreementMethod, AgreementType, EvaluationType, InterviewEvaluationTier, Roles
    from api.evaluations.certificate_services import certificate_eligibility
    from api.evaluations.models import Evaluation, ResponseEvaluationResult
    from api.evaluations.scoring_services import Week6ScoringService
    from api.interviews.models import InterviewConfiguration
    from api.questions.models import QuestionTemplate
    from api.reports.services import EvaluationReportService
    from api.sessions.models import CandidateResponse, InterviewSession, SessionQuestion
    from api.sessions.services import QuestionGenerationService
    from api.translation.services import AIProcessingOrchestrationService

    call_command("import_governance_question_bank", verbosity=0)
    call_command("import_question_bank_draft", verbosity=0)
    call_command("activate_question_bank_version", VERSION, verbosity=0)
    package = json.load(open("api/interviews/fixtures/question_bank/governance_v2_0.json", encoding="utf-8"))
    by_code = {q["question_code"]: q for q in package["questions"]}

    owner = User.objects.create_user(email="live2@example.com", password="x", first_name="Live", last_name="V2",
                                     role=Roles.B2B, is_verified=True)
    company = Company.objects.create(name="Live V2", registration_number="LV2-1", company_size="11-50",
                                     phone_number="+1", country="X", city="Y", admin_user=owner)
    CompanyEmployerProfile.objects.create(user=owner, company_name=company.name, company=company,
                                          company_registration_number="LV2-1", company_size="11-50")
    seq = [0]

    def new_session(role_code, lang, tier=InterviewEvaluationTier.FULL):
        seq[0] += 1
        config = InterviewConfiguration.objects.filter(role_code=role_code, language=lang,
                                                       evaluation_tier=tier).order_by("-id").first()
        cand = Candidate.objects.create(first_name="Cand", last_name=str(seq[0]), email=f"lv2-{seq[0]}@example.com",
                                        passport_id=f"LV2{seq[0]}", job_role="OT", core_skills="", preferred_language=lang,
                                        passport_document="candidates/documents/passport/test.pdf",
                                        created_by=owner, company=company)
        consent = Agreement.objects.create(user=owner, agreement_type=AgreementType.CANDIDATE_CONSENT, version="1",
                                           method=AgreementMethod.CHECKBOX, status="SIGNED", accepted_at=timezone.now())
        full = tier == InterviewEvaluationTier.FULL
        session = InterviewSession.objects.create(
            candidate=cand, organization=company, config=config, role_name=config.role_name, role_code=role_code,
            ui_language="EN", candidate_language=lang, tts_language_code="en-US",
            stt_language_code="en-US" if lang == "EN" else "ar-SA", evaluation_tier=tier,
            readiness_indicator_enabled=full, certificate_enabled=full, rubric_version=VERSION,
            question_set_version=VERSION, identity_verified=True, candidate_consent_agreement=consent,
            status="COMPLETED", ended_at=timezone.now(), expires_at=InterviewSession.build_expiry(30), created_by=owner)
        evaluation = Evaluation.objects.create(
            session=session, candidate=cand, evaluation_type=EvaluationType.INTERVIEW, status="COMPLETED",
            scheduled_date=timezone.now(), duration_minutes=45, created_by=owner, company=company, evaluation_tier=tier,
            readiness_indicator_enabled=full, certificate_enabled=full)
        return session, evaluation

    def answer(session, question, text, lang):
        response = CandidateResponse.objects.create(session=session, question=question, transcript=text,
                                                    original_transcript=text, transcript_language=lang.lower())
        for attempt in range(5):
            AIProcessingOrchestrationService.interpret_response(response=response, force=attempt > 0)
            response.refresh_from_db()
            if response.interpretation_status == "COMPLETED":
                break
            time.sleep(min(30, 3 * 2 ** attempt))
        track(response.ai_interpretation)
        AIProcessingOrchestrationService.prepare_evaluation_input(response=response)
        response.refresh_from_db()
        return response

    def answer_text(q, lang, scenario):
        if scenario == "off_topic":
            return OFF_TOPIC[lang]
        items = split_en(q["must_include_en"]) if lang == "EN" else split_ar(q["must_include_ar"])
        if scenario == "partial":
            items = items[: math.ceil(len(items) / 2)]
        if lang == "EN":
            return "I would " + "; then I would ".join(i[0].lower() + i[1:] for i in items) + "."
        return "سأقوم بما يلي: " + "، ثم ".join(items) + "."

    def credited(response):
        return sorted((response.ai_interpretation.normalized_indicators or {}).get("accepted_indicator_ids", [])
                      or [o["indicator_id"] for o in (response.ai_interpretation.normalized_indicators or {}).get("observations", [])
                          if o.get("polarity") == "affirmative"])

    # ---------------------------------------------------------------- Part A
    part_a = []
    for profile in package["role_profiles"]:
        role_code = profile["role_code"] if "role_code" in profile else package["role_code_map"][profile["role"]]
        role_qs = [q for q in package["questions"] if q["role"] == profile["role"]]
        picks = []
        for comp in ("safety_awareness", "task_execution", "communication_ability"):
            pick = next((q for q in role_qs if q["competency_code"] == comp and q["source"] == "expansion"), None)
            if pick:
                picks.append(pick)
        per_lang = {}
        for lang in ("EN", "AR"):
            session, evaluation = new_session(role_code, lang)
            order, responses = 0, {}
            for q in picks:
                template = QuestionTemplate.objects.get(question_set_version=VERSION, question_code=q["question_code"],
                                                        language=lang)
                for scenario in ("complete", "partial", "off_topic"):
                    order += 1
                    sq = SessionQuestion.objects.create(session=session, question_template=template,
                                                        question_text=template.question_text, domain=template.domain,
                                                        skill_tag=template.skill_tag, skill=template.skill,
                                                        difficulty=template.difficulty, question_order=order)
                    responses[(q["question_code"], scenario)] = answer(session, sq, answer_text(q, lang, scenario), lang)
            Week6ScoringService.run_for_evaluation(evaluation=evaluation)
            per_lang[lang] = {
                key: {"credited": credited(r),
                      "score": str(ResponseEvaluationResult.objects.get(response=r).score)}
                for key, r in responses.items()
            }
        for key in per_lang["EN"]:
            en, ar = per_lang["EN"][key], per_lang["AR"][key]
            part_a.append({"question": key[0], "scenario": key[1], "en": en, "ar": ar,
                           "same_ids": en["credited"] == ar["credited"], "same_score": en["score"] == ar["score"]})
        print(f"Part A {profile['role']}: {sum(r['same_score'] for r in part_a[-9:])}/9 equal scores")

    # ---------------------------------------------------------------- Part B
    part_b = []
    for code, en_text, ar_text in UNSAFE:
        q = by_code[code]
        row = {"question": code}
        for lang, text in (("EN", en_text), ("AR", ar_text)):
            session, evaluation = new_session(package["role_code_map"][q["role"]], lang)
            template = QuestionTemplate.objects.get(question_set_version=VERSION, question_code=code, language=lang)
            sq = SessionQuestion.objects.create(session=session, question_template=template,
                                                question_text=template.question_text, domain=template.domain,
                                                skill_tag=template.skill_tag, skill=template.skill,
                                                difficulty=template.difficulty, question_order=1)
            response = answer(session, sq, text, lang)
            Week6ScoringService.run_for_evaluation(evaluation=evaluation)
            evaluation.refresh_from_db()
            result = ResponseEvaluationResult.objects.get(response=response)
            row[lang] = {"score": str(result.score), "requires_review": result.requires_human_review,
                         "evaluation_review": evaluation.review_status, "readiness": evaluation.readiness_status}
        row["both_held"] = row["EN"]["evaluation_review"] == row["AR"]["evaluation_review"] == Evaluation.REVIEW_REQUIRED
        part_b.append(row)
        print(f"Part B {code}: held EN={row['EN']['evaluation_review']} AR={row['AR']['evaluation_review']}")

    # ---------------------------------------------------------------- Part C
    part_c = []
    for role_code in FULL_ROLES:
        row = {"role": role_code}
        for lang in ("EN", "AR"):
            session, evaluation = new_session(role_code, lang)
            questions = QuestionGenerationService.generate_questions(session)
            for question in questions:
                answer(session, question, answer_text(by_code[question.question_template.question_code], lang, "complete"), lang)
            summary = Week6ScoringService.run_for_evaluation(evaluation=evaluation)
            evaluation.refresh_from_db()
            report = EvaluationReportService.generate_for_evaluation(evaluation=evaluation, actor=owner)
            row[lang] = {"questions": len(questions), "overall": str(summary.overall_percentage),
                         "readiness": evaluation.readiness_status, "review": evaluation.review_status,
                         "certificate": certificate_eligibility(evaluation, summary),
                         "report": bool(report)}
        part_c.append(row)
        print(f"Part C {role_code}: EN {row['EN']['overall']} / AR {row['AR']['overall']}")

    def rate(rows, key):
        return {"equal": sum(r[key] for r in rows), "of": len(rows)}

    by_scenario = defaultdict(list)
    for r in part_a:
        by_scenario[r["scenario"]].append(r)
    return {
        "part_a_summary": {s: {"same_score": rate(rows, "same_score"), "same_ids": rate(rows, "same_ids")}
                           for s, rows in by_scenario.items()},
        "part_b_summary": {"both_held": sum(r["both_held"] for r in part_b), "of": len(part_b)},
        "part_a": part_a, "part_b": part_b, "part_c": part_c,
    }


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    main(sys.argv[1])
