# FraudSentinel

**Governed agentic AI copilot for digital payment fraud alert investigation (UPI / IMPS / Net Banking)**

An integrated, working implementation of the Agentic AI Digital Transformation
Hackathon banking challenge. A fraud investigator opens an alert, presses
*Investigate*, and an orchestrator assembles masked evidence, runs four
specialised agents, validates their output, and routes the case automatically,
to a human, to escalation, or to an exception queue. Nothing touches an account
until a human with the right role says so — with one deliberate exception, a
*temporary hold* on a deterministic hard flag.

---

## Run it in two commands

No installation, no API key, no model server.

```bash
python3 data/seed.py        # generate the synthetic bank
python3 app/server.py       # http://localhost:8000
```

Sign in as `l1@bank.test` / `l1` (or `l2` / `admin` with the matching password),
pick a model, press **Investigate** on `ALT-001` … `ALT-004`.

Prove it works:

```bash
python3 tests/verify.py     # 19 checks across Stage 3 and Stage 4
```

### Run it against real open-weight models

The default provider is an **offline stub** so the pipeline runs anywhere. It is
not a language model, and everything it produces is stamped `simulated`. The
graded Stage 3 comparison must be run against real models — see
[docs/running-models-locally.md](docs/running-models-locally.md) for the hardware
maths, which models actually fit, and the exact commands.

The short version:

```bash
# 1. Install Ollama                     https://ollama.com/download
#    (or: brew install ollama)

# 2. Keep models off the boot disk if it is tight
export OLLAMA_MODELS=/Volumes/Avinash/ollama-models

# 3. Pull the three models (~14 GB total)
ollama pull mistral:7b && ollama pull llama3.1:8b && ollama pull qwen2.5:7b

# 4. Point the app at them
export FS_LLM_PROVIDER=ollama
export FS_LLM_BASE_URL=http://localhost:11434/v1
export FS_MODELS=mistral:7b,llama3.1:8b,qwen2.5:7b
python3 app/server.py
```

### Or run the same models hosted, via OpenRouter

If the laptop cannot hold a 7B model — or you simply do not want 14 GB of
weights on disk — OpenRouter serves the same open-weight models over the same
OpenAI-compatible wire format. Nothing in the pipeline changes:

```bash
# 1. Key from https://openrouter.ai/keys, into the gitignored override file
echo 'FS_OPENROUTER_API_KEY=sk-or-v1-...' >> .env.local

# 2. Switch provider. Base URL and the `vendor/model` slugs come from the
#    preset in app/config.py, so leave FS_LLM_BASE_URL and FS_MODELS blank.
export FS_LLM_PROVIDER=openrouter
python3 app/server.py
```

The preset models are the hosted counterparts of the local trio —
`mistralai/mistral-nemo`, `meta-llama/llama-3.1-8b-instruct`,
`qwen/qwen-2.5-7b-instruct` — so a hosted run and a local run are comparable.
Override `FS_MODELS` with any slugs from <https://openrouter.ai/models>.
Slugs are retired over time (OpenRouter no longer serves
`mistralai/mistral-7b-instruct`, hence Nemo); preflight checks yours against the
live list on startup and names any that have gone.

Two things are worth setting before you record Stage 3 numbers:

- `FS_OPENROUTER_PROVIDERS=Together` pins the upstream host. OpenRouter routes
  each request to whichever host is healthiest, and latency is a property of the
  host as much as the model — unpinned, two runs of one model are not the same
  measurement. Add `FS_OPENROUTER_PROVIDERS_ONLY=1` to forbid fallback entirely.
- `FS_OPENROUTER_NO_TRAIN=1` (the default) excludes upstreams that train on
  prompts. The prompts carry customer data, so leave it on.

Groq or any other OpenAI-compatible endpoint works the same way — set
`FS_LLM_BASE_URL` and `FS_LLM_API_KEY`. Whichever you choose, run every model
through **one** provider, or the latency comparison is meaningless.

`FS_MODELS` is the approved-model allowlist: the orchestrator rejects any model
not named in it, so whatever tags you pull must be listed there and recorded in
the report.

