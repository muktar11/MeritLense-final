"""Load a DRAFT question bank package (e.g. GOV2.0-DRAFT) side by side with
the live bank, without making any of it reachable by live assessments.

Everything this command writes is inert until a separate Production
approval and cutover:
- QuestionTemplate rows: is_active=False, question_status=DRAFT, own
  question_set_version - live session generation only reads active rows.
- ScoringRuleSet / ScoringRule rows: is_active=False - live scoring only
  resolves active rule sets.
- No InterviewConfiguration changes - live configs keep pointing at the
  current version.
- No InterviewRubric rows - the AI interpretation prompt looks rubrics up
  by role + skill only (no version or active filter), so a draft rubric
  would leak into live interpretation.
- Nothing of any other version is touched (no archiving, no updates).
"""
import json
import re
from collections import Counter
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
from api.interviews.management.commands.import_governance_question_bank import (
    VERSION_TAG as LIVE_VERSION_TAG,
    _scoring_shape,
    _weighted_indicators,
)
from api.interviews.question_allocation import FULL, SCREENING, critical_competencies, validate_allocation
from api.questions.models import QuestionTemplate

DEFAULT_FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "question_bank" / "governance_v2_0_draft.json"
LANGUAGES = ("EN", "AR")
QUESTIONS_PER_ROLE = 100


def split_points(text, language="EN"):
    """Semicolon-delimited lists (Arabic also uses U+061B), falling back to
    commas only when no semicolon is present - same rule as the v1.2
    importer, plus the Arabic semicolon it never handled."""
    text = str(text or "")
    if language == "AR" and re.search(r"[;؛]", text):
        return [p.strip() for p in re.split(r"[;؛]", text) if p.strip()]
    if ";" in text:
        return [p.strip() for p in text.split(";") if p.strip()]
    separators = r"[,،]" if language == "AR" else r","
    return [p.strip() for p in re.split(separators, text) if p.strip()]


