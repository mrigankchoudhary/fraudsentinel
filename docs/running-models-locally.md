# Running the open-weight models

The Stage 3 comparison has to run against real models. The stub provider exists
so the pipeline demos anywhere; anything it produces is stamped `simulated` and
is not a model comparison.

This page covers what will actually run, on what hardware, and the exact commands.

---

## 1. The models this project uses

```
mistral:7b    llama3.1:8b    qwen2.5:7b
```

Three open-weight models from three different families — Mistral AI, Meta and
Alibaba. The brief asks for "at least three open/open-weight models (for example
Qwen, Llama, Mistral, Gemma or approved equivalents)", so this set meets it.

| Tag | Family | Disk | RAM to run |
|---|---|---|---|
| `mistral:7b` | Mistral AI | ~4.4 GB | ~5.5 GB |
| `llama3.1:8b` | Meta | ~4.9 GB | ~6 GB |
| `qwen2.5:7b` | Alibaba | ~4.7 GB | ~5.5 GB |
| **total** | | **~14 GB** | **~6 GB** (one at a time) |

Figures are for the default Q4 quantisation; confirm with `ollama list` after
pulling. Two separate limits, and people usually only check the first:

- **Disk** is the *sum* — all ~14 GB sit on disk at once.
- **RAM** is the *largest single model*, because Ollama loads one at a time and
  unloads it after about five minutes idle. Here that is `llama3.1:8b` at ~6 GB.

### What to expect on 8 GB of RAM

This machine is an Apple M1 with 8 GB unified memory. macOS already uses 3–4 GB,
so a 6 GB model does not fit cleanly and the system will swap. It will run — just
slowly, with the fans up.

Plan around it rather than being surprised by it:

- `~/` has about 9.7 GB free, which is **less than the 14 GB** these models need.
  Setting `OLLAMA_MODELS` to the external drive (section 2) is not optional here.
- The full benchmark is **1,080 model calls** (30 cases × 3 models × 3 runs ×
  4 agents). Measure one case first (section 5) and multiply — if a single
  investigation takes 60 s, `--runs 3` is a nine-hour job. Run it overnight, or
  use `--runs 1` for a first pass, or move to a hosted provider (section 6).
- Close other applications before a long run. Swap pressure is the whole problem.

If it turns out too slow to be workable, the same three families have smaller
tags — `mistral:7b` has no smaller sibling, but `llama3.2:3b` (~2 GB) and
`qwen2.5:3b` (~1.9 GB) are drop-in replacements that fit in RAM comfortably.
Swap the tags in `FS_MODELS` and nothing else changes. Record whichever tags you
actually ran in the report; the guide asks for the exact tags either way.

---

## 2. Install Ollama

Download from <https://ollama.com/download>, or:

```bash
brew install ollama
```

Then start the server (the app installer does this for you):

```bash
ollama serve      # leave running, or use the menu-bar app
```

### Keep models off a full boot disk

Ollama stores models in `~/.ollama` by default. If your home disk is tight —
**it currently has about 9.7 GB free, which is less than even the small set
needs comfortably** — move the store to the external drive first:

```bash
export OLLAMA_MODELS=/Volumes/Avinash/ollama-models
```

Set it **before** pulling anything, and in the same shell that runs `ollama serve`.
To make it permanent:

```bash
echo 'export OLLAMA_MODELS=/Volumes/Avinash/ollama-models' >> ~/.zshrc
```

One caveat: if the external drive is unmounted, Ollama will not find its models.

---

## 3. Pull the models

```bash
ollama pull mistral:7b
ollama pull llama3.1:8b
ollama pull qwen2.5:7b

ollama list          # confirm the real sizes
```

About 14 GB of download. Check one works before going further:

```bash
ollama run mistral:7b "Reply with the JSON {\"ok\": true} and nothing else."
```

If that comes back wrapped in prose rather than bare JSON, that is not a setup
problem — it is exactly what the benchmark's structured-output compliance metric
is there to measure.

---

## 4. Point FraudSentinel at them

`.env` is already set up for this:

```bash
FS_LLM_PROVIDER=ollama
FS_LLM_BASE_URL=http://localhost:11434/v1
FS_MODELS=mistral:7b,llama3.1:8b,qwen2.5:7b
```

`app/config.py` reads `.env.local` then `.env` on import — `.env.local` wins and
is gitignored, which is where API keys belong. Then just start the server:

```bash
python3 app/server.py
```

A real environment variable always beats the file, so you can still demo offline
without editing anything:

```bash
FS_LLM_PROVIDER=stub python3 app/server.py
```

On startup the server checks the endpoint and tells you if Ollama is unreachable
or a configured model has not been pulled — rather than letting every
investigation fail as an exception case, which looks like a broken pipeline.

`FS_MODELS` is an **allowlist**: `orchestrator._validate_payload()` rejects any
model not named in it, and the rejection becomes an exception case. If a model
you pulled does not appear in the dropdown, this is why.

The dashboard banner should lose the amber "Simulated model provider" warning.
If it does not, the app is still on the stub.

---

## 5. Sanity-check before the full benchmark

One case, one model, real inference:

```bash
python3 -c "
import sys; sys.path.insert(0,'.')
from app import orchestrator
r = orchestrator.investigate({'alert_id':'ALT-003','model':'mistral:7b','mode':'benchmark'})
print(r['route'], r['raw_decision'], r['latency_ms'], 'ms  simulated=', r['simulated'])"
```

Expect `ESCALATE` and `simulated= False`.

