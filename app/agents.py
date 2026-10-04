"""The four specialised agents (guide Section 6.3).

Each agent is a prompt + a model call + a parse. Agents hold NO tools that can
act on an account — that restriction is the 'unsafe autonomous action' guardrail
in Stage 4, and it is enforced structurally here rather than by instruction:
nothing in this module can reach app.mock_cbs.
"""
import functools, os

from . import audit, config, evidence, llm

AGENTS = ["transaction_analysis", "customer_behaviour", "risk_policy", "recommendation"]

TITLES = {
    "transaction_analysis": "Transaction Analysis Agent",
    "customer_behaviour": "Customer Behaviour Agent",
    "risk_policy": "Risk/Policy Agent",
    "recommendation": "Recommendation Agent",
}


@functools.lru_cache(maxsize=64)
def prompt(agent: str, version: str) -> str:
    path = os.path.join(config.PROMPT_DIR, version, f"{agent}.txt")
    if not os.path.exists(path):
        raise FileNotFoundError(f"no prompt for {agent} at version {version}")
    with open(path) as f:
        return f.read().strip()


def _user_message(agent, pack, features, prior, hard_flags):
    lines = ["EVIDENCE PACK", evidence.render(pack), ""]

    if agent == "customer_behaviour":
        lines += ["TRANSACTION ANALYSIS AGENT FINDINGS",
                  _summarise(prior.get("transaction_analysis")), ""]
    elif agent == "risk_policy":
        lines += ["TRANSACTION ANALYSIS AGENT FINDINGS",
                  _summarise(prior.get("transaction_analysis")), "",
                  "CUSTOMER BEHAVIOUR AGENT FINDINGS",
                  _summarise(prior.get("customer_behaviour")), "",
                  "DETERMINISTIC HARD FLAGS (already decided, cannot be downgraded)",
                  ", ".join(h["code"] + " " + h["detail"] for h in hard_flags) or "none", ""]
    elif agent == "recommendation":
        for a in AGENTS[:3]:
            lines += [TITLES[a].upper() + " FINDINGS", _summarise(prior.get(a)), ""]
        lines += ["DETERMINISTIC HARD FLAGS (cannot be downgraded)",
                  ", ".join(h["code"] + " " + h["detail"] for h in hard_flags) or "none", ""]
        if features.get("data_incomplete"):
            lines += ["MANDATORY EVIDENCE MISSING: "
                      + ", ".join(features["missing_sources"]), ""]

    lines.append("Return only the JSON described in your instructions.")
    return "\n".join(lines)


def _summarise(result) -> str:
    if not result or not result.get("ok"):
        return "(unavailable)"
    o = result["output"]
    parts = []
    for f in o.get("findings", [])[:6]:
        parts.append("- " + str(f))
    for key in ("risk_signals", "deviations", "hard_flags"):
        if o.get(key):
            parts.append(f"- {key}: " + ", ".join(map(str, o[key])))
    for p in o.get("policies_triggered", [])[:6]:
        parts.append(f"- policy {p.get('policy_id')}: {p.get('reason')}")
    if o.get("sub_score") is not None:
        parts.append(f"- sub_score: {o['sub_score']}")
    return "\n".join(parts) or "(no findings)"


def run(agent, case_id, model, version, pack, features, prior, hard_flags):
    """Returns {ok, output, raw, json_first_attempt, latency_ms, error, simulated}."""
    sys_prompt = prompt(agent, version)
    user = _user_message(agent, pack, features, prior, hard_flags)
    try:
        raw, latency, simulated = llm.complete(model, sys_prompt, user, agent, version)
    except llm.ModelError as e:
        audit.log(case_id, f"agent:{agent}", "agent", model, version,
                  inputs=user, output={"error": str(e)})
        return {"ok": False, "error": str(e), "output": None, "raw": None,
                "json_first_attempt": False, "latency_ms": None, "simulated": None}

    parsed, first_attempt = llm.extract_json(raw)
    result = {"ok": parsed is not None, "output": parsed, "raw": raw,
              "json_first_attempt": first_attempt, "latency_ms": latency,
              "simulated": simulated,
              "error": None if parsed is not None else "model did not return valid JSON"}
    audit.log(case_id, f"agent:{agent}", "agent", model, version,
              inputs=user, output=parsed if parsed is not None else {"raw": raw[:2000]},
              latency_ms=latency)
    return result