class Command(BaseCommand):
    help = "Load a DRAFT question bank package as an inactive, separate version (never live)."

    def add_arguments(self, parser):
        parser.add_argument("fixture_path", nargs="?", default=str(DEFAULT_FIXTURE))
        parser.add_argument("--dry-run", action="store_true", help="Validate only; write nothing.")

    def handle(self, *args, **options):
        path = Path(options["fixture_path"]).expanduser().resolve()
        if not path.exists():
            raise CommandError(f"Package not found: {path}")
        package = json.loads(path.read_text(encoding="utf-8"))
        self.validate(package)
        if options["dry_run"]:
            self.stdout.write(self.style.SUCCESS(f"Dry run: {package['version']} is valid, nothing written."))
            return
        with transaction.atomic():
            counts = self.load(package)
        self.stdout.write(self.style.SUCCESS(
            f"Loaded {package['version']} as an inactive draft: {counts['templates']} templates, "
            f"{counts['rule_sets']} rule sets, {counts['rules']} rules. Live bank untouched."
        ))

    # -- validation -------------------------------------------------------

    @staticmethod
    def validate(package):
        version = package.get("version", "")
        if "DRAFT" not in version.upper() or not str(package.get("status", "")).upper().startswith("DRAFT"):
            raise CommandError("This command only loads packages whose version and status are DRAFT.")
        if version == LIVE_VERSION_TAG or package.get("rule_set_version") == "governance-v1.2-corrected":
            raise CommandError("Refusing to load over the live version.")

        questions = package["questions"]
        role_map = package["role_code_map"]
        codes = [q["question_code"] for q in questions]
        dupes = [c for c, n in Counter(codes).items() if n > 1]
        if dupes:
            raise CommandError(f"Duplicate question codes: {dupes[:10]}")
        per_role = Counter(q["role"] for q in questions)
        for role in role_map:
            if per_role.get(role) != QUESTIONS_PER_ROLE:
                raise CommandError(f"{role}: expected {QUESTIONS_PER_ROLE} questions, found {per_role.get(role)}")

        required = ("question_en", "question_ar", "must_include_en", "must_include_ar",
                    "negative_indicators", "competency_code", "criticality", "score_note")
        for q in questions:
            empty = [f for f in required if not str(q.get(f) or "").strip()]
            if empty:
                raise CommandError(f"{q['question_code']}: empty {', '.join(empty)}")
            if role_map.get(q["role"]) != q["role_code"]:
                raise CommandError(f"{q['question_code']}: role code {q['role_code']} does not match {q['role']}")
            # New questions must use the approved 5 / 3 / 0 framework.
            # Approved v1.2 questions keep their approved score note, which
            # for 70 of them is a different approved shape (5/0, 3/2/0,
            # completion %).
            if q["source"] == "expansion" and _scoring_shape(q["score_note"])["scoring_type"] != "0/3/5":
                raise CommandError(f"{q['question_code']}: new question is not on the 5 / 3 / 0 framework")

        gaps = []
        for profile in package["role_profiles"]:
            critical = critical_competencies(profile)
            for tier in (FULL, SCREENING):
                gaps += [f"{profile['role']}: {g}" for g in validate_allocation(profile["allocation"][tier], tier, critical)]
        if gaps:
            raise CommandError("Allocation matrix invalid: " + "; ".join(gaps))

    # -- load -------------------------------------------------------------

    def load(self, package):
        version = package["version"]
        rule_set_version = package["rule_set_version"]
        role_map = package["role_code_map"]
        templates = {}
        for q in package["questions"]:
            role_code = role_map[q["role"]]
            texts = {"EN": q["question_en"], "AR": q["question_ar"]}
            must = {"EN": q["must_include_en"], "AR": q["must_include_ar"]}
            for lang in LANGUAGES:
                template, _ = QuestionTemplate.objects.update_or_create(
                    role_code=role_code,
                    question_code=q["question_code"],
                    language=lang,
                    question_version=version,
                    defaults={
                        "role_name": q["role"],
                        "domain": q["sector"],
                        "skill_tag": q["competency_en"],
                        "skill": q["competency_en"],
                        "skill_id": q["competency_code"],
                        "sequence_number": q["seq"],
                        "question_text": texts[lang],
                        "question_type": InterviewQuestionType.SCENARIO,
                        "question_format": InterviewQuestionFormat.SCENARIO,
                        "question_status": QuestionLifecycleStatus.DRAFT,
                        "difficulty": QuestionDifficulty.MEDIUM,
                        "difficulty_score": 2,
                        "expected_steps": split_points(must[lang], lang),
                        "keywords": split_points(q["ideal_keywords"]),
                        "weight": 1,
                        "scoring_type": _scoring_shape(q["score_note"])["scoring_type"],
                        "estimated_time_seconds": 60,
                        "expected_answer_type": ExpectedAnswerType.STRUCTURED,
                        # BOTH = usable by Full and Screening; FULL = Full only.
                        "evaluation_tier": InterviewEvaluationTier.BOTH if q["screening_eligible"] else InterviewEvaluationTier.FULL,
                        "rubric_version": version,
                        "question_set_version": version,
                        "is_mandatory": True,
                        "follow_up_allowed": False,
                        "critical_question": q["criticality"] == "Critical",
                        "is_active": False,
                    },
                )
                templates[(q["question_code"], lang)] = template

        by_role = {}
        for q in package["questions"]:
            by_role.setdefault(q["role"], []).append(q)
        rule_sets = rules = 0
        for role, role_questions in by_role.items():
            for tier, tier_questions in (
                (InterviewEvaluationTier.FULL, role_questions),
                (InterviewEvaluationTier.SCREENING, [q for q in role_questions if q["screening_eligible"]]),
            ):
                rule_set, _ = ScoringRuleSet.objects.update_or_create(
                    company=None,
                    role_code=role_map[role],
                    evaluation_tier=tier,
                    version=rule_set_version,
                    defaults={
                        "name": f"{role} ({tier.title()}) - {version}",
                        "role_name": role,
                        "description": package["status"],
                        "is_active": False,
                    },
                )
                rule_sets += 1
                for q in tier_questions:
                    shape = _scoring_shape(q["score_note"])
                    # Scored against the English Must Include list in every
                    # language, as in v1.2.
                    must_include = split_points(q["must_include_en"])
                    for lang in LANGUAGES:
                        ScoringRule.objects.update_or_create(
                            rule_set=rule_set,
                            question_template=templates[(q["question_code"], lang)],
                            defaults={
                                "competency_code": q["competency_code"],
                                "competency_name": q["competency_en"],
                                "question_code": q["question_code"],
                                "expected_indicators": must_include,
                                "required_indicators": must_include,
                                "weighted_indicators": _weighted_indicators(must_include, shape["max_score"]),
                                "critical_failure_indicators": split_points(q["negative_indicators"]),
                                "max_score": shape["max_score"],
                                "pass_threshold": shape["pass_threshold"],
                                "weight": 1,
                                "scoring_method": ScoringRule.SCORING_METHOD_WEIGHTED_MATCH,
                                "is_active": False,
                            },
                        )
                        rules += 1
        return {"templates": len(templates), "rule_sets": rule_sets, "rules": rules}
