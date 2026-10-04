"""FraudSentinel — Streamlit front-end.

Streamlit Community Cloud runs one process and exposes one port, so it cannot
serve `ui/` behind `app/server.py`. This module is the alternative front-end for
that host: the same orchestrator, guardrails, approval matrix and benchmark
harness, driven in-process instead of over HTTP.

    streamlit run streamlit_app.py

What is deliberately NOT duplicated here: any decision logic. Routing lives in
orchestrator.decide_route(), the approval matrix in orchestrator.decide(), the
model allowlist in config.MODELS. This file renders and collects input. If a
rule appears to be enforced twice, that is a bug — fix it in app/, not here.

Deployment notes live in docs/deployment-streamlit.md.
"""
import os
import sys

import streamlit as st

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

st.set_page_config(page_title="FraudSentinel", page_icon="🛡️", layout="wide")


# ---------------------------------------------------------------------------
# Configuration must be settled BEFORE app.config is imported
# ---------------------------------------------------------------------------
# app/config.py reads the environment once, at import. On Community Cloud there
# is no .env — settings arrive as Streamlit secrets — so they have to be pushed
# into os.environ first or the app silently comes up on the offline stub.
# Secrets may be written either way round, because both are natural to reach for:
#
#   FS_LLM_PROVIDER = "openrouter"        <- flat, mirrors .env and the env vars
#
#   [llm]                                 <- sectioned, mirrors config.toml
#   provider = "openrouter"
#
# Only the flat form used to be read, so pasting the sectioned file into the
# Secrets box left the app on the stub with no error to explain why.
SECTION_MAP = {
    "llm": {
        "provider": "FS_LLM_PROVIDER",
        "base_url": "FS_LLM_BASE_URL",
        "models": "FS_MODELS",
        "api_key": "FS_LLM_API_KEY",
        "timeout": "FS_LLM_TIMEOUT",
    },
    "openrouter": {
        "api_key": "FS_OPENROUTER_API_KEY",
        "app_name": "FS_OPENROUTER_APP_NAME",
        "site_url": "FS_OPENROUTER_SITE_URL",
        "providers": "FS_OPENROUTER_PROVIDERS",
        "providers_only": "FS_OPENROUTER_PROVIDERS_ONLY",
        "no_train": "FS_OPENROUTER_NO_TRAIN",
    },
    "app": {
        "prompt_version": "FS_PROMPT_VERSION",
        "port": "FS_PORT",
        "db": "FS_DB",
        "now": "FS_NOW",
    },
}


def _as_env(value) -> str:
    """TOML is typed; the environment is strings only."""
    if isinstance(value, bool):
        return "1" if value else "0"      # config.LLM_* read "1"/"true"/"yes"
    if isinstance(value, (list, tuple)):
        return ",".join(str(v) for v in value)
    return str(value)


def _apply_secrets() -> None:
    try:
        secrets = dict(st.secrets)
    except Exception:
        return                      # no secrets at all: .env and defaults apply

    applied = []
    for key, value in secrets.items():
        # Sectioned form: [llm], [openrouter], [app].
        if key in SECTION_MAP and hasattr(value, "items"):
            for sub, env_name in SECTION_MAP[key].items():
                v = value.get(sub)
                if v is None or v == "" or v == []:
                    continue        # a blank must not shadow a provider preset
                os.environ.setdefault(env_name, _as_env(v))
                applied.append(env_name)
            continue
        # Flat form.
        if key.startswith(("FS_", "OPENROUTER_")) and value not in (None, "", []):
            os.environ.setdefault(key, _as_env(value))
            applied.append(key)

    # Recorded so the UI can say whether secrets arrived at all — the difference
    # between "no secrets" and "secrets in a shape I did not read" is the whole
    # diagnosis, and never contains a value.
    os.environ.setdefault("FS_SECRETS_APPLIED", ",".join(sorted(set(applied))))


_apply_secrets()

from app import audit, config, db, mock_cbs, orchestrator   # noqa: E402
from app.server import USERS                                # noqa: E402


