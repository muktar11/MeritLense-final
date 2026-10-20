# Rulebook v1.0: methodology decisions

20 Oct 2026. Raised under REL-02 of the MeritLense Assessment Governance and Decision Rulebook v1.0 (`docs/governance/MeritLense_Assessment_Rulebook_Implementation_v1.pdf`).

## Decided by management, 20 Oct 2026

Management answered items 1–5 below. These are settled and are not to be reopened.

| # | Topic | Decision | Built |
| --- | --- | --- | --- |
| 1 | Partial credit | Score each question by its own approved rubric, including permitted partial credit and the 70 legacy exceptions. No universal "one missing = 3, two missing = 0" rule. Accept equivalent wording and concise answers. Incomplete rubrics are question-bank configuration issues, not candidate-review cases. | `api/evaluations/rubric_specs.py`, `rubric_specs_gov2_0.json`; interpretation prompt `week5-v3-concise-answers` |
| 2 | Mandatory indicators | Mandatory only where the approved scoring specification explicitly says so; Must Include alone is not enough. Reconcile the 89 D-02 drafts in one batch. A secondary omission, an essential missing requirement and an affirmed unsafe action are distinct. | No approved specification makes any point mandatory, so none of the 89 drafts is applied (batch result below). An affirmed negative indicator is scored by the rules. |
| 3 | Scheduled-assessment safety | A confirmed critical safety failure prevents Ready and the certificate, whatever the average. A missing detail is not by itself an unsafe action. Keep the readiness boundaries; don't copy scheduled rules onto the AI path. | Pending: needs item B below |
| 4 | Certificates, reports, transcripts | Ready and Partially Ready may be certified under the eligibility requirements; Not Ready and Screening never. No routine admin approval. Reports for every reliable outcome; transcripts wherever conversation was captured. | Pending |
| 5 | Competency thresholds | One stable threshold per role and competency: 60% default, approved role-specific exceptions. | `Week6ScoringService._aggregate_competencies`; exceptions read from the role profile's `competency_thresholds` (none approved today) |

### D-02 batch reconciliation (decision 2)

All 89 drafted classifications are on questions with the generic score note ("5 = core evidence complete and safe; 3 = ... one material element missing; 0 = hard-negative ..."). That note assigns no mandatory consequence to any point, and no other approved specification does. Result:

- **Established requirements kept:** the note's own bands, and the hard-negative 0 for an affirmed unsafe, unauthorized, concealing or falsifying action.
- **Not applied:** all 89 drafts. A missing drafted point lowers the band like any other point; it does not zero the answer.
- **No draft needs a separate decision.** Where a draft is a "do not ..." safety point (e.g. SNC-SAF-001 "do not restrain or put objects in the mouth"), the opposite affirmed action is already a negative indicator and scores 0 as a critical failure.

## Decided by management, 20 Oct 2026 (second round)

| Item | Decision | Built |
| --- | --- | --- |
| A. Two or more omissions on the generic note | Classify core and supporting points across the 2,026 questions, longest lists first. Missing a supporting detail must not force zero. No interim zero for customer scoring on affected answers. Submit classifications and scoring conditions in one batch. | Answers that cover some points but miss two or more are left unscored as `PENDING_APPROVAL`. An answer with no required point at all is insufficient evidence, which SCR-03 already scores 0; the result shows "scoring rule awaiting approval" and `recalculate_pending_rubric_results` re-scores them once a rule is approved. Classification reviewed by management (see Still open A); no question rule approved yet. |
| B. Scheduled critical safety failure | Explicit field with a required evidence note. A confirmed failure is Not Ready and blocks the readiness certificate; its absence bypasses nothing. | `EvaluatorRating.critical_safety_failure` and note (migration 0030), scorecard UI, evaluator report |
| C. ECG-TSK-001 | Map the six steps to the original four before changing the denominator. | Mapping below. Until confirmed, only a complete answer is scored. |
| D. HK-PRO-001 | 2 for clarifying and adjusting without mentioning calm, absent refusal or other disqualifying evidence. | Band added |
| E. HK-BEH-002 | 3 for leaving the cash untouched without reporting, absent taking or concealment. | Band added; "fails to report" scores 3 on this question, not a critical failure |
| 89 D-02 classifications | Removed as unapproved mandatory gates; kept as ordinary scoring evidence. | Already built |

## Still open

**A. Rule templates for the generic-note questions** (`GOV2.0-RC1_rule_templates_for_decision.xlsx`).

