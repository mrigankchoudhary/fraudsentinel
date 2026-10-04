"""Model layer.

Two providers, one interface:

  "ollama" / "openrouter" / "groq" / "openai-compatible"
      Real open-weight models over the OpenAI chat-completions wire format.
      This is what you must use for the graded Stage 3 comparison.

      "ollama"     keeps inference on this machine.
      "openrouter" reaches the same class of open-weight models as a hosted
                   service — the fallback when a laptop cannot hold a 7B model,
                   and the only extra work is a key, the `vendor/model` slugs
                   and two attribution headers.

  "stub"
      A deterministic offline reasoner. It lets the whole orchestration, the
      guardrails, HITL and the UI run and be demonstrated with no model server
      installed.

      !! The stub is NOT a language model. Benchmark numbers produced against
      !! it are SIMULATED and must never be presented as a real open-model
      !! comparison. Every artefact it touches is labelled simulated=true.
"""
import hashlib, json, random, re, time, urllib.error, urllib.request

from . import config


class ModelError(RuntimeError):
    pass


def complete(model: str, system: str, user: str,
             agent: str = "recommendation", version: str = "v3") -> tuple:
    """Returns (text, latency_ms, simulated).

    `agent` and `version` are ignored by real providers — the prompt already says
    everything. The stub uses them to pick its shape, which is why they are
    passed explicitly rather than guessed from the prompt text.
    """
    t0 = time.perf_counter()
    if config.LLM_PROVIDER == "stub":
        text = _stub(model, system, user, agent, version)
        simulated = True
    else:
        text = _http(model, system, user)
        simulated = False
    return text, int((time.perf_counter() - t0) * 1000), simulated


# ---------------------------------------------------------------------------
# Real models
# ---------------------------------------------------------------------------
def _http(model: str, system: str, user: str) -> str:
    payload_out = {
        "model": model,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "temperature": config.LLM_TEMPERATURE,   # fairness control: 0 for every model
        "max_tokens": config.LLM_MAX_TOKENS,
        "stream": False,
    }
    headers = {"Content-Type": "application/json"}
    if config.LLM_PROVIDER == "openrouter":
        headers.update(_openrouter_headers())
        extra = _openrouter_routing()
        if extra:
            payload_out.update(extra)

    req = urllib.request.Request(
        config.LLM_BASE_URL.rstrip("/") + "/chat/completions",
        data=json.dumps(payload_out).encode(), headers=headers)
    if config.LLM_API_KEY:
        req.add_header("Authorization", f"Bearer {config.LLM_API_KEY}")
    try:
        with urllib.request.urlopen(req, timeout=config.LLM_TIMEOUT) as r:
            payload = json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise ModelError(f"{model}: HTTP {e.code} {_detail(e)}") from e
    except Exception as e:
        raise ModelError(f"{model}: {e}") from e

    # OpenRouter reports upstream failures (no provider for a slug, rate limit,
    # credit exhausted) as a 200 with an `error` object rather than an HTTP
    # error, so a response has to be inspected, not just parsed.
    err = payload.get("error") if isinstance(payload, dict) else None
    if err:
        msg = err.get("message", err) if isinstance(err, dict) else err
        code = err.get("code", "") if isinstance(err, dict) else ""
        raise ModelError(f"{model}: provider error {code} {msg}")
    try:
        return payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as e:
        raise ModelError(f"{model}: unexpected response shape {payload}") from e


def _openrouter_headers() -> dict:
    """Attribution headers. OpenRouter attributes the request to an app on your
    account dashboard, which is where the usage evidence for the report lives."""
    h = {}
    if config.OPENROUTER_SITE_URL:
        h["HTTP-Referer"] = config.OPENROUTER_SITE_URL
    if config.OPENROUTER_APP_NAME:
        h["X-Title"] = config.OPENROUTER_APP_NAME
    return h


