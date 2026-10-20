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
| A. Two or more omissions on the generic note | Classify core and supporting points across the 2,026 questions, longest lists first. Missing a supporting detail must not force zero. No interim zero for customer scoring on affected answers. Submit classifications and scoring conditions in one batch. | Answers that cover some points but miss two or more are left unscored as `PENDING_APPROVAL`. An answer with no required point at all is insufficient evidence, which SCR-03 already scores 0; the result shows "scoring rule awaiting approval" and `recalculate_pending_rubric_results` re-scores them once a rule is approved. Proposed batch: `rubric_core_points_gov2_0.json` (status PROPOSED) and `GOV2.0-RC1_core_points_for_approval.xlsx`. |
| B. Scheduled critical safety failure | Explicit field with a required evidence note. A confirmed failure is Not Ready and blocks the readiness certificate; its absence bypasses nothing. | `EvaluatorRating.critical_safety_failure` and note (migration 0030), scorecard UI, evaluator report |
| C. ECG-TSK-001 | Map the six steps to the original four before changing the denominator. | Mapping below. Until confirmed, only a complete answer is scored. |
| D. HK-PRO-001 | 2 for clarifying and adjusting without mentioning calm, absent refusal or other disqualifying evidence. | Band added |
| E. HK-BEH-002 | 3 for leaving the cash untouched without reporting, absent taking or concealment. | Band added; "fails to report" scores 3 on this question, not a critical failure |
| 89 D-02 classifications | Removed as unapproved mandatory gates; kept as ordinary scoring evidence. | Already built |

## Still open

**A. Approve the core/supporting batch** (`GOV2.0-RC1_core_points_for_approval.xlsx`).
- Proposed conditions:
  - 5 = all core points, answer safe. Supporting details are not required; the note says "5 = core evidence complete and safe".
  - 3 = exactly one core point missing, answer safe.
  - 0 = two or more core points missing, or an affirmed hard-negative.
- Impact: 2,026 questions. 155 short lists are core throughout, so they behave exactly like today. The rest stop penalising missing supporting detail.
- Until approved, answers missing two or more points are held as pending, so most results stay held.
- To switch on: set `status` to `APPROVED` and deploy, then run `recalculate_pending_rubric_results`.

**C. ECG-TSK-001: confirm the denominator.**

| Original v1.2 step (4) | Current Must Include (6) | Relation |
| --- | --- | --- |
| — | 1. Check mobility plan and required aid or helpers | New, independent preparatory step |
| — | 2. Explain and obtain consent | New, independent preparatory step |
| Positions self correctly | 3. Secure the chair and position feet safely | Rewording of the original step |
| Supports weight properly | 4. Trained technique, no pulling arms or carrying full weight | Rewording of the original step |
| Moves slowly | 5. Stand gradually | Same step |
| Checks balance | 6. Check balance; stop for pain or dizziness | Same step, extended |

- Recommendation: keep the approved 4-step denominator over steps 3–6, with steps 1–2 as supporting detail. The package's own `en_points` for this question is 4.

## Also open, already listed in the rulebook

- **SCH-05:** fixed points for each scorecard level. The current 0–100 entry stays until the mapping is supplied.
- **SCH-06:** how Behavioral Indicators and consistency count on the employer-led path. Today Behavior & Integrity is rated but left out of the average.
- **D-04:** severity and gate consequences for the 735 drafted negative indicators (SCR-07). Every affirmed negative stays a critical failure except where a question's own note gives it a band.
