import re


FIXED_QUESTION_SKILL_TAGS = (
    "Safety Awareness",
    "Behavior & Integrity",
    "Psych & Professional",
    "Task Execution",
)
CONSISTENCY_SKILL_TAG = "Consistency"
ALL_CANONICAL_SKILL_TAGS = FIXED_QUESTION_SKILL_TAGS + (CONSISTENCY_SKILL_TAG,)

SKILL_TAG_CODES = {
    "Safety Awareness": "safety_awareness",
    "Behavior & Integrity": "behavior_integrity",
    "Psych & Professional": "psych_professional",
    "Task Execution": "task_execution",
    "Consistency": "consistency",
}


def _normalize_key(value):
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").strip().lower()).strip()


def _slugify_skill(value):
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")


_SKILL_TAG_ALIASES = {}


def _register_aliases(label, *values):
    for value in values:
        key = _normalize_key(value)
        if key:
            _SKILL_TAG_ALIASES[key] = label


_register_aliases(
    "Safety Awareness",
    "Safety Awareness",
    "safety_awareness",
    "Patient Safety",
    "Hygiene & Infection Control",
    "Road & Load Safety",
    "Chemical & Equipment Safety",
    "Emergency Response",
    "Agricultural Safety",
    "Service & Hygiene Knowledge",
    "Site Safety",
    "Knowledge & Safety",
    "patient_safety",
    "hygiene_infection_control",
    "road_load_safety",
    "chemical_equipment_safety",
    "emergency_response",
    "agricultural_safety",
    "service_hygiene_knowledge",
    "site_safety",
    "knowledge_score",
    "hygiene_score",
)
_register_aliases(
    "Behavior & Integrity",
    "Behavior & Integrity",
    "behavior_integrity",
    "Integrity & Reliability",
    "Integrity & Discipline",
    "Behavior & Reliability",
    "Integrity & Guest Relations",
    "integrity_reliability",
    "integrity_discipline",
    "behavior_reliability",
    "integrity_guest_relations",
    "behavioral_score",
)
_register_aliases(
    "Psych & Professional",
    "Psych & Professional",
    "psych_professional",
    "Behavior & Empathy",
    "Behavior & Attention to Detail",
    "Situational Judgment",
    "Technical Knowledge",
    "Task & Environmental Knowledge",
    "Task & Crop Knowledge",
    "Knowledge & Task",
    "behavior_empathy",
    "behavior_attention_to_detail",
    "situational_judgment",
    "technical_knowledge",
    "task_environmental_knowledge",
    "task_crop_knowledge",
    "task_knowledge_score",
    "technical_score",
    "psych_score",
)
_register_aliases(
    "Task Execution",
    "Task Execution",
    "task_execution",
    "task_score",
)
_register_aliases(
    "Consistency",
    "Consistency",
    "Behavioral Consistency",
    "consistency",
    "consistency_score",
)


def normalize_skill_tag(value="", *, scoring_type="", fallback=""):
    key = _normalize_key(value)
    if key in _SKILL_TAG_ALIASES:
        return _SKILL_TAG_ALIASES[key]

    if str(scoring_type or "").strip().lower() == "completion_pct":
        return "Task Execution"

    return str(value or fallback or "").strip()


def normalize_skill_code(value="", *, scoring_type="", fallback=""):
    label = normalize_skill_tag(value=value, scoring_type=scoring_type, fallback=fallback)
    if label in SKILL_TAG_CODES:
        return SKILL_TAG_CODES[label]
    return _slugify_skill(label)


def normalize_skill_fields(*, skill_tag="", skill="", skill_id="", scoring_type="", fallback=""):
    raw_label = str(skill_tag or skill or fallback or "").strip()
    explicit_skill = str(skill or "").strip()
    label = normalize_skill_tag(value=raw_label, scoring_type=scoring_type, fallback=fallback)
    if not label:
        return {
            "skill_tag": "",
            "skill": "",
            "skill_id": "",
        }

    is_canonical = label in SKILL_TAG_CODES
    display_skill = label if is_canonical else (explicit_skill or label)
    display_tag = label if is_canonical else (str(skill_tag or explicit_skill or fallback or "").strip() or label)
    explicit_code = str(skill_id or "").strip()
    code = normalize_skill_code(
        value=explicit_code or raw_label or display_skill,
        scoring_type=scoring_type,
        fallback=label,
    )
    return {
        "skill_tag": display_tag,
        "skill": display_skill,
        "skill_id": code,
    }


def is_fixed_skill_tag(value):
    return normalize_skill_tag(value) in ALL_CANONICAL_SKILL_TAGS
