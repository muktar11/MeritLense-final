import json
import re
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from api.core.constants import (
    ExpectedAnswerType,
    InterviewEvaluationTier,
    InterviewQuestionFormat,
    InterviewQuestionType,
    QuestionDifficulty,
    QuestionLifecycleStatus,
)
from api.evaluations.models import ScoringRule, ScoringRuleSet
from api.interviews.models import InterviewConfiguration, InterviewRubric
from api.questions.models import QuestionTemplate
from api.questions.skill_tags import normalize_skill_tag

VERSION_TAG = "GOV1.2-FINAL"
RULE_SET_VERSION = "governance-v1.2-corrected"

LANGUAGES = ("EN", "AR")


ARABIC_SEMICOLON = "؛"  # ؛


def _split_points(text):
    if not text:
        return []
    text = str(text)
    # "Ideal Keywords" in particular is semicolon-delimited in most rows
    # but comma-delimited (no semicolons at all) in 74 of 441 - fall back
    # to comma splitting only when no semicolon is present, rather than
    # always splitting on both, which would wrongly fragment a
    # semicolon-delimited phrase that also happens to contain a comma.
    # Every Arabic Must Include uses the Arabic semicolon, which must count
    # as a semicolon too - otherwise each Arabic blueprint imports as one
    # single step containing the whole paragraph.
    if ";" in text or ARABIC_SEMICOLON in text:
        parts = re.split(f"[;{ARABIC_SEMICOLON}]", text)
    else:
        parts = text.split(",")
    return [p.strip() for p in parts if p.strip()]


