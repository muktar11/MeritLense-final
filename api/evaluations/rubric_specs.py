"""Question-specific rubric execution (rulebook GOV-06, SCR-01 to SCR-08).

Each question is scored by its own approved score note, held as executable
configuration in api/interviews/fixtures/question_bank/rubric_specs_*.json:

- generic 5 / 3 / 0 note: 5 when every point is evidenced, 3 when exactly one
  is missing; the undefined "two or more missing" case is left unscored as
  pending approval (SCR-08, management decision A of 20 Oct 2026). A
  question is scored by its own approved rule instead (full, partial and
  prerequisite predicates, conditional points) only once that rule is
  approved and its acceptance cases pass (management review of 20 Oct 2026:
  no count-based rule; Core alone never zeroes an answer);
- legacy notes (SCR-05): 0/5, 0/2/3 and completion %, executed as written;
- a Must Include point is never mandatory by default (management decision of
  20 Oct 2026, SCR-06), so no point zeroes an answer on its own;
- an affirmed negative indicator scores 0 and is a critical failure, as
  before, unless the question's note gives it a band of its own (for example
  HK-BEH-001: "3: delayed").
"""
import json
from decimal import ROUND_HALF_UP, Decimal
from functools import lru_cache
from pathlib import Path

SPEC_DIR = Path(__file__).resolve().parents[1] / "interviews" / "fixtures" / "question_bank"
SPEC_FILES = ("rubric_specs_gov2_0.json",)
# Reviewed Core / Supporting classification of the generic-note questions
# and, per question, its approved scoring rule. A rule is applied only when
# the question is APPROVED and its acceptance cases cover the required
# categories and pass; every other question keeps the generic rule.
CLASSIFICATION_FILES = ("rubric_core_points_gov2_0.json",)
REVIEW_APPROVED = "APPROVED"
# Acceptance categories required before a question rule can activate
# (management review of 20 Oct 2026). "prerequisite_omission" and
# "condition_inapplicable" apply only to rules that have such points.
ACCEPTANCE_CATEGORIES = (
    "full", "partial", "supporting_omission", "below_minimum", "equivalent_paraphrase",
    "affirmed_unsafe", "negated_unsafe", "evidence_loss",
)

BAND_FULL = "FULL"
BAND_PARTIAL = "PARTIAL"
BAND_ZERO = "ZERO"
BAND_NEGATIVE = "NEGATIVE_INDICATOR"
BAND_NEGATIVE_CAP = "NEGATIVE_INDICATOR_BAND"
BAND_COMPLETION = "COMPLETION"
BAND_MULTI_OMISSION = "MULTI_OMISSION"
# Question rules: an approved prerequisite missing (0, not a critical
# failure - distinct from an affirmed unsafe action), and an answer below
# the question's approved minimum viable competence.
BAND_PREREQUISITE_MISSING = "PREREQUISITE_MISSING"
BAND_BELOW_MINIMUM = "BELOW_MINIMUM"
# A conditional point is missing, the answer does not establish whether its
# condition applies, and that decides the band: held, never guessed.
BAND_CONDITION_UNDETERMINED = "CONDITION_UNDETERMINED"

# The answer's band has no approved rule yet: it is left unscored (never a
# guessed zero) and recalculated once the rule is approved (REL-02).
STATUS_PENDING = "PENDING_APPROVAL"


@lru_cache(maxsize=None)
def _load_specs():
    specs = {}
    for name in SPEC_FILES:
        path = SPEC_DIR / name
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            specs[data["bank_version"]] = data
    for name in CLASSIFICATION_FILES:
        path = SPEC_DIR / name
        if path.exists():
            classification = json.loads(path.read_text(encoding="utf-8"))
            if classification["bank_version"] in specs:
                specs[classification["bank_version"]] = {
                    **specs[classification["bank_version"]],
                    "classification": classification,
                }
    return specs


def spec_set_for_version(question_set_version):
    return _load_specs().get(question_set_version or "")


