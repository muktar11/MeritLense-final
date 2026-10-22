"""Apply the management decisions of 22 Oct 2026 to the GOV2.0-RC1 rubric
configuration (idempotent; run from the repository root):

1. the five rule templates are approved as the implementation framework
   (5 / 3 / 0 wording below), activating every mapped question;
2. the 24 questions whose Core outcomes were all marked central keep a
   partial-credit band wherever safe, meaningful partial competence can be
   defined; the rest are binary with a recorded justification;
3. conditional Core points are identified so that they affect scoring only
   when their condition is established (silence alone never establishes it);
4. the 147 additional Core proposals are adopted where the question's
   requirements support them, each with its rationale;
5. ECG-TSK-001 keeps its four original outcomes as the denominator and
   represents the mobility-plan and consent points as applicable safeguards;
   the scoring consequence of an unevidenced safeguard is escalated.

    python scripts/apply_rule_decisions_2026_10_22.py
"""
import json
import re
from pathlib import Path

BANK = Path(__file__).resolve().parents[1] / "api" / "interviews" / "fixtures" / "question_bank"
CLASSIFICATION = BANK / "rubric_core_points_gov2_0.json"
SPECS = BANK / "rubric_specs_gov2_0.json"
QUESTIONS = BANK / "governance_v2_0.json"
DECISION_REF = "Management decisions of 22 Oct 2026 (scoring templates, the 24 questions, selection and " \
               "conditional requirements, certificates and additional proposals, ECG-TSK-001)"
CLASSIFICATION_VERSION = "GOV2.0-RC1-core-reviewed-2026-10-22"
TEMPLATE_VERSION = 2


def split_points(text):
    # Same rule as import_question_bank_draft.split_points for English text.
    if ";" in text:
        return [p.strip() for p in text.split(";") if p.strip()]
    return [p.strip() for p in text.split(",") if p.strip()]


# ---------------------------------------------------------------- templates
TEMPLATE_TEXT = {
    "full": "5: all applicable Core outcomes are demonstrated correctly and safely. Equivalent methods and natural "
            "paraphrases are accepted; a missing detail within an outcome does not fail the outcome. Supporting "
            "points are never required.",
    "partial": "3: the answer demonstrates the question's central practical competence safely, with compensable "
               "gaps (any number of other Core outcomes missing). The central competence is the question's central "
               "outcome(s), or its approved partial-credit mapping.",
    "zero": "0: the answer does not demonstrate minimum practical competence (central competence not shown, or no "
            "applicable Core outcome evidenced), or meets an explicitly defined zero-score condition. An affirmed "
            "unsafe action is scored by the question's defined rules and severity: 0 and a critical failure unless "
            "the question's note gives it a band of its own.",
    "conditional": "A conditional Core point ('as required', 'if needed', 'if immediate danger') affects the score "
                   "only when its condition is established by the question's scenario or the candidate's own words. "
                   "Silence alone does not establish applicability; an inapplicable point is never missing. "
                   "Permission or manner limits ('if safe', 'only if trained', 'only as needed') do not make a "
                   "point conditional.",
    "combined": "A combined point counts when its components are evidenced in substance; source IDs and text are kept.",
    "exceptions": "Partial credit is kept wherever safe, meaningful partial competence can be defined (the question's "
                  "'partial' mapping). Binary scoring (exception BINARY) only where the assessed action cannot be "
                  "partially performed, each with a recorded justification. Approved legacy score notes are "
                  "unchanged.",
}

