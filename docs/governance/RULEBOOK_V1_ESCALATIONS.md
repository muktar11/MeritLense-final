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

## Still open

Each item names the question or rule, our recommendation, and what it changes. The system applies the interim shown until decided.

**A. Generic note: a safe answer missing two or more points (SCR-08, 2,026 questions).**
- The note defines 5 (all points) and 3 (one material element missing), but not two or more missing.
- Interim: no band above 0, marked `INTERIM_PENDING_DECISION` on the result.
- Impact: 1,130 of these questions list 5 to 8 points, so a concise answer covering most of them can score 0.
- Recommendation: mark which points are core on those 1,130 questions, as one SME batch drafted for approval. Missing a non-core point would then never lower the band. Until approved, keep the interim.
- Example: HK-TSK-002 (7 points). An answer covering 5 points, safely, scores 0 today.

**B. Scheduled path: how a critical safety failure is confirmed (decision 3, OUT-04).**
- Recommendation: the evaluator records an explicit "critical safety failure" mark on the scorecard with a short evidence note. That mark caps the outcome at Not Ready and blocks the certificate. A low Safety rating alone does not.
- Impact: one new scorecard field. Unflagged results keep their current outcome.

**C. ECG-TSK-001: completion denominator.** The note says steps_completed/4, but the approved Must Include lists 6 steps.
- Interim: completion over the 6 listed steps.
- Recommendation: confirm 6.

**D. HK-PRO-001 ("3: all 3 | 2: calm only | 0: refusal").** An answer that clarifies and adjusts, but doesn't mention listening calmly, has no band.
- Interim: 0.
- Recommendation: 2.

**E. HK-BEH-002 ("5: no touch+inform | 3: touches | 0: takes").** An answer that leaves the cash untouched but doesn't inform anyone has no band.
- Interim: 0.
- Recommendation: 3.

## Also open, already listed in the rulebook

- **SCH-05:** fixed points for each scorecard level. The current 0–100 entry stays until the mapping is supplied.
- **SCH-06:** how Behavioral Indicators and consistency count on the employer-led path. Today Behavior & Integrity is rated but left out of the average.
- **D-04:** severity and gate consequences for the 735 drafted negative indicators (SCR-07). Every affirmed negative stays a critical failure except where a question's own note gives it a band.