Management direction of 20 Oct 2026: draft full, partial and zero conditions from the existing specifications and the agreed methodology. Group equivalent questions under reusable templates with per-question mappings and exceptions. Submit only new or conflicting decisions.

- **Source of record.** `rubric_core_points_gov2_0.json` holds:
  - the reviewed Core/Supporting classification (management workbook);
  - five templates (`TPL-SAFEGUARD`, `TPL-INTEGRITY`, `TPL-HYGIENE`, `TPL-COMMUNICATION`, `TPL-PROCEDURE`), each `PROPOSED`;
  - a mapping for every question: template, central outcome(s), exception.
- **Rule under a template:**
  - 5: every Core outcome evidenced and safe. Supporting is never required.
  - 3: every central outcome evidenced safely; other Core outcomes are compensable gaps.
  - 0: a central outcome missing (`BELOW_MINIMUM`), nothing evidenced, or an affirmed negative indicator (critical failure, unchanged).
- **Central outcomes:** 1,595 questions use the decisive safeguard identified in the second pass; 431 use the main outcome chosen in the template pass.
- **Exceptions:** 24 questions where every Core outcome is central have no 3 band (`NO_PARTIAL_CREDIT`). These need question approval.
- **Decisions requested** (sheet "Decisions requested"):
  - D1: the central-outcome definition.
  - D2: 3 for one or more compensable gaps.
  - D3: conditional points are candidate omissions, never holds.
  - D4: combined points.
  - D5: the 24 exceptions.
  - D6: ECG-TSK-001.

  Everything else is unchanged and needs no approval.
- **Activation:**
  - Approving a template activates its mapped questions. 2,002 of 2,026 become scorable once all five are approved; the 24 exceptions need their own approval.
  - A question-specific rule still overrides its template. It needs its question approved.
  - Every rule must pass acceptance cases generated from its mapping before it scores.
- **No routine holds:**
  - Once a bank has an approved template, question selection draws only questions whose rule is complete.
  - If approved coverage cannot be met that way, coverage wins and a visible "rubric availability exception" is logged.
  - Unclear applicability is a candidate omission. Only genuinely unresolved evidence (uncertain or lost) stays incomplete.
- **Additional 147 proposals** (sheet "Additional 147 proposals"):
  - Supporting points the engineering second pass would make Core, each with its reason.
  - Consequence: required for 5, a compensable gap for 3, never a gate.
  - These are proposals only.
- **Re-scoring.** `recalculate_pending_rubric_results` works as follows:
  - It runs on named evaluations under a run label and preserves the original records and rule versions in a write-once `ScoreRecalculationSnapshot`.
  - It then applies the normal eligibility rules automatically, with no separate release step.
  - `--test-data` marks resulting certificates as test documents: watermarked, and reported as `TEST_DOCUMENT` by the public verification endpoint. A previous certificate's record and PDF reference stay in the snapshot.

**C. ECG-TSK-001: resolve the conflict (no caps).**

- Approved score note: "completion_pct: steps_completed/4×100". The package records `en_points` 4 and `ar_points` 6.
- v1.2 Must Include (4): positions self correctly; supports weight properly; checks balance; moves slowly. v1.2 negative indicators: pulls abruptly; no balance check.

| GOV2.0 point | v1.2 outcome |
| --- | --- |
| 1. Check the mobility plan and required aid or helpers | none (added in GOV2.0) |
| 2. Explain and obtain consent | none (added in GOV2.0) |
| 3. Secure the chair and position feet safely | Positions self correctly |
| 4. Trained standing technique, no pulling arms or carrying full weight | Supports weight properly |
| 5. Stand gradually | Moves slowly |
| 6. Check balance and stop for pain or dizziness | Checks balance (extended) |

- GOV2.0 negative indicators: pulls the arms; lifts unsafely; ignores required assistance or dizziness.
- Current behaviour: only a complete answer (all 6) is scored; an incomplete one is held.
- Options:
  - A: denominator 4 over points 3–6, with an explicit consequence you set for points 1–2.
  - B: denominator 6, with the note revised.
  - C: other.

## Also open, already listed in the rulebook

- **SCH-05:** fixed points for each scorecard level. The current 0–100 entry stays until the mapping is supplied.
- **SCH-06:** how Behavioral Indicators and consistency count on the employer-led path. Today Behavior & Integrity is rated but left out of the average.
- **D-04:** severity and gate consequences for the 735 drafted negative indicators (SCR-07). Every affirmed negative stays a critical failure except where a question's own note gives it a band.