# ------------------------------------------------- decision 2: the 24 questions
# partial: groups of point numbers; every group needs one evidenced point
# (a group of several points means any one of them shows the competence).
PARTIAL = {
    "HCA-COM-004": ([[3]], "Adapting communication (clear simple language or an approved aid) is the central "
                           "competence; confirming understanding is a compensable gap."),
    "HCA-HYG-016": ([[1]], "Cleaning with a material-appropriate approved product is the central competence; "
                           "disinfection is conditional ('if required') and a compensable gap."),
    "CCG-TSK-017": ([[4]], "Close supervision is the safeguard that keeps the child safe during the activity; "
                           "material choice is a compensable gap (an affirmed unsafe material is still a zero "
                           "through the negative indicator)."),
    "CC-TSK-015": ([[2, 6]], "Either occupant-hazard control (wet-floor/trip control or keeping routes open) shows "
                             "safe cleaning around occupants; the other is a compensable gap. Blocking an emergency "
                             "route stays a zero through the negative indicator."),
    "CC-TSK-016": ([[2]], "Testing/using an approved product is the central competence; avoiding overwetting is a "
                          "compensable gap (affirmed overwetting stays a zero)."),
    "HK-TSK-004": ([[1, 3]], "Either element of the sequence (high to low, or floor last because dust falls) shows "
                             "the cleaning-order competence; the other is a compensable gap."),
    "NA-BEH-010": ([[1, 3]], "Protecting the patient or promptly reporting through safeguarding each shows safe "
                             "partial competence; the other is a compensable gap. Ignoring or joining in stays a "
                             "zero."),
    "NA-SAF-021": ([[1, 2]], "Stopping the transfer or locking the bed each removes the immediate risk; the other "
                             "is a compensable gap."),
    "EMS-COM-021": ([[1, 2]], "Escalating the change (point 1), or notifying the nurse/clinical lead with handover "
                              "(point 2), shows the central competence; the remaining procedure is a compensable "
                              "gap. Leaving without telling anyone stays a zero."),
    "EMS-TSK-010": ([[2]], "Recording what was actually consumed is the central competence of a recording "
                           "question; reporting concerning intake is a compensable gap (ignoring a concern when "
                           "affirmed stays a zero)."),
    "SNC-COM-012": ([[1, 4]], "Addressing the person first or preserving their voice and choices each shows the "
                              "central competence; the other is a compensable gap."),
    "SNC-HYG-017": ([[1, 2]], "Hand hygiene or cleaning the shared item each controls one contamination route; the "
                              "other is a compensable gap."),
    "IC-SAF-014": ([[1]], "Not removing the guard or entering without isolation and authorization is the safe "
                          "central action; specifying approved LOTO by authorized persons is a compensable gap."),
    "ES-TSK-010": ([[1]], "Checking aisles/exits, crowd, hazards and welfare is the patrol itself; reporting/"
                          "escalating is a compensable gap."),
    "DRV-TSK-021": ([[2]], "Inspecting the vehicle is the central competence; recording is a compensable gap "
                           "(falsifying or not recording damage when affirmed stays a zero)."),
    "ST-TSK-005": ([[1]], "Explaining task status, isolation/permit status and hazards is the central handover "
                          "competence; confirming understanding is a compensable gap (transferring responsibility "
                          "before confirmation when affirmed stays a zero)."),
    "ST-COM-005": ([[3]], "Reporting the actual result against the acceptance criterion honestly is the central "
                          "competence; stating safe status and next action is a compensable gap."),
    "ST-SAF-007": ([[1]], "Rerouting or protecting the cable removes the trip hazard; keeping the emergency route "
                          "clear is a compensable gap (blocking it when affirmed stays a zero)."),
    "LS-COM-009": ([[1, 2]], "A clear report of the problem or an urgent request for support each shows the central "
                             "competence; the other is a compensable gap."),
    "LS-SAF-011": ([[1, 2]], "Warning and stopping movement, or moving the person out of the crush zone, each "
                             "removes the immediate danger; the other is a compensable gap."),
    "LS-TSK-019": ([[2]], "Recording actual quantity/status is the central competence of a recording question; "
                          "prompt reporting of significant change is a compensable gap."),
}
BINARY = {
    "WS-SAF-020": "The assessed action is a single refusal to ride on forks or a pallet; both Core points state that "
                  "one act and it cannot be performed in part. Supporting points are not required for 5.",
    "FW-BEH-005": "The assessed action is a single refusal to use another person's authorization; both Core points "
                  "state that one act and it cannot be performed in part.",
    "FW-SAF-019": "The assessed action is a single refusal to ride without an approved seat; both Core points state "
                  "that one act and it cannot be performed in part.",
}

