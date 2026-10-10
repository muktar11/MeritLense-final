"""Build the GOV2.0-DRAFT question bank package from the 2,100-question
technical handoff.

Development-time only (needs openpyxl, which is not a runtime dependency).
Reads the handoff workbook + reconciliation JSON and the approved v1.2
fixture, applies the management decisions of 10 Oct 2026, and writes one
version-controlled JSON package that `import_question_bank_draft` can load.

Management decisions applied (10 Oct 2026):
- The 324 approved-question content edits are carried as the new version
  (conditional approval). The v1.2 fixture is never modified.
- The four proposed Non-Critical -> Critical changes are NOT applied: each
  needs separate approval. The approved v1.2 value is kept and the proposal
  is recorded on the question as `proposed_criticality`.
- Communication Ability is Non-Critical in Commercial Cleaner and General
  Labor (approved). Skilled Trades is also Non-Critical, matching its role
  profile, but stays an open approval item until justified.
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
DEFAULT_OUTPUT = REPO / "api/interviews/fixtures/question_bank/governance_v2_0_draft.json"

VERSION = "GOV2.0-DRAFT"
RULE_SET_VERSION = "governance-v2.0-draft"
STANDARD_SCORE_NOTE = (
    "5 = core evidence complete and safe; 3 = safe/correct direction with one material "
    "element missing; 0 = hard-negative, unsafe/unauthorized action, concealment, or falsification."
)
PENDING_CRITICALITY_IDS = {"SNC-GEN-005", "CCG-PRO-004", "ECG-PRO-004", "RS-BAD-003"}
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
        proposed = None
        if code in PENDING_CRITICALITY_IDS:
            proposed = criticality
            criticality = live["criticality"]
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
        if live:
            question["changed_from_v1_2"] = [f for f in CONTENT_FIELDS if question[f] != _clean(live.get(f))]
        if proposed:
            question["proposed_criticality"] = proposed
            question["proposed_criticality_status"] = proposed_by_id.get(code, {}).get("status", "pending approval")
        questions.append(question)

    matrix = _allocation_matrix(v12, recon)
    package = {
        "version": VERSION,
        "rule_set_version": RULE_SET_VERSION,
        "status": "DRAFT - not approved for Production import",
        "based_on": v12["version"],
        "source_files": {
            Path(xlsx_path).name: _sha256(xlsx_path),
            Path(recon_path).name: _sha256(recon_path),
        },
        "management_decisions": "2026-10-10: conditional approval of approved-question edits; "
        "Communication Ability Non-Critical for Commercial Cleaner and General Labor; 5/3/0 scoring "
        "for all questions; 21 Full / 7 Screening by role competency allocation matrix; no "
        "Production import or deployment.",
        "pending_approvals": [
            {"id": "CRIT-4", "item": "Non-Critical -> Critical for SNC-GEN-005, CCG-PRO-004, ECG-PRO-004, "
             "RS-BAD-003 (held at the approved v1.2 value in this package)"},
            {"id": "COMM-ST", "item": "Skilled Trades Communication Ability classified Non-Critical "
             "(role-profile value); needs justification and approval"},
            {"id": "MATRIX", "item": "Role competency allocation matrix, including the Behavioral -> "
             "Communication slot for Commercial Cleaner, General Labor and Skilled Trades"},
            {"id": "SME", "item": "Occupational SME sign-off (handoff exception 4)"},
            {"id": "PROD", "item": "Production import and cutover (separate approval)"},
        ],
        "role_code_map": v12["role_code_map"],
        "role_profiles": [
            {**profiles[role], "allocation": matrix[role]} for role in v12["role_code_map"]
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
