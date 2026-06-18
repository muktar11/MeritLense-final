from collections import defaultdict
from decimal import Decimal
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from api.core.constants import InterviewEvaluationTier
from api.interviews.models import InterviewConfiguration, InterviewRubric
from api.questions.models import QuestionTemplate

try:
    from openpyxl import load_workbook
except ImportError as exc:  # pragma: no cover - environment/setup fallback
    raise ImportError(
        "openpyxl is required to import MeritLense role packages. "
        "Install project dependencies first."
    ) from exc


LANGUAGE_MAP = {
    "en": "EN",
    "ar": "AR",
}

TIER_MAP = {
    "full": InterviewEvaluationTier.FULL,
    "screening": InterviewEvaluationTier.SCREENING,
}


class Command(BaseCommand):
    help = "Import a MeritLense question bank role package workbook into interview configs, rubrics, and question templates."

    def add_arguments(self, parser):
        parser.add_argument("xlsx_path", help="Absolute or relative path to the role package workbook (.xlsx)")

    @transaction.atomic
    def handle(self, *args, **options):
        workbook_path = Path(options["xlsx_path"]).expanduser().resolve()
        if not workbook_path.exists():
            raise CommandError(f"Workbook not found: {workbook_path}")

        workbook = load_workbook(workbook_path, data_only=True)
        metadata = self._parse_metadata(workbook)
        rubric_rows = self._parse_rubrics(workbook)
        question_rows = self._parse_questions(workbook)
        criteria_by_skill = self._parse_answer_blueprint(workbook)
        if question_rows:
            metadata["rubric_version"] = question_rows[0]["rubric_version"]
            metadata["question_set_version"] = question_rows[0]["question_set_version"]
        else:
            metadata["rubric_version"] = ""
            metadata["question_set_version"] = ""

        self._sync_rubrics(metadata, rubric_rows, criteria_by_skill)
        self._sync_questions(metadata, question_rows)
        self._sync_configs(metadata, question_rows)

        self.stdout.write(
            self.style.SUCCESS(
                f"Imported role package for {metadata['role_name']} ({metadata['role_code']}) from {workbook_path.name}"
            )
        )

    def _parse_metadata(self, workbook):
        sheet = workbook["README"]
        return {
            "role_name": sheet["B3"].value,
            "role_code": sheet["B4"].value,
            "package_version": sheet["B7"].value,
            "languages": sheet["B8"].value,
        }

    def _parse_rubrics(self, workbook):
        sheet = workbook["Rubric_Map"]
        rows = []
        for row in sheet.iter_rows(min_row=3, values_only=True):
            if not row[0] or row[0] == "TOTAL":
                continue
            weight_value = row[2]
            if isinstance(weight_value, str) and weight_value.endswith("%"):
                weight = Decimal(weight_value.rstrip("%")) / Decimal("100")
            else:
                weight = Decimal(str(weight_value))
            rows.append(
                {
                    "skill_tag": row[0],
                    "scoring_category": row[1],
                    "weight": weight,
                    "max_score": int(row[3]),
                    "scoring_type": row[4],
                    "domain": row[5] or "",
                    "notes": row[6] or "",
                }
            )
        return rows

    def _parse_questions(self, workbook):
        rows = []
        for sheet_name in ["Questions_EN", "Questions_AR"]:
            sheet = workbook[sheet_name]
            for row in sheet.iter_rows(min_row=3, values_only=True):
                if not isinstance(row[0], int):
                    continue
                rows.append(
                    {
                        "sequence_number": row[0],
                        "domain": row[1],
                        "skill_tag": row[2],
                        "question_text": row[3],
                        "question_type": row[4],
                        "difficulty": row[5],
                        "scoring_type": row[6],
                        "weight": Decimal(str(row[7])),
                        "evaluation_tier": TIER_MAP[row[8]],
                        "language": LANGUAGE_MAP.get(str(row[9]).lower(), str(row[9]).upper()),
                        "rubric_version": row[10] or "",
                        "question_set_version": row[11] or "",
                    }
                )
        return rows

    def _parse_answer_blueprint(self, workbook):
        sheet = workbook["Answer_Blueprint"]
        grouped = defaultdict(list)
        for row in sheet.iter_rows(min_row=3, values_only=True):
            if not row[0]:
                continue
            grouped[row[1]].append(
                {
                    "question_ref": row[2],
                    "must_include_points": row[3],
                    "ideal_keywords": row[4],
                    "negative_indicators": row[5],
                    "score_note": row[6],
                }
            )
        return grouped

    def _sync_rubrics(self, metadata, rubric_rows, criteria_by_skill):
        for row in rubric_rows:
            InterviewRubric.objects.update_or_create(
                role_code=metadata["role_code"],
                skill_tag=row["skill_tag"],
                rubric_version=metadata["rubric_version"],
                defaults={
                    "role_name": metadata["role_name"],
                    "role_code": metadata["role_code"],
                    "scoring_category": row["scoring_category"],
                    "weight": row["weight"],
                    "max_score": row["max_score"],
                    "scoring_type": row["scoring_type"],
                    "domain": row["domain"],
                    "notes": row["notes"],
                    "question_set_version": metadata["question_set_version"],
                    "evaluation_criteria": criteria_by_skill.get(row["skill_tag"], []),
                    "is_active": True,
                },
            )

    def _sync_questions(self, metadata, question_rows):
        for row in question_rows:
            QuestionTemplate.objects.update_or_create(
                role_code=metadata["role_code"],
                language=row["language"],
                evaluation_tier=row["evaluation_tier"],
                sequence_number=row["sequence_number"],
                question_set_version=row["question_set_version"],
                defaults={
                    "role_name": metadata["role_name"],
                    "domain": row["domain"],
                    "skill_tag": row["skill_tag"],
                    "skill": row["skill_tag"],
                    "question_text": row["question_text"],
                    "question_type": row["question_type"],
                    "difficulty": row["difficulty"],
                    "scoring_type": row["scoring_type"],
                    "weight": row["weight"],
                    "rubric_version": row["rubric_version"],
                    "is_mandatory": True,
                    "is_active": True,
                },
            )

    def _sync_configs(self, metadata, question_rows):
        grouped = defaultdict(int)
        rubric_versions = {}
        question_set_versions = {}
        for row in question_rows:
            key = (row["language"], row["evaluation_tier"])
            grouped[key] += 1
            rubric_versions[key] = row["rubric_version"]
            question_set_versions[key] = row["question_set_version"]

        for (language, evaluation_tier), total_questions in grouped.items():
            InterviewConfiguration.objects.update_or_create(
                role_code=metadata["role_code"],
                language=language,
                evaluation_tier=evaluation_tier,
                defaults={
                    "role_name": metadata["role_name"],
                    "duration_minutes": 30 if evaluation_tier == InterviewEvaluationTier.SCREENING else 45,
                    "total_questions": total_questions,
                    "allow_retries": True,
                    "max_retries": 1,
                    "enable_translation": language == "AR",
                    "enable_task_module": evaluation_tier == InterviewEvaluationTier.FULL,
                    "enable_integrity_checks": evaluation_tier == InterviewEvaluationTier.FULL,
                    "rubric_version": rubric_versions[(language, evaluation_tier)],
                    "question_set_version": question_set_versions[(language, evaluation_tier)],
                    "is_active": True,
                },
            )
