"""Competency-allocation question selection for pooled question banks
(GOV2.0 onwards).

Selection rules (management decision, 10 Oct 2026):
- Exactly the tier's question count (21 Full / 7 Screening), filled
  competency by competency from the role's approved allocation matrix.
- Every Critical competency in the role profile is covered.
- Only questions of the session's role and language; Screening draws only
  from Screening-eligible questions.
- Candidate-skill matching is secondary: it only orders questions *within*
  a competency, never changes how many each competency gets.
- Retakes use equivalent alternative questions (same competency, not seen
  before) wherever the pool allows; a previously seen question is reused
  only when the competency has no unseen alternative left.
- If the pool cannot satisfy the allocation or Critical coverage, raise
  AllocationGap for review instead of bypassing the methodology.

Pure Python on purpose: it runs on fixture rows (offline validation and
simulation) and on QuestionTemplate rows alike, via `QuestionCandidate`.
It is not wired into live session generation, which still uses the fixed
GOV1.2 sets until a separate Production approval.
"""
import random
from dataclasses import dataclass, field

FULL = "FULL"
SCREENING = "SCREENING"
TIER_TARGETS = {FULL: 21, SCREENING: 7}


@dataclass(frozen=True)
class QuestionCandidate:
    code: str
    role_code: str
    language: str
    competency_code: str
    critical: bool
    screening_eligible: bool
    keywords: tuple = ()

    @classmethod
    def from_fixture(cls, question, language):
        return cls(
            code=question["question_code"],
            role_code=question["role_code"],
            language=language,
            competency_code=question["competency_code"],
            critical=question["criticality"] == "Critical",
            screening_eligible=bool(question["screening_eligible"]),
            keywords=tuple(k.strip().lower() for k in question["ideal_keywords"].split(";") if k.strip()),
        )

    @classmethod
    def from_template(cls, template):
        from api.core.constants import InterviewEvaluationTier

        return cls(
            code=template.question_code,
            role_code=template.role_code,
            language=template.language,
            competency_code=template.skill_id,
            critical=bool(template.critical_question),
            screening_eligible=template.evaluation_tier == InterviewEvaluationTier.BOTH,
            keywords=tuple(str(k).strip().lower() for k in (template.keywords or []) if str(k).strip()),
        )


class AllocationGap(Exception):
    """The pool or matrix cannot meet the approved methodology - flag for
    review rather than silently selecting outside it."""

    def __init__(self, gaps):
        self.gaps = gaps
        super().__init__("; ".join(gaps))


@dataclass
class SelectionResult:
    questions: list
    reused_codes: list = field(default_factory=list)


def critical_competencies(role_profile):
    return {code for code, level in role_profile["criticality"].items() if level == "Critical"}


def validate_allocation(allocation, tier, critical):
    """Matrix-level checks, independent of any pool."""
    gaps = []
    target = TIER_TARGETS[tier]
    total = sum(allocation.values())
    if total != target:
        gaps.append(f"{tier} allocation totals {total}, expected exactly {target}")
    for comp in sorted(critical):
        if allocation.get(comp, 0) < 1:
            gaps.append(f"{tier} allocation gives Critical competency {comp} no question")
    for comp, n in allocation.items():
        if n < 0:
            gaps.append(f"{tier} allocation for {comp} is negative ({n})")
    return gaps


def _skill_match(candidate_skills, question):
    if not candidate_skills:
        return 0
    words = {w for skill in candidate_skills for w in skill.lower().split() if len(w) > 3}
    return sum(1 for kw in question.keywords if any(w in kw for w in words))


def select_questions(
    *, pool, allocation, critical, tier, role_code, language,
    candidate_skills=(), previous_codes=(), rng=None,
):
    rng = rng or random.Random()
    gaps = validate_allocation(allocation, tier, critical)
    if gaps:
        raise AllocationGap(gaps)

    eligible = [
        q for q in pool
        if q.role_code == role_code
        and q.language == language
        and (tier == FULL or q.screening_eligible)
    ]
    previous = set(previous_codes)
    selected = []
    reused = []
    for comp, need in allocation.items():
        if need == 0:
            continue
        group = [q for q in eligible if q.competency_code == comp]
        if len(group) < need:
            gaps.append(f"{role_code}/{language}/{tier}: {comp} needs {need}, pool has {len(group)}")
            continue
        # Unseen first (retake equivalence), then skill match (secondary),
        # then random so exposure spreads across the pool.
        ranked = sorted(
            group,
            key=lambda q: (q.code in previous, -_skill_match(candidate_skills, q), rng.random()),
        )
        chosen = ranked[:need]
        reused.extend(q.code for q in chosen if q.code in previous)
        selected.extend(chosen)

    if gaps:
        raise AllocationGap(gaps)
    covered = {q.competency_code for q in selected}
    missing = sorted(critical - covered)
    if missing:
        raise AllocationGap([f"{role_code}/{language}/{tier}: Critical competency not covered: {', '.join(missing)}"])
    return SelectionResult(questions=selected, reused_codes=reused)
