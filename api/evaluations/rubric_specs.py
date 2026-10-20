"""Question-specific rubric execution (rulebook GOV-06, SCR-01 to SCR-08).

Each question is scored by its own approved score note, held as executable
configuration in api/interviews/fixtures/question_bank/rubric_specs_*.json:

- generic 5 / 3 / 0 note: 5 when every point is evidenced, 3 when exactly one
  is missing; the undefined "two or more missing" case is left unscored as
  pending approval (SCR-08, management decision A of 20 Oct 2026). A
  question is scored by its own rule instead once that rule is complete:
  a question-specific rule, or a reusable rule template applied through the
  question's mapping (Core outcomes, central outcomes, exceptions), with
  passing acceptance cases (management direction of 20 Oct 2026: no
  count-based rule; Core alone never zeroes an answer; only new
  methodological decisions need approval);
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
# Reviewed Core / Supporting classification of the generic-note questions,
# reusable rule templates, and each question's mapping onto its template.
CLASSIFICATION_FILES = ("rubric_core_points_gov2_0.json",)
REVIEW_APPROVED = "APPROVED"
# Acceptance categories a rule must cover and pass before it scores
# (management review of 20 Oct 2026). "partial" is not required where the
# question is an approved no-partial-credit exception; "prerequisite_omission"
# only where the rule has an approved prerequisite.
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
        rule = active_rule(spec_set, question_code)
        if rule is not None:
            # The result records exactly which rule version scored it.
            spec = {**spec, "rule": rule, "negative_caps": rule.get("negative_caps") or {}}
            spec_version = f"{spec_version}+{spec_set['classification']['classification_version']}+{rule['rule_version']}"
    return spec_version, spec


def rule_complete(question_set_version, question_code):
    """True when the question's answers can be scored without a held band:
    a legacy or question-specific spec that is fully defined, or a generic
    question with an active rule. Questions without a complete rule are
    not selected once the bank has an approved rule template."""
    entry = spec_for(question_set_version, question_code)
    if entry is None:
        return True
    spec = entry[1]
    if spec.get("kind") == "generic_530":
        return bool(spec.get("rule"))
    return not spec.get("pending_unless_complete")


def selection_gate_active(question_set_version):
    spec_set = spec_set_for_version(question_set_version) or {}
    templates = (spec_set.get("classification") or {}).get("templates") or {}
    return any(t.get("status") == REVIEW_APPROVED for t in templates.values())


def active_rule(spec_set, question_code):
    """The rule that scores a generic-note question, or None (generic band).

    A question-specific rule needs the question APPROVED. A template rule
    needs its template APPROVED, a valid mapping, and - for a mapping
    exception such as no partial credit - the question APPROVED. Either way
    its acceptance cases must cover every required category and pass."""
    classification = spec_set.get("classification") or {}
    entry = (classification.get("questions") or {}).get(question_code)
    if not entry:
        return None
    if entry.get("rule"):
        rule = entry["rule"] if entry.get("review_status") == REVIEW_APPROVED else None
    else:
        rule = template_rule(classification, question_code)
    return rule if rule is not None and not acceptance_gaps(rule) else None


def template_rule(classification, question_code):
    entry = classification["questions"][question_code]
    mapping = entry.get("mapping") or {}
    template = (classification.get("templates") or {}).get(mapping.get("template"))
    if not template or template.get("status") != REVIEW_APPROVED:
        return None
    if mapping.get("exception") and entry.get("review_status") != REVIEW_APPROVED:
        return None
    return build_template_rule(entry, mapping, template, mapping.get("template"))


def build_template_rule(entry, mapping, template, template_id):
    """Full: every Core outcome. Partial (minimum viable competence): every
    central outcome; the other Core outcomes are compensable gaps. A no-
    partial-credit exception has no 3 band."""
    core, central = sorted(entry["core"]), sorted(mapping.get("central") or [])
    if not core or not central or not set(central) <= set(core):
        return None
    no_partial = mapping.get("exception") == "NO_PARTIAL_CREDIT" or set(central) == set(core)
    rule = {
        "rule_version": f"{template_id}.v{template['version']}",
        "full": [[o] for o in core],
        "partial": None if no_partial else [[o] for o in central],
        "prerequisites": [],
        "negative_caps": {},
    }
    rule["acceptance_cases"] = generated_acceptance_cases(rule, core, central, mapping.get("total_points"))
    return rule


def generated_acceptance_cases(rule, core, central, total_points):
    """Expected outcomes derived from the mapping, so a mapping that cannot
    produce its template's bands (e.g. central outside Core) never scores."""
    total = total_points or max(core)
    every = list(range(1, total + 1))
    non_central = [o for o in core if o not in central]

    def case(category, matched, score, negatives=()):
        return {"category": category, "matched": matched, "negatives": list(negatives),
                "total_points": total, "expected": {"score": score}}

    cases = [
        case("full", every, 5),
        case("supporting_omission", core, 5),
        case("equivalent_paraphrase", core, 5),
        case("below_minimum", [o for o in core if o != central[0]], 0),
        case("affirmed_unsafe", every, 0, negatives=[1]),
        case("negated_unsafe", every, 5),
        {"category": "evidence_loss", "expected": {"status": "INCOMPLETE"}, "total_points": total},
    ]
    if rule["partial"]:
        cases.append(case("partial", central, 3 if non_central else 5))
    return cases


def acceptance_gaps(rule):
    """Required acceptance categories the rule's cases do not cover, plus any
    case whose expected outcome the rule does not produce. Empty = ready."""
    if not rule or not rule.get("rule_version") or not rule.get("full"):
        return ["rule"]
    required = set(ACCEPTANCE_CATEGORIES)
    if not rule.get("partial"):
        required.discard("partial")
    if rule.get("prerequisites"):
        required.add("prerequisite_omission")
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
    if rule.get("partial") and _band_met({"groups": rule["partial"]}, satisfied, None):
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
        # The question's own rule. "full" and "partial" are lists of groups
        # (a group lists equivalent points; any one suffices), all of which
        # must be evidenced; prerequisites must hold for any credit. A point
        # the answer shows does not apply counts as met. An unmentioned
        # conditional point is a candidate omission, never a hold.
        rule = spec["rule"]
        satisfied = matched | set(inapplicable_ordinals)
        score, band = _rule_band(rule, satisfied)
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
        completed = min(len(matched), denominator)
        score = _quantize(max_score * Decimal(completed) / Decimal(denominator)) if denominator else Decimal("0")
        band = BAND_COMPLETION
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