# ---------------------------------------------------------------------------
# First boot
# ---------------------------------------------------------------------------
@st.cache_resource(show_spinner="Seeding the demo database …")
def ensure_database() -> str:
    """Seed on first boot.

    The filesystem on Community Cloud is ephemeral: it is wiped on every restart
    and redeploy. That is acceptable for a demo — the data is synthetic and
    regenerating it takes seconds — but it does mean cases, the audit trail and
    benchmark results do not survive a restart. docs/deployment-streamlit.md
    says what to change when they need to.
    """
    if not os.path.exists(config.DB_PATH):
        import subprocess
        subprocess.run([sys.executable, os.path.join(ROOT, "data", "seed.py")],
                       check=True, capture_output=True)
    return config.DB_PATH


ensure_database()

ROUTE_COLOUR = {"A": "green", "H": "blue", "E": "red"}
ROLE_HINT = {
    "L1": "may clear and verify, and hand a case to L2",
    "L2": "may also hold funds and close an escalation",
    "admin": "may also change thresholds and run the benchmark",
}
QUEUE_BLURB = {
    "HITL": "Medium risk, low confidence, missing evidence, or a sampled automatic "
            "decision. An investigator must decide before anything happens to the money.",
    "ESCALATE": "Hard flags and high risk. Funds are already on a temporary hold where a "
                "hard flag fired; L2 confirms or releases.",
    "EXCEPTION": "The workflow could not complete safely: invalid model output, missing "
                 "evidence, prompt injection or a model failure. Nothing is auto-decided here.",
}


# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------
# The demo role is kept in the query string, not only in st.session_state.
# Session state is per-connection and is lost the moment the tab is refreshed or
# the app wakes from sleeping, which on Community Cloud happens often enough to
# be irritating mid-demo. The URL survives both.
#
# This is safe *only* because these are fixed demo credentials already printed on
# the login page — anyone who can open the app can already sign in as admin, so
# the query parameter grants nothing new. It would be an authentication bypass
# against real accounts. The production path is Supabase Auth (docs/governance/).
ROLE_PARAM = "as"


def current_user():
    if st.session_state.get("user"):
        return st.session_state.user

    # Restore from the URL after a refresh. No audit entry here: the sign-in was
    # already recorded, and re-logging it on every rerun would bury the real ones.
    email = st.query_params.get(ROLE_PARAM)
    u = USERS.get(str(email).lower().strip()) if email else None
    if u:
        st.session_state.user = {"role": u["role"], "id": u["id"], "name": u["name"]}
        return st.session_state.user
    return None


def sign_in(email: str) -> None:
    u = USERS[email]
    st.session_state.user = {"role": u["role"], "id": u["id"], "name": u["name"]}
    st.query_params[ROLE_PARAM] = email
    audit.log(None, "login", f"{u['role']}:{u['id']}")
    st.rerun()


def sign_out() -> None:
    # Both, and in this order: clearing session state alone leaves the parameter
    # in the URL, and current_user() would sign you straight back in.
    st.query_params.clear()
    st.session_state.clear()
    st.rerun()


def login_view() -> None:
    st.title("FraudSentinel")
    st.caption("Governed agentic AI copilot for digital payment fraud alert investigation")

    with st.form("login"):
        email = st.text_input("Email", placeholder="l1@bank.test")
        password = st.text_input("Password", type="password")
        if st.form_submit_button("Sign in", type="primary"):
            u = USERS.get(email.lower().strip())
            if not u or u["password"] != password:
                st.error("Invalid credentials")
            else:
                sign_in(email.lower().strip())

    st.subheader("Demo roles")
    st.caption("One click signs in and stays signed in across a page refresh.")
    for addr, u in USERS.items():
        col1, col2, col3 = st.columns([1.2, 2.2, 2.6])
        if col1.button(f"Use {u['role']}", key=f"use-{u['role']}", width="stretch"):
            sign_in(addr)
        col2.markdown(f"`{addr}`")
        col3.caption(ROLE_HINT[u["role"]])

    st.caption("Fixed demo credentials, as in the PoC. The production path is Supabase "
               "Auth with row-level security — see docs/governance/.")