### Run the open-model comparison

```bash
pip install matplotlib
python3 benchmark/run_benchmark.py --runs 3
```

30 labelled cases × every model × 3 runs. Writes a CSV, a JSON summary and the
report figures to `benchmark/results/`, and fills the dashboard's benchmark
screen.

**On a deployed instance** there is no shell to type that into, so an admin can
start the same run from the dashboard: **Model benchmark → Run a comparison**.
It calls `benchmark.run()` — the one the CLI calls — so the fairness controls,
scoring and artefacts are identical. One run at a time, progress is shown while
it works, and the model list is the same `FS_MODELS` allowlist the orchestrator
enforces.

Results survive a reseed: `data/seed.py` rebuilds the synthetic data but carries
`benchmark_results` across, because those rows cost real model calls. Pass
`--drop-benchmarks` to discard them deliberately.

---

## What is actually implemented

| Brief requirement | Where it lives | How to see it |
|---|---|---|
| UI/UX connected to the automation | `ui/`, `app/server.py` | The Investigate button POSTs the same webhook contract n8n exposes |
| n8n or equivalent orchestration | `n8n/*.json` (importable) and `app/orchestrator.py` | Both implement the same 20 nodes; `n8n/build_workflows.py` generates the JSON |
| Multiple agent roles | `app/agents.py`, `prompts/v3/` | Four agents, chained, each with its own prompt and output schema |
| Conditional routing | `orchestrator.decide_route()` | Conditions evaluated in the brief's stated order |
| Tool / data integration | `app/evidence.py`, `data/schema.sql` | Transactions, customer, devices, beneficiaries, watchlist, policy rules |
| HITL approval | `orchestrator.decide()`, Case view | Paused case + approval matrix + mandatory override reason |
| Escalation | hard flags HF1–HF3 | TC-03 places a temporary hold and escalates to L2 |
| Exception handling | `orchestrator._exception()` | Injection, invalid output, missing data, model failure, unknown alert |
| 3 test cases (+adversarial) | `data/test_cases.json`, `tests/verify.py` | TC-01 normal, TC-02 ambiguous, TC-03 high risk, TC-04 injection |
| ≥3 open-model comparison | `benchmark/` | Mistral, Llama and Qwen on identical cases, evidence, prompts and schema; raw decisions scored |
| Guardrails & governance | `docs/governance/` | Risk → Guardrail → NIST AI RMF → implementation |
| Process & architecture diagrams | `docs/diagrams/` | Figures 1–3 as SVG, regenerable, plus a draw.io file |

---

## How a case flows

```
Alert ──► Validate payload ──► Fetch evidence ──► Evidence pack (PII masked, E-IDs)
                                                        │
                            Injection scan ◄─────────────┤
                            Hard flags HF1–HF3 ◄─────────┘
                                   │
     Transaction Analysis ─► Customer Behaviour ─► Risk/Policy ─► Recommendation
                                   │
                            Output validator  (schema · grounding · numbers · 1 retry)
                                   │
                            Decision router
         ┌─────────────────┬───────┴────────┬──────────────────┐
    AUTO_PROCEED        HITL            ESCALATE           EXCEPTION
      (A)               (H)                (E)                (E)
   release          human decides    temporary hold      human reviews
   10% sampled      approval matrix   + L2 review        raw evidence
         └─────────────────┴────────────────┴──────────────────┘
                                   │
                        Mock core banking + append-only audit log
```

### The three design decisions that matter

**Hard flags are deterministic and cannot be downgraded.** A watchlist hit, an
account-takeover pattern, or a split burst to a brand-new payee is decided by
code, before any model runs. A model may escalate further; it can never clear
one. This is what keeps a confidently wrong model from releasing money.

**Agents cannot act.** `app/agents.py` has no path to `app/mock_cbs.py`. The
restriction is structural, not a line in a prompt — so a successful prompt
injection still cannot move funds.

**The raw model decision is recorded separately from the system decision.** The
benchmark scores the raw one. Scoring after the guardrails would measure the
guardrails and flatter the model.

---

