# Deploying FraudSentinel on Streamlit Community Cloud

**Written for:** whoever deploys and demos this build — assumes you can use git and
a browser, not that you know the codebase.

This covers the hosted demo: a public URL anyone can open, running the real
four-agent pipeline against open-weight models through OpenRouter.

---

## 1. What you are deploying, and why it is a second front-end

The PoC ships two front-ends over one engine:

| | `ui/` + `app/server.py` | `streamlit_app.py` |
|---|---|---|
| Serves | Vanilla JS dashboard over HTTP | Streamlit widgets |
| Run with | `python3 app/server.py` | `streamlit run streamlit_app.py` |
| Host | Anything that can run a process and expose a port | Streamlit Community Cloud |
| Dependencies | Python standard library only | `streamlit` |

They are front-ends, not forks. Routing lives in `orchestrator.decide_route()`,
the approval matrix in `orchestrator.decide()`, the model allowlist in
`config.MODELS`, the guardrails in `app/guardrails.py`. Both screens call the
same functions. **If you find a decision rule written twice, that is a bug —
fix it in `app/`, not in a front-end.**

### Why Streamlit needs its own front-end

Community Cloud runs one process and exposes one port — the Streamlit one. It
has no way to also expose `app/server.py` on port 8000, so embedding the
existing dashboard in an `iframe` pointed at `localhost:8000` renders a blank
box for everyone except you: the browser is on the viewer's machine, and
`localhost` there is their laptop, not the server.

If you would rather deploy the existing dashboard unchanged, Streamlit is the
wrong host — use Render, Railway, Fly.io or any VM, where `python3
app/server.py` works as-is. Streamlit buys you a free, zero-config public URL;
the cost is this second front-end.

---

## 2. Before you deploy: get the secret out of the repository

`.env` is **tracked by git** in this project. Anything you put in it is
published the moment you push, and Community Cloud deploys from a GitHub repo,
so this is the step that most often goes wrong.

```bash
git ls-files --error-unmatch .env   # if this succeeds, .env is tracked
git log -S'sk-or-v1-' -- .env       # any commits listed = key already published
```

If a real key was ever committed, **revoke it** at <https://openrouter.ai/keys>
and issue a new one. Do not rely on rewriting history: once pushed, assume it
was scraped. Automated scanners find OpenRouter and OpenAI keys on GitHub within
minutes of a push.

Keys belong in exactly two places:

- **Locally** — `.env.local`, which is gitignored. `app/config.py` loads it
  *before* `.env` and first write wins, so it overrides the committed file:
  ```bash
  echo 'FS_OPENROUTER_API_KEY=sk-or-v1-...' >> .env.local
  ```
- **On Community Cloud** — the app's **Secrets** panel (section 4). Never in the
  repo.

Leave the key line in the committed `.env` blank.

---

## 3. Push the repository

Community Cloud deploys a branch of a GitHub repo. It needs three things that
are already in place:

| File | Why it matters |
|---|---|
| `streamlit_app.py` | The entrypoint. Keep it at the repository root. |
| `requirements.txt` | Must list `streamlit`. Community Cloud installs from it; without the entry the app dies on import. |
| `data/seed.py` | Builds the SQLite database on first boot. |

```bash
git add streamlit_app.py requirements.txt
git commit -m "feat: streamlit front-end for hosted demo"
git push origin main
```

A public repository gets a free app. A private one needs the GitHub permission
Streamlit asks for during sign-in.

---

## 4. Create the app

1. Sign in at <https://share.streamlit.io> with GitHub.
2. **New app** → pick the repo, the branch (`main`), and
   `streamlit_app.py` as the main file path.
3. Open **Advanced settings → Secrets** *before* deploying, and paste:

   ```toml
   FS_LLM_PROVIDER = "openrouter"
   FS_OPENROUTER_API_KEY = "sk-or-v1-..."
   FS_OPENROUTER_APP_NAME = "FraudSentinel"
   FS_OPENROUTER_SITE_URL = "https://<your-app>.streamlit.app"
   FS_OPENROUTER_NO_TRAIN = "1"
   FS_PROMPT_VERSION = "v3"
   ```

   This is TOML, not shell: values are quoted, there is no `export`.