# ---------------------------------------------------------------------------
# Shared rendering
# ---------------------------------------------------------------------------
def provider_banner() -> None:
    if config.LLM_PROVIDER != "stub":
        return
    st.warning(
        "**Simulated model provider.** Orchestration, guardrails, routing and HITL "
        "below are real, but agent reasoning comes from the offline stub. Benchmark "
        "figures produced in this mode are **not** a real open-model comparison.",
        icon="⚠️")
    # Being on the stub when you did not intend it is almost always a secrets
    # problem, and the useful question is whether any secret arrived at all.
    applied = [k for k in os.environ.get("FS_SECRETS_APPLIED", "").split(",") if k]
    with st.expander("Why is this on the stub?"):
        if not applied:
            st.markdown(
                "**No settings were read from Streamlit secrets.** Open the app's "
                "**Settings → Secrets** and paste either form:\n\n"
                "```toml\nFS_LLM_PROVIDER = \"openrouter\"\n"
                "FS_OPENROUTER_API_KEY = \"sk-or-v1-...\"\n```\n"
                "or the sectioned form:\n\n"
                "```toml\n[llm]\nprovider = \"openrouter\"\n\n"
                "[openrouter]\napi_key = \"sk-or-v1-...\"\n```")
        elif "FS_LLM_PROVIDER" not in applied:
            st.markdown(
                f"Secrets were read (`{'`, `'.join(applied)}`), but none of them set "
                "the provider. Add `FS_LLM_PROVIDER = \"openrouter\"`, or "
                "`provider = \"openrouter\"` under `[llm]`.")
        else:
            st.markdown(
                "`FS_LLM_PROVIDER` was read but resolved to `stub`. A real "
                "environment variable beats a secret, so check for an override "
                "wherever this is running.")
        st.caption("Names only — no secret value is ever shown here.")


def money(n) -> str:
    return "—" if n is None else f"₹{float(n):,.2f}"


def case_rows(route: str):
    return db.q("""select * from cases where route=? and status='awaiting_human'
                   order by created_at desc limit 200""", (route,))


def open_case(case_id: str) -> None:
    st.session_state.case_id = case_id


# ---------------------------------------------------------------------------
# Alert queue
# ---------------------------------------------------------------------------
def alerts_view() -> None:
    st.header("Alert queue")
    st.caption("Rule-engine alerts awaiting investigation. Investigating one runs the "
               "full four-agent pipeline and routes the result.")

    alerts = db.q("""select a.*, t.amount, t.channel, c.home_city
                     from alerts a
                     join transactions t on t.id=a.txn_id
                     join customers c on c.id=a.customer_id
                     where a.status='new' order by a.created_at desc limit 100""")
    if not alerts:
        st.info("No open alerts. Re-seed with `python3 data/seed.py` to refill the demo data.")
        return

    col1, col2, col3 = st.columns(3)
    alert_id = col1.selectbox("Alert", [a["id"] for a in alerts])
    model = col2.selectbox("Model", config.MODELS)
    version = col3.selectbox("Prompt version", ["v3", "v2", "v1"],
                             index=["v3", "v2", "v1"].index(config.PROMPT_VERSION)
                             if config.PROMPT_VERSION in ("v3", "v2", "v1") else 0)

    if st.button("Investigate", type="primary"):
        with st.spinner(f"Running four agents on {alert_id} with {model} …"):
            result = orchestrator.investigate(
                {"alert_id": alert_id, "model": model, "prompt_version": version})
        route = result.get("route")
        st.success(f"Routed to **{route}** — case `{result.get('case_id', '')[:8]}`")
        if result.get("case_id"):
            open_case(result["case_id"])
            st.rerun()

    st.dataframe(
        [{"Alert": a["id"], "Rule": a["rule_fired"], "Amount": money(a["amount"]),
          "Channel": a["channel"], "City": a["home_city"], "Raised": a["created_at"]}
         for a in alerts],
        width="stretch", hide_index=True)