def spec_for(question_set_version, question_code):
    """(spec_version, spec) for a question, or None when its bank version has
    no rubric configuration (pre-pooled versions keep their old scoring)."""
    spec_set = spec_set_for_version(question_set_version)
    if spec_set is None:
        return None
    spec_version = spec_set["spec_version"]
    spec = spec_set["questions"].get(question_code)
    if spec is None:
        spec = {"kind": "generic_530", **spec_set["generic_530"]}
        classification = spec_set.get("classification") or {}
        entry = (classification.get("questions") or {}).get(question_code)
        if entry and entry.get("review_status") == REVIEW_APPROVED and rule_is_activatable(entry.get("rule")):
            # The result records exactly which rule version scored it.
            spec = {**spec, "rule": entry["rule"], "negative_caps": entry["rule"].get("negative_caps") or {}}
            spec_version = f"{spec_version}+{classification['classification_version']}+{entry['rule']['rule_version']}"
    return spec_version, spec


def acceptance_gaps(rule):
    """Required acceptance categories the rule's cases do not cover, plus any
    case whose expected outcome the rule does not produce. Empty = ready."""
    if not rule or not rule.get("rule_version") or not rule.get("full") or not rule.get("partial"):
        return ["rule"]
    required = set(ACCEPTANCE_CATEGORIES)
    if rule.get("prerequisites"):
        required.add("prerequisite_omission")
    if rule.get("conditional"):
        required.add("condition_inapplicable")
    cases = rule.get("acceptance_cases") or []
    gaps = sorted(required - {case.get("category") for case in cases})
    spec = {"kind": "generic_530", "full_score": 5, "partial_score": 3, "rule": rule,
            "negative_caps": rule.get("negative_caps") or {}}
    for case in cases:
        if case.get("category") == "evidence_loss":
            # Exercised by the scorer: lost evidence is incomplete, never
            # an executed band (SCR-04). The case documents the expectation.
            continue
        outcome = execute(spec, max_score=5, matched_ordinals=case.get("matched", []),
                          total_points=case["total_points"], negative_ordinals=case.get("negatives", []),
                          inapplicable_ordinals=case.get("inapplicable", []))
        expected = case["expected"]
        score_ok = "score" not in expected or (
            outcome["score"] is not None and Decimal(str(expected["score"])) == outcome["score"])
        if expected.get("status", "DEFINED") != outcome["status"] or not score_ok:
            gaps.append(f"case {case.get('name') or case['category']}: expected {expected}, "
                        f"got {outcome['score']} {outcome['status']}")
    return gaps


def rule_is_activatable(rule):
    return not acceptance_gaps(rule)


def _quantize(value):
    return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _band_met(band, matched, total):
    if band.get("all"):
        return total > 0 and len(matched) == total
    if "min_count" in band:
        return len(matched) >= band["min_count"]
    groups = band.get("groups") or []
    return bool(groups) and all(any(ordinal in matched for ordinal in group) for group in groups)


def _rule_band(rule, satisfied):
    if not set(rule.get("prerequisites") or []) <= satisfied:
        return Decimal("0"), BAND_PREREQUISITE_MISSING
    if _band_met({"groups": rule["full"]}, satisfied, None):
        return None, BAND_FULL
    if _band_met({"groups": rule["partial"]}, satisfied, None):
        return None, BAND_PARTIAL
    return Decimal("0"), BAND_BELOW_MINIMUM