4. **Deploy**. First boot takes a couple of minutes — it installs
   dependencies, then seeds the database.

### How secrets reach the engine

`app/config.py` reads configuration from the environment **once, at import**.
Community Cloud provides secrets as `st.secrets`, not as environment variables,
so `streamlit_app.py` copies every `FS_*` and `OPENROUTER_*` secret into
`os.environ` *before* importing `app.config`:

```python
_apply_secrets()                       # must come first
from app import audit, config, db, mock_cbs, orchestrator
```

Get that order wrong and the app silently comes up on the offline stub with no
error — the banner at the top of every page is what tells you. If you add a new
setting, give it an `FS_` prefix or it will not be picked up.

### Settings worth knowing

| Secret | Default | Why set it |
|---|---|---|
| `FS_LLM_PROVIDER` | `stub` | `stub`, `ollama`, `openrouter`. **`ollama` cannot work on Community Cloud** — there is no model server in the container. |
| `FS_MODELS` | provider preset | The approved-model allowlist. Leave unset to take the OpenRouter slugs from `config.PROVIDER_PRESETS`. |
| `FS_OPENROUTER_PROVIDERS` | — | Pins the upstream host. Set it before recording latency figures (section 7). |
| `FS_OPENROUTER_NO_TRAIN` | `1` | Sends `data_collection: deny`, so no upstream trains on a prompt. Leave on. |
| `FS_NOW` | seed reference | The demo data is time-relative; leave unset so the scripted cases stay exact. |

Changing a secret restarts the app. Because the database is rebuilt on a cold
start (section 5), treat a secret change as "the demo data resets".

---

## 5. The database resets, and what that means for your demo

Community Cloud's filesystem is **ephemeral**. It is wiped on every restart,
redeploy, secret change and when the app sleeps after inactivity.

`streamlit_app.py` seeds on boot when the file is missing:

```python
@st.cache_resource
def ensure_database():
    if not os.path.exists(config.DB_PATH):
        subprocess.run([sys.executable, "data/seed.py"], check=True)
```

So the app always comes up working, but **cases, the audit trail and benchmark
results do not survive a restart**. The synthetic customer and alert data does,
because it is regenerated identically each time.

For a graded demo this is usually fine — and worth saying out loud in the
report, because "the audit trail is append-only" and "the audit trail is wiped
on redeploy" are both true here for different reasons. Practically:

- Do a live run during the demo rather than relying on cases from yesterday.
- Download anything you need to keep. `benchmark/results/*.csv` and
  `summary_*.json` are written to the same ephemeral disk, so pull them out of
  the container (or re-run the comparison locally) before a restart.
- A free app sleeps after about a week idle; the first visit afterwards is a
  cold start with an empty database.

**To make it persist**, you need a host with a real disk or an external
database. `app/db.py` is the only module that talks to storage — the docstring
says swapping SQLite for the Supabase/Postgres schema in `data/schema.sql` means
replacing that file and nothing else. That is the production path
`docs/governance/` already assumes.

---

## 6. Verify the deployment

Open the app URL and check, in order:

1. **No amber banner.** The orange *"Simulated model provider"* banner means
   `FS_LLM_PROVIDER` did not arrive — recheck the secret name and the TOML
   quoting. Everything below it still works, but the reasoning is the offline
   stub and any benchmark figure is simulated.
2. **Sign in** as `admin@bank.test` / `admin`. Fixed demo credentials, as in the
   PoC; the production path is Supabase Auth with row-level security
   (`docs/governance/`).
3. **Settings → Runtime** shows `provider: openrouter`, the endpoint, and the
   `vendor/model` slugs. This is the fastest way to confirm what the container
   actually resolved.
4. **Alert queue → Investigate.** A real model call takes a few seconds. If
   every case lands in the Exception queue, go to section 8.