# ------------------------------------- decision 4: the 147 additional proposals
BASIS = {
    "TASK": "Adopted: a substantive part of the task or safeguard the question asks for. Required for 5; if missing "
            "with the central competence shown, 3. Not central, not a prerequisite, no gate.",
    "CONDITIONAL": "Not adopted: conditional or permission-limited action; it counts only where applicable, so "
                   "promoting it would make full credit depend on an unestablished condition. Stays Supporting.",
    "DUPLICATE": "Not adopted: restates the substance of an existing Core outcome; promotion would double-count it.",
    "GENERIC": "Not adopted: a generic step (stop, follow the procedure, routine hand hygiene) that the question's "
               "scenario does not turn on.",
    "SCOPE": "Not adopted: outside what the question asks or what its scenario establishes.",
}
ADDITIONAL = {
    "HK-SAF-002:3": "DUPLICATE", "HK-TSK-015:2": "CONDITIONAL", "HK-TSK-020:3": "TASK", "NA-BEH-002:4": "TASK",
    "NA-COM-001:2": "TASK", "NA-TSK-001:2": "TASK", "NA-TSK-002:1": "SCOPE", "NA-TSK-006:6": "CONDITIONAL",
    "NA-TSK-012:1": "SCOPE", "HCA-SAF-004:2": "TASK", "HCA-SAF-005:3": "CONDITIONAL", "HCA-TSK-001:5": "DUPLICATE",
    "HCA-TSK-002:2": "TASK", "HCA-TSK-003:7": "DUPLICATE", "HCA-HYG-011:5": "GENERIC", "HCA-HYG-014:5": "TASK",
    "HCA-HYG-016:6": "TASK", "HCA-HYG-018:5": "TASK", "HCA-HYG-019:3": "TASK", "HCA-HYG-019:5": "TASK",
    "HCA-SAF-012:2": "TASK", "HCA-SAF-013:3": "TASK", "HCA-SAF-015:2": "CONDITIONAL", "HCA-SAF-019:2": "TASK",
    "HCA-TSK-008:2": "TASK", "HCA-TSK-008:3": "SCOPE", "HCA-TSK-020:3": "TASK", "EMS-TSK-001:2": "TASK",
    "EMS-TSK-001:5": "TASK", "EMS-HYG-011:3": "TASK", "EMS-SAF-007:2": "TASK", "EMS-TSK-021:3": "TASK",
    "EMS-TSK-022:3": "TASK", "BPS-HYG-005:5": "TASK", "SNC-GEN-004:5": "TASK", "SNC-HYG-006:1": "DUPLICATE",
    "SNC-SAF-018:4": "CONDITIONAL", "CCG-TSK-001:2": "TASK", "CCG-SAF-010:3": "TASK", "CCG-SAF-021:4": "CONDITIONAL",
    "ECG-COM-016:5": "DUPLICATE", "ECG-HYG-004:3": "TASK", "HH-SHK-002:6": "SCOPE", "HH-HYG-012:3": "TASK",
    "HH-HYG-013:5": "CONDITIONAL", "FDA-SHK-001:6": "CONDITIONAL", "FDA-SAF-008:2": "TASK",
    "FDA-SAF-021:2": "DUPLICATE", "FDA-TSK-022:5": "TASK", "RS-SHK-002:7": "SCOPE", "RS-SHK-004:3": "TASK",
    "RS-HYG-018:2": "TASK", "RS-SAF-002:2": "DUPLICATE", "RS-SAF-003:2": "DUPLICATE", "RS-SAF-015:4": "TASK",
    "RS-TSK-011:5": "SCOPE", "CC-TEK-001:2": "TASK", "CC-BEH-008:1": "GENERIC", "CC-HYG-007:6": "CONDITIONAL",
    "CC-HYG-018:5": "CONDITIONAL", "CC-SAF-011:4": "TASK", "IC-SAF-009:4": "GENERIC", "IC-SAF-015:5": "CONDITIONAL",
    "IC-SAF-017:5": "GENERIC", "IC-SAF-020:4": "TASK", "IC-TSK-010:5": "TASK", "IC-TSK-012:1": "TASK",
    "IC-TSK-012:6": "TASK", "IC-TSK-016:6": "TASK", "SG-SJ-001:4": "DUPLICATE", "SG-SAF-006:5": "TASK",
    "SG-SAF-009:4": "GENERIC", "SG-SAF-013:5": "GENERIC", "ES-ER-005:2": "TASK", "ES-TSK-014:1": "TASK",
    "DRV-SAF-001:4": "CONDITIONAL", "DRV-SAF-006:4": "CONDITIONAL", "DRV-SAF-022:2": "TASK", "WS-RLS-003:5": "TASK",
    "GL-SS-003:1": "DUPLICATE", "GL-TSK-001:7": "TASK", "GL-SAF-001:2": "CONDITIONAL", "GL-SAF-017:3": "CONDITIONAL",
    "GL-SAF-021:3": "CONDITIONAL", "GL-TSK-013:6": "TASK", "ST-TSK-002:4": "TASK", "ST-TSK-019:5": "TASK",
    "FW-COM-002:5": "CONDITIONAL", "FW-SAF-002:4": "SCOPE", "FW-SAF-004:4": "CONDITIONAL",
    "FW-HYG-013:2": "CONDITIONAL", "FW-SAF-023:2": "CONDITIONAL", "LS-COM-001:3": "CONDITIONAL",
    "LS-COM-010:5": "TASK", "LS-HYG-006:2": "TASK", "HK-SAF-003:2": "CONDITIONAL", "HK-SAF-004:2": "TASK",
    "HK-SAF-006:3": "DUPLICATE", "HK-BEH-007:3": "TASK", "HK-HYG-006:1": "TASK", "HK-HYG-015:3": "SCOPE",
    "HK-HYG-019:3": "DUPLICATE", "HK-SAF-007:4": "TASK", "HK-SAF-008:2": "CONDITIONAL", "HK-SAF-009:2": "TASK",
    "HK-SAF-009:4": "CONDITIONAL", "HK-SAF-015:2": "CONDITIONAL", "HK-SAF-018:2": "CONDITIONAL",
    "HK-SAF-019:1": "CONDITIONAL", "HK-SAF-020:3": "TASK", "HK-SAF-021:2": "TASK", "HK-SAF-023:3": "DUPLICATE",
    "NA-HYG-005:4": "TASK", "NA-BEH-010:2": "CONDITIONAL", "NA-COM-015:2": "TASK", "NA-HYG-007:1": "TASK",
    "NA-HYG-007:4": "CONDITIONAL", "NA-HYG-010:4": "TASK", "NA-HYG-016:4": "TASK", "NA-SAF-012:3": "SCOPE",
    "NA-SAF-019:1": "TASK", "HCA-SAF-021:2": "GENERIC", "BPS-SAF-011:4": "CONDITIONAL", "HH-SAF-013:4": "TASK",
    "FDA-SHK-004:3": "TASK", "FDA-SAF-004:3": "TASK", "FDA-SAF-013:2": "CONDITIONAL", "RS-COM-005:2": "TASK",
    "RS-COM-011:3": "TASK", "CC-BEH-013:1": "GENERIC", "CC-HYG-002:4": "TASK", "CC-SAF-008:4": "GENERIC",
    "IC-BR-002:4": "TASK", "IC-CES-003:4": "TASK", "IC-BEH-009:1": "GENERIC", "IC-SAF-011:3": "TASK",
    "IC-SAF-021:1": "TASK", "SG-SAF-012:3": "GENERIC", "ES-SAF-002:4": "TASK", "DRV-TSK-001:3": "TASK",
    "DRV-BEH-002:1": "TASK", "WS-SAF-011:3": "TASK", "GL-COM-016:4": "DUPLICATE", "FW-BEH-005:3": "DUPLICATE",
    "LS-BEH-014:4": "CONDITIONAL", "LS-COM-005:3": "TASK", "LS-COM-008:3": "TASK",
}

