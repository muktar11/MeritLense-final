# Rule decisions of 22 Oct 2026: implementation and validation summary

Scope: AI-assessed scoring of the GOV2.0-RC1 question bank (2,100 questions; 2,026 on the generic 5/3/0 note, 74 with their own approved legacy notes). The scheduled, employer-led path is unchanged.

## Outcome

- **Every generic question is now scored by its approved template rule.** All 2,026 rules are active, and all 16,758 generated acceptance cases pass.
- **No answer is held** for a missing scoring rule. Two or more omissions are scored, not held.
- **One question is not used:** ECG-TSK-001. It has one escalated conflict (below). Selection skips it, and no coverage depends on it.
- **No new gates.** No new mandatory point, safety gate or certificate gate was created.

## What was implemented

### 1. Scoring templates (approved)

| Score | Rule |
| --- | --- |
| 5 | All applicable Core outcomes demonstrated correctly and safely. Supporting points never required. |
| 3 | The question's central practical competence demonstrated safely, with compensable gaps. Any number of other Core outcomes may be missing. |
| 0 | Minimum practical competence not shown: the central competence is missing, or no applicable Core outcome is evidenced. Or an explicitly defined zero condition is met. |

- **Substance, not wording.** The AI interpreter now credits an outcome's substance. Equivalent methods and natural paraphrases count, and a missing minor detail inside an outcome does not fail that outcome. The interpreter prompt version is `+indicator-ids-v2`, so every result shows which version scored it.
- **Unsafe actions** are scored by each question's own defined rules and severity. That means 0 and a critical failure, unless the question's note gives the action its own band (for example HK-BEH-001 "delayed" = 3).
- **The 74 approved legacy score notes are unchanged.**

### 2. The 24 questions where every Core outcome was central

21 keep a 3 band through a question-specific partial mapping. Each mapping records its rationale. Three examples:

- **HCA-COM-004:** adapting communication alone = 3.
- **NA-SAF-021:** stopping the transfer *or* locking the bed = 3.
- **ST-TSK-005:** a complete status handover without confirming understanding = 3.

3 are binary, each with a justification. In each, the assessed action is a single refusal that cannot be performed in part:

- **WS-SAF-020:** riding forks or a pallet.
- **FW-BEH-005:** using another person's authorization.
- **FW-SAF-019:** riding a tractor without a seat.

The full list is in `validation_2026-10-22/all_central_questions.csv`.

### 3. Selection and conditional requirements

- **Selection.** Only questions with complete rules are selected. If the complete rules cannot meet the approved coverage, the session is not started. A "Rubric availability exception" is shown and logged, and no unresolved rule is used. Every role currently meets coverage.
- **Conditional points.** 277 questions have Core points that apply only in a named situation ("as required", "if needed", "if immediate danger"). There are 291 such points, 99 of them central. Each counts only when the scenario or the candidate's own words establish the condition, and the interpreter must quote those words. Silence alone never makes a point applicable.
- **Limits are not conditions.** Permission and manner limits ("if safe", "only if trained", "only as needed") are not treated as conditions, and neither is a condition on one detail inside a compound point. The list is in `conditional_points.csv`.
- **Safeguard against free credit.** An answer that evidences no applicable Core outcome scores 0, even if every unmet point is conditional.

### 4. Certificates and the 147 additional proposals

- **Re-scoring and certificates.** After an authorized, named re-score (`recalculate_pending_rubric_results --evaluation … --run-label …`), the normal certificate eligibility rules apply automatically. `--test-data` marks certificates as test documents: they are watermarked, and verification reports them as `TEST_DOCUMENT`. The previous records, rule versions and certificate reference are kept in a write-once snapshot.
- **147 proposals: 80 adopted, 67 not.** Each decision and its reason is recorded on the question.
  - Adopted where the point is a substantive part of the task or safeguard the question asks for.
  - Not adopted where the point is:
    - conditional or permission-limited (32);
    - a duplicate of an existing Core outcome (15);
    - a generic step the scenario does not turn on (11);
    - out of scope (9).
- **Tested effect.** An answer missing only an adopted point now scores 3 instead of 5. An answer missing only a non-adopted point still scores 5. No adopted point is central, a prerequisite or a gate. Detail: `additional_core_decisions.csv`.

### 5. ECG-TSK-001

- **Denominator.** The four original outcomes are the denominator: positions self, supports weight, moves slowly, checks balance.
- **Safeguards.** The mobility-plan check and consent are represented as applicable safeguards. There are no caps, and neither safeguard is optional.
- **Escalated.** See below.

## Escalation (one): ECG-TSK-001, an unevidenced safeguard

**What the agreed rules settle:**
- An answer that evidences both safeguards scores outcomes/4.
- An affirmed unsafe action (pulling the arms, ignoring required help) scores 0 and is a critical failure.

**What they cannot settle:** the four outcomes completed while the plan check or consent is not evidenced. Every way to give that a consequence needs a cap, a different denominator, or an optional safeguard, and all three are excluded. Until it is decided, such answers are held and the question is not selected.

**Recommendation:** each unevidenced safeguard counts as one outcome not completed, within the existing denominator of 4. That needs no cap, keeps the denominator at 4, and makes neither safeguard optional.

| Answer | Expected score (of 5) |
| --- | --- |
| All four outcomes and both safeguards | 5.00 (100%) |
| All four outcomes, consent not evidenced | 3.75 (75%) |
| All four outcomes, neither safeguard | 2.50 (50%) |
| Three outcomes and both safeguards | 3.75, already in effect |
| Pulls the arms (affirmed) | 0 and critical failure, already in effect |

## Validation

**1. Acceptance cases.** The generated cases for every one of the 2,026 rules all pass: 16,758 cases. They cover:
- full credit;
- partial credit;
- supporting omission;
- below minimum;
- paraphrase;
- affirmed and negated unsafe action;
- evidence loss;
- conditional silence;
- conditional established.

**2. Scoring effects, before → after.** "Before" is the previous configuration, which scored 5 for all points, 3 for one missing and held two or more. Counts are per question.

| Answer profile | Effect |
| --- | --- |
| All points | 5 → 5 (2,026) |
| Every Core outcome, Supporting omitted | now 5 everywhere (was 3 on 802, held on 943) |
| Central competence only | 3 on 2,023 (was held on 1,926); 5 on the 3 binary questions |
| One compensable Core gap | 3 → 3 (1,987) |
| Central competence missing | 0 on 2,026 (was 3 on 236, held on 1,778) |
| Conditional points silent | 5 on 276 (was 3 on 37, held on 239) |

**3. Coverage.** Selection was run from complete rules only, with ECG-TSK-001 excluded. It meets the approved allocation and Critical coverage for all 21 roles, both tiers and both languages (168 of 168 runs). No availability exception arises today.

**4. Test suite.** 50 rubric and interpreter tests pass. They exercise the full production pipeline in English and Arabic: selection, the availability exception, conditional flags in the real prompt, the held-then-re-scored path with snapshot and test-document certificate, the binary and partial mappings, the additional Core effects, and ECG-TSK-001.

**5. Reproducing the results.** `python scripts/validate_rule_decisions_2026_10_22.py` regenerates every figure above and writes the CSV detail to `docs/governance/validation_2026-10-22/`. `scripts/apply_rule_decisions_2026_10_22.py` regenerates the configuration from the decisions.

## Not yet done

- **Not deployed.** Merging backend PR #228, then the UI PR #235, deploys this to production.
- **Held assessments.** Assessments already held on production need a named re-score run on the VM after deployment, with `--test-data` for test assessments.
- **Esc 6** above.