# ---------------------------------------------------------------------------
# Queues
# ---------------------------------------------------------------------------
def queue_view(route: str, title: str) -> None:
    st.header(title)
    st.caption(QUEUE_BLURB[route])

    rows = case_rows(route)
    if not rows:
        st.info("Nothing waiting. Investigate an alert to populate this queue.")
        return

    for r in rows:
        rec = db.jload(r["recommendation"], {}) or {}
        flags = db.jload(r["hard_flags"], []) or []
        label = (f"{r['ahe']} · {r['id'][:8]} · alert {r['alert_id']} · "
                 f"{rec.get('decision') or 'exception'} · risk {rec.get('risk_score', '—')}")
        with st.container(border=True):
            left, right = st.columns([5, 1])
            left.markdown(f"**{label}**")
            left.caption((rec.get("exception_reason") or rec.get("rationale") or "")[:200])
            if flags:
                left.markdown(" ".join(f":red[{f['code']}]" for f in flags))
            if right.button("Open", key=f"open-{r['id']}"):
                open_case(r["id"])
                st.rerun()


# ---------------------------------------------------------------------------
# Case view
# ---------------------------------------------------------------------------
AGENTS = [("transaction_analysis", "Transaction Analysis Agent"),
          ("customer_behaviour", "Customer Behaviour Agent"),
          ("risk_policy", "Risk/Policy Agent"),
          ("recommendation", "Recommendation Agent")]