# ------------------------------------------------ decision 3: conditional points
CONDITION_MARK = re.compile(
    r"\b(if|when (?:needed|required|indicated|necessary|applicable|appropriate|available)|"
    r"where (?:needed|required|applicable|available|indicated|necessary|possible|present)|"
    r"as (?:required|needed|applicable|indicated|necessary))\b", re.I)
LEADING_CONDITION = re.compile(r"^(?:only )?(?:if|when|where)\b", re.I)
# Qualifiers that limit how or by whom a step is done, not whether it applies.
MANNER_LIMIT = re.compile(r"\bonly (?:as|where|when|if) needed\b", re.I)
PERMISSION_LIMIT = re.compile(
    r"\bif (?:you are |they are |it is )?(?:trained|authori[sz]ed|permitted|allowed|safe|safely|competent|"
    r"qualified|policy permits|procedure permits|site procedure permits)\b", re.I)
NEED_CONDITION = re.compile(r"\b(?:as|when|where|if) (?:required|needed|necessary|indicated)\b", re.I)


def point_condition(text):
    """'CONDITION' when the whole point applies only in a situation it names;
    None for permission/manner limits and for a condition on one detail of a
    compound point (decision 1: a missing detail does not fail the outcome)."""
    if not CONDITION_MARK.search(text):
        return None
    if LEADING_CONDITION.search(text):
        return "CONDITION"
    if MANNER_LIMIT.search(text):
        return None
    if PERMISSION_LIMIT.search(text) and not NEED_CONDITION.search(text):
        return None
    if "," in text:
        return None
    return "CONDITION"


def write_classification(data):
    """The file's existing layout: one line per question entry."""
    questions = data.pop("questions")
    head = json.dumps(data, indent=2, ensure_ascii=False)[:-2]
    rows = ",\n".join(f"    {json.dumps(code)}: {json.dumps(entry, ensure_ascii=False)}"
                      for code, entry in questions.items())
    CLASSIFICATION.write_text(f'{head},\n  "questions": {{\n{rows}\n  }}\n}}\n', encoding="utf-8")
    data["questions"] = questions


