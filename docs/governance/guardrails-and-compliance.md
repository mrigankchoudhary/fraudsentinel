# Stage 4 — AI Guardrails & Governance / Compliance

**Primary framework: NIST AI Risk Management Framework (AI RMF 1.0).**
Its four functions — Govern, Map, Measure, Manage — fit a proof of concept that has
to show risk identification, measurement through benchmarks, and operational
controls. Its trustworthiness characteristics map onto the risks the brief lists.
India's Digital Personal Data Protection Act 2023 is referenced for privacy
context. RBI's FREE-AI framework may be added as a sector reference **only if the
faculty approves it** — do not cite it in the submission otherwise.

---

## 1. Guardrails draft

| Category | Guardrails in this build |
|---|---|
| **Data** | Synthetic data only (`data/seed.py`); PII masked before any model call (`app/guardrails.mask_pii`); least-privilege keys — the service key lives only in the orchestrator, never in the browser; the evidence pack carries only what the task needs |
| **Model** | Versioned prompts (`prompts/v1..v3`); temperature 0 and a fixed token ceiling for every model; strict JSON schema; evidence-grounding check; untrusted-input isolation; exactly one repair retry, then the case goes to a human |
| **Action** | Agents hold **no** action tools — enforced structurally: nothing in `app/agents.py` can reach `app/mock_cbs.py`; actions run only after human approval or a deterministic hard flag; holds are temporary, never account closure |
| **Human oversight** | HITL for medium risk and low confidence; L2 approval for holds and escalation closures; mandatory written reason on every override; 10% of automatic decisions sampled back to a human |
| **Auditability** | Every step logged with input hash, model, prompt version, output, latency, human ID and timestamp; the log is append-only and the dashboard exposes it |

---

## 2. Compliance mapping

Risk → Guardrail → Governance requirement → Implementation in the PoC.

| # | Risk | Guardrail | NIST AI RMF requirement | Implementation (file / behaviour you can demonstrate) |
|---|---|---|---|---|
| 1 | **Privacy** — customer identity reaching a third-party model | PII masking before every model call; synthetic data throughout | MEASURE / MANAGE — *privacy-enhanced* | `app/guardrails.mask_pii()` strips names, accounts, phone, email, PAN; `app/evidence.build_pack()` masks every item before it is numbered. Visible in the Case view: the evidence pack shows `XXXXXX9087`, never a full account |
| 2 | **Confidential data** — keys or customer data leaking | Least-privilege keys; secrets outside the repo; local model option | GOVERN — data-handling policy; *secure and resilient* | Service key only in the orchestrator; the browser never receives the resume token (`app/server.py` strips it on every case read); Ollama keeps inference on-premises, and on the hosted OpenRouter path `FS_OPENROUTER_NO_TRAIN=1` sends `data_collection: deny` so no upstream trains on a prompt. Model keys live in the gitignored `.env.local`, never in the committed `.env` |
| 3 | **Hallucination** — a confident, invented rationale | Evidence-ID citation plus grounding validation | MEASURE — *valid and reliable* | `app/guardrails.validate_output()` rejects evidence IDs not in the pack and monetary amounts not present in the evidence; hallucination is a reported benchmark metric, not an assumption |
| 4 | **Bias** — uneven treatment across customer segments | No protected attributes in prompts; segment-level error analysis | MEASURE — *fair, harmful bias managed* | Prompts receive segment, city and income band but no name, gender or religion; `benchmark/score.py` reports false-positive rate, which can be cut by segment from `benchmark_results` |
| 5 | **Prompt injection** — customer free text steering the model | Untrusted-field isolation and pattern scan | MANAGE — *secure and resilient* | `app/guardrails.scan_injection()`; remarks are wrapped in `<untrusted>` tags with any nested closing tag neutralised; TC-04 proves it end to end — the case is quarantined and routed to a human, and the model's output is discarded |
| 6 | **Unsafe autonomous action** — the system moving money on its own | Agents hold no action tools; approval gate before any action | GOVERN / MAP — human oversight defined | Only the post-approval branch and the hard-flag branch call `app/mock_cbs.py`. The single autonomous money-touching action in the system is a **temporary hold** on a hard flag — which is conservative, never permissive |
| 7 | **Access control** — the wrong person authorising an outcome | Role-based permissions and an approval matrix | GOVERN — roles and responsibilities | `orchestrator.APPROVAL_MATRIX`: L1 may clear and verify; HOLD and escalation closure require L2. Demonstrable: sign in as L1 on TC-03 and the clearance is refused |
| 8 | **Decision thresholds** — undocumented or drifting cut-offs | Documented, configurable risk and confidence thresholds | GOVERN — risk tolerance documented | Thresholds live in `policy_rules`, not in the workflow; the admin Settings screen edits them and every change writes an `audit_log` row |
| 9 | **Human approval** — rubber-stamping the AI | HITL for the medium band; mandatory override reasons; sampling | MANAGE — human oversight of AI decisions | Wait node / `awaiting_human` state; override without a reason is rejected; 10% of automatic clearances are pulled back to a human specifically to counter automation bias |
| 10 | **Logging** — no record of what the model was asked | Step-level logging of every model call and decision | MANAGE — monitoring | `app/audit.py` writes after every step with a SHA-256 input hash, so the exact prompt can be proven without storing customer data |
| 11 | **Auditability** — decisions that cannot be defended later | Append-only, attributable records | GOVERN — *accountable and transparent* | `audit_log` carries model, prompt version, input hash and human ID; the Audit trail screen shows a complete case history |
| 12 | **Model unavailability** — provider down mid-investigation | Error path to an exception queue, never a silent pass | MANAGE — incident response | `orchestrator._exception()` and `n8n/error_workflow.json`: every failure becomes a human-reviewable case. No alert is ever dropped |
| 13 | **Stale or missing evidence** — deciding on partial facts | Completeness check that blocks clearance | MEASURE — *valid and reliable* | `data_incomplete` blocks PROCEED in both the validator and the router. TC-02 demonstrates it: a 26-hour-old device feed forces human review |

---

## 3. What this PoC does **not** cover

Stating the gaps is part of the governance case, not an admission against it.

- **Authentication is a demo.** In-memory sessions and fixed passwords. Production
  path is Supabase Auth with row-level security, which the schema already enables.
- **No adversarial robustness testing beyond TC-04.** One injection pattern class
  is demonstrated; a real deployment needs a red-team suite.
- **Bias analysis is structural, not statistical.** The hooks exist and the data
  supports a segment cut, but 30 synthetic cases cannot establish fairness.
- **The hard-flag rules are hand-written.** They encode a reasonable fraud
  heuristic, not a tuned model; thresholds would need calibration against real
  alert outcomes.
- **Model drift is unmonitored.** A production system would re-run the benchmark
  on a schedule and alert on regression in escalation recall.
