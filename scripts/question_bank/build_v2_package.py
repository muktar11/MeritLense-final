"""Build the GOV2.0 question bank package from the 2,100-question
technical handoff.

Development-time only (needs openpyxl, which is not a runtime dependency).
Reads the handoff workbook + reconciliation JSON and the approved v1.2
fixture, applies the management decisions of 10 Oct 2026, and writes one
version-controlled JSON package that `import_question_bank_draft` can load.

Management decisions applied (10 Oct 2026):
- The 324 approved-question content edits are carried as the new version
  (conditional approval). The v1.2 fixture is never modified.
- The four Non-Critical -> Critical changes (SNC-GEN-005, CCG-PRO-004,
  ECG-PRO-004, RS-BAD-003) were held at the v1.2 value until management
  approved them on 13 Oct 2026; they are now applied and recorded on the
  question as `criticality_change`.
- Communication Ability is Non-Critical in Commercial Cleaner, General
  Labor and (approved 13 Oct 2026, with S3 safety safeguards) Skilled Trades.
- Every question uses the approved 5 / 3 / 0 scoring framework. Approved
  questions keep their exact v1.2 score note.
- Selection uses the role allocation matrix below (exactly 21 Full / 7
  Screening, every Critical competency covered).

Usage:
    python scripts/question_bank/build_v2_package.py \
        <handoff.xlsx> <reconciliation.json> [output.json]
"""
import hashlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

import openpyxl

REPO = Path(__file__).resolve().parents[2]
V12_FIXTURE = REPO / "api/interviews/fixtures/question_bank/governance_v1_2_corrected.json"
DEFAULT_OUTPUT = REPO / "api/interviews/fixtures/question_bank/governance_v2_0.json"

# Release candidate: approved for integration and validation (13 Oct 2026);
# real customer assessments start only after management confirms launch.
VERSION = "GOV2.0-RC1"
RULE_SET_VERSION = "governance-v2.0-rc1"
STANDARD_SCORE_NOTE = (
    "5 = core evidence complete and safe; 3 = safe/correct direction with one material "
    "element missing; 0 = hard-negative, unsafe/unauthorized action, concealment, or falsification."
)
APPROVED_CRITICALITY_IDS = {"SNC-GEN-005", "CCG-PRO-004", "ECG-PRO-004", "RS-BAD-003"}
CRITICALITY_APPROVED_ON = "2026-10-13"
COMMUNICATION_ADDED_ROLES = ("Commercial Cleaner", "General Labor", "Skilled Trades")
COMPETENCY_ORDER = (
    "safety_awareness", "task_execution", "hygiene_standards",
    "communication_ability", "behavior_integrity",
)
COMPETENCY_LABELS = {
    "safety_awareness": "Safety Awareness",
    "task_execution": "Practical Task Execution",
    "hygiene_standards": "Hygiene & Standards",
    "communication_ability": "Communication Ability",
    "behavior_integrity": "Behavioral Indicators",
}
CONTENT_FIELDS = {
    "question_en": "Question (English)",
    "question_ar": "Question (Arabic)",
    "must_include_en": "Must Include (English)",
    "must_include_ar": "Must Include (Arabic)",
    "ideal_keywords": "Ideal keywords",
    "negative_indicators": "Negative indicators",
}


def _sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _clean(value):
    return "" if value is None else str(value).strip()


def _allocation_matrix(v12, recon):
    """Exactly 21 Full / 7 Screening per role, by competency.

    Starts from the approved v1.2 Full and Screening sets' own composition
    (approved, and already covering every Critical competency). For the three
    roles approved to include Communication Ability, the Full allocation
    moves one slot from Behavioral Indicators (Non-Critical) to Communication
    Ability (Non-Critical) - otherwise their new Communication questions
    could never be selected. Critical allocations are unchanged."""
    by_code = {q["question_code"]: q for q in v12["questions"]}
    matrix = {}
    for role, ids in recon["approved_full_question_ids"].items():
        full = Counter(by_code[i]["competency_code"] for i in ids)
        screening = Counter(by_code[i]["competency_code"] for i in recon["approved_screening_question_ids"][role])
        if role in COMMUNICATION_ADDED_ROLES and full["communication_ability"] == 0:
            full["behavior_integrity"] -= 1
            full["communication_ability"] += 1
        matrix[role] = {
            "FULL": {c: full[c] for c in COMPETENCY_ORDER if full[c]},
            "SCREENING": {c: screening[c] for c in COMPETENCY_ORDER if screening[c]},
        }
    return matrix