def case_view(case_id: str) -> None:
    c = db.q1("select * from cases where id=?", (case_id,))
    if not c:
        st.error("No such case.")
        if st.button("← Back"):
            st.session_state.case_id = None
            st.rerun()
        return

    for k in ("evidence", "features", "agent_outputs", "recommendation", "hard_flags"):
        c[k] = db.jload(c[k], {})
    rec = c["recommendation"] or {}
    feats = c["features"] or {}
    ev = c["evidence"] or {}
    cited = set(rec.get("evidence_ids") or [])
    user = current_user()

    alert = db.q1("""select a.*, t.amount, t.channel, cu.home_city
                     from alerts a join transactions t on t.id=a.txn_id
                     join customers cu on cu.id=a.customer_id where a.id=?""", (c["alert_id"],))

    # An escalated case belongs to L2. L1 gets no queue for it and no decision
    # form either — orchestrator.decide() would refuse the submission anyway.
    with_l2 = c["route"] == "ESCALATE" and user["role"] == "L1"
    is_open = c["status"] == "awaiting_human" and not with_l2

    if st.button("← Back to queue"):
        st.session_state.case_id = None
        st.rerun()

    st.header(f"Case {c['id'][:8]} · alert {c['alert_id']}")
    st.markdown(f":{ROUTE_COLOUR.get(c['ahe'], 'gray')}[**{c['ahe']} · {c['route']}**]")

    left, right = st.columns(2)
    with left:
        st.markdown(
            f"**Transaction** {money(alert['amount'] if alert else None)} "
            f"{alert['channel'] if alert else ''}  \n"
            f"**Rule fired** {alert['rule_fired'] if alert else '—'}  \n"
            f"**Customer** {alert['customer_id'] if alert else '—'} · "
            f"{alert['home_city'] if alert else ''}  \n"
            f"**Model** {c['model'] or '—'} · prompts {c['prompt_version'] or ''}  \n"
            f"**End-to-end** {c['latency_ms'] or '—'} ms  \n"
            f"**Status** {c['status']}"
            + (f" → **{c['final_outcome']}**" if c["final_outcome"] else ""))
    with right:
        st.metric("Recommendation", rec.get("decision") or "no valid recommendation")
        score = rec.get("risk_score")
        if score is not None:
            st.progress(min(100, int(score)) / 100,
                        text=f"risk {score}/100 · confidence {rec.get('confidence', '—')}")
        if c["raw_decision"] and c["raw_decision"] != rec.get("decision"):
            st.caption(f"Model said **{c['raw_decision']}**; guardrails adjusted it.")
        if rec.get("rationale"):
            st.caption(rec["rationale"])

    if c["hard_flags"]:
        st.error("**Hard flags fired.** "
                 + "  \n".join(f"{h['code']} {h['name']} — {h['detail']}" for h in c["hard_flags"])
                 + "  \nA model may escalate a hard flag further but can never clear one.")
    if rec.get("exception_reason"):
        st.error(f"**Exception ({rec.get('exception_stage', '')}).** {rec['exception_reason']}")
    if rec.get("injection_hits"):
        st.warning("**Prompt injection quarantined.** "
                   + "; ".join(f"{h['field']}: “{h['matched']}”" for h in rec["injection_hits"])
                   + ". The text was passed to the models as data inside `<untrusted>` tags.")
    if feats.get("data_incomplete"):
        st.warning("**Mandatory evidence missing or stale:** "
                   + ", ".join(feats.get("missing_sources") or [])
                   + ". Automatic clearance is blocked.")

    # ---- agent findings --------------------------------------------------
    st.subheader("Agent findings")
    ao = c["agent_outputs"] or {}
    for col, (key, title) in zip(st.columns(2) + st.columns(2), AGENTS):
        r = ao.get(key) or {}
        o = r.get("output") or {}
        with col.container(border=True):
            st.markdown(f"**{title}** · {o.get('sub_score', '—')}/100"
                        if r.get("ok") else f"**{title}**")
            if not r.get("ok"):
                st.error(r.get("error") or "no valid output")
                continue
            items = list(o.get("findings") or [])
            items += [f"Policy {p['policy_id']}: {p['reason']}"
                      for p in (o.get("policies_triggered") or [])]
            if key == "recommendation" and o.get("decision"):
                items += [f"Decision: {o['decision']} at risk {o.get('risk_score')}, "
                          f"confidence {o.get('confidence')}."]
                items += [f"Missing: {m}" for m in (o.get("missing_information") or [])]
            for it in items:
                st.markdown(f"- {it}")
            signals = o.get("risk_signals") or o.get("deviations") or []
            if signals:
                st.caption(" ".join(f"`{s}`" for s in signals))
            st.caption(f"{r.get('latency_ms', '—')} ms"
                       + ("" if r.get("json_first_attempt", True) else " · JSON needed recovery"))

    # ---- evidence --------------------------------------------------------
    with st.expander("Evidence pack", expanded=False):
        st.caption("PII is masked before any model call. Cited items are the ones the "
                   "recommendation relies on; the validator rejects any ID not in this list.")
        for i in (ev.get("items") or []):
            mark = "✅" if i["id"] in cited else "　"
            if i.get("untrusted"):
                st.warning(f"{mark} **{i['id']}** {i['text']}  \n"
                           "*Untrusted customer text — passed to models as data, never as "
                           "instructions.*")
            else:
                st.markdown(f"{mark} **{i['id']}** {i['text']}")

    # ---- handover / decision --------------------------------------------
    if with_l2 and c["status"] == "awaiting_human":
        st.warning("**This case is with L2.** It was escalated, so it is no longer in your "
                   "queue and only L2 can close it. You are seeing it read-only.")

    if is_open and c["human_reason"]:
        with st.container(border=True):
            st.subheader(f"Handed over by {c['decided_role'] or ''}")
            st.caption("This case was escalated rather than closed. Everything the previous "
                       "investigator recorded is below — it is the only context you have.")
            st.markdown(f"**Their decision** {c['human_decision'] or '—'}  \n"
                        f"**Their reason** {c['human_reason']}  \n"
                        f"**Investigator** {c['decided_by'] or '—'} ({c['decided_role'] or '—'})")

    if is_open:
        decision_form(c, user)
    elif c["status"] != "awaiting_human":
        with st.container(border=True):
            st.subheader("Outcome")
            st.markdown(f"**Human decision** {c['human_decision'] or '—'}  \n"
                        f"**Final outcome** {c['final_outcome'] or '—'}  \n"
                        f"**Reason** {c['human_reason'] or '—'}  \n"
                        f"**Decided by** {c['decided_by'] or '—'} ({c['decided_role'] or '—'})")

    # ---- trails ----------------------------------------------------------
    actions = mock_cbs.for_case(c["id"])
    if actions:
        with st.expander("Core banking actions"):
            st.dataframe([{"Action": a["action"], "Endpoint": a["endpoint"],
                           "Result": (a.get("response") or {}).get("message", ""),
                           "Reference": (a.get("response") or {}).get("reference", ""),
                           "When": a["executed_at"]} for a in actions],
                         width="stretch", hide_index=True)

    with st.expander("Audit trail"):
        st.dataframe([{"Step": a["step"], "Actor": a["actor"], "Model": a["model"] or "—",
                       "Prompts": a["prompt_version"] or "—", "ms": a["latency_ms"],
                       "Input hash": a["input_hash"] or "—", "When": a["ts"]}
                      for a in audit.for_case(c["id"])],
                     width="stretch", hide_index=True)


