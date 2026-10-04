"""Runtime configuration. Thresholds live in the policy_rules table (guide 6.5)
so an admin can change them from the UI; this module only holds defaults and
environment wiring."""
import json, os
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_dotenv(path=os.path.join(ROOT, ".env")):
    """Read .env into the environment if present.

    Deliberately tiny and dependency-free, and a real environment variable always
    wins — so `FS_LLM_PROVIDER=stub python3 app/server.py` overrides the file
    rather than being silently ignored.
    """
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key, value = key.strip(), value.strip().strip('"').strip("'")
                os.environ.setdefault(key, value)
    except FileNotFoundError:
        pass


# .env is committed (it documents the defaults), so a real API key does not
# belong in it. .env.local is gitignored and loaded first — with setdefault,
# first write wins, so it overrides .env without being tracked.
_load_dotenv(os.path.join(ROOT, ".env.local"))
_load_dotenv()
DB_PATH = os.environ.get("FS_DB", os.path.join(ROOT, "data", "fraudsentinel.db"))
PROMPT_DIR = os.path.join(ROOT, "prompts")

# --- model layer ----------------------------------------------------------
# "stub"      : deterministic rule-based reasoner, no network. Lets the whole
#               pipeline run and be graded without any model server.
# "ollama"    : OpenAI-compatible endpoint on this machine, http://localhost:11434/v1
# "openrouter": OpenAI-compatible gateway to hosted open-weight models. Same wire
#               format as Ollama, so nothing downstream changes — only the base
#               URL, a Bearer key and the model slugs (`vendor/model`).
# "groq" / "openai-compatible": same again, different base URL.
#
# PROVIDER_PRESETS saves you from having to remember a base URL and a model list
# per provider: pick FS_LLM_PROVIDER and the rest has a sane default that any
# explicit FS_* variable still overrides.
PROVIDER_PRESETS = {
    "stub": {
        "base_url": "",
        "models": "mistral:7b,llama3.1:8b,qwen2.5:7b",
    },
    "ollama": {
        "base_url": "http://localhost:11434/v1",
        "models": "mistral:7b,llama3.1:8b,qwen2.5:7b",
    },
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        # The hosted counterparts of the local trio, so a hosted run is
        # comparable with a local one. Mistral is the exception: OpenRouter has
        # retired mistral-7b-instruct, so this is mistral-nemo (12B, Apache-2.0)
        # — still open-weight, but a size up. Say so in the report; preflight
        # names any slug that has since been retired too.
        "models": ("mistralai/mistral-nemo,"
                   "meta-llama/llama-3.1-8b-instruct,"
                   "qwen/qwen-2.5-7b-instruct"),
    },
    "groq": {
        "base_url": "https://api.groq.com/openai/v1",
        "models": "llama-3.1-8b-instant,mixtral-8x7b-32768",
    },
}

LLM_PROVIDER = os.environ.get("FS_LLM_PROVIDER", "stub")
_PRESET = PROVIDER_PRESETS.get(LLM_PROVIDER, PROVIDER_PRESETS["ollama"])

LLM_BASE_URL = os.environ.get("FS_LLM_BASE_URL") or _PRESET["base_url"]
# OpenRouter's own convention is OPENROUTER_API_KEY, so accept it as well —
# it is the variable their docs and most tooling already set.
LLM_API_KEY  = (os.environ.get("FS_LLM_API_KEY")
                or (os.environ.get("FS_OPENROUTER_API_KEY", "")
                    or os.environ.get("OPENROUTER_API_KEY", "")
                    if LLM_PROVIDER == "openrouter" else "")
                or "")
LLM_TIMEOUT  = float(os.environ.get("FS_LLM_TIMEOUT") or "120")

# --- OpenRouter specifics -------------------------------------------------
# Attribution headers: OpenRouter uses them for the per-app dashboard and rate
# limits. Optional, but set them and your runs are identifiable in the activity
# log, which is useful evidence for the report.
OPENROUTER_SITE_URL = os.environ.get("FS_OPENROUTER_SITE_URL", "")
OPENROUTER_APP_NAME = os.environ.get("FS_OPENROUTER_APP_NAME", "FraudSentinel")

# Upstream routing controls. A benchmark is only fair if every case for a model
# goes to the same upstream host, so pin it when you care about latency numbers:
#   FS_OPENROUTER_PROVIDERS=Together,DeepInfra   (preference order)
#   FS_OPENROUTER_PROVIDERS_ONLY=1               (never fall back off that list)
OPENROUTER_PROVIDERS = [p.strip() for p in os.environ.get(
    "FS_OPENROUTER_PROVIDERS", "").split(",") if p.strip()]
OPENROUTER_PROVIDERS_ONLY = os.environ.get(
    "FS_OPENROUTER_PROVIDERS_ONLY", "").lower() in ("1", "true", "yes")
# Exclude upstreams that log or train on prompts — the privacy control named in
# docs/governance/guardrails-and-compliance.md for the hosted path.
OPENROUTER_NO_TRAIN = os.environ.get(
    "FS_OPENROUTER_NO_TRAIN", "1").lower() in ("1", "true", "yes")

PROMPT_VERSION = os.environ.get("FS_PROMPT_VERSION", "v3")

# Open-weight models offered in the UI dropdown and used by the benchmark.
# Defaults follow the provider (Ollama tags vs OpenRouter slugs).
# Record the exact tags you actually ran in the report (guide 6.12).
# `or` rather than a get() default: .env ships FS_MODELS blank so that switching
# FS_LLM_PROVIDER is a one-line change, and a blank value must fall through to
# the preset rather than being read as "no approved models".
MODELS = [m.strip() for m in (os.environ.get("FS_MODELS")
                              or _PRESET["models"]).split(",") if m.strip()]

# Temperature 0 and a fixed token ceiling for every model — a fairness control.
LLM_TEMPERATURE = 0.0
LLM_MAX_TOKENS = 900

DEFAULT_THRESHOLDS = {
    "AUTO_PROCEED_MAX": 30.0,
    "ESCALATE_MIN": 60.0,
    "MIN_AUTO_CONFIDENCE": 0.8,
    "AUTO_SAMPLE_RATE": 0.10,
    "NEW_BENEFICIARY_HOURS": 24.0,
    "SIM_CHANGE_HOURS": 24.0,
    "NEW_DEVICE_HOURS": 72.0,
    "AMOUNT_RATIO_HIGH": 3.0,
    "SPLIT_BURST_COUNT": 3.0,
    "HITL_TIMEOUT_MIN": 30.0,
}

def reference_now() -> datetime:
    """The instant the engine reasons 'from'.

    The demo data is time-relative (a SIM change '6 hours ago'), so by default we
    anchor to the generator's reference instant and the test cases stay exact
    however long after seeding you run them. Set FS_NOW=live to use wall clock.
    """
    env = os.environ.get("FS_NOW", "")
    if env and env != "live":
        return datetime.fromisoformat(env)
    if env == "live":
        return datetime.now(timezone.utc)
    try:
        with open(os.path.join(ROOT, "data", "seed_meta.json")) as f:
            return datetime.fromisoformat(json.load(f)["reference_now"])
    except Exception:
        return datetime.now(timezone.utc)
