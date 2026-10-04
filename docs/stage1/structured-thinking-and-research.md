# Stage 1 — Structured Thinking, STOIC Prompt Engineering & Research

> **Before you submit:** section 3 uses a common expansion of STOIC. Replace the
> component names with the exact definitions taught in your class, keeping the
> structure. Verify every vendor claim in section 5 against a current source and
> cite it.

## 1. Critical thinking analysis

| Element | Analysis |
|---|---|
| **Core question** | Why does fraud alert investigation take so long and produce inconsistent, poorly documented decisions? |
| **Assumptions challenged** | *"More rules mean more safety."* More rules raise more alerts, most of them false positives, and analyst attention is finite — so past a point more rules reduce safety. *"Analysts are the bottleneck."* The real constraint is manual evidence assembly across systems; the analyst is where the queue is visible, not where the time goes. |
| **Evidence needed** | Alert volume per day, false-positive share, average handling time, number of systems touched per case, time from alert to fund hold. These are estimates until the Stage 5 expert confirms them — say so in the report rather than presenting them as measured. |
| **Root cause (5 Whys)** | Decision is slow → analyst gathers evidence by hand → data sits in five or more separate systems → there is no unified case view → those systems were built for operations, not for investigation. |
| **Biases to control** | Anchoring on whichever rule fired; confirmation bias once a hypothesis forms; **automation bias** once AI is introduced — which is why overrides require a written reason and 10% of automatic clearances are sampled back to a human. |
| **Alternative views considered** | Buy a commercial platform; hire more analysts; tune the existing rules. Each is scored in section 4 rather than dismissed. |
| **Implication** | The solution must compress evidence assembly and reasoning while keeping humans accountable and every decision explainable. Speed that costs explainability is not a win here. |

## 2. Systems thinking analysis

### Stakeholders

| Stakeholder | Interest | Current pain | Effect of FraudSentinel |
|---|---|---|---|
| Customer | Safe money, minimal friction | Genuine payments blocked; slow call-backs | Faster clearance of genuine cases; quicker holds on real fraud |
| L1 analyst | Clear, fast cases | Screen-switching, fatigue, repetitive notes | Evidence pack and a draft rationale ready when the case opens |
| L2 investigator / fraud head | Only genuinely complex cases escalated | Poorly documented escalations | Structured escalation carrying evidence and stated reasons |
| Operations / core banking | Correct holds and releases | Hold requests arrive by e-mail | API-driven actions, only after approval |
| Compliance / audit / AML | Traceability and regulatory defensibility | Free-text notes with gaps | Immutable audit trail with model and human attribution |
| Regulator | Customer protection, responsible AI | — | Documented human oversight and governance mapping |

### Causal loops

- **R1 (reinforcing — alert fatigue).** More fraud losses → more rules → more alerts → analyst fatigue → lower review quality → more missed fraud → more losses.
- **B1 (balancing — capacity).** Backlog → overtime and hiring → backlog falls, but with a delay and at rising cost.

FraudSentinel breaks R1 at the *alerts → fatigue* link by removing manual evidence
gathering. It deliberately does **not** break it at the *analyst → decision* link,
because that is where human judgement belongs.

### Iceberg

| Level | Fraud investigation |
|---|---|
| Event | A fraudulent UPI transfer clears before the analyst reaches the alert |
| Pattern | Backlogs at peaks and after hours; high false-positive share; inconsistent notes |
| Structure | Siloed systems; sequential manual handoffs; rule-only triage; no unified case view |
| Mental model | "Rules are the safety net" and "AI cannot be trusted in fraud decisions" |

**Leverage point:** the evidence assembly and reasoning step between *alert raised*
and *human decides*. It is high-effort, highly repeatable, and safe to automate
**precisely because** the human keeps the final decision.

## 3. STOIC prompt engineering

| Component | Purpose in our prompts |
|---|---|
| **S — Situation** | Who we are and the business context (Indian retail bank, digital payment fraud alerts) |
| **T — Task** | What the model must produce |
| **O — Objective** | What a good answer optimises for (usefulness, realism, feasibility, human authority) |
| **I — Instructions** | Step-by-step method and required output format |
| **C — Constraints** | Limits: synthetic data, open models, n8n, timeline, governance, no autonomous high-risk action |

### Refinement log

**v1 (naive)**
```
Give me AI ideas for bank fraud.
```

**v2 (context added)**
```
I am a student building an agentic AI hackathon project for an Indian bank's fraud team.
Suggest 5 AI solution ideas for fraud investigation.
```

**v3 (full STOIC)**
```
SITUATION: A mid-sized Indian retail bank raises thousands of fraud alerts daily on UPI, IMPS and
net banking transfers. L1 analysts manually check core banking, CRM/KYC, device logs, beneficiary
lists and watchlists, call customers, and write free-text notes before deciding to proceed,
verify, hold or escalate.
TASK: Generate 6 distinct agentic AI solution ideas that redesign part of this process.
OBJECTIVE: Maximise investigation speed and decision consistency while preserving human decision
authority and auditability.
INSTRUCTIONS: For each idea give: name, process step targeted, agents involved, human-in-the-loop
points, data needed, main risk. Then score each 1-5 on usefulness, realism and feasibility with
one-line justifications. Return a table.
CONSTRAINTS: Build in 4-6 weeks by a student team; n8n for orchestration; open-weight models only;
synthetic data only; no fully autonomous blocking of customer funds; must work downstream of the
bank's existing detection engine.
```