def decision_form(c: dict, user: dict) -> None:
    """Collects a decision. Every rule about who may do what is enforced by
    orchestrator.decide(), which raises ApprovalError — this only renders."""
    with st.container(border=True):
        st.subheader("Your decision")
        st.caption(f"You are signed in as **{user['role']}**. "
                   + ("You may clear or verify, and hand a case to L2; holding funds and "
                      "closing an escalation require L2."
                      if user["role"] == "L1" else
                      "You may clear, verify, hold funds and close an escalation.")
                   + " An override always needs a written reason.")

        escalate_label = ("Escalate to L2" if user["role"] == "L1"
                          else "Escalate to the AML queue")
        choice = st.radio("Action",
                          ["APPROVE", "OVERRIDE", "ESCALATE"],
                          format_func=lambda v: {"APPROVE": "Approve the recommendation",
                                                 "OVERRIDE": "Override",
                                                 "ESCALATE": escalate_label}[v],
                          horizontal=True, key=f"dec-{c['id']}")

        outcome = None
        if choice == "OVERRIDE":
            outcome = st.selectbox("Final outcome", ["PROCEED", "VERIFY", "HOLD", "ESCALATE"],
                                   key=f"out-{c['id']}")

        # Handing a case on without saying why leaves the next investigator with
        # nothing to work from, so a reason is required for ESCALATE as it is
        # for OVERRIDE. orchestrator.decide() enforces the same rule.
        needs_reason = choice in ("OVERRIDE", "ESCALATE")
        placeholder = ("Why are you deciding this way?" if choice != "ESCALATE"
                       else "Why does this need L2? The next investigator sees only this."
                       if user["role"] == "L1" else "Why is this going to the AML queue?")
        reason = st.text_area(f"Reason {'(required)' if needs_reason else '(optional)'}",
                              placeholder=placeholder, key=f"reason-{c['id']}")

        if st.button("Submit decision", type="primary", key=f"submit-{c['id']}"):
            if needs_reason and not reason.strip():
                st.error("Escalating to L2 requires a reason." if choice == "ESCALATE"
                         else "An override requires a written reason.")
                return
            # The resume token never reaches a browser in the HTTP build. Here the
            # browser is not in the loop at all — this runs server-side — so reading
            # it from the row keeps the same guarantee.
            token = db.q1("select resume_token from cases where id=?", (c["id"],))["resume_token"]
            try:
                out = orchestrator.decide(c["id"], token, choice, outcome, reason,
                                          user["id"], user["role"])
            except orchestrator.ApprovalError as e:
                st.error(str(e))
                return
            except (KeyError, ValueError) as e:
                st.error(str(e))
                return

            cbs = out.get("cbs") or {}
            if out.get("escalated"):
                st.success(f"Case handed to L2 — now waiting in the escalation queue. "
                           f"Core banking: {cbs.get('message')} ({cbs.get('reference')})")
                st.session_state.case_id = None      # no longer this investigator's
            else:
                st.success(f"Case closed as {out['final_outcome']}. "
                           f"Core banking: {cbs.get('message')} ({cbs.get('reference')})")
            st.rerun()


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------
def audit_view() -> None:
    st.header("Audit trail")
    st.caption("Every orchestrator step, model call and human action, append-only. Each row "
               "carries the model, prompt version, a hash of the exact input and the actor — "
               "this is what makes a decision defensible months later.")
    rows = db.q("select * from audit_log order by id desc limit 400")
    st.dataframe([{"When": r["ts"], "Case": (r["case_id"] or "—")[:8], "Step": r["step"],
                   "Actor": r["actor"], "Model": r["model"] or "—",
                   "Prompts": r["prompt_version"] or "—", "ms": r["latency_ms"],
                   "Input hash": r["input_hash"] or "—"} for r in rows],
                 width="stretch", hide_index=True)


