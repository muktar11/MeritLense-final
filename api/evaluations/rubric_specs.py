"""Question-specific rubric execution (rulebook GOV-06, SCR-01 to SCR-08).

Each question is scored by its own approved score note, held as executable
configuration in api/interviews/fixtures/question_bank/rubric_specs_*.json:

- generic 5 / 3 / 0 note: scored by its approved rule template applied
  through the question's mapping (management decisions of 22 Oct 2026):
  5 = all applicable Core outcomes; 3 = the central practical competence
  shown safely with compensable gaps; 0 = below minimum competence or a
  defined zero condition. A question whose Core outcomes are all central
  keeps a 3 band through its partial mapping, or is binary with a recorded
  justification. A conditional Core point counts only when its condition is
  established - silence alone never establishes it. Without a complete rule
  the older generic band applies (5 all, 3 one missing, two or more held as
  pending, SCR-08) and the question is not selected;
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
# question is binary; "prerequisite_omission" only where the rule has an
# approved prerequisite; "conditional_silent" only where it has a
# conditional Core point.
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
# A question with no partial competence to define (management decision 2 of
# 22 Oct 2026): scored 5 or 0, with a recorded justification.
EXCEPTION_BINARY = "BINARY"

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
    if spec.get("status") == "INACTIVE":
        return False  # kept out of use by management decision
    return not (spec.get("safeguards") and spec.get("safeguard_consequence") is None)


def conditional_ordinals(question_set_version, question_code):
    """Must Include points of the question's active rule that apply only when
    their condition is established (management decision 3 of 22 Oct 2026)."""
    entry = spec_for(question_set_version, question_code)
    rule = entry[1].get("rule") if entry else None
    return sorted(rule.get("conditional") or []) if rule else []


def selection_gate_active(question_set_version):
    spec_set = spec_set_for_version(question_set_version) or {}
    templates = (spec_set.get("classification") or {}).get("templates") or {}
    return any(t.get("status") == REVIEW_APPROVED for t in templates.values())


def active_rule(spec_set, question_code):
    """The rule that scores a generic-note question, or None (generic band).

    A question-specific rule needs the question APPROVED. A template rule
    needs its template APPROVED and a valid mapping; no question-by-question
    approval (management decisions of 22 Oct 2026). Either way its
    acceptance cases must cover every required category and pass."""
    classification = spec_set.get("classification") or {}
    entry = (classification.get("questions") or {}).get(question_code)
    if not entry:
        return None
    if entry.get("rule") and entry.get("review_status") == REVIEW_APPROVED and not acceptance_gaps(entry["rule"]):
        return entry["rule"]
    # Otherwise the question's approved template, if it has one.
    rule = template_rule(classification, question_code)
    return rule if rule is not None and not acceptance_gaps(rule) else None


def template_rule(classification, question_code):
    entry = classification["questions"][question_code]
    mapping = entry.get("mapping") or {}
    template = (classification.get("templates") or {}).get(mapping.get("template"))
    if not template or template.get("status") != REVIEW_APPROVED:
        return None
    return build_template_rule(entry, mapping, template, mapping.get("template"))


def build_template_rule(entry, mapping, template, template_id):
    """Full: every applicable Core outcome. Partial (the central practical
    competence): every central outcome, or the question's approved partial
    mapping where all Core outcomes are central (each group needs one of its
    points); the other Core outcomes are compensable gaps. A BINARY question
    has no 3 band and must say why. A question whose Core outcomes are all
    central with neither has no complete rule (decision 2 of 22 Oct 2026:
    partial credit is not removed merely because every outcome is central)."""
    core, central = sorted(entry["core"]), sorted(mapping.get("central") or [])
    if not core or not central or not set(central) <= set(core):
        return None
    partial = mapping.get("partial") or ([[o] for o in central] if set(central) != set(core) else None)
    if mapping.get("exception") == EXCEPTION_BINARY:
        if not (mapping.get("binary_justification") or "").strip():
            return None
        partial = None
    elif partial is None:
        return None
    elif not all(group and set(group) <= set(core) for group in partial) or \
            {group[0] for group in partial} == set(core):
        return None  # a partial band must be narrower than full credit
    conditional = sorted(set(mapping.get("conditional") or []) & set(core))
    rule = {
        "rule_version": f"{template_id}.v{template['version']}",
        "full": [[o] for o in core],
        "partial": partial,
        "prerequisites": [],
        "conditional": conditional,
        "negative_caps": {},
    }
    rule["acceptance_cases"] = generated_acceptance_cases(rule, core, conditional, mapping.get("total_points"))
    return rule


def generated_acceptance_cases(rule, core, conditional, total_points):
    """Expected outcomes derived from the mapping, so a mapping that cannot
    produce its template's bands (e.g. central outside Core) never scores.
    Conditions are established except where a case tests silence."""
    total = total_points or max(core)
    every = list(range(1, total + 1))
    partial = rule["partial"]

    def case(category, matched, score, negatives=(), established=conditional):
        return {"category": category, "matched": sorted(matched), "negatives": list(negatives),
                "established": list(established), "total_points": total, "expected": {"score": score}}

    first_group = set(partial[0]) if partial else {rule["full"][0][0]}
    cases = [
        case("full", every, 5),
        case("supporting_omission", core, 5),
        case("equivalent_paraphrase", core, 5),
        case("below_minimum", [o for o in core if o not in first_group], 0),
        case("affirmed_unsafe", every, 0, negatives=[1]),
        case("negated_unsafe", every, 5),
        {"category": "evidence_loss", "expected": {"status": "INCOMPLETE"}, "total_points": total},
    ]
    if partial:
        minimal = {group[0] for group in partial}
        cases.append(case("partial", minimal, 3))
    if conditional:
        # Silence on a conditional point: it does not apply, so it is not
        # missing; once its condition is established, it is a gap.
        unconditional = [o for o in core if o not in conditional]
        cases.append(case("conditional_silent", unconditional, 5 if unconditional else 0, established=()))
        if unconditional:
            partial_met = bool(partial) and all(set(group) & set(unconditional) for group in partial)
            cases.append(case("conditional_established", unconditional, 3 if partial_met else 0))
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
    if rule.get("conditional"):
        required.add("conditional_silent")
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
                          inapplicable_ordinals=case.get("inapplicable", []),
                          established_ordinals=case.get("established", []))
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


def _rule_band(rule, satisfied, matched):
    if not set(rule.get("prerequisites") or []) <= satisfied:
        return Decimal("0"), BAND_PREREQUISITE_MISSING
    if not matched & {o for group in rule["full"] for o in group}:
        # No applicable Core outcome evidenced: no competence shown, even
        # where every unmet point is conditional and not established.
        return Decimal("0"), BAND_ZERO if not matched else BAND_BELOW_MINIMUM
    if _band_met({"groups": rule["full"]}, satisfied, None):
        return None, BAND_FULL
    if rule.get("partial") and _band_met({"groups": rule["partial"]}, satisfied, None):
        return None, BAND_PARTIAL
    return Decimal("0"), BAND_BELOW_MINIMUM


def execute(spec, *, max_score, matched_ordinals, total_points, negative_ordinals, inapplicable_ordinals=(),
            established_ordinals=()):
    """Returns a dict: score, band, critical_failure, missing_count, status.

    matched_ordinals / negative_ordinals are 1-based positions in the
    question's Must Include and Negative Indicator lists. A conditional
    point applies only when it is in established_ordinals (its condition is
    established by the scenario or the answer); inapplicable_ordinals are
    points the answer shows do not apply."""
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
        # the answer shows does not apply counts as met, and so does a
        # conditional point whose condition is not established: silence
        # alone does not establish applicability (decision 3, 22 Oct 2026).
        rule = spec["rule"]
        inapplicable = set(inapplicable_ordinals) | (
            set(rule.get("conditional") or []) - matched - set(established_ordinals))
        satisfied = matched | inapplicable
        score, band = _rule_band(rule, satisfied, matched)
        if band == BAND_FULL:
            score = Decimal(str(spec["full_score"]))
        elif band == BAND_PARTIAL:
            score = Decimal(str(spec["partial_score"]))
        result["missing_prerequisites"] = sorted(set(rule.get("prerequisites") or []) - satisfied)
        result["inapplicable_ordinals"] = sorted(inapplicable)
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
        unmet_safeguards = sorted(set(spec.get("safeguards") or []) - matched - set(inapplicable_ordinals))
        if unmet_safeguards and spec.get("safeguard_consequence") is None:
            # ECG-TSK-001: the consequence of an applicable safeguard left
            # unevidenced is escalated, not guessed (REL-02).
            result.update(score=None, band=BAND_COMPLETION, status=STATUS_PENDING,
                          unmet_safeguards=unmet_safeguards)
            return result
        # Only the note's own steps count towards its denominator.
        steps = matched & set(spec["steps"]) if spec.get("steps") else matched
        completed = min(len(steps), denominator)
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
