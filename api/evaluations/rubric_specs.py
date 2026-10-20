"""Question-specific rubric execution (rulebook GOV-06, SCR-01 to SCR-08).

Each question is scored by its own approved score note, held as executable
configuration in api/interviews/fixtures/question_bank/rubric_specs_*.json:

- generic 5 / 3 / 0 note: 5 when every point is evidenced, 3 when exactly one
  is missing; the undefined "two or more missing" case is left unscored as
  pending approval (SCR-08, management decision A of 20 Oct 2026);
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
# Core/supporting classification of the generic-note questions (decision A,
# 20 Oct 2026). Applied only once its status is APPROVED.
CORE_FILES = ("rubric_core_points_gov2_0.json",)
CORE_APPROVED = "APPROVED"

BAND_FULL = "FULL"
BAND_PARTIAL = "PARTIAL"
BAND_ZERO = "ZERO"
BAND_NEGATIVE = "NEGATIVE_INDICATOR"
BAND_NEGATIVE_CAP = "NEGATIVE_INDICATOR_BAND"
BAND_COMPLETION = "COMPLETION"
BAND_MULTI_OMISSION = "MULTI_OMISSION"

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
    for name in CORE_FILES:
        path = SPEC_DIR / name
        if path.exists():
            core = json.loads(path.read_text(encoding="utf-8"))
            if core.get("status") == CORE_APPROVED and core["bank_version"] in specs:
                specs[core["bank_version"]] = {
                    **specs[core["bank_version"]],
                    "core_points": core["core_points"],
                    "core_scores": core["approved_scores"],
                    "spec_version": f"{specs[core['bank_version']]['spec_version']}+{core['classification_version']}",
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
    spec = spec_set["questions"].get(question_code)
    if spec is None:
        spec = {"kind": "generic_530", **spec_set["generic_530"]}
        core = (spec_set.get("core_points") or {}).get(question_code)
        if core:
            spec = {**spec, "core": core, "core_scores": spec_set["core_scores"]}
    return spec_set["spec_version"], spec


def _quantize(value):
    return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _band_met(band, matched, total):
    if band.get("all"):
        return total > 0 and len(matched) == total
    if "min_count" in band:
        return len(matched) >= band["min_count"]
    groups = band.get("groups") or []
    return bool(groups) and all(any(ordinal in matched for ordinal in group) for group in groups)


def execute(spec, *, max_score, matched_ordinals, total_points, negative_ordinals):
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
    if kind == "generic_530" and spec.get("core"):
        # Approved core/supporting classification: the note's "core
        # evidence" and "material element" are the core points; supporting
        # details never lower the band.
        scores = spec["core_scores"]
        missing_core = len(set(spec["core"]) - matched)
        result["missing_core_count"] = missing_core
        if missing_core == 0:
            score, band = Decimal(str(scores["full"])), BAND_FULL
        elif missing_core == 1:
            score, band = Decimal(str(scores["one_core_missing"])), BAND_PARTIAL
        else:
            score, band = Decimal(str(scores["two_or_more_core_missing"])), BAND_ZERO
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