5. **Audit trail** has rows carrying the model, prompt version and input hash.

### Role behaviour to demonstrate

| Role | Sees | Cannot |
|---|---|---|
| `l1@bank.test` / `l1` | Alert, review, exception queues | See the escalation queue at all; close an escalated case. Opening one by link shows it read-only. |
| `l2@bank.test` / `l2` | All queues | Change thresholds |
| `admin@bank.test` / `admin` | All queues, Settings, the benchmark runner | — |

Escalating to L2 **requires a written reason**; L2 then sees it as a *"Handed
over by L1"* panel. That hand-off is the clearest 30-second demonstration of
human-in-the-loop governance in the build.

---

## 7. Running the benchmark on the deployed app

**Model benchmark → Run a comparison**, as admin. It calls the same
`benchmark.run()` the CLI uses, so the fairness controls, scoring and artefacts
are identical.

Two things to understand before you press it:

- **It runs synchronously and holds the page open.** 3 models × 30 cases × 3
  runs is 270 model calls; on a hosted provider that is minutes, and billed. If
  the browser tab closes, Streamlit stops the script. Start with 1 run per case
  to prove the path, and run the full comparison from a shell:
  ```bash
  FS_LLM_PROVIDER=openrouter python3 benchmark/run_benchmark.py --runs 3
  ```
- **Pin the upstream host first.** OpenRouter routes each request to whichever
  host is healthiest, and latency is a property of the host as much as the
  model. Unpinned, two runs of one model are not the same measurement:
  ```toml
  FS_OPENROUTER_PROVIDERS = "Together,DeepInfra"
  FS_OPENROUTER_PROVIDERS_ONLY = "1"
  ```

`summary_<run_id>.json` records the provider and endpoint, so the report can say
where the numbers came from. Results are written to the ephemeral disk — see
section 5.

---

## 8. Troubleshooting

| Symptom | Cause |
|---|---|
| `ModuleNotFoundError: streamlit` in the build log | `streamlit` missing from `requirements.txt` |
| App loads but shows the amber simulated banner | `FS_LLM_PROVIDER` secret not set, misspelled, or unquoted in the TOML |
| `HTTP 401` on every investigation | No API key reached the container. Check **Secrets**, not `.env` — the committed `.env` has a blank key line by design |
| `provider error 402` | OpenRouter account out of credit |
| `provider error 404` on a slug | The slug is retired. Check <https://openrouter.ai/models> and set `FS_MODELS`. OpenRouter no longer serves `mistralai/mistral-7b-instruct`, which is why the preset uses `mistralai/mistral-nemo` |
| `no allowed providers` | `FS_OPENROUTER_PROVIDERS_ONLY = "1"` and none of the named hosts serves that model, or `FS_OPENROUTER_NO_TRAIN` excluded all of them |
| Every case lands in the Exception queue | The model is ignoring the JSON schema. This is a legitimate benchmark finding — report it as a structured-output compliance failure rather than hiding it |
| Queues empty after a redeploy | Expected: ephemeral disk (section 5) |
| `database not found` | First-boot seeding failed. The build log carries the `data/seed.py` traceback |
| Latency figures move between runs | The upstream host changed. Pin it (section 7) |
| App is slow to wake | Free-tier cold start after sleeping |

---

## 9. Pre-demo checklist

- [ ] The key in git history has been revoked; the live key exists only in Secrets
- [ ] The committed `.env` has no key in it
- [ ] App URL opens with **no** amber banner
- [ ] Settings → Runtime shows `openrouter` and the expected slugs
- [ ] One investigation run end-to-end, just now, on the deployed URL
- [ ] L1 → escalate-with-reason → L2 sees the hand-off panel
- [ ] Benchmark figures exported locally if you intend to quote them

---

## See also

- `README.md` — local setup and the configuration table
- `docs/running-models-locally.md` — Ollama locally, OpenRouter hosted, model slugs
- `docs/governance/guardrails-and-compliance.md` — why `FS_OPENROUTER_NO_TRAIN` is on
