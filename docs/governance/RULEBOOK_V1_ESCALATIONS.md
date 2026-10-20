# Rulebook v1.0: decisions needed from the methodology owner

20 Oct 2026. Raised under REL-02 of the MeritLense Assessment Governance and Decision Rulebook v1.0 (`docs/governance/MeritLense_Assessment_Rulebook_Implementation_v1.pdf`).

Engineering is going ahead with every correction the rulebook fully defines. The five items below cannot be built as written without guessing a value, which REL-02 forbids. Each one names the rules involved, the evidence and a recommended resolution. One answer per item is enough.

## 1. The "two or more omissions" case is undefined for 96% of the bank

**Rules:** GOV-06, SCR-02, SCR-08, REL-02.

**Evidence.**
- 2,016 of the 2,100 questions in GOV2.0-RC1 use one generic score note: "5 = core evidence complete and safe; 3 = safe/correct direction with one material element missing; 0 = hard-negative, unsafe/unauthorized action, concealment, or falsification."
- That note does not say how to score a safe answer missing two or more points. It also does not say which points are "material".
- GOV-06 says a question with an undefined rubric must not be selected. Read literally, that leaves only the 84 questions with their own score notes selectable, and no role can fill a Full assessment.

**Recommendation.** Approve one bank-wide default:
- every Must Include point is material unless the question says otherwise;
- an answer that is safe, but misses two or more material points, scores 0.

Questions that need different treatment override it in their own specification. This keeps the bank usable and avoids a universal proportional rule, which SCR-08 rules out.

**Until decided:** the scorer keeps today's behaviour, where any missing required point scores 0. Partial credit (SCR-02) is not switched on.

## 2. Which Must Include points are mandatory

**Rules:** SCR-06, GOV-05, D-02.

**Evidence.**
- SCR-06 says not every Must Include point is mandatory by default.
- GOV-05 says the 89 AI-drafted mandatory classifications (D-02) are not proof of approval.
- So for most questions nothing approved says which missing points are failed mandatory conditions and which are ordinary omissions.

**Recommendation.** Decide this together with item 1:
- a missing point lowers the band (5 → 3 → 0) under the default in item 1;
- "mandatory" means a point whose absence alone forces 0, and applies only where a question's specification says so;
- the 89 D-02 drafts are reviewed as a batch and either approved or dropped.

## 3. Safety gate for employer-led (scheduled) assessments

**Rules:** OUT-01, OUT-04, CER-02.

**Evidence.**
- Today the outcome is the plain average of four ratings, with no Critical-competency rule.
- Safety 30 with three other ratings of 90 averages 75, which is Ready.
- OUT-04 says a high average must not bypass "an applicable approved safety gate". None is defined for this path.

**Recommendation.** For employer-led results, any Critical competency in the role profile rated below 40 caps the outcome at Not Ready, and below 60 caps it at Partially Ready. This uses the existing 40/60 boundaries (OUT-01) and the existing role profiles, with no new numbers.

**Already fixed regardless (CER-02):** Not Ready no longer receives a readiness certificate on this path.

## 4. Source of the approved certificate policy

**Rules:** CER-01, CER-03.

**Evidence.** CER-01 says to document "the actual gate configuration", not one inferred from current code or the 19 Oct draft. No document holding that approved configuration has been supplied to engineering.

**Request.** Send the approved certificate policy, or confirm that the current code checks are the approved policy:
- **AI-led:** Full tier, certificate enabled, not held, identity verified, completed, consent signed, complete evidence, minimum quality, critical competencies at 60% or above, review flags resolved, not Not Ready.
- **Employer-led:** Full tier, certificate enabled, not held, rating present, identity verified, completed, consent signed, all ratings present, not Not Ready.

## 5. Competency pass marks depend on which questions are drawn

**Rules:** OUT-02, GOV-03, GOV-05.

**Evidence.**
- A competency passes when its score reaches the sum of its questions' pass thresholds. Those thresholds are set by the importer, not by an approved specification:
  - 0/3/5 questions pass at 3 (60%);
  - legacy 0/5 questions pass only at 5 (100%);
  - completion-percentage questions pass at 7 of 10.
- So the competency pass mark changes with the question draw. In the production run on 20 Oct, Safety Awareness needed 68% because one 0/5 question was drawn, while Hygiene needed 60%. Both scored 60%; Safety failed and Hygiene passed.
- Two candidates with the same percentage on the same competency can therefore get different results.

**Recommendation.** One approved pass threshold per competency, 60% unless the role profile says otherwise, independent of which questions are drawn. Per-question pass thresholds stop affecting competency status.

## Also open, already listed in the rulebook

- **SCH-05:** fixed points for each scorecard level. The current 0–100 entry stays until the mapping is supplied.
- **SCH-06:** how Behavioral Indicators and consistency count on the employer-led path. Today Behavior & Integrity is rated but left out of the average.
- **D-04:** severity and gate consequences for the 735 drafted negative indicators (SCR-07).