| Version | Change made | Observed effect |
|---|---|---|
| v1 → v2 | Added domain and user context | Ideas became banking-specific but stayed generic ("use ML to detect fraud"); no structure, not comparable |
| v2 → v3 | Added objective, method, scoring, output format and constraints | Ideas became comparable and pre-scored, feasible within the stated constraints, and every idea addressed human-in-the-loop |
| v3 → v3.1 | Asked the model to critique its own top idea for risks | Surfaced prompt injection and automation bias, both of which became Stage 4 guardrails |

**The same refinement runs through the agent prompts.** `prompts/v1`, `v2` and `v3`
hold three versions of all four agent prompts. Run the same test case through each
and record structured-output compliance and hallucination per version:

```bash
for v in v1 v2 v3; do
  python3 -c "
import sys; sys.path.insert(0,'.')
from app import orchestrator
r = orchestrator.investigate({'alert_id':'ALT-003','model':'qwen2.5:7b','mode':'benchmark','prompt_version':'$v'})
print('$v', r['route'], r.get('raw_decision'), 'json_ok=', r.get('json_first_attempt'))"
done
```

This is the strongest evidence of prompt refinement you can submit, because it is
measured rather than asserted.

## 4. Idea generation and screening

| # | Idea | Usefulness | Realism | Feasibility | Total | Decision |
|---|---|---|---|---|---|---|
| 1 | Agentic alert investigation copilot with HITL | 5 | 5 | 5 | **15** | **Selected** |
| 2 | Replace the real-time scoring engine with an LLM | 3 | 1 | 2 | 6 | Rejected — latency and regulatory risk |
| 3 | Customer-facing scam-warning chatbot | 4 | 4 | 4 | 12 | Future extension |
| 4 | Mule account network graph detection | 5 | 3 | 2 | 10 | Rejected — needs large real network data |
| 5 | Dispute / chargeback automation | 3 | 4 | 3 | 10 | Rejected — lower fraud-prevention impact |
| 6 | Automatic rule tuning from analyst outcomes | 4 | 3 | 3 | 10 | Future extension |

## 5. Market research — existing solutions

Verify each row against a current vendor source and cite it in the report.

| Solution | What it does | Gap relevant to our problem |
|---|---|---|
| FICO Falcon Fraud Manager | Real-time transaction scoring for card and payments fraud | Scores alerts; the investigation narrative is still assembled by analysts |
| NICE Actimize | Fraud and financial-crime platform with case management | Enterprise cost and complexity; limited open-model agentic reasoning |
| SAS Fraud Management | Analytics-driven detection and alert management | Detection focus; evidence synthesis stays manual |
| Feedzai | ML risk platform for payments and banking | Strong scoring; investigator copilot capability varies |
| Featurespace (acquired by Visa) | Adaptive behavioural analytics | Detection and scoring rather than investigation workflow |
| Clari5 (India) | Real-time enterprise fraud management used by Indian banks | Rule and analytics based; human investigation remains manual |
| MuleHunter.AI (RBI Innovation Hub) | AI model to identify mule accounts | Single-purpose; does not orchestrate case investigation |

## 6. Technology research

| Technology | Role | Decision |
|---|---|---|
| n8n | Low-code orchestration with AI Agent, Wait, Switch and Error Trigger nodes | **Selected** — orchestration |
| LangGraph / CrewAI / AutoGen | Code-first multi-agent frameworks | Reviewed — n8n preferred for visual auditability and brief alignment |
| Ollama / OpenRouter / Groq | Serving open-weight models behind an OpenAI-compatible API | **Selected** — model layer. Ollama local, OpenRouter hosted; one wire format, so `app/llm.py` treats them identically and either can serve the Stage 3 comparison |
| Llama Guard / NeMo Guardrails / Guardrails AI | Safety classification and output validation | Reviewed — custom validators chosen so the grounding check is specific to our evidence IDs |
| Supabase | Postgres, auth, row-level security, realtime | **Selected** — data and access control |
| Lovable / Streamlit | Rapid UI | Reviewed — this build ships its own dependency-free dashboard so the PoC runs anywhere |

## 7. Opportunity justification

Commercial platforms are mature at *detecting and scoring* suspicious transactions.
The step after the alert — gathering evidence from several systems, reasoning
against policy, and writing a defensible rationale — remains largely manual. That
step is repetitive, rule-informed and evidence-heavy, which suits specialised
agents. It is also comparatively safe to automate: the bank's detection engine is
untouched, and humans keep the final decision.

Idea 1 scored highest on all three screening criteria, matches the illustrative
flow in the brief, and can be built and benchmarked with synthetic data inside the
hackathon timeline. Crucially, it is falsifiable: escalation recall on known
high-risk cases either is 100% or it is not, and the benchmark reports which.