def _scoring_shape(score_note):
    note = str(score_note or "")
    if "completion_pct" in note:
        return {"scoring_type": "completion_pct", "max_score": 10, "pass_threshold": 7}
    tiers = sorted(set(int(n) for n in re.findall(r"(\d+)\s*[:=]", note)), reverse=True)
    if not tiers:
        return {"scoring_type": "0/5", "max_score": 5, "pass_threshold": 5}
    scoring_type = "/".join(str(t) for t in sorted(tiers))
    max_score = max(tiers)
    pass_threshold = sorted(tiers)[len(tiers) // 2] if len(tiers) >= 3 else max_score
    return {"scoring_type": scoring_type, "max_score": max_score, "pass_threshold": pass_threshold}


def _weighted_indicators(points, max_score):
    if not points:
        return {}
    base = max_score / len(points)
    weights = {}
    running = 0
    for i, point in enumerate(points):
        if i == len(points) - 1:
            weights[point] = str(max_score - running)
        else:
            w = round(base)
            running += w
            weights[point] = str(w)
    return weights


class Command(BaseCommand):
    help = (
        "Import the approved Governance v1.2 Question Bank "
        "(MeritLense_Question_Bank_FINAL_APPROVED_FOR_IMPLEMENTATION_CORRECTED) "
        "fixture into QuestionTemplate, InterviewConfiguration, InterviewRubric and "
        "ScoringRuleSet/ScoringRule. Prospective only - never touches existing "
        "InterviewSession/Evaluation/ScoringResult rows or anything already scored."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "fixture_path",
            nargs="?",
            default=str(
                Path(__file__).resolve().parents[2]
                / "fixtures"
                / "question_bank"
                / "governance_v1_2_corrected.json"
            ),
            help="Path to the governance question bank JSON fixture.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Parse and validate the fixture without writing anything to the database.",
        )

    def handle(self, *args, **options):
        fixture_path = Path(options["fixture_path"]).expanduser().resolve()
        if not fixture_path.exists():
            raise CommandError(f"Fixture not found: {fixture_path}")

        data = json.loads(fixture_path.read_text(encoding="utf-8"))
        self._validate(data)

        if options["dry_run"]:
            self.stdout.write(self.style.SUCCESS("Dry run: fixture is valid, no changes written."))
            return

        with transaction.atomic():
            self._import(data)

        self.stdout.write(
            self.style.SUCCESS(
                f"Imported {len(data['questions'])} questions across "
                f"{len(data['role_code_map'])} roles (version {VERSION_TAG})."
            )
        )

    # -- validation -----------------------------------------------------

    def _validate(self, data):
        questions = data["questions"]
        role_map = data["role_code_map"]
        if len(questions) != 441:
            raise CommandError(f"Expected 441 questions in fixture, found {len(questions)}")

        from collections import Counter

        per_role_full = Counter(q["role"] for q in questions)
        per_role_screening = Counter(q["role"] for q in questions if q["is_screening"])
        for role in role_map:
            if per_role_full.get(role) != 21:
                raise CommandError(f"{role}: expected 21 Full questions, found {per_role_full.get(role)}")
            if per_role_screening.get(role) != 7:
                raise CommandError(f"{role}: expected 7 Screening questions, found {per_role_screening.get(role)}")

        codes = [q["question_code"] for q in questions]
        if len(set(codes)) != len(codes):
            dupes = [c for c, n in Counter(codes).items() if n > 1]
            raise CommandError(f"Duplicate question codes in fixture: {dupes}")

        # Hard check: every Critical competency in each role's profile must
        # have at least one dedicated Screening question (Behavioral
        # Indicators is the one approved exception - it may be evidenced
        # across responses instead of needing its own question).
        comp_label_to_code = {
            "Safety Awareness": "safety_awareness",
            "Practical Task Execution": "task_execution",
            "Behavioral Indicators": "behavior_integrity",
            "Communication Ability": "communication_ability",
            "Hygiene & Standards": "hygiene_standards",
        }
        screening_codes_by_role = {}
        for q in questions:
            if q["is_screening"]:
                screening_codes_by_role.setdefault(q["role"], set()).add(q["competency_code"])
        for profile in data["role_profiles"]:
            role = profile["role"]
            covered = screening_codes_by_role.get(role, set())
            for label, code in comp_label_to_code.items():
                if code == "behavior_integrity":
                    continue
                if profile["criticality"].get(code) == "Critical" and code not in covered:
                    raise CommandError(
                        f"{role}: {label} is Critical but has no dedicated Screening question in the fixture"
                    )

    # -- import -----------------------------------------------------------

    def _import(self, data):
        role_map = data["role_code_map"]
        questions = data["questions"]

        by_role = {}
        for q in questions:
            by_role.setdefault(q["role"], []).append(q)

        for role_name, role_questions in by_role.items():
            role_code = role_map[role_name]
            self._import_role(role_code, role_name, role_questions)

    def _import_role(self, role_code, role_name, role_questions):
        # Deactivate any pre-governance active templates for this role
        # that this import isn't about to replace - prospective new
        # content takes over going forward; already-completed sessions
        # keep referencing their own QuestionTemplate rows (PROTECTed by
        # ScoringRule/SessionQuestion FKs) regardless of is_active.
        QuestionTemplate.objects.filter(role_code=role_code, is_active=True).exclude(
            question_set_version=VERSION_TAG
        ).update(is_active=False, question_status=QuestionLifecycleStatus.ARCHIVED)

        templates_by_code_lang = {}
        for q in role_questions:
            evaluation_tier = InterviewEvaluationTier.BOTH if q["is_screening"] else InterviewEvaluationTier.FULL
            shape = _scoring_shape(q["score_note"])
            texts = {"EN": q["question_en"], "AR": q["question_ar"]}
            for lang in LANGUAGES:
                template, _ = QuestionTemplate.objects.update_or_create(
                    role_code=role_code,
                    question_code=q["question_code"],
                    language=lang,
                    question_version=VERSION_TAG,
                    defaults={
                        "role_name": role_name,
                        "domain": q["sector"] or role_name,
                        "skill_tag": q["competency_en"],
                        "skill": q["competency_en"],
                        "skill_id": q["competency_code"],
                        "sequence_number": q["seq"],
                        "question_text": texts[lang],
                        "question_type": InterviewQuestionType.SCENARIO,
                        "question_format": InterviewQuestionFormat.SCENARIO,
                        "question_status": QuestionLifecycleStatus.ACTIVE,
                        "difficulty": QuestionDifficulty.MEDIUM,
                        "difficulty_score": 2,
                        "expected_steps": _split_points(q["must_include_en"] if lang == "EN" else q["must_include_ar"]),
                        "keywords": _split_points(q["ideal_keywords"]),
                        "weight": 1,
                        "scoring_type": shape["scoring_type"],
                        "estimated_time_seconds": 60,
                        "expected_answer_type": ExpectedAnswerType.STRUCTURED,
                        "evaluation_tier": evaluation_tier,
                        "rubric_version": VERSION_TAG,
                        "question_set_version": VERSION_TAG,
                        "is_mandatory": True,
                        "follow_up_allowed": False,
                        "critical_question": q["criticality"] == "Critical",
                        "is_active": True,
                    },
                )
                templates_by_code_lang[(q["question_code"], lang)] = template

        self._sync_config(role_code, role_name, InterviewEvaluationTier.FULL, 21)
        self._sync_config(role_code, role_name, InterviewEvaluationTier.SCREENING, 7)

        self._sync_rule_set(role_code, role_name, InterviewEvaluationTier.FULL, role_questions, templates_by_code_lang)
        self._sync_rule_set(
            role_code, role_name, InterviewEvaluationTier.SCREENING,
            [q for q in role_questions if q["is_screening"]], templates_by_code_lang,
        )

        self._sync_rubrics(role_code, role_name, role_questions)

    def _sync_config(self, role_code, role_name, evaluation_tier, total_questions):
        # InterviewConfiguration has no uniqueness constraint on
        # (role_code, language, evaluation_tier) - some environments
        # already carry pre-existing duplicate rows for that combination.
        # update_or_create's own .get() would raise MultipleObjectsReturned
        # against those, so resolve defensively: update the most recent
        # matching row if any exist, else create one. Pre-existing
        # duplicate rows are left untouched either way - cleaning those up
        # is a separate, independent concern from importing new content.
        for language in LANGUAGES:
            defaults = {
                "role_name": role_name,
                "duration_minutes": 30 if evaluation_tier == InterviewEvaluationTier.SCREENING else 45,
                "total_questions": total_questions,
                "allow_retries": True,
                "max_retries": 1,
                "enable_translation": language == "AR",
                "enable_task_module": evaluation_tier == InterviewEvaluationTier.FULL,
                "enable_integrity_checks": evaluation_tier == InterviewEvaluationTier.FULL,
                "rubric_version": VERSION_TAG,
                "question_set_version": VERSION_TAG,
                "is_active": True,
            }
            existing = (
                InterviewConfiguration.objects.filter(
                    role_code=role_code, language=language, evaluation_tier=evaluation_tier,
                )
                .order_by("-id")
                .first()
            )
            if existing:
                for field, value in defaults.items():
                    setattr(existing, field, value)
                existing.save()
            else:
                InterviewConfiguration.objects.create(
                    role_code=role_code, language=language, evaluation_tier=evaluation_tier, **defaults,
                )

    def _sync_rule_set(self, role_code, role_name, evaluation_tier, tier_questions, templates_by_code_lang):
        rule_set, _ = ScoringRuleSet.objects.update_or_create(
            company=None,
            role_code=role_code,
            evaluation_tier=evaluation_tier,
            version=RULE_SET_VERSION,
            defaults={
                "name": f"{role_name} ({evaluation_tier.title()}) - Governance v1.2",
                "role_name": role_name,
                "is_active": True,
            },
        )

        for q in tier_questions:
            shape = _scoring_shape(q["score_note"])
            must_include = _split_points(q["must_include_en"])
            negative = _split_points(q["negative_indicators"])
            for lang in LANGUAGES:
                template = templates_by_code_lang[(q["question_code"], lang)]
                ScoringRule.objects.update_or_create(
                    rule_set=rule_set,
                    question_template=template,
                    defaults={
                        "competency_code": q["competency_code"],
                        "competency_name": q["competency_en"],
                        "question_code": q["question_code"],
                        "expected_indicators": must_include,
                        "required_indicators": must_include,
                        "weighted_indicators": _weighted_indicators(must_include, shape["max_score"]),
                        "critical_failure_indicators": negative,
                        "max_score": shape["max_score"],
                        "pass_threshold": shape["pass_threshold"],
                        "weight": 1,
                        "scoring_method": ScoringRule.SCORING_METHOD_WEIGHTED_MATCH,
                        "is_active": True,
                    },
                )

    def _sync_rubrics(self, role_code, role_name, role_questions):
        # Group by the NORMALIZED label, not the raw workbook competency_en
        # string - InterviewRubric.save() normalizes skill_tag on write
        # (e.g. "Behavioral Indicators" -> "Behavior & Integrity" via the
        # alias registered above), so update_or_create's lookup kwargs
        # must already be normalized or they'll never match the stored
        # row on a second run and collide with its own unique constraint.
        by_skill = {}
        for q in role_questions:
            normalized = normalize_skill_tag(q["competency_en"])
            by_skill.setdefault(normalized, []).append(q)

        for skill_tag, skill_questions in by_skill.items():
            criteria = [
                {
                    "question_ref": q["question_code"],
                    "must_include_points": q["must_include_en"],
                    "ideal_keywords": q["ideal_keywords"],
                    "negative_indicators": q["negative_indicators"],
                    "score_note": q["score_note"],
                }
                for q in skill_questions
            ]
            shapes = [_scoring_shape(q["score_note"]) for q in skill_questions]
            InterviewRubric.objects.update_or_create(
                role_code=role_code,
                skill_tag=skill_tag,
                rubric_version=VERSION_TAG,
                defaults={
                    "role_name": role_name,
                    "scoring_category": skill_tag,
                    "weight": 1,
                    "max_score": sum(s["max_score"] for s in shapes) or 1,
                    "scoring_type": shapes[0]["scoring_type"] if shapes else "",
                    "domain": skill_tag,
                    "notes": "",
                    "question_set_version": VERSION_TAG,
                    "evaluation_criteria": criteria,
                    "is_active": True,
                },
            )