CORRECTIONS = Path(__file__).resolve().parent / "corrections"
# Weighting decision (management item 4, 13 Oct 2026, delegated to
# engineering on comparative evidence - scripts/question_bank/compare_weighting.py):
# 203 paired assessments gave identical readiness / hold / certificate
# decisions under both methods; within a role the points-based score
# agreed with the readiness decision far more often (5 inversions + 9 ties
# of 419 decision-ordered pairs, vs 16 + 97 for equal competency weight).
# With the matrix now set by approved role responsibilities, question counts
# are approved weights, so the current method is kept.
AGGREGATION = "RESPONSE_SUM"
# Most restrictive first: a question's review status is the strictest of
# the corrections applied to it.
STATUS_ORDER = (
    "AI_DRAFT_PENDING_SME",
    "EDITORIAL_PENDING_BILINGUAL_REVIEW",
    "GOVERNANCE",
    "EDITORIAL",
)


def _apply_corrections(questions):
    """Apply scripts/question_bank/corrections/0*.json in file order.

    Each file maps question_code -> {"fields": {...}} plus a category and
    approval status. Every corrected question records which categories
    touched it, its strictest review status, and (D-06) any proposal held
    back for SME review."""
    by_code = {q["question_code"]: q for q in questions}
    for path in sorted(CORRECTIONS.glob("0*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        if "corrections" not in data:
            continue
        for code, correction in data["corrections"].items():
            question = by_code[code]
            question.update(correction["fields"])
            question.setdefault("corrections_applied", []).append(data["category"])
            statuses = [question.get("review_status"), data["status"]]
            question["review_status"] = next(s for s in STATUS_ORDER if s in statuses)
            if "proposed_for_sme" in correction:
                question["proposed_for_sme"] = correction["proposed_for_sme"]


def build(xlsx_path, recon_path, output_path):
    v12 = json.loads(V12_FIXTURE.read_text(encoding="utf-8"))
    recon = json.loads(Path(recon_path).read_text(encoding="utf-8"))
    v12_by_code = {q["question_code"]: q for q in v12["questions"]}
    sector_by_role = {q["role"]: q["sector"] for q in v12["questions"]}
    profiles = {p["role"]: p for p in v12["role_profiles"]}
    full_ids = {i for ids in recon["approved_full_question_ids"].values() for i in ids}
    screening_ids = {i for ids in recon["approved_screening_question_ids"].values() for i in ids}
    proposed_by_id = {c["id"]: c for c in recon["prospective_governance_change_record"]}

    wb = openpyxl.load_workbook(xlsx_path, read_only=True)
    rows = list(wb["Questions"].iter_rows(values_only=True))
    header = rows[0]
    sheet = [dict(zip(header, r)) for r in rows[1:] if any(v is not None for v in r)]

    seq_by_role = Counter()
    questions = []
    for row in sheet:
        code = _clean(row["Question code"])
        role = _clean(row["Role"])
        comp = _clean(row["Competency code"])
        seq_by_role[role] += 1
        live = v12_by_code.get(code)
        criticality = _clean(row["Criticality"])
        change = None
        if code in APPROVED_CRITICALITY_IDS:
            change = {"from": live["criticality"], "to": criticality, "approved": CRITICALITY_APPROVED_ON,
                      "basis": proposed_by_id.get(code, {}).get("authority", "")}
        question = {
            "role": role,
            "role_code": _clean(row["Role code"]),
            "sector": sector_by_role[role],
            "seq": seq_by_role[role],
            "question_code": code,
            "source": "approved_v1_2" if live else "expansion",
            "competency_code": comp,
            "competency_en": COMPETENCY_LABELS[comp],
            "criticality": criticality,
            "screening_eligible": _clean(row["Screening eligible"]) == "Yes",
            "v1_2_full_set": code in full_ids,
            "v1_2_screening_set": code in screening_ids,
            **{field: _clean(row[col]) for field, col in CONTENT_FIELDS.items()},
            "en_points": int(row["EN points"]),
            "ar_points": int(row["AR points"]),
            "score_note": live["score_note"] if live else STANDARD_SCORE_NOTE,
        }
        if change:
            question["criticality_change"] = change
        questions.append(question)

    _apply_corrections(questions)
    for question in questions:
        live = v12_by_code.get(question["question_code"])
        if live:
            question["changed_from_v1_2"] = [f for f in CONTENT_FIELDS if question[f] != _clean(live.get(f))]

    matrix_file = json.loads((CORRECTIONS / "07_allocation_matrix.json").read_text(encoding="utf-8"))
    matrix = {
        role: {"FULL": spec["FULL"], "SCREENING": spec["SCREENING"]}
        for role, spec in matrix_file["roles"].items()
    }
    rationale = {role: spec["rationale"] for role, spec in matrix_file["roles"].items()}
    package = {
        "version": VERSION,
        "rule_set_version": RULE_SET_VERSION,
        "status": "RELEASE CANDIDATE - approved for integration and end-to-end validation; "
        "real customer assessments start only after management confirms launch",
        "based_on": v12["version"],
        "source_files": {
            Path(xlsx_path).name: _sha256(xlsx_path),
            Path(recon_path).name: _sha256(recon_path),
        },
        "management_decisions": "2026-10-10: conditional approval of approved-question edits; "
        "Communication Ability Non-Critical for Commercial Cleaner and General Labor; 5/3/0 scoring "
        "for all questions; 21 Full / 7 Screening by role competency allocation matrix. "
        "2026-10-13: four Criticality changes approved; Skilled Trades Communication Non-Critical "
        "with S3 safeguards; selection matrix approved; weighting method delegated to engineering "
        "on comparative evidence; integration into the existing infrastructure authorised.",
        "scoring": {
            "framework": "5/3/0 for new questions; approved v1.2 score notes preserved",
            "aggregation": AGGREGATION,
            "aggregation_note": "Overall score = points earned / points possible across the 21 (or 7) "
            "questions, so each competency weighs in proportion to its approved matrix allocation. Kept "
            "after comparing it with equal competency weighting on identical answers (13 Oct 2026): "
            "identical decisions, better agreement with readiness. Readiness, gates and certificates are "
            "decided per competency and are unaffected by this choice.",
            "negative_severity": "D-04 S1/S2/S3 proposed per indicator; scoring keeps every "
            "negative as a critical-failure indicator until the SME gate mapping is approved.",
            "mandatory_indicators": "D-02: where classified, only mandatory Must Include points are "
            "required for full marks; unclassified questions keep every point required.",
        },
        "pending_approvals": [
            {"id": "D-02", "item": "Mandatory vs scored Must Include classification (89 long lists drafted)"},
            {"id": "D-04", "item": "Negative-indicator severities and gate mapping (735 drafted)"},
            {"id": "SME", "item": "Occupational and bilingual specialist sign-off of all AI-drafted content"},
            {"id": "LAUNCH", "item": "Management confirmation of launch after reviewing the closure report"},
        ],
        "role_code_map": v12["role_code_map"],
        "role_profiles": [
            {**profiles[role], "allocation": matrix[role], "allocation_rationale": rationale[role]}
            for role in v12["role_code_map"]
        ],
        "questions": questions,
    }
    Path(output_path).write_text(json.dumps(package, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    by_source = Counter(q["source"] for q in questions)
    print(f"Wrote {output_path}: {len(questions)} questions ({dict(by_source)}), version {VERSION}")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    build(sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else DEFAULT_OUTPUT)