def main():
    questions = {q["question_code"]: q for q in json.loads(QUESTIONS.read_text(encoding="utf-8"))["questions"]}
    data = json.loads(CLASSIFICATION.read_text(encoding="utf-8"))
    data["classification_version"] = CLASSIFICATION_VERSION
    if "GOV2.0-RC1-core-reviewed-2026-10-20" not in data["supersedes"]:
        data["supersedes"].append("GOV2.0-RC1-core-reviewed-2026-10-20")
    data["status"] = "TEMPLATES_APPROVED"
    data["activation"] = (
        "A generic-note question is scored by its template rule: the template is APPROVED and the question's "
        "mapping is valid (Core outcomes, central outcomes or an approved partial mapping, conditional points) "
        "and its generated acceptance cases pass. A question where every Core outcome is central needs a "
        "partial mapping or a BINARY justification. A question-specific 'rule', where present, needs the "
        "question APPROVED. Questions without a complete rule are not selected (decision 3).")
    data["decisions_2026_10_22"] = {
        "reference": DECISION_REF,
        "templates": "Approved as the implementation framework with question-specific mappings.",
        "partial_credit": "21 of the 24 all-central questions keep a 3 band through a partial mapping; 3 are "
                          "BINARY with a justification.",
        "conditional": "Conditional Core points affect scoring only when the condition is established; silence "
                       "alone does not establish applicability.",
        "additional_core": "Of the 147 additional Core proposals, those supported by the question's requirements "
                           "are adopted (core_additions records every decision and its rationale). Classification "
                           "creates no mandatory, safety or certificate gate.",
    }
    for template in data["templates"].values():
        template.update(TEMPLATE_TEXT, status="APPROVED", version=TEMPLATE_VERSION,
                        approved={"by": "Management", "date": "2026-10-22", "reference": DECISION_REF})

    adopted = 0
    for code, entry in data["questions"].items():
        points = split_points(questions[code]["must_include_en"])
        mapping = entry["mapping"]
        if "core_additions" not in entry:
            additions = []
            for point in entry.get("candidate_additional_core") or []:
                basis = ADDITIONAL[f"{code}:{point}"]
                additions.append({"point": point, "text": points[point - 1],
                                  "decision": "ADOPTED" if basis == "TASK" else "NOT_ADOPTED",
                                  "basis": basis, "rationale": BASIS[basis]})
                if basis == "TASK":
                    entry["core"] = sorted(set(entry["core"]) | {point})
            if additions:
                entry["core_additions"] = additions
        adopted += sum(a["decision"] == "ADOPTED" for a in entry.get("core_additions", []))
        mapping.pop("exception", None)
        mapping.pop("partial", None)
        mapping.pop("partial_rationale", None)
        mapping.pop("binary_justification", None)
        if code in PARTIAL:
            mapping["partial"], mapping["partial_rationale"] = PARTIAL[code]
        elif code in BINARY:
            mapping["exception"] = "BINARY"
            mapping["binary_justification"] = BINARY[code]
        conditional = [o for o in entry["core"] if point_condition(points[o - 1])]
        if conditional:
            mapping["conditional"] = conditional
        else:
            mapping.pop("conditional", None)
    assert adopted == sum(1 for basis in ADDITIONAL.values() if basis == "TASK"), adopted
    assert len(ADDITIONAL) == 147 and len(PARTIAL) + len(BINARY) == 24
    write_classification(data)

    specs = json.loads(SPECS.read_text(encoding="utf-8"))
    specs["spec_version"] = "GOV2.0-RC1-rubrics-2026-10-22"
    ecg = specs["questions"]["ECG-TSK-001"]
    ecg.pop("pending_unless_complete", None)
    ecg.update({
        "denominator": 4,
        "steps": [3, 4, 5, 6],
        "safeguards": [1, 2],
        "safeguard_consequence": None,
        "open": "Escalated (RULEBOOK_V1_ESCALATIONS.md, Esc 6). The four original v1.2 outcomes are the "
                "denominator: 3 = positions self correctly, 4 = supports weight properly, 5 = moves slowly, "
                "6 = checks balance. Points 1 (check the mobility plan and required aid or helpers) and 2 "
                "(explain and obtain consent) are applicable safeguards: an answer that evidences both is "
                "scored steps/4; an affirmed unsafe action is scored by the negative indicators. The consequence "
                "of an unevidenced safeguard cannot be resolved from the agreed rules (no caps, not optional, "
                "denominator fixed at 4), so such an answer is held and the question is not selected until "
                "decided.",
    })
    SPECS.write_text(json.dumps(specs, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"templates approved; {adopted} additional Core points adopted; "
          f"{sum(1 for e in data['questions'].values() if e['mapping'].get('conditional'))} questions with "
          f"conditional points")


if __name__ == "__main__":
    main()
