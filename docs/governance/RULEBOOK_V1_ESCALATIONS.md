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

**A. Essential / Core / Supporting classification, reviewed per question** (`GOV2.0-RC1_core_points_for_approval.xlsx`, `rubric_core_points_gov2_0.json`, classification `GOV2.0-RC1-ecs-2026-10-20`).

Management review of 20 Oct: don't activate the first pass, don't assign Supporting by category, define omission consequences, and keep unchanged rows unreviewed. The second pass answers each point.

- **Nothing is active.** Every question is `UNREVIEWED`. Classification applies per question, only when that question is marked `APPROVED`; every other question keeps today's rule (two or more omissions held as pending, no interim zero). A result scored under an approved row records the classification version.
- **Every point re-read in its task.** 9,352 points: 1,774 Essential, 4,313 Core, 3,265 Supporting. 1,595 questions have at least one Essential point.
- **Proposed scoring conditions, per question:**
  - 0: an affirmed negative indicator. This is a critical failure, as today.
  - 0: any Essential point missing (`ESSENTIAL_MISSING`). This is an essential missing requirement. It is not an unsafe action and not a critical failure.
  - 5: all Essential and Core points evidenced, answer safe. Supporting points never lower the band.
  - 3: all Essential points evidenced, and no more Core points missing than the question's own allowance.
  - 0: more Core points missing than that allowance (`CORE_OMISSIONS`).
  - 0: nothing evidenced (SCR-03), as today.
- **Allowance, proposed default:** half the Core points, rounded down, minimum 1.
  - 1,941 questions allow 1.
  - 74 allow 2.
  - 11 have only Essential points, so any omission is 0.
  - A missing Essential point never qualifies for 3. Two missing Core points are 0 only where the question's allowance says so.
- **Flagged patterns, resolved per point.** A point can carry several patterns.
  - Combined core points (2,851): 817 Essential, 2,034 Core.
  - Safety wording (950): 2 Essential, 323 Core, 625 Supporting.
  - Checks / preparation (380): 2 Essential, 49 Core, 329 Supporting.
  - Documentation (615): 24 Core, 591 Supporting.
  - Consent / agreement (29): 5 Core, 24 Supporting. Most of these 24 are "agreed instructions" wording, not consent to care.
  - 376 first-pass Supporting points are now Core and 3 are Essential. Examples: hand hygiene and PPE in hygiene and care tasks, consent before personal care, patient identification, allergy confirmation, airway and breathing checks, wheel brakes, "do not continue walking until assessed".
- **Combined indicators, proposed rule:**
  - A point naming several actions with "and" or commas is evidenced only when each action is evidenced in substance; concise or equivalent wording is accepted.
  - Alternatives with "/" or "or" need one of them.
  - 3,766 points have this form and are marked per question.
  - This rule isn't in the interpretation prompt yet.
- **Overlap with the 89 D-02 drafts:** 244 drafted mandatory points on 89 questions.
  - First pass: 222 Core, 22 Supporting.
  - Second pass: 87 Essential, 154 Core, 3 Supporting. The 3 are FDA-COM-008 #7, IC-TSK-013 #8 and FW-TSK-006 #6, each secondary to its task.
  - The drafts remain unapproved and are not gates. Where a drafted point is now Essential, it zeroes the answer only after that question is approved.
- **To switch on:** mark approved rows `APPROVED` (reviewer and date), deploy, then run `recalculate_pending_rubric_results`. Before re-scoring, that command preserves each held assessment's original summary, competency and response results and rule versions in a write-once `ScoreRecalculationSnapshot`. Certificate eligibility and report supersession then run as normal.

**C. ECG-TSK-001: confirm the step consequences.**

| Original v1.2 step (4) | Current Must Include (6) | Relation |
| --- | --- | --- |
| — | 1. Check mobility plan and required aid or helpers | New, independent preparatory step |
| — | 2. Explain and obtain consent | New, independent preparatory step |
| Positions self correctly | 3. Secure the chair and position feet safely | Rewording of the original step |
| Supports weight properly | 4. Trained technique, no pulling arms or carrying full weight | Rewording of the original step |
| Moves slowly | 5. Stand gradually | Same step |
| Checks balance | 6. Check balance; stop for pain or dizziness | Same step, extended |

- Proposal (`rubric_specs_gov2_0.json` → `ECG-TSK-001.proposed`, not active): score 10 × (steps 3–6 evidenced) / 4, the original denominator. Steps 1 and 2 are separate requirements, not optional detail:
  - Mobility plan and required aid or helpers not evidenced: capped at 50%.
  - Explanation and consent not evidenced: capped at 75%. Both missing: 50%.
  - Affirmed ignoring of required assistance or dizziness, pulling the arms or unsafe lifting: 0 and critical failure (existing negative indicators).
  - Proceeding after the person refuses: proposed new negative indicator, 0 and critical failure.
- Alternative for decision: step 1 as Essential (0 when not evidenced) instead of the 50% cap.
- Until confirmed, only a complete answer (all 6 steps) is scored; an incomplete one is held as pending.

## Also open, already listed in the rulebook

- **SCH-05:** fixed points for each scorecard level. The current 0–100 entry stays until the mapping is supplied.
- **SCH-06:** how Behavioral Indicators and consistency count on the employer-led path. Today Behavior & Integrity is rated but left out of the average.
- **D-04:** severity and gate consequences for the 735 drafted negative indicators (SCR-07). Every affirmed negative stays a critical failure except where a question's own note gives it a band.
