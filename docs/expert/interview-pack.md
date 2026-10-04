# Stage 5 — Industry Expert Interview & Validation

Stage 5 carries 10 of the 50 marks and **depends on someone outside the team**.
Start it in week 1, not when the build is finished.

Marks split: 5 for completing the interview properly, 5 for the expert's approved
designation level. A Director / VP / AVP / Functional Head / CXO scores 5; a
Manager scores 3; an Analyst or Consultant scores 2. Aim high: the difference
between a Director and an Analyst is 3 marks — 6% of the total.

## 1. Faculty approval — submit before scheduling

The brief requires approval **before** the assessed interview. If the expert
changes for any reason, approval must be sought again.

| Field required by the brief | Entry |
|---|---|
| Expert name | |
| Current designation | |
| Organisation | |
| Relevant function / domain | e.g. Fraud Risk Management, Digital Banking |
| Experience details | |
| Professional profile / LinkedIn link | |
| Short justification | Why this person can validate both a fraud investigation process and an agentic AI PoC |

## 2. Preparation checklist

- [ ] Prototype runs all three test cases end to end (`python3 tests/verify.py`)
- [ ] Written consent to record, and to publish an excerpt on the project website
- [ ] 10-minute walkthrough ready: AS-IS, TO-BE, architecture, guardrails mapping
- [ ] Live demo loaded with TC-01, TC-02, TC-03; screenshots as a fallback
- [ ] Cloud recording **and** auto-transcription switched on before anyone speaks

## 3. Agenda (45–60 minutes)

| Time | Segment |
|---|---|
| 0–5 | Introductions, consent confirmation, purpose |
| 5–25 | The five mandatory questions, asked verbatim |
| 25–40 | Show AS-IS, TO-BE, architecture, the live PoC, HITL controls and guardrails |
| 40–55 | Validation: feasibility, risks, usefulness, improvements; follow-ups |
| 55–60 | Thanks and next steps |

## 4. The five mandatory questions (verbatim from the brief)

1. What makes fraud investigation a complex task in your organisation? What information, rules, evidence and judgement must an investigator normally consider?
2. Which people, systems, data sources or departments does a fraud investigator depend on, and where do these dependencies create difficulties or delays?
3. How important is speed in fraud investigation, and which stages of the process are most time-sensitive?
4. What types and volume of information must investigators examine, and what problems arise when information is incomplete or spread across different systems?
5. What kinds of unusual or ambiguous fraud cases are difficult to handle using standard rules, and how are such cases currently managed?

## 5. Follow-up questions worth asking

These are where the validation marks actually come from — they ask the expert to
judge *our* design, not to describe their job.

- Does Figure 1 reflect how investigation actually works at your bank? What is missing or wrong?
- Our auto-clear band is risk < 30 with confidence ≥ 0.8, and we sample 10% of those back to a human. Are those numbers defensible? Which outcomes would you *never* let a system take automatically?
- Is the AI's rationale detailed enough for an investigator to sign off on, and for an auditor to accept a year later?
- We hold funds automatically on a watchlist hit, an account-takeover pattern, or a split burst. Is holding without a human the right call, or too aggressive?
- What is the biggest regulatory, operational or model risk in deploying this at a real bank?
- What single improvement would make this most useful to your team?

## 6. After the interview

Produce a full question-and-answer transcript corrected against the recording.
Store the video, transcript, approval confirmation and designation evidence
(LinkedIn screenshot, business card or company page) in this folder.

| Area | Expert comment | Our response / action taken |
|---|---|---|
| Feasibility | | |
| Risks | | |
| Usefulness | | |
| Suggested improvements | | |

**Then implement at least one suggested improvement and show it in the final
video.** This is the difference between "we interviewed an expert" and "we were
validated by an expert", and the marks matrix rewards the second.
