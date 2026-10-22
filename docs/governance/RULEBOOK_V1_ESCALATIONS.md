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

## Decided by management, 22 Oct 2026 (implemented)

Settled; not to be reopened. Full implementation and validation summary: `RULE_DECISIONS_2026-10-22_IMPLEMENTATION_SUMMARY.md`.

| # | Decision | Built |
| --- | --- | --- |
| 1 | Templates approved: 5 = all applicable Core outcomes correct and safe; 3 = central practical competence shown safely with compensable gaps; 0 = below minimum competence or a defined zero condition. A missing detail within an outcome does not fail it; equivalent methods and paraphrases accepted; unsafe actions by the question's own rules; legacy exceptions kept. | Five templates `APPROVED` v2; every one of the 2,026 generic questions has an active rule. Interpreter prompt `+indicator-ids-v2` credits an outcome's substance. |
| 2 | Keep partial credit wherever safe, meaningful partial competence can be defined; binary only where it cannot, with a justification. | 21 of the 24 questions have a `partial` mapping with its rationale; 3 are `BINARY` (WS-SAF-020, FW-BEH-005, FW-SAF-019: a single refusal that cannot be performed in part). |
| 3 | Select only questions with complete rules; coverage never overrides completeness; otherwise an availability exception. Conditional requirements count only when applicable; silence does not establish applicability. | Selection raises a "Rubric availability exception" (audit log, session not started). 277 questions have conditional Core points; each counts only when the interpreter quotes the scenario or answer establishing it. |
| 4 | Normal certificate eligibility after an authorized re-score; test documents identified; previous versions kept. 147 proposals adopted where supported; no new gates; rationale recorded and effects tested. | Already built in `recalculate_pending_rubric_results`. 80 of 147 adopted, 67 not (conditional 32, duplicate 15, generic 11, out of scope 9); every decision recorded in `core_additions`. |
| 5 | ECG-TSK-001: four original outcomes as denominator; mobility plan and consent as applicable safeguards; no caps, not optional; escalate an unresolved consequence before activation. | Built as directed; one consequence escalated (Esc 6). Not selectable until decided. |

## Decided by management, 22 Oct 2026 (final decisions)

| # | Decision | Built |
| --- | --- | --- |
| Esc 6 | ECG-TSK-001 stays inactive. Another fully defined, validated question of the same role and competency is used wherever required, without duplication; exactly 21 Full and 7 Screening questions with all required coverage. The proposed safeguard deductions are not approved; no new weighting. | `rubric_specs_gov2_0.json` ECG-TSK-001 `status: INACTIVE`; `rule_complete` excludes it. Selection checked for all 21 roles, both tiers and languages: 21 / 7 questions, no duplicates, coverage met. |
| Deploy | Approved, using the established deployment and rollback procedures. | |
| Test data | All existing assessments are internal tests: back up and verify, then remove them with their reports and certificates (verification links stop resolving). Accounts, question bank, rules, configuration and the audit record are kept. No re-scoring. | `clear_trial_data --all-assessments` |
| Final check | Fresh, clearly identified test sessions under the updated rules: selection, scoring, readiness, reports and certificate eligibility, AI and scheduled, English and Arabic. | |

## Previously escalated (now decided)

**Esc 6. ECG-TSK-001: the consequence of an unevidenced safeguard.**

The agreed rules settle everything except one case. An answer that evidences both safeguards is scored steps/4. An affirmed unsafe action, such as pulling the arms or ignoring required assistance, scores 0 under the negative indicators. What the agreed rules cannot settle is an answer that completes the four outcomes but does not evidence the mobility-plan check or consent. Every way to give that a consequence needs one of the three things the decision rules out:
- a cap, which is excluded;
- a change to the denominator of 4, which is fixed;
- treating the safeguard as optional, which is excluded.

Today such an answer is held (`unmet_safeguards`), not scored, and the question is not selected.

Engineering recommendation (not approved; the question stays inactive): count each unevidenced safeguard as one outcome not completed, within the existing denominator of 4: score = (outcomes evidenced − safeguards not evidenced) / 4, floor 0. No cap, the denominator stays 4, and neither safeguard is optional.

| Answer | Expected score |
| --- | --- |
| All four outcomes and both safeguards | 100% (5.00 of 5) |
| All four outcomes, consent not evidenced | 75% (3.75) |
| All four outcomes, neither safeguard | 50% (2.50) |
| Three outcomes and both safeguards | 75% (3.75), already scored this way |
| Pulls the arms or ignores required help (affirmed) | 0 and critical failure, already scored this way |

To activate it: set `safeguard_consequence` in `rubric_specs_gov2_0.json` and add the deduction to the completion branch of `rubric_specs.execute`.

**Previous record of the ECG-TSK-001 mapping (20 Oct 2026):**

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

## Also open, already listed in the rulebook

- **SCH-05:** fixed points for each scorecard level. The current 0–100 entry stays until the mapping is supplied.
- **SCH-06:** how Behavioral Indicators and consistency count on the employer-led path. Today Behavior & Integrity is rated but left out of the average.
- **D-04:** severity and gate consequences for the 735 drafted negative indicators (SCR-07). Every affirmed negative stays a critical failure except where a question's own note gives it a band.
