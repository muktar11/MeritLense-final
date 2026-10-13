# MeritLense Platform Guide

**Status as of 13 October 2026.** This is the reference for what MeritLense does, how the AI assessment works end to end, what is working today, and what still needs attention. Keep it updated as items are resolved.

> **Before going live, work through the [Production checklist](#10-production-checklist-do-not-forget).** The most important item: **Google Cloud billing must be enabled** for production. Without it, question audio and translation don't work, and every interview answered in a language other than English or Arabic waits for human review.

---

## Contents

1. [What MeritLense is](#1-what-meritlense-is)
2. [Status at a glance](#2-status-at-a-glance)
3. [Accounts, roles and access](#3-accounts-roles-and-access)
4. [The AI assessment, end to end](#4-the-ai-assessment-end-to-end)
5. [Other main features](#5-other-main-features)
6. [What needs attention](#6-what-needs-attention)
7. [Operations reference](#7-operations-reference)
8. [Recent changes (13 October 2026)](#8-recent-changes-13-october-2026)
9. [Glossary](#9-glossary)
10. [Production checklist (do not forget)](#10-production-checklist-do-not-forget)

---

## 1. What MeritLense is

MeritLense is a **workforce-readiness assessment platform**. An employer (a company or an individual household employer) sends a candidate a link. The candidate completes an AI-led interview for a specific job role. MeritLense then produces a structured, evidence-based readiness report and, for Full Assessments, a certificate.

The core principle: **AI reads and labels the answers; fixed, approved rules decide the score and readiness.** The AI never decides on its own whether someone is ready.

### System map

| Part | What it is | Where |
|---|---|---|
| Website and dashboards | Next.js app, English and Arabic (RTL) | `https://meritlense.com` (repo `muktar11/MeritLense-ui`) |
| API and business logic | Django REST backend with PostgreSQL | `https://api.meritlense.com` (repo `muktar11/MeritLense-final`) |
| Speech-to-text | OpenAI Whisper | Transcribes recorded answers |
| AI interpretation | OpenAI `gpt-4o-mini` | Labels each answer against the approved indicators |
| On-screen translation | Google Translate, falling back to OpenAI `gpt-4o` | Translated questions and statements for the candidate |
| Answer translation | Google Translate | Translates answers not in the question's language (see §6) |
| Read-aloud voice | Google Text-to-Speech, falling back to the browser's voice | Question audio |
| Payments | Stripe | Packages and subscriptions |

There is **no staging environment**. Merging to `main` in either repo deploys straight to production.

---

## 2. Status at a glance

### Working in production

- **Accounts:**
  - registration for companies and individuals, with Terms and Privacy acceptance recorded;
  - all nationalities, an "Other" option with a typed value, phone codes for every country, and city lists.
- **Business verification gate:** companies must have their trade licence approved before using the platform.
- **Agreements:** company and individual agreements, signed by OTP or checkbox.
- **Question bank:** **GOV2.0-RC1** is active, with 2,100 questions (21 roles × 100). Each session draws its questions from the pool by approved competency quotas, so retakes get fresh questions.
- **Creating assessments:** choose the candidate, assessment type (Screening or Full), role and language. A slot is reserved, and the link can be copied or emailed by MeritLense.
- **Candidate prechecks:**
  - consent and privacy notice;
  - camera and microphone check;
  - verbal confirmation;
  - identity check, comparing the face with the ID photo.
- **AI interview:**
  - questions in English or Arabic, with a translation into the candidate's own language;
  - voice or typed answers;
  - timer, integrity monitoring and a blurred camera preview.
- **Scoring:** deterministic, rule-based scoring and readiness (5/3/0 per question, approved thresholds).
  - Critical-question safeguards and unsafe-answer detection work in English and Arabic.
  - English and Arabic answers score identically.
- **Human review hold:** results that need a person's judgement are held, and admins release them from the **Review Queue**.
- **Reports and certificates:** bilingual PDF reports with QR verification. Certificates are issued for Full Assessments only.
- **Dashboards:** company and individual dashboards with **Customize Dashboard** widgets, Analytics and Insights, and a Requires Attention queue.
- **Compare Candidates:** a separate page that compares 2–4 candidates on specific assessments, with radar and bar charts and a PDF export.
- **Billing and Payments:** Stripe, packages, assessment slots and add-on points.
- **Team members:** company team members with four permission areas.
- **Language and consent:**
  - English/Arabic switcher on every page;
  - cookie banner (Accept All / Just This Time / Reject), with choices recorded.

### Working, with limitations

| Area | Limitation | Why |
|---|---|---|
| Question read-aloud | Server voice fails for every language. Candidates hear their device's voice if it has one, otherwise a notice tells them to read the text | Google Cloud billing is off (§6.1) |
| On-screen translation | Works for 20 languages through the OpenAI fallback. Afaan Oromo, Tigrinya, Afar, Twi, Luganda and Malagasy show "translation unavailable" | Google Translate is down, and the fallback was unreliable for those six (Afar came out as Somali) |
| Answers in other languages | Scored correctly from the original-language answer, **but held for human review** with "Translation failed…" | Answer translation uses Google Translate |

### Decisions pending

See §6 for each item, and the full to-do list in the **[Production checklist](#10-production-checklist-do-not-forget)**.

1. Enable Google Cloud billing, or approve the OpenAI fallback for answer translation.
2. Policy for off-topic answers (D-04), which currently cause low-confidence holds.
3. Switch off the AI-drafted D-02 mandatory points until the subject-matter expert (SME) signs off.
4. SME sign-off of the GOV2.0 question bank.
5. Clear trial data before launch.

---

## 3. Accounts, roles and access

| Role | Who | Can do |
|---|---|---|
| **Company admin (B2B)** | The company's main account | Everything for the company: candidates, assessments, results, comparison, billing, team, company profile |
| **Team member (B2B)** | Staff invited by the company admin | Only what the admin grants: **Add candidates**, **Set evaluations**, **Set scores**, **Payments**. "Full access" = all four. Dashboard customization, comparison and analytics need full access |
| **Individual employer (B2C)** | A household or private employer | Their own candidates, assessments, results, comparison and billing. Their dashboard layout is personal |
| **Admin / Superadmin** | MeritLense staff | Platform administration, **Review Queue** (release held results), cross-account comparison, accounts, audit log, revenue |
| **Candidate** | The person being assessed | No account. Uses a private interview link that carries a token |

### Gates before an account can work

1. **Email verification** at registration.
2. **Terms and Privacy acceptance.** Required at registration, and recorded with version, language, IP and browser. Admins creating an account for someone don't tick it on their behalf.
3. **Trade licence approval (companies only).** Until a MeritLense admin approves it, the company only sees Company Profile.
4. **Signed agreement.** The company or individual agreement must be signed before the dashboard unlocks.

---

## 4. The AI assessment, end to end

### 4.1 Key concepts

| Concept | Meaning |
|---|---|
| **Role** | One of 21 approved job roles, such as Driver, Nursing Assistant or Housekeeper. A candidate is only ever assessed and compared within one exact role |
| **Assessment type** | **Screening**: 7 questions, 30 minutes, no certificate. **Full Assessment**: 21 questions, 45 minutes, certificate-eligible. They are never mixed in comparisons |
| **Question bank** | **GOV2.0-RC1**: 100 questions per role (2,100 in total), each approved in English and Arabic, with a fixed Question ID (for example `NA-TSK-009`) |
| **Competencies** | Each role has five: **Task execution**, **Safety awareness**, **Hygiene standards**, **Behavior and integrity**, **Communication ability**. The role profile marks each one Critical or Required |
| **Critical question** | A question whose failure can't be outweighed by other good answers |
| **Must Include points** | The approved points a good answer should contain |
| **Negative indicators** | Unsafe or unacceptable statements |
| **5/3/0** | Each question scores 5 (meets), 3 (partial) or 0 (does not meet), from which points were evidenced |
| **Source language** | English and Arabic are the only approved languages. Arabic sessions use the Arabic questions; every other session uses the English ones. All other languages are **translations shown beside** the source question, never used for scoring |

### 4.2 Lifecycle of one assessment

```
Employer creates assessment ──► Slot reserved ──► Link sent to candidate
        │
        ▼
Candidate opens link ──► Prechecks ──► Orientation ──► Questions (timed)
                                                            │
                                                            ▼
                              Each answer: transcript ──► interpretation ──► rule input
                                                            │
                                                            ▼
                      Session complete ──► Scoring ──► Readiness ──► Review hold?
                                                                         │
                                                ┌────────────────────────┴───────┐
                                                ▼                                ▼
                                    Held: admin reviews, releases    Released: report (+ certificate)
```

**1. Create the assessment** (the Create Assessment popup on the dashboard).
- Pick the candidate, the assessment type, the role and the candidate language (EN/AR).
- The language is pre-filled from the candidate's preferred language. If that language is neither English nor Arabic, the popup explains that the candidate will see questions translated into it.
- One **assessment slot** is reserved from the package. It's released again if the session is cancelled or expires.
- The interview link can be copied, or emailed to the candidate by MeritLense ("Send link").
- Interviews can also be **scheduled** for a time, including live interviews with an evaluator.

**2. Candidate prechecks.** All must pass before the first question.
1. **Consent**: the candidate agrees to the assessment and data processing.
2. **Privacy notice**: acknowledged.
3. **Camera and microphone check.**
4. **Verbal confirmation**: the candidate reads and records *"I confirm that I am completing this assessment myself and that these are my own answers."*
   - It's shown in English or Arabic, with a translation into the candidate's language.
   - The statement shown is stored with the recording.
5. **Identity verification**: a live photo is matched against the ID photo on file. A minimum match score is required.

**3. Orientation.** A short tour of the interview screen.

**4. Questions.**
- **Selection.** The session draws its 7 or 21 questions from the role's pool by the approved competency allocation.
  - Critical competencies are always covered.
  - A retake gets questions the candidate hasn't seen.
  - There is no fallback to other roles' questions.
- **Screen layout:**
  - the original question, with a switch to see the other approved language version;
  - the question translated into the candidate's **answer language**, chosen in the "Your Language" panel and defaulting to their preferred language;
  - separate **Listen** buttons for the original and the translation.
- **Answering:**
  - **Record** (voice) or **Type**;
  - switching language or answer type never erases an answer already typed or recorded;
  - languages without speech recognition (Afaan Oromo, Tigrinya, Afar, Twi, Luganda) answer by typing.
- **Timer.** A countdown card shows time remaining out of the total. It's based on the server's start time, so reloading the page doesn't reset it. Warnings appear at 5 min, 1 min and 30 s. At zero the session completes automatically.
- **Integrity monitoring:**
  - The camera checks for "no face", "more than one face" and "camera off".
  - A problem gets a short grace countdown, after which it is logged. Repeated problems can pause or end the session.
  - The on-screen preview is blurred for comfort; recorded evidence is not affected.

**5. Each answer is processed** (§4.3), and the session completes after the last question or when time runs out.

**6. Scoring, readiness, review, report and certificate** (§4.4–4.7).

### 4.2a Question bank numbers and retakes (verified in production, 13 Oct 2026)

- **Active bank:** GOV2.0-RC1, with **2,100 questions in English plus the same 2,100 in Arabic**: exactly 100 per role per language, for 21 roles.
- **Older versions:** GOV1.2-FINAL (441 EN + 441 AR) is still marked active, but sessions only draw from their own version (GOV2.0-RC1), so it isn't used. Older v1.2 rows are inactive.
- **Per session:** Screening asks 7 questions, Full asks 21, drawn by the approved competency quotas.
- **Does a candidate get the same question again?** Not until the pool runs out. For the same candidate and role, unseen questions are always picked first; a seen question is reused only in a competency with no unseen ones left. Fully fresh assessments per candidate per role:
  - **Full:** 3 for most roles; 4 for Nursing Assistant, Home Care Assistant, Elderly Medical Support, Basic Patient Support, Restaurant Staff, Security Guard and Event Security;
  - **Screening:** about 7–10.

  After that, only the exhausted competencies repeat questions.

### 4.3 How each answer is processed

| Step | What happens | Technology |
|---|---|---|
| 1. Transcript | A voice answer is transcribed in the chosen answer language. A typed answer is used as written. The original audio and transcript are always kept as evidence | OpenAI Whisper |
| 2. Translation | If the answer's language differs from the session's language, the answer is translated. The original is kept | Google Translate |
| 3. Interpretation | The AI compares the answer with the question's **approved indicators** (Must Include points and Negative indicators, by indicator ID) and returns which points were evidenced, with the supporting words. An unsafe statement counts only if the AI **quotes the candidate's words**; otherwise it is sent to review | OpenAI `gpt-4o-mini` |
| 4. Rule input | The labelled evidence is turned into a fixed input for the rule engine | MeritLense |
| 5. Scoring | Each question gets 5, 3 or 0 by the approved rules. Question scores add up to competency and overall scores (`RESPONSE_SUM` aggregation). Competencies are compared with their pass thresholds | MeritLense rule engine (deterministic) |

**What the AI does not do:** it doesn't score, decide readiness, rank candidates or write conclusions. The same answer with the same indicators always produces the same score.

### 4.4 Readiness decision

Readiness is decided once scoring completes. It is written once, and corrections are separate, audited records.

| Readiness | When |
|---|---|
| **Ready** | All required competencies were covered and met, and no critical failure |
| **Partially Ready** | The role profile allows partial readiness: some non-critical gaps |
| **Not Ready** ("Readiness Gaps Identified") | A critical failure, or a required competency below its threshold |
| **Insufficient Evidence** (`INCOMPLETE`) | Too few competencies could be assessed to decide |
| **Pending** | Scoring incomplete, readiness not enabled (for example some Screening setups), or awaiting review |

### 4.5 Human review hold (release safeguard)

The hold sits between scoring and the employer: **a result that needs a person's judgement is not shown to the employer** until an admin reviews it. The score is hidden ("withheld"), and the report and certificate are not released.

**Reasons a result is held** (these are shown to the reviewer):

| Reason | Example |
|---|---|
| Answer interpretation needs human review | Low-confidence reading, typically an off-topic or unclear answer |
| Critical safety indicator detected | The candidate said something unsafe, quoted as evidence |
| Critical question scored 0 | A Ready or Partially Ready result with a failed Critical question can't be released on the overall score alone |
| Translation failed | The answer was interpreted from the original language without translation (currently all non-EN/AR answers, see §6.1) |
| Transcript issue | Speech recognition problems |
| Initial launch period | Only if `REVIEW_ALL_RESULTS` is switched on: every result is reviewed |

**How to release:** Admin dashboard → **Review Queue**. Open the evaluation, read the answers, transcripts and evidence, then record a decision (Ready / Partially Ready / Not Ready / Insufficient Evidence) with notes. The decision is stored as an audited correction, and the original automatic decision is kept. The report is then generated and released.

### 4.6 Reports

- A bilingual (EN/AR) **PDF report** per evaluation, with an executive summary, readiness level, overall score, competency results, evidence summary, risk indicators, improvement plan, consent and identity status, and an audit trail.
- A **QR code** and verification link prove the report is genuine.
- "Overall score" describes aggregate performance only. **Readiness is the authoritative result** and may override the score, for example after a critical failure.

### 4.7 Certificates

Issued only when **all** of these hold:
- it is a **Full Assessment** (Screening never issues a certificate);
- the result is **not on review hold**;
- identity was verified, the session completed, and candidate consent was signed;
- the assessment is complete;
- readiness is **Ready** or **Partially Ready**.

### 4.8 Languages: what works today

Interface: **English and Arabic** (Arabic is fully right-to-left).

| Language group | On-screen translation | Listen (read aloud) | Voice answers |
|---|---|---|---|
| English, Arabic | Approved text (no translation needed) | Browser voice today (server voice down) | Yes |
| Amharic, Filipino, Chinese, Hindi, Urdu, Bengali, Tamil, Telugu, Malayalam, Punjabi, Sinhala, Indonesian, Nepali, Swahili, Vietnamese, Khmer, Burmese | Yes (OpenAI fallback, `gpt-4o`) | Browser voice if the device has one, otherwise a notice | Yes |
| Somali | Yes (OpenAI fallback) | No | Yes |
| Malagasy | **Unavailable** until Google Translate works | No | Yes |
| Afaan Oromo, Tigrinya, Afar, Twi (Akan), Luganda | **Unavailable** until Google Translate works | No | **No (typed answers only)** |

**Answers in any language other than English or Arabic** are scored correctly, but held for review until answer translation works again (§6.1).

### 4.9 Live (scheduled) interviews

An assessment can be scheduled for a date and time. The candidate does the same prechecks, then joins a live call room up to 15 minutes early, where an evaluator conducts the interview with interpretation support. See `docs/LIVE_INTERPRETED_CALLS.md`.

---

## 5. Other main features

### 5.1 Dashboards
- **Company and individual Overview:** headline numbers (evaluations completed, certificates, assessment slots available/reserved, success rate) and **widgets**.
- **Customize Dashboard:** show, hide and reorder widgets, and reset to the default.
  - Company accounts have a company-wide layout; only the company admin edits it.
  - Individual accounts have a personal layout.
- **Default widgets:**
  - **Requires Attention**: results held for review or with insufficient evidence;
  - **Overall Readiness Index**;
  - Recent Evaluations or Candidate List;
  - Evaluation Status.
- **Optional widgets:** trends, scores by role, job roles, languages, time of day, monthly activity, candidate comparison.
- **Analytics and Insights:** every chart in one place.

### 5.2 Candidates
- Add, edit and share candidates. A candidate's profile includes their preferred language, which can be any of the 26 supported languages.
- Score history and full reports per candidate.

### 5.3 Evaluations and scheduling
- A list of assessments by status (scheduled, in progress, completed, cancelled).
- Reschedule or cancel; the slot is released on cancellation.
- Send or resend the interview link by email.

### 5.4 Assessment Results
- Scores, readiness, reports and certificates per evaluation. Held results show as "Human review" without a score.

### 5.5 Compare Candidates
A separate page in the sidebar.
1. **Choose the job role.** Only candidates assessed for that exact role appear.
2. **Filter (optional)** by date range and assessment type.
3. **Pick 2–4 candidates.** If a candidate has several assessments, choose which one to use. Once you pick a Full Assessment, Screening-only candidates are locked, and the reverse.
4. **Compare.** The result tabs are:
   - **Overview:** radar or bar chart, overall-score bars, readiness status, competency bars;
   - **Detailed Scores:** type and date, score (withheld if on review hold), readiness, critical indicators, coverage, review flag;
   - **Key Insights:** factual differences and limitations;
   - **Reports:** branded PDF, chart PNG, each candidate's full report.
5. Only assessments scored under the role's **current scoring version** can be compared. The comparison restates recorded results; it **never** ranks candidates or makes a new readiness decision.

### 5.6 Billing and Payments
- **Packages and subscriptions** through Stripe.
- **Assessment slots:** one is used per assessment. It's reserved when the assessment is created and released if it's cancelled or expires.
- **Points:** a separate balance, used only for optional add-ons (practical simulation test, video introduction, reference verification, job-role matching, extended insights). Never used for the core assessment.
- See `docs/PAYMENTS_AND_ENTITLEMENTS.md`.

### 5.7 Company profile and team
- Company details, logo, stamp for signed agreements, trade licence.
- Invite team members and set their permissions.

### 5.8 Admin (MeritLense staff)
- **Review Queue:** release held results (§4.5).
- Accounts and company verification (trade licence approval).
- Cross-account candidate comparison (audit-logged).
- Readiness overview, revenue, audit log.

### 5.9 Privacy and consent records
- Terms and Privacy acceptance at registration: version **v1.6**, stored per account.
- Candidate consent and privacy acknowledgement per interview.
- The cookie choice is recorded as history. "Just This Time" lasts for the current visit only.

---

## 6. What needs attention

Ordered by impact.

### 6.1 Google Cloud billing (blocks audio and translation): **critical**
- **Symptom.** In production, Google Text-to-Speech and Google Translate both fail. Every question-audio request returns "Text-to-speech provider request failed", and every translation falls back or fails.
- **Effect:**
  - candidates hear only their device's voice, or none;
  - six low-resource languages have no on-screen translation;
  - **every interview answered in a language other than English or Arabic is held for human review**.
- **Action (owner: account holder):** enable billing on the Google Cloud project used by `GOOGLE_TTS_API_KEY` / `GOOGLE_TRANSLATE_API_KEY`. No code change is needed; audio and translation resume automatically.
- **Alternative (needs approval):** let answer translation also fall back to OpenAI. This touches the scoring pipeline's input, which is why it hasn't been done.

### 6.2 Off-topic answers cause low-confidence holds (policy D-04)
An off-topic or "I don't know" answer is interpreted with low confidence and holds the whole result for review. **Decision needed:** treat a clearly off-topic answer as a normal 0 (no hold), or keep holding.

### 6.3 AI-drafted D-02 mandatory points
Some "mandatory point" classifications in GOV2.0 were drafted by AI pending SME review. One English answer scored a false 0 on RS-SAF-013 and CCG-COM-005 because of them. **Decision needed:** switch the drafts off until the SME signs off.

### 6.4 SME sign-off of the GOV2.0 bank
GOV2.0-RC1 is live with management approval, but the subject-matter-expert review packet (`MeritLense_Question_Bank_v2_SME_Review_Packet.xlsx`) is not yet signed off.

### 6.5 Clear trial data before launch
Test registrations, candidates and validation sessions remain in production. Use `clear_trial_data`. Always run the **dry run** first and keep a verified backup; this is run on the VM. Accounts, companies, packages and configuration are kept. See `docs/runbooks/gov2_integration.md`.

### 6.6 A stray test configuration is active
An interview configuration `verify_role` (named "Nursing Assistant", 1 question, version v1) is active in production. It could confuse employers. **Action:** deactivate it (`deactivate_test_artifacts`, or the admin interface).

### 6.7 Smaller items
- **Privacy Policy v1.6** has no cookie section. Add one before launch.
- **Profile and candidate screens** still use the older 66-country list. Registration has the full list.
- **No staging environment.** Every merge goes live. Changes rely on local tests and browser checks.
- **Read-aloud quality** depends on the candidate's device voice until §6.1 is fixed.

---

## 7. Operations reference

### Deploying
- **Backend:** merge to `main` in `MeritLense-final`. The GitHub Action "Deploy Backend (VM)" pulls, runs migrations and restarts the server.
- **Frontend:** merge to `main` in `MeritLense-ui`. "Deploy Frontend" builds a static export and publishes it.
- Check runs with `gh run list` / `gh run watch`.

### Useful management commands (run on the VM)

| Command | Purpose |
|---|---|
| `activate_question_bank_version <version>` (`--rollback`) | Switch the live question bank |
| `load_indicator_registry` | Load approved indicator IDs and translations |
| `trial_data_inventory` | Read-only count of trial and test data |
| `clear_trial_data` (dry run by default) | Remove trial assessment data, keeping accounts and configuration |
| `deactivate_test_artifacts` | Deactivate test configurations and templates |
| `simulate_question_selection` | Check the question allocation rules |
| `repair_arabic_expected_steps` | Legacy v1.2 Arabic repair (dry run by default) |

### Settings that change behaviour

| Setting | Current | Effect |
|---|---|---|
| `INDICATOR_ID_EXTRACTION_ENABLED` | **on** in production | Interpretation by approved indicator ID (required for bilingual scoring) |
| `REVIEW_ALL_RESULTS` | off | When on, every result is held for review (launch-period option) |
| `ENABLE_TRANSLATION_PIPELINE` | on | Answer translation step |
| `OPENAI_INTERPRETATION_MODEL` | `gpt-4o-mini` | Answer interpretation |
| `OPENAI_DISPLAY_TRANSLATION_MODEL` | `gpt-4o` | On-screen question translation fallback |

### Testing
- Backend: `python manage.py test api.<app>`. Use a separate `DB_NAME` per run to avoid clashing test databases.
- Frontend: `npx tsc --noEmit`, `npx eslint`, then check the real feature in a browser.

### Other documents
- `docs/DEPLOYMENT.md`: deployment details.
- `docs/PAYMENTS_AND_ENTITLEMENTS.md`: packages, slots, points.
- `docs/LIVE_INTERPRETED_CALLS.md`: live interviews.
- `docs/runbooks/gov2_integration.md`: question bank v2 integration, backup and cleanup.

---

## 8. Recent changes (13 October 2026)

| Area | Change | PRs |
|---|---|---|
| Question bank | GOV2.0-RC1 integrated and activated; scheduling roles fix | backend #190, #191; UI #201, #202 |
| Review | Release fix; admin Review Queue | backend #192; UI #202 |
| Interview link | MeritLense emails the link to the candidate | backend #193; UI #203 |
| Verbal confirmation | New statement, stored with the recording | backend #194; UI #206 |
| Arabic safety | Arabic answers reach the AI as Arabic, so unsafe Arabic answers are detected | backend #195 |
| Navigation | Globe language switcher; sidebar labels; account menu with real Log out | UI #204, #205 |
| Consent | Required Terms checkbox; cookie banner | backend #196; UI #207 |
| Registration | All nationalities with "Other", phone codes, city lists, fully bilingual company form | backend #197; UI #208 |
| Create Assessment | Screening/Full cards, candidate language, summary footer | UI #209 |
| Dashboard | Customize Dashboard for individual accounts; Requires Attention | backend #198; UI #210 |
| Compare Candidates | Separate page, assessment-level selection, never mixes types, richer PDF; redesigned to the deck design | backend #199; UI #211, #213 |
| Interview page | Original question plus translation, answer language, audio fixes, timer card, camera blur, translated verbal statement | backend #200–#202; UI #212 |
| Cookie banner | Accept All / Just This Time / Reject | backend #203; UI #213 |

---

## 9. Glossary

| Term | Meaning |
|---|---|
| **B2B / B2C** | Company account / individual (household) employer account |
| **Slot** | One assessment's worth of package entitlement |
| **Screening / Full Assessment** | 7-question quick check / 21-question certificate-eligible assessment |
| **GOV2.0-RC1** | The current approved question bank version (2,100 questions) |
| **Indicator ID** | The fixed ID of one Must Include point or Negative indicator, the same in English and Arabic |
| **Critical question / competency** | A gate that good answers elsewhere can't outweigh |
| **Readiness** | The authoritative outcome: Ready / Partially Ready / Not Ready / Insufficient Evidence |
| **Review hold** | A result withheld from the employer until an admin reviews it |
| **Readiness correction** | An admin's audited review decision; the original decision is kept |
| **D-01 … D-07** | Approved scoring-policy decisions (for example D-04: how negative indicators are handled) |
| **SME** | Subject-matter expert who signs off question content |

---

## 10. Production checklist (do not forget)

Everything that still has to be done for production, in one place. Tick items off here (`[x]`) as they are completed, with the date and who did it. **B** = blocks launch, **R** = recommended before launch, **A** = after launch / ongoing.

### 10.1 Google Cloud billing: **B**

Google Cloud billing must be **enabled** for production. Two Google services stop working without it:

| Service | Setting (server `.env`) | What breaks while billing is off |
|---|---|---|
| Google Text-to-Speech | `GOOGLE_TTS_API_KEY` | Question read-aloud. Candidates only hear their device's voice, or see "audio not available" |
| Google Translate | `GOOGLE_TRANSLATE_API_KEY` (+ `GOOGLE_TRANSLATE_PROJECT_ID` if used) | (1) Answer translation fails, so **every interview answered in a language other than English or Arabic is held for human review**. (2) No on-screen translation for Afaan Oromo, Tigrinya, Afar, Twi, Luganda, Malagasy. (3) Other languages fall back to OpenAI: slower, and costs OpenAI credit |

- [ ] **Enable billing** on the Google Cloud project that owns these keys (Google Cloud Console → Billing → link a billing account). Both keys may belong to the same project; check each.
- [ ] Make sure the **Cloud Text-to-Speech API** and **Cloud Translation API** are both *enabled* in that project (APIs & Services → Library).
- [ ] Set a **budget alert** in Google Cloud Billing, so costs are visible.
- [ ] **Check after enabling** (no code change or redeploy needed):
  - [ ] Open an interview and press **Listen in English**. Audio comes from the server; there's no "audio not available" notice. In the audit log, new `QUESTION_AUDIO_GENERATED` entries appear without errors.
  - [ ] Choose **Afar** or **Tigrinya** as the answer language. A translated question appears instead of "translation unavailable".
  - [ ] Complete one short Screening with typed **Amharic** answers. In the admin Review Queue it should **not** be held with "Translation failed".
  - [ ] Note: the platform skips Google for 10 minutes after a failure, so allow up to 10 minutes after enabling before testing.

### 10.2 Decisions needed from management: **B**

- [ ] **Answer translation fallback.** If Google billing can't be enabled soon: approve, or decline, letting answer translation fall back to OpenAI (like on-screen translation already does). Not done, because it touches the scoring pipeline's input.
- [ ] **D-04, off-topic answers.** An off-topic or "I don't know" answer currently holds the whole result for review. Decide: score it as a normal 0 without a hold, or keep holding.
- [ ] **D-02, AI-drafted mandatory points.** Switch off the AI-drafted mandatory-point classifications until the SME signs off. They caused a false 0 on RS-SAF-013 and CCG-COM-005.
- [ ] **SME sign-off** of the GOV2.0 question bank (`MeritLense_Question_Bank_v2_SME_Review_Packet.xlsx`).
- [ ] **Launch review policy.** Decide whether to switch on `REVIEW_ALL_RESULTS` (every result reviewed before release) for the first weeks.
- [ ] **Reviewer staffing.** Name who works the admin **Review Queue**, and how quickly held results must be released.
- [ ] **Certificate strictness.** Decide whether a missed required point on an otherwise Ready result should block the certificate.

### 10.3 Production data clean-up: **B**

- [ ] **Take and verify a database backup** first (restore check plus row counts).
- [ ] Run `trial_data_inventory` (read-only) and keep the output.
- [ ] Run `clear_trial_data` **as a dry run**, review it, then run it for real. This removes test registrations, candidates and validation sessions; accounts, companies, packages and configuration are kept. Known test data:
  - `ejtiazportal+reg-*` registrations (4);
  - `ejtiazportal+test1/test2` candidates;
  - validation sessions on the internal test accounts (company and individual);
  - the 13 Oct interview-language test session.
- [ ] **Deactivate the stray test configuration** `verify_role`, which shows as "Nursing Assistant" with 1 question (version v1). Use `deactivate_test_artifacts`, or switch it off in the admin.
- [ ] Re-check that only the 21 GOV2.0-RC1 roles (Full and Screening, EN and AR) are active.

### 10.4 Security: **B**

Several secrets were shared in chat or email during development. **Rotate them before launch:**
- [ ] **OpenAI API key** (it was pasted in chat during testing). Create a new key, update `.env` on the server, restart, and delete the old key.
- [ ] **Stripe secret key** and **webhook secret** (shared during development). Roll them in the Stripe dashboard and update `.env`.
- [ ] **VM / SSH credentials** and the **admin and superadmin passwords** used for testing.
- [ ] **Test-account passwords** that remain after clean-up.
- [ ] Confirm Stripe runs with **live** keys (`sk_live_…`) and the live webhook endpoint is registered and receiving events.
- [ ] Confirm `DEBUG` is off and only the production domains are allowed (CORS / allowed hosts).

### 10.5 Legal and content: **R**

- [ ] **Privacy Policy v1.6 needs a cookie section** describing necessary, analytics and marketing cookies and the banner choices (Accept All / Just This Time / Reject). Publish it as a new version, so new acceptances record it.
- [ ] Have the **Terms and Privacy Arabic text** reviewed.
- [ ] Confirm the candidate consent and data-processing text with whoever is responsible for data protection.

### 10.6 Product gaps: **R**

- [ ] **Profile and candidate screens** still use the older 66-country list; registration has the full list. Switch them to the same full list.
- [ ] **Low-resource languages** (Afaan Oromo, Tigrinya, Afar, Twi, Luganda, Malagasy): after Google billing is on, test a real interview in at least one of them.
- [ ] **Upcoming questions are visible to the candidate.** The interview link's data includes the text of all the session's questions from the start, so a candidate with browser developer tools could read ahead. No answer keys are exposed (no must-include points, keywords or scoring). Fix: return only the current question's text to the candidate, and translate upcoming questions on the server instead of in the browser.
- [ ] **Indicator translation review** (`MeritLense_Indicator_Translation_Review_v1.2.xlsx`, about 1,613 Arabic indicator texts). Import the approved rows when they come back.

### 10.7 Infrastructure and operations: **R / A**

- [ ] **Staging environment** (R). Today every merge to `main` goes straight to production. A staging copy would allow testing first.
- [ ] **Automatic daily database backups**, with a periodic restore test (R).
- [ ] **Monitoring and alerts** for server errors, failed deploys and API downtime (R).
- [ ] **Email sending:** check that invitations, interview links and OTP emails arrive, and aren't marked as spam (R).
- [ ] **Cost watch** (A): monthly OpenAI usage (interpretation, transcription, translation fallback), Google Cloud usage, Stripe fees.
- [ ] **Review Queue** (A): check daily that no result is waiting too long.
- [ ] **Audit log** (A): look regularly for `ERROR` entries on question audio, translation and scoring.
- [ ] **Keep this guide updated** (A) whenever an item above is resolved or something new comes up.
