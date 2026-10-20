# MeritLense assessment rules: implementation register

**Governing source:** MeritLense Assessment Governance and Decision Rulebook v1.0, 20 Oct 2026, issued by Ahmed Bashir. A copy is at [governance/MeritLense_Assessment_Rulebook_Implementation_v1.pdf](governance/MeritLense_Assessment_Rulebook_Implementation_v1.pdf). It replaces the 19 Oct draft "MeritLense Candidate Assessment Rules", which used numbered sections (§1–§12).

**How to use this file.**
- Code cites rules by their rulebook ID, for example `# SCR-04` or `rulebook CER-02`.
- This file records, for each rule, whether the platform follows it and where.
- When a change closes a gap, update the row here in the same commit.
- Open methodology questions are in [governance/RULEBOOK_V1_ESCALATIONS.md](governance/RULEBOOK_V1_ESCALATIONS.md), referred to below as **Esc 1–5**. Don't guess those values (REL-02).

**Status key:**
- **Done**: implemented and tested.
- **Done (PR)**: implemented and deployed (backend #224, #225 and later).
- **Partial**: some of the rule is in place; the row says what is missing.
- **Gap**: not built yet.
- **Blocked**: waiting on a methodology decision (Esc 1–4 or the rule named).

## Principles and precedence

| ID | Rule in brief | Status | Where |
| --- | --- | --- | --- |
| GOV-01 | AI extracts evidence, rules score, employer decides | Done | `ResponseInterpretationService` (no scoring in prompt), `Week6ScoringService` |
| GOV-02 | AI-led and employer-led paths stay distinct | Done | AI no longer writes scheduled outcomes (`_apply_evaluation_rollups`). No report is produced for a scheduled assessment until it is rated (`generate_for_evaluation`, `generate_completed_session_outputs`) |
| GOV-03 | Same decision logic for B2B and B2C | Done | Scoring has no account-type branch; readiness display differs only by package entitlement |
| GOV-04 | Completion, evidence, readiness, review, certificate stored separately | Partial | Separate fields exist; evidence validity is per response only |
| GOV-05 | Approved versioned spec governs; record contradictions | Done | Version pinning on session and evaluation; contradictions in the escalations file |
| GOV-06 | Every selectable question has executable bands | Blocked (Esc 1) | 2,016 questions share an incomplete generic note |

## Assessment paths and languages

| ID | Rule in brief | Status | Where |
| --- | --- | --- | --- |
| AI-01 | 21/7 questions by role allocation, no cross-role fallback | Done | `QuestionGenerationService`, `question_allocation.py` |
| AI-02 | Approved question in selected language; AI marks covered, missing and uncertain evidence, never scores | Partial | "Contradicted" is not a separate evidence class |
| AI-03 | Never claim practical observation | Done (PR) | Report reliability factors and methodology |
| SCH-01 | Employer conducts and rates; own equivalent questions allowed; coverage of role criteria required | Gap | Only bank questions in order; no criteria coverage |
| SCH-02 | Show criteria not yet assessed; link each rating to evidence | Gap | |
| SCH-03 | Structured scorecard; no second reviewer; rating attributed to employer | Partial | No second reviewer. An Admin correction overwrites `rated_by` (see AUD-01) |
| LAN-01 | Keep bidirectional voice and text translation | Done | `api/live_calls` |
| LAN-02 | Link original, translation, speaker, language and sequence; claim stored media only if kept | Partial | Candidate turns stored with original and translation; evaluator turns are not stored |
| LAN-03 | EN/AR canonical; same question and rubric identity across languages | Done | Indicator-ID mode, V2 registry |
| LAN-04 | AI never fabricates, coaches or overwrites ratings | Done (PR) | Overwrite closed in `_apply_evaluation_rollups` |

## AI question scoring and calculation

| ID | Rule in brief | Status | Where |
| --- | --- | --- | --- |
| SCR-01 | 5 only when full-credit conditions are met | Done | `_score_response` |
| SCR-02 | 3 where the question's partial rule allows | Blocked (Esc 1) | The missing-required-point zeroing stays until decided |
| SCR-03 | Confirmed skip = 0 and kept in the denominator | Gap | No explicit skip action exists. Unanswered questions are non-completion (CAL-03) |
| SCR-04 | Evidence loss is not a zero | Partial | Unreliable evidence is left unscored (incomplete). Retry and recapture are not built |
| SCR-05 | 70 legacy notes executed as written (0/5, 0/2/3, completion %) | Partial | Legacy maxima are used. Band conditions are not executed: HK-PRO-001 "calm only" scores 0, not 2. Defined, so next to build |
| SCR-06 | Missing point, failed mandatory, zero and critical failure are distinct | Blocked (Esc 2) | |
| SCR-07 | Negative evidence read in context; each negative needs severity | Partial | Context handling done. Severities blocked on D-04 |
| SCR-08 | Resolve "two or more omissions" per question | Blocked (Esc 1) | |
| CAL-01 | Overall % = earned / assigned maximum | Done | `_build_session_summary` (RESPONSE_SUM). Check that layer weighting never overrides RESPONSE_SUM |
| CAL-02 | Keep approved aggregation and weights | Done (PR) | FIX-02 weights repaired; equal-competency weighting not used |
| CAL-03 | Every assigned question reconciled to a response state | Done (PR) | `run_for_evaluation`: unanswered questions make the summary incomplete; counts in summary metadata |
| CAL-04 | No final % while evidence is unresolved | Partial | Readiness becomes Incomplete. The percentage is still shown without a "provisional" label |

## Employer scorecard and readiness

| ID | Rule in brief | Status | Where |
| --- | --- | --- | --- |
| SCH-04 | One level per criterion, evidence note, Not Assessed option | Gap | Free 0–100 entry; comments optional |
| SCH-05 | Level → fixed score mapping | Blocked (SCH-05) | Current 0–100 entry kept until mapping supplied |
| SCH-06 | Behavioral Indicators stays a scoring competency | Blocked (SCH-06) | `behavior_integrity` is excluded from the average today |
| SCH-07 | Draft editing, lock on submit, no AI overwrite | Partial | Lock and audit done. AI overwrite closed (PR). No draft state; rule version not stored on the rating |
| OUT-01 | 60+ Ready, 40–<60 Partially Ready, <40 Not Ready, before rounding, subject to gates | Done | `calculate_evaluator_outcome` (scheduled). AI path is decided by gates |
| OUT-02 | AI path keeps role-profile Critical/Non-Critical checks; verify the versioned threshold configuration | Partial (Esc 5) | `evaluate_role_competency_readiness`. Competency pass marks come from importer-set per-question thresholds, so they vary with the questions drawn |
| OUT-03 | Confirmed critical failure prevents Ready | Done | `_apply_evaluation_rollups` |
| OUT-04 | High average can't bypass a safety gate on employer path | Blocked (Esc 3) | |
| OUT-05 | Incomplete and Insufficient Evidence shown as their cause, not poor performance | Done | Readiness INCOMPLETE → "Insufficient Evidence" |
| OUT-06 | Ready / Partially Ready / Not Ready = جاهز / جاهز جزئيًا / غير جاهز | Done (PR) | `_resolve_readiness_indicator`, `READINESS_GAUGE`, comparison labels, templates. Frontend label map needs the matching UI PR |

## Outputs, certificates and corrections

| ID | Rule in brief | Status | Where |
| --- | --- | --- | --- |
| DOC-01 | Valid results and reports delivered automatically; certificate problems never block them | Partial | Outputs are independent. A report needs signed consent: a manager-started session without consent still gets no report, now logged as an error |
| DOC-02 | Names: Interview Transcript, Assessment Analysis Report, Readiness Certificate | Gap | |
| DOC-03 | Report states path, versions, coverage, reasons; risks under the right competency | Partial | Risk mapping done on both paths. Evaluator report: a risk block and improvement item for each rating, plus the Behavioral row |
| DOC-04 | Competency with a critical failure never "Passed" or "no risk" | Done (PR) | Competency table status, explanation, risk evidence |
| DOC-05 | Report only what was measured; label employer-led results | Done | Audio, observation, simulation and other-role claims removed. "Cognitive" band remains in the API payload only (not displayed) |
| CER-01 | Document the approved certificate configuration per path | Blocked (Esc 4) | |
| CER-02 | ≥60 alone is not enough; Not Ready gets no readiness certificate | Done (PR) | Scheduled path now refuses Not Ready |
| CER-03 | Keep the approved Partially Ready treatment | Done | Partially Ready is eligible on both paths |
| AUD-01 | Corrections record original, replacement, reason, actor, time | Partial | Evaluator corrections recorded, but `rated_by` is overwritten |
| AUD-02 | Recalculate after correction; no manual readiness selection | Gap | `HumanReviewService.approve` still accepts a chosen readiness |
| AUD-03 | New output version; previous marked superseded | Done | Reports versioned, earlier ones marked STALE |

## Recovery, exceptions and points

| ID | Rule in brief | Status | Where |
| --- | --- | --- | --- |
| EXC-01 | Routine cases automated; quality monitoring never delays valid outcomes | Partial | One response flagged for review makes the whole summary incomplete |
| EXC-02 | Low confidence is a signal; translation failure doesn't void reliable original | Gap | |
| EXC-03 | Classified, bounded, idempotent retries | Partial | Queue retries exist; `run_async_job` can return FAILED and drop the message |
| EXC-04 | Failed jobs stay retryable or become visible incidents | Partial | Output failures now always logged (PR); no incident record |
| EXC-05 | No unsupported Ready; uncertain questions never inflate % | Partial | See CAL-04 |
| EXC-06 | Incident with owner, deadline, next action; admin notified in-system and by email | Gap | |
| EXC-07 | Admin repairs delivery; engine decides readiness | Gap | See AUD-02 |
| CRD-01–03 | Exact once-only points restoration for verified platform failure | Gap | Reservation release exists; confirmed points are never restored |

## Engineering closure

| ID | Item | Status |
| --- | --- | --- |
| FIX-01 | HK-SAF-001 partial case, HK-PRO-001 legacy 2 | Confirmed in the bank. Execution is SCR-05 |
| FIX-02 | Negative and zero indicator weights | Done (PR). Cause was whole-number rounding in `_weighted_indicators`; importer fixed, scorer ignores invalid maps |
| FIX-03 | Same importer patterns across the bank | Partial. The weight guard covers every rule; generic-answer review not done |
| FIX-04 | Reconcile selected IDs with role/tier coverage | Gap |
| QA-01 | Real-provider journeys plus fixtures | Partial. Controlled tests added; real-provider EN/AR run pending |
| REL-01/02 | Controlled release; don't guess undefined values | In progress. This PR plus the escalations file |