def execute(spec, *, max_score, matched_ordinals, total_points, negative_ordinals, inapplicable_ordinals=()):
    """Returns a dict: score, band, critical_failure, missing_count, status.

    matched_ordinals / negative_ordinals are 1-based positions in the
    question's Must Include and Negative Indicator lists."""
    max_score = Decimal(str(max_score))
    matched = set(matched_ordinals)
    missing_count = max(total_points - len(matched), 0)
    result = {"missing_count": missing_count, "critical_failure": False, "status": "DEFINED"}

    caps = {int(key): Decimal(str(value)) for key, value in (spec.get("negative_caps") or {}).items()}
    uncapped = sorted(o for o in negative_ordinals if o not in caps)
    if uncapped:
        # An affirmed negative indicator is the note's hard-negative 0 band.
        result.update(score=Decimal("0.00"), band=BAND_NEGATIVE, critical_failure=True,
                      negative_ordinals=uncapped)
        return result

    kind = spec.get("kind")
    if kind == "generic_530" and spec.get("rule"):
        # The question's own approved rule. "full" and "partial" are lists of
        # groups (a group lists equivalent points; any one suffices), all of
        # which must be evidenced; prerequisites must hold for any credit.
        # An inapplicable conditional point counts as satisfied; one whose
        # applicability is unknown is tried both ways, and if that changes
        # the band the answer is held rather than guessed.
        rule = spec["rule"]
        conditional = set(rule.get("conditional") or [])
        satisfied = matched | (conditional & set(inapplicable_ordinals))
        undetermined = conditional - satisfied
        low = _rule_band(rule, satisfied)
        high = _rule_band(rule, satisfied | undetermined)
        if low[1] != high[1]:
            result.update(score=None, band=BAND_CONDITION_UNDETERMINED, status=STATUS_PENDING,
                          undetermined_ordinals=sorted(undetermined))
            return result
        score, band = low
        if band == BAND_FULL:
            score = Decimal(str(spec["full_score"]))
        elif band == BAND_PARTIAL:
            score = Decimal(str(spec["partial_score"]))
        result["missing_prerequisites"] = sorted(set(rule.get("prerequisites") or []) - satisfied)
    elif kind == "generic_530":
        if missing_count == 0 and total_points > 0:
            score, band = Decimal(str(spec["full_score"])), BAND_FULL
        elif missing_count <= spec["partial_max_missing"] and total_points > 0:
            score, band = Decimal(str(spec["partial_score"])), BAND_PARTIAL
        elif not matched:
            # No required point at all is insufficient evidence: the
            # approved zero condition of SCR-03, not an undefined band.
            score, band = Decimal("0"), BAND_ZERO
        elif spec.get("multi_omission_score") is None:
            # Management decision A (20 Oct 2026): no interim zero for
            # customer scoring while the rule awaits approval.
            result.update(score=None, band=BAND_MULTI_OMISSION, status=STATUS_PENDING)
            return result
        else:
            score, band = Decimal(str(spec["multi_omission_score"])), BAND_MULTI_OMISSION
    elif kind == "completion":
        denominator = int(spec["denominator"])
        if spec.get("pending_unless_complete") and len(matched) < total_points:
            result.update(score=None, band=BAND_COMPLETION, status=STATUS_PENDING)
            return result
        # "counted": only these steps count toward the denominator;
        # "missing_caps": a separate requirement whose absence caps the
        # score at a fraction of the maximum.
        counted = set(spec.get("counted") or matched)
        completed = min(len(matched & counted), denominator)
        score = _quantize(max_score * Decimal(completed) / Decimal(denominator)) if denominator else Decimal("0")
        band = BAND_COMPLETION
        caps_hit = {int(o): Decimal(str(f)) for o, f in (spec.get("missing_caps") or {}).items() if int(o) not in matched}
        if caps_hit:
            score = min(score, _quantize(max_score * min(caps_hit.values())))
            result["capped_by_missing"] = sorted(caps_hit)
    else:
        score, band = Decimal("0"), BAND_ZERO
        for entry in spec.get("bands") or []:
            if _band_met(entry, matched, total_points):
                score = Decimal(str(entry["score"]))
                band = BAND_FULL if score >= max_score else BAND_PARTIAL
                break

    capped = [caps[o] for o in negative_ordinals if o in caps]
    if capped and min(capped) < score:
        score, band = min(capped), BAND_NEGATIVE_CAP
    result.update(score=_quantize(min(score, max_score)), band=band)
    if capped:
        result["negative_ordinals"] = sorted(o for o in negative_ordinals if o in caps)
    return result
