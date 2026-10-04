# Stage 6 — Final report outline

Keep it concise. The brief asks for a *concise project report*, and every section
below maps to a marked criterion. Figures are numbered once and reused in the
website and the video.

| # | Section | Source | Figures |
|---|---|---|---|
| 1 | Problem | README + `docs/stage1/` §7 | — |
| 2 | STOIC prompt engineering and research | `docs/stage1/structured-thinking-and-research.md` | prompt v1→v3 outputs |
| 3 | AS-IS and TO-BE process | `docs/diagrams/figure-1-as-is.svg`, `figure-2-to-be.svg` | Fig 1, Fig 2 |
| 4 | Architecture | `docs/diagrams/figure-3-architecture.svg` | Fig 3 |
| 5 | Workflow screenshots | `docs/screenshots/` | Fig 4–20 |
| 6 | Model comparison | `benchmark/results/summary_*.json` | Fig 21 |
| 7 | Test results | `tests/verify.py` output, `data/test_cases.json` | Fig 11–15 |
| 8 | Guardrails and governance | `docs/governance/guardrails-and-compliance.md` | mapping table |
| 9 | Expert findings | `docs/expert/interview-pack.md` §6 | — |
| 10 | Limitations and recommendations | README "Honest limitations" + governance §3 | — |
| Annex | Expert interview transcript (Q&A) | `docs/expert/` | — |

## Screenshot register

Capture these with the app running and save them as `docs/screenshots/fig-NN-*.png`
so the numbering stays consistent across the report, the website and the video.

| Fig | Caption |
|---|---|
| 4 | n8n main workflow canvas (import `n8n/main_workflow.json`) |
| 5 | Alert queue — investigation triggered from the UI |
| 6 | Evidence pack with masked PII and evidence IDs |
| 7–10 | Output of the Transaction, Customer Behaviour, Risk/Policy and Recommendation agents |
| 11 | TC-01 auto-proceeded, risk below threshold, core banking release |
| 12 | TC-02 missing-evidence banner and the HITL case view |
| 13 | TC-02 resumed after the investigator's approval |
| 14 | TC-03 hard flags, temporary hold and the L2 escalation queue |
| 15 | TC-03 L1 refused: "cannot be cleared below L2" |
| 16 | TC-04 prompt injection quarantined |
| 17 | Exception queue after a deliberately invalid model output |
| 18 | Audit trail for a complete case, with model and prompt version |
| 19 | Settings — thresholds changed, change written to the audit log |
| 20 | `python3 tests/verify.py` — 19/19 passing |
| 21 | Benchmark dashboard / `benchmark/results/benchmark_*.png` |

## Benchmark results table

Fill from `benchmark/results/summary_*.json`. Record the exact model tags.

| Metric | `mistral:7b` | `llama3.1:8b` | `qwen2.5:7b` |
|---|---|---|---|
| Accuracy / macro-F1 | | | |
| Escalation recall | | | |
| False-positive rate | | | |
| Hallucination rate | | | |
| Explanation quality (1–5) | | | |
| JSON compliance | | | |
| Latency p50 / p95 (s) | | | |
| Task completion | | | |
| Consistency across 3 runs | | | |

## What to say about the benchmark

Report the metrics, then make a recommendation and defend it. Weight **escalation
recall** and **hallucination** above latency: a missed fraud case costs more than
a slow decision. Say where the winning model still fails — a comparison with no
stated weakness reads as marketing, not evaluation.

If any run used the stub provider, label it. Numbers from the stub are a dry run
of the harness, not a model comparison, and presenting them otherwise is the one
thing in this project that would be genuinely dishonest.