## Layout

```
fraudsentinel/
├── app/            orchestrator, agents, guardrails, features, audit, server
├── ui/             dashboard (no build step, no framework)
├── n8n/            importable workflow JSON + the generator that produces it
├── prompts/        v1 naive · v2 context · v3 full STOIC — four agents each
├── data/           schema (Postgres + SQLite), synthetic seed, test & benchmark cases
├── benchmark/      comparison harness, scorer, results
├── tests/          verify.py — 19 end-to-end checks
├── docs/           stage1 · diagrams · governance · expert · report
└── website/        public project site
```

## Deploying

The dashboard in `ui/` is served by `app/server.py`, so any host that can run a
process and expose a port runs it unchanged — Render, Railway, Fly.io, a VM.

For **Streamlit Community Cloud** there is a second front-end,
`streamlit_app.py`, because Community Cloud exposes only the Streamlit port and
cannot serve `ui/` alongside it. It is a front-end, not a fork: routing,
approval matrix, guardrails and the model allowlist stay in `app/`.

```bash
pip install -r requirements.txt
streamlit run streamlit_app.py
```

Full guide, including secrets handling and the ephemeral-disk caveat:
[docs/deployment-streamlit.md](docs/deployment-streamlit.md).

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `FS_LLM_PROVIDER` | `stub` | `stub`, `ollama`, `openrouter`, `groq` |
| `FS_LLM_BASE_URL` | per provider preset | OpenAI-compatible endpoint; blank takes the preset |
| `FS_LLM_API_KEY` | — | Bearer token, if the provider needs one |
| `FS_MODELS` | per provider preset | Approved model list; anything else is rejected |
| `FS_LLM_TIMEOUT` | `120` | Seconds per model call |
| `FS_OPENROUTER_API_KEY` | — | OpenRouter key; `OPENROUTER_API_KEY` is accepted too |
| `FS_OPENROUTER_APP_NAME` | `FraudSentinel` | `X-Title` attribution header |
| `FS_OPENROUTER_SITE_URL` | — | `HTTP-Referer` attribution header |
| `FS_OPENROUTER_PROVIDERS` | — | Upstream host preference order, e.g. `Together,DeepInfra` |
| `FS_OPENROUTER_PROVIDERS_ONLY` | `0` | `1` forbids falling back off that list |
| `FS_OPENROUTER_NO_TRAIN` | `1` | Refuse upstreams that train on prompts |
| `FS_PROMPT_VERSION` | `v3` | Default prompt version |
| `FS_NOW` | seed reference time | `live` for wall clock; the demo data is time-relative |
| `FS_PORT` | `8000` | Dashboard port |

`app/config.py` loads `.env.local` then `.env`, and `.env.local` wins. `.env` is
committed (it documents the defaults); `.env.local` is gitignored, so that is
where API keys go. A real environment variable still beats both.

Decision thresholds are **not** environment variables. They live in the
`policy_rules` table so an admin can change them from the UI, and every change is
written to the audit log.

## Importing the n8n workflows

```bash
python3 n8n/build_workflows.py     # regenerate after editing the generator
```

In n8n: **Import from File** for `main_workflow.json`, `error_workflow.json` and
`mock_cbs.json`. Set `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, `LLM_BASE_URL`,
`LLM_API_KEY`, `MOCK_CBS_WORKFLOW_ID`, `AUDIT_WORKFLOW_ID` and
`ERROR_WORKFLOW_ID` as n8n environment variables, then run `data/schema.sql` and
`data/seed.sql` in Supabase.

## Honest limitations

- Authentication is a demo (in-memory sessions, fixed passwords). The schema
  supports Supabase Auth + row-level security; wiring it is not done.
- The stub provider is **not** a model. Any benchmark run against it is a dry run
  of the harness.
- Hard-flag thresholds are reasoned, not calibrated against real alert outcomes.
- Bias analysis is structural — the hooks and the segment data exist, but 30
  synthetic cases cannot establish fairness.
- SQLite is used locally for zero-setup. `data/schema.sql` is the Postgres
  schema the Supabase deployment uses.