# ---------------------------------------------------------------------------
# Benchmark
# ---------------------------------------------------------------------------
def benchmark_view() -> None:
    st.header("Model benchmark")
    st.caption("Identical cases, evidence packs, prompts and output schema for every model, "
               "temperature 0, same host. Decisions scored are the **raw model decision**, "
               "before guardrails — otherwise the guardrails would hide the models' mistakes.")

    rows = db.q("select * from benchmark_results order by id desc limit 5000")
    if rows:
        by_model: dict = {}
        for r in rows:
            by_model.setdefault(r["model"], []).append(r)

        def pct(n, d):
            return round(100 * n / d, 1) if d else 0.0

        table = []
        for model, rs in by_model.items():
            hi = [r for r in rs if r["expected"] in ("ESCALATE", "HOLD")]
            gen = [r for r in rs if r["expected"] == "PROCEED"]
            lat = sorted(r["latency_ms"] for r in rs if r["latency_ms"])
            table.append({
                "Model": model, "n": len(rs),
                "Accuracy %": pct(sum(r["raw_decision"] == r["expected"] for r in rs), len(rs)),
                "Escalation recall %": pct(
                    sum(r["raw_decision"] in ("HOLD", "ESCALATE") for r in hi), len(hi)),
                "False positive %": pct(
                    sum(r["raw_decision"] in ("HOLD", "ESCALATE") for r in gen), len(gen)),
                "Hallucination %": pct(sum(r["hallucinated"] for r in rs), len(rs)),
                "JSON compliance %": pct(sum(r["json_valid"] for r in rs), len(rs)),
                "p50 ms": lat[len(lat) // 2] if lat else 0,
            })
        st.dataframe(table, width="stretch", hide_index=True)
        st.caption("A missed high-risk case costs far more than a slow one, so weight "
                   "escalation recall and hallucination above latency when you choose.")
    else:
        st.info("No benchmark has been run yet.")

    benchmark_runner()


def benchmark_runner() -> None:
    user = current_user()
    with st.container(border=True):
        st.subheader("Run a comparison")
        if config.LLM_PROVIDER == "stub":
            st.warning("Provider is the offline stub, so a run started here produces "
                       "**simulated** numbers, not a model comparison.")
        if user["role"] != "admin":
            st.caption(f"Signed in as **{user['role']}**. Starting a benchmark is an admin "
                       "action: it costs model calls and overwrites the dashboard figures.")
            return

        models = st.multiselect("Models (the approved list)", config.MODELS,
                                default=config.MODELS)
        col1, col2 = st.columns(2)
        runs = col1.selectbox("Runs per case", [1, 3, 5], index=1)
        version = col2.selectbox("Prompt version", ["v3", "v2", "v1"])

        total = len(models) * 30 * runs
        st.caption(f"{len(models)} models × 30 cases × {runs} runs = {total} investigations. "
                   "On a hosted provider that is billed usage, and the run holds this page "
                   "open until it finishes — for a long comparison prefer the CLI: "
                   "`python3 benchmark/run_benchmark.py --runs 3`.")

        if st.button("Start benchmark", type="primary", disabled=not models):
            from benchmark import run_benchmark

            bar = st.progress(0.0, text="Starting …")

            def progress(done, tot, model):
                bar.progress(done / tot, text=f"{model} — {done}/{tot} investigations")

            try:
                meta = run_benchmark.run(models, runs=runs, prompt_version=version,
                                         progress=progress)
            except Exception as e:                      # a failed run must not kill the page
                bar.empty()
                st.error(f"Benchmark failed: {type(e).__name__}: {e}")
                return
            bar.empty()
            audit.log(None, "benchmark_started", f"{user['role']}:{user['id']}",
                      output={"models": models, "runs": runs, "prompt_version": version})
            st.success(f"Run `{meta['run_id']}` finished in {meta['elapsed_seconds']}s — "
                       f"ranking: {' > '.join(meta['ranking'])}")
            st.rerun()


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------
def settings_view() -> None:
    st.header("Settings")
    st.caption("Decision thresholds live in the `policy_rules` table, not in the "
               "environment, so an admin can change them here and every change is written "
               "to the audit log.")
    user = current_user()
    th = db.thresholds()
    with st.form("thresholds"):
        values = {k: st.number_input(k, value=float(v), step=1.0) for k, v in th.items()}
        if st.form_submit_button("Save", type="primary"):
            changed = {k: v for k, v in values.items() if v != th[k]}
            for k, v in changed.items():
                db.set_threshold(k, v, f"{user['role']}:{user['id']}")
            st.success(f"{len(changed)} threshold(s) updated." if changed else "No changes.")
            st.rerun()

    st.subheader("Runtime")
    st.table([
        {"Setting": "Model provider", "Value": config.LLM_PROVIDER},
        {"Setting": "Endpoint", "Value": config.LLM_BASE_URL or "(offline stub)"},
        {"Setting": "Models", "Value": ", ".join(config.MODELS)},
        {"Setting": "Prompt version", "Value": config.PROMPT_VERSION},
        {"Setting": "Database", "Value": config.DB_PATH},
    ])


# ---------------------------------------------------------------------------
# Shell
# ---------------------------------------------------------------------------
def main() -> None:
    if not current_user():
        login_view()
        return

    user = current_user()
    counts = {
        "hitl": len(case_rows("HITL")),
        "escalation": len(case_rows("ESCALATE")),
        "exception": len(case_rows("EXCEPTION")),
    }

    pages = ["Alert queue", f"My review queue ({counts['hitl']})"]
    # Once a case is escalated it belongs to L2, so L1 does not see the queue at
    # all — it is not theirs to work and they cannot close anything in it.
    if user["role"] != "L1":
        pages.append(f"Escalation queue ({counts['escalation']})")
    pages += [f"Exception queue ({counts['exception']})", "Audit trail", "Model benchmark"]
    if user["role"] == "admin":
        pages.append("Settings")

    with st.sidebar:
        st.markdown("### FraudSentinel")
        st.caption(f"{user['name']} · **{user['role']}**")
        page = st.radio("Go to", pages, label_visibility="collapsed")
        # An open case takes over the main panel, so changing page in the sidebar
        # has to close it — otherwise the nav looks dead while a case is open.
        if page != st.session_state.get("page"):
            st.session_state.page = page
            st.session_state.case_id = None
        st.divider()
        st.caption(f"provider: `{config.LLM_PROVIDER}`")
        if st.button("Sign out"):
            sign_out()

    provider_banner()

    if st.session_state.get("case_id"):
        case_view(st.session_state.case_id)
        return

    if page.startswith("Alert queue"):
        alerts_view()
    elif page.startswith("My review queue"):
        queue_view("HITL", "Human review queue")
    elif page.startswith("Escalation queue"):
        queue_view("ESCALATE", "Escalation queue")
    elif page.startswith("Exception queue"):
        queue_view("EXCEPTION", "Exception queue")
    elif page.startswith("Audit trail"):
        audit_view()
    elif page.startswith("Model benchmark"):
        benchmark_view()
    elif page.startswith("Settings"):
        settings_view()


main()