**Note the latency and multiply by 270** — that is the number of investigations in
a `--runs 3` pass over three models. 30 s per case means roughly two and a quarter
hours; 90 s per case means closer to seven.

Then the comparison:

```bash
pip install matplotlib
python3 benchmark/run_benchmark.py --runs 3
```

Start with `--runs 1` to confirm the whole thing completes, then do the real
3-run pass. The runner handles a model erroring or timing out — it records a
failed task rather than dropping the row, because dropping it would flatter the
model.

---

## 6. Alternative: a hosted provider (OpenRouter)

If local inference is too slow, or the weights will not fit, any
OpenAI-compatible endpoint works with no code change. **OpenRouter** is the
supported hosted path and has a preset in `app/config.py`.

### Setup

```bash
# 1. Key from https://openrouter.ai/keys.
#    .env is committed; .env.local is gitignored and loaded with priority.
echo 'FS_OPENROUTER_API_KEY=sk-or-v1-...' >> .env.local

# 2. Switch provider. Leave FS_LLM_BASE_URL and FS_MODELS blank in .env and
#    the preset supplies both.
export FS_LLM_PROVIDER=openrouter
python3 app/server.py
```

The server prints the endpoint it resolved on startup, so you can see at a
glance which path a run took:

```
  model provider : openrouter
  endpoint       : https://openrouter.ai/api/v1
  models         : mistralai/mistral-nemo, meta-llama/llama-3.1-8b-instruct, qwen/qwen-2.5-7b-instruct
```

### Model slugs

OpenRouter names models `vendor/model`, not as Ollama tags. The preset is the
hosted equivalent of the local trio, so a hosted run can be compared with a
local one:

| Local (Ollama) | Hosted (OpenRouter) | Note |
|---|---|---|
| `mistral:7b` | `mistralai/mistral-nemo` | OpenRouter has retired `mistralai/mistral-7b-instruct`. Nemo is 12B and Apache-2.0 — still open-weight, but a size up, so record it as a deviation rather than claiming a like-for-like Mistral comparison |
| `llama3.1:8b` | `meta-llama/llama-3.1-8b-instruct` | like for like |
| `qwen2.5:7b` | `qwen/qwen-2.5-7b-instruct` | like for like |

Check <https://openrouter.ai/models> rather than guessing — slugs change as
models are retired, and one that does not exist comes back as an error that the
orchestrator turns into an exception case. Preflight checks your `FS_MODELS`
against the live list on startup and names any slug that is not served.

### Pin the upstream before you record numbers

OpenRouter is a gateway: one slug may be served by several upstream hosts, and it
picks per request. Latency is a property of the host as much as of the model, so
an unpinned benchmark is not a repeatable measurement.

```bash
export FS_OPENROUTER_PROVIDERS=Together,DeepInfra   # preference order
export FS_OPENROUTER_PROVIDERS_ONLY=1               # never fall back off it
```

`summary_<run_id>.json` records the provider and endpoint for exactly this
reason — the report should be able to say where the numbers came from.

A run can also be started from the dashboard (**Model benchmark → Run a
comparison**, admin only), which is how you produce the comparison on a deployed
instance with no shell. It is the same `benchmark.run()` the CLI calls.

### Privacy

`FS_OPENROUTER_NO_TRAIN=1` is the default and sends `data_collection: deny`, so
requests are only routed to upstreams that do not train on prompts.

Hosted inference arguably gives a **better** fairness story than local: every
model runs on identical hardware, which is precisely the control the brief asks
for. The trade-off is that evidence leaves the machine — fine here, because the
data is entirely synthetic, but worth stating in the report, and it is the reason
`docs/governance/` lists a local Ollama deployment as the privacy-preserving
option for real customer data.

---

## Troubleshooting

| Symptom | Cause |
|---|---|
| Dashboard still shows the amber simulated banner | `FS_LLM_PROVIDER` is still `stub`, or the server was started in a different shell from the one with the exports |
| `connection refused` on port 11434 | `ollama serve` is not running |
| Model missing from the dropdown | Not listed in `FS_MODELS` |
| `ollama: command not found` | Ollama is not installed yet — section 2. `ollama serve` only works after installing |
| Every case lands in the exception queue | The model is ignoring the JSON schema. Check `ollama run <tag>` by hand; very small models sometimes cannot hold the format. This is a legitimate benchmark finding — report it as a structured-output compliance failure rather than hiding it |
| Extremely slow, fans at full speed | The model does not fit in RAM and macOS is swapping. Use a smaller tag |
| `no such file or directory` after a reboot | `OLLAMA_MODELS` points at an unmounted drive |
| OpenRouter: `HTTP 401` | No key, or a stale `.env.local` is overriding the one in `.env`. Set `FS_OPENROUTER_API_KEY` (or `FS_LLM_API_KEY`) |
| OpenRouter: `provider error 402` | Account out of credit |
| OpenRouter: `provider error 404` on a slug | The slug is wrong or retired — check <https://openrouter.ai/models> |
| OpenRouter: `no allowed providers` | `FS_OPENROUTER_PROVIDERS_ONLY=1` and none of the named hosts serves that model, or `FS_OPENROUTER_NO_TRAIN=1` excluded all of them |
| Latency figures move between runs on OpenRouter | The upstream host changed. Pin it with `FS_OPENROUTER_PROVIDERS` |
| `sqlite3.OperationalError: disk I/O error` | Stale WAL sidecars from a killed server. `rm -f data/fraudsentinel.db-wal data/fraudsentinel.db-shm && python3 data/seed.py` |