def _openrouter_routing() -> dict:
    """Upstream routing block.

    Two things matter for a defensible benchmark. One, latency and refusal
    behaviour are properties of the *upstream host*, not just the model, so
    pinning the provider keeps a comparison apples-to-apples. Two, prompts carry
    customer data, so hosts that train on them are excluded by default.
    """
    prov = {}
    if config.OPENROUTER_PROVIDERS:
        prov["order"] = config.OPENROUTER_PROVIDERS
        prov["allow_fallbacks"] = not config.OPENROUTER_PROVIDERS_ONLY
    if config.OPENROUTER_NO_TRAIN:
        prov["data_collection"] = "deny"
    return {"provider": prov} if prov else {}


def _detail(e) -> str:
    """OpenRouter puts the useful part of a failure in a JSON error body."""
    try:
        raw = e.read()
    except Exception:
        return ""
    try:
        body = json.loads(raw)
        err = body.get("error", body)
        return str(err.get("message", err)) if isinstance(err, dict) else str(err)
    except Exception:
        return repr(raw[:300])


def extract_json(text: str):
    """Models wrap JSON in prose or fences more often than they should. Try the
    strict parse first, then recover. Whether recovery was needed is itself a
    metric (structured-output compliance), so the caller is told."""
    if text is None:
        return None, False
    s = text.strip()
    try:
        return json.loads(s), True          # clean, first-attempt compliant
    except json.JSONDecodeError:
        pass
    fence = re.search(r"```(?:json)?\s*(.+?)```", s, re.S)
    if fence:
        try:
            return json.loads(fence.group(1).strip()), False
        except json.JSONDecodeError:
            pass
    start, depth = s.find("{"), 0
    if start >= 0:
        for i in range(start, len(s)):
            if s[i] == "{":
                depth += 1
            elif s[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(s[start:i + 1]), False
                    except json.JSONDecodeError:
                        break
    return None, False


# ---------------------------------------------------------------------------
# Offline stub
# ---------------------------------------------------------------------------
# Each entry gives the simulated model a distinct, reproducible character so the
# benchmark exercises the scoring code end to end. These are invented traits for
# a dry run — not measurements of the real models.
_PERSONA = {
    "llama3.1:8b":  {"bias":  0, "json_fail": 0.04, "halluc": 0.03, "lat": (1900, 3400), "verbose": 0.25},
    "qwen2.5:7b":   {"bias": -2, "json_fail": 0.01, "halluc": 0.02, "lat": (1500, 2600), "verbose": 0.05},
    "mistral:7b":   {"bias": -6, "json_fail": 0.09, "halluc": 0.07, "lat": (1100, 1900), "verbose": 0.35},
    "gemma2:9b":    {"bias":  3, "json_fail": 0.06, "halluc": 0.05, "lat": (2100, 3900), "verbose": 0.30},
}
_DEFAULT_PERSONA = {"bias": 0, "json_fail": 0.05, "halluc": 0.04,
                    "lat": (1500, 3000), "verbose": 0.2}


def _rng(model, user):
    """Deterministic per (model, case, agent) — reruns reproduce exactly."""
    h = hashlib.sha256((model + "|" + user).encode()).hexdigest()
    return random.Random(int(h[:16], 16))


def _stub(model: str, system: str, user: str, agent: str, version: str) -> str:
    p = _PERSONA.get(model, _DEFAULT_PERSONA)
    rng = _rng(model, user + "|" + agent + "|" + version)
    facts = _parse_facts(user)
    ids = re.findall(r"^(E\d+)\.", user, re.M) or ["E1"]
    time.sleep(rng.uniform(*p["lat"]) / 1000.0 * 0.02)   # token latency, scaled down

    # v1 and v2 never ask for JSON or for evidence citations, so a model given
    # them answers in prose. That is the whole point of the refinement log: the
    # earlier versions fail structured-output compliance by construction.
    if version == "v1":
        return ("This transaction looks suspicious to me. There are a few things that stand "
                "out and the bank should probably take a closer look before letting it "
                "through. I would suggest contacting the customer.")
    if version == "v2":
        out = _stub_recommendation(facts, ids, p, rng) if agent == "recommendation" else None
        if out:
            # Right substance, wrong contract: prose wrapper, no evidence IDs.
            return (f"Based on the findings, my assessment is {out['decision']}. "
                    f"{out['rationale'].split(':', 1)[-1].strip()} "
                    f"I would put the risk at roughly {out['risk_score']} out of 100.")
        return ("Here are my findings: the activity deviates from the customer's usual "
                "pattern in several respects, which I have described above.")

    if agent == "transaction_analysis":
        out = _stub_transaction(facts, ids, p, rng)
    elif agent == "customer_behaviour":
        out = _stub_behaviour(facts, ids, p, rng)
    elif agent == "risk_policy":
        out = _stub_policy(facts, ids, p, rng)
    else:
        out = _stub_recommendation(facts, ids, p, rng)

    # Simulated hallucination: cite an evidence ID that does not exist. The
    # output validator is supposed to catch this — that is the point.
    if rng.random() < p["halluc"]:
        out.setdefault("evidence_ids", []).append(f"E{rng.randint(60, 99)}")

    text = json.dumps(out, ensure_ascii=False)

    # Simulated structured-output failure: fence it or prepend chatter.
    if rng.random() < p["json_fail"]:
        text = rng.choice([
            f"```json\n{text}\n```",
            f"Here is my analysis:\n\n{text}",
            f"{text}\n\nLet me know if you need more detail.",
        ])
    return text


def _parse_facts(user: str) -> dict:
    """The stub reads the same evidence text a real model would."""
    g = lambda rx, cast=float, d=None: (
        cast(m.group(1).replace(",", "")) if (m := re.search(rx, user)) else d)
    return {
        "amount": g(r"debit of Rs([\d,]+\.\d\d)"),
        "ratio": g(r"is ([\d.]+)x the 90-day maximum"),
        "vel_count": g(r"^E\d+\. (\d+) outgoing transfers", int) or g(r"(\d+) outgoing transfers totalling", int),
        "vel_sum": g(r"outgoing transfers totalling\s+Rs([\d,]+\.\d\d)"),
        "same_payee": g(r"(\d+) of them went to", int),
        "ben_hours": g(r"was added\s+([\d.]+) hours ago"),
        "prior_payments": g(r"has received\s+(\d+) prior payments", int),
        "device_hours": g(r"device first seen\s+([\d.]+) hours ago"),
        "new_device": "NEW device" in user,
        "mismatch": "MISMATCH" in user,
        "sim_hours": g(r"SIM change on the account was ([\d.]+) hours ago"),
        "watchlist": "MATCH — account is listed" in user,
        "data_gap": "Mandatory evidence is missing" in user,
        "injection": "<untrusted>" in user and bool(re.search(
            r"ignore previous instructions|SYSTEM:", user, re.I)),
        "hard_flags": re.findall(r"\b(HF[123])\b", user),
    }


def _clamp(v):
    return max(0, min(100, int(round(v))))


def _stub_transaction(f, ids, p, rng):
    score, findings, signals, cited = 5, [], [], []
    if f["ratio"] and f["ratio"] >= 3:
        score += 30; signals.append("amount_deviation")
        findings.append(f"Transfer is {f['ratio']}x the customer's 90-day maximum ({ids[3] if len(ids)>3 else ids[0]}).")
        cited.append(ids[3] if len(ids) > 3 else ids[0])
    elif f["ratio"] and f["ratio"] >= 1.5:
        score += 12; signals.append("elevated_amount")
        findings.append(f"Transfer is {f['ratio']}x the 90-day maximum ({ids[3] if len(ids)>3 else ids[0]}).")
        cited.append(ids[3] if len(ids) > 3 else ids[0])
    if (f["same_payee"] or 0) >= 3:
        score += 35; signals.append("split_transfers")
        findings.append(f"{f['same_payee']} transfers to the same payee inside 15 minutes "
                        f"totalling Rs{f['vel_sum']:,.2f} ({ids[4] if len(ids)>4 else ids[0]}).")
        cited.append(ids[4] if len(ids) > 4 else ids[0])
    elif (f["vel_count"] or 0) >= 3:
        score += 15; signals.append("velocity")
        findings.append(f"{f['vel_count']} outgoing transfers in 15 minutes ({ids[4] if len(ids)>4 else ids[0]}).")
        cited.append(ids[4] if len(ids) > 4 else ids[0])
    if not findings:
        findings.append(f"Amount and frequency are consistent with the 90-day baseline ({ids[3] if len(ids)>3 else ids[0]}).")
        cited.append(ids[3] if len(ids) > 3 else ids[0])
    if f["injection"]:
        findings.append("The payment remark contains instruction-like text; treated as data only.")
    return {"findings": findings, "risk_signals": signals,
            "evidence_ids": sorted(set(cited)),
            "sub_score": _clamp(score + p["bias"] + rng.randint(-3, 3))}


def _stub_behaviour(f, ids, p, rng):
    score, findings, devs, cited = 5, [], [], []
    pick = lambda i: ids[min(i, len(ids) - 1)]
    if f["ben_hours"] is not None and f["ben_hours"] < 1:
        score += 28; devs.append("beneficiary_minutes_old")
        findings.append(f"Beneficiary was added {f['ben_hours']} hours before the transfer ({pick(5)}).")
        cited.append(pick(5))
    elif f["ben_hours"] is not None and f["ben_hours"] < 24:
        score += 15; devs.append("new_beneficiary")
        findings.append(f"Beneficiary was added {f['ben_hours']} hours ago ({pick(5)}).")
        cited.append(pick(5))
    if f["new_device"]:
        score += 18; devs.append("new_device")
        findings.append(f"Login from a device first seen {f['device_hours']} hours ago ({pick(6)}).")
        cited.append(pick(6))
    if f["mismatch"]:
        score += 18; devs.append("location_mismatch")
        findings.append(f"Login city differs from home city and the 30-day pattern ({pick(6)}).")
        cited.append(pick(6))
    if f["sim_hours"] is not None and f["sim_hours"] < 24:
        score += 25; devs.append("sim_swap")
        findings.append(f"SIM was changed {f['sim_hours']} hours ago ({pick(7)}).")
        cited.append(pick(7))
    ato = {"new_device", "sim_swap"} <= set(devs) or {"sim_swap", "location_mismatch"} <= set(devs)
    findings.append("Pattern is consistent with account takeover." if ato
                    else "No account-takeover pattern in the behavioural evidence.")
    if f["data_gap"]:
        findings.append("Part of the behavioural evidence is missing or stale; "
                        "confidence in this assessment is reduced.")
    if not cited:
        cited.append(pick(6))
    return {"findings": findings, "deviations": devs,
            "evidence_ids": sorted(set(cited)),
            "sub_score": _clamp(score + p["bias"] + rng.randint(-3, 3))}


def _stub_policy(f, ids, p, rng):
    pick = lambda i: ids[min(i, len(ids) - 1)]
    score, pols, findings, cited = 5, [], [], []
    if f["watchlist"]:
        score += 45
        pols.append({"policy_id": "PR-01", "reason": f"Beneficiary is on the mule watchlist ({pick(8)})."})
        cited.append(pick(8))
    if f["ben_hours"] is not None and f["ben_hours"] < 24:
        score += 15
        pols.append({"policy_id": "PR-05", "reason": f"Beneficiary younger than 24h ({pick(5)})."})
        cited.append(pick(5))
    if f["sim_hours"] is not None and f["sim_hours"] < 24:
        score += 20
        pols.append({"policy_id": "PR-06", "reason": f"SIM changed within 24h ({pick(7)})."})
        cited.append(pick(7))
    if f["new_device"]:
        score += 10
        pols.append({"policy_id": "PR-07", "reason": f"Device newer than 72h ({pick(6)})."})
        cited.append(pick(6))
    if f["ratio"] and f["ratio"] >= 3:
        score += 15
        pols.append({"policy_id": "PR-08", "reason": f"Amount exceeds 3x the 90-day maximum ({pick(3)})."})
        cited.append(pick(3))
    if (f["same_payee"] or 0) >= 3:
        score += 20
        pols.append({"policy_id": "PR-09", "reason": f"Split burst to one new payee ({pick(4)})."})
        cited.append(pick(4))
    hf = sorted(set(f["hard_flags"]))
    if hf:
        score = max(score, 80)
        findings.append("Deterministic hard flags " + ", ".join(hf) +
                        " are present and cannot be downgraded.")
    if not pols:
        findings.append("No bank policy thresholds are triggered by this transfer.")
    if not cited:
        cited.append(pick(0))
    return {"policies_triggered": pols, "hard_flags": hf, "findings": findings,
            "evidence_ids": sorted(set(cited)),
            "sub_score": _clamp(score + p["bias"] + rng.randint(-3, 3))}


def _stub_recommendation(f, ids, p, rng):
    pick = lambda i: ids[min(i, len(ids) - 1)]
    score = 5
    cited, bits = [], []
    if f["watchlist"]:
        score += 45; cited.append(pick(8))
        bits.append(f"beneficiary is on the mule watchlist ({pick(8)})")
    if f["ben_hours"] is not None and f["ben_hours"] < 24:
        score += 16; cited.append(pick(5))
        bits.append(f"payee added {f['ben_hours']}h before the transfer ({pick(5)})")
    if f["sim_hours"] is not None and f["sim_hours"] < 24:
        score += 22; cited.append(pick(7))
        bits.append(f"SIM changed {f['sim_hours']}h ago ({pick(7)})")
    if f["new_device"]:
        score += 12; cited.append(pick(6))
        bits.append(f"login from a device first seen {f['device_hours']}h ago ({pick(6)})")
    if f["mismatch"]:
        score += 12; cited.append(pick(6))
        bits.append(f"login city outside the 30-day pattern ({pick(6)})")
    if (f["same_payee"] or 0) >= 3:
        score += 28; cited.append(pick(4))
        bits.append(f"{f['same_payee']} transfers to one new payee in 15 minutes ({pick(4)})")
    if f["ratio"] and f["ratio"] >= 3:
        score += 14; cited.append(pick(3))
        bits.append(f"amount is {f['ratio']}x the 90-day maximum ({pick(3)})")

    score = _clamp(score + p["bias"] + rng.randint(-4, 4))
    missing = []
    if f["data_gap"]:
        missing = ["device_login_feed"]

    if f["hard_flags"]:
        decision, score = "ESCALATE", max(score, 82)
    elif score >= 60:
        decision = "ESCALATE"
    elif score >= 30 or missing:
        decision = "VERIFY"
    else:
        decision = "PROCEED"

    typ = ("mule" if f["watchlist"] else
           "account_takeover" if (f["sim_hours"] is not None and f["sim_hours"] < 24
                                  and f["new_device"]) else
           "social_engineering" if (f["ben_hours"] is not None and f["ben_hours"] < 24
                                    and f["ratio"] and f["ratio"] >= 3) else "none")

    if bits:
        rationale = ("Recommend " + decision + ": " + "; ".join(bits[:4]) + ".")
    else:
        rationale = (f"Recommend {decision}: amount, payee and device are all consistent "
                     f"with the customer's 90-day baseline ({pick(3)}).")
        cited.append(pick(3))
    if missing:
        rationale += " Device evidence is stale, so the case cannot be cleared automatically."
    if f["injection"]:
        rationale += " The payment remark contained instruction-like text, which was ignored."
    if rng.random() < p["verbose"]:
        rationale += " Suggest confirming with the customer through an out-of-band channel."

    conf = 0.92 if f["hard_flags"] else (0.74 if missing else
                                         0.88 if score < 30 or score >= 60 else 0.66)
    return {"decision": decision, "risk_score": score,
            "confidence": round(min(0.99, max(0.3, conf + rng.uniform(-0.05, 0.05))), 2),
            "rationale": rationale, "evidence_ids": sorted(set(cited)),
            "missing_information": missing, "suspected_typology": typ}
