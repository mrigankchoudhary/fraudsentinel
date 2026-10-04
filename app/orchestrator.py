"""The orchestrator (guide Sections 6.4-6.7).

Mirrors the 20-node n8n workflow in n8n/main_workflow.json node for node, so the
two implementations stay comparable:

   1 webhook        -> investigate()
   2 validate       -> _validate_payload
   3 fetch evidence -> evidence.fetch_bundle
   4 evidence pack  -> evidence.build_pack        (PII masked, E-IDs assigned)
   5 injection scan -> guardrails.scan_injection
   6 hard flags     -> features.hard_flags
   7 router         -> _agent_plan
   8-11 agents      -> agents.run x4
   12 validator     -> guardrails.validate_output (1 repair retry, then exception)
   13 decision router -> route()
   14 create case   -> _persist
   15 respond       -> returns case_id immediately
   16 wait          -> case.status = awaiting_human
   17 apply decision-> decide()                   (approval matrix enforced)
   18 execute       -> mock_cbs.execute
   19 audit         -> audit.log after every step
   20 error         -> _exception()
"""
import random, time, uuid

from . import agents, audit, config, db, evidence, features, guardrails, llm, mock_cbs

ROUTES = ("AUTO_PROCEED", "HITL", "ESCALATE", "EXCEPTION")

# Who may authorise what (guide 6.6). L1 may clear and verify; anything that
# holds money or closes an escalation needs L2.
APPROVAL_MATRIX = {
    "L1": {"PROCEED", "VERIFY", "ESCALATE"},
    "L2": {"PROCEED", "VERIFY", "HOLD", "ESCALATE"},
    "admin": {"PROCEED", "VERIFY", "HOLD", "ESCALATE"},
}


class PayloadError(ValueError):
    pass


def _validate_payload(payload: dict) -> dict:
    alert_id = str(payload.get("alert_id", "")).strip()
    if not alert_id:
        raise PayloadError("alert_id is required")
    if not db.q1("select id from alerts where id=?", (alert_id,)):
        raise PayloadError(f"unknown alert_id {alert_id!r}")

    model = str(payload.get("model") or config.MODELS[0]).strip()
    if model not in config.MODELS:
        raise PayloadError(f"model {model!r} is not in the approved list {config.MODELS}")

    mode = str(payload.get("mode") or "live").strip()
    if mode not in ("live", "benchmark"):
        raise PayloadError(f"mode must be 'live' or 'benchmark', got {mode!r}")

    version = str(payload.get("prompt_version") or config.PROMPT_VERSION).strip()
    if version not in ("v1", "v2", "v3"):
        raise PayloadError(f"unknown prompt_version {version!r}")

    return {"alert_id": alert_id, "model": model, "mode": mode, "prompt_version": version}


def investigate(payload: dict) -> dict:
    """Node 1-15. Runs the pipeline and returns as soon as the case is routed."""
    t0 = time.perf_counter()
    case_id = str(uuid.uuid4())

    try:
        p = _validate_payload(payload)
    except PayloadError as e:
        return _exception(case_id, payload.get("alert_id"), None, None,
                          f"payload rejected: {e}", stage="validate_payload")

    audit.log(case_id, "investigate_requested", "orchestrator",
              p["model"], p["prompt_version"], inputs=p, output=p)

    th = db.thresholds()
    try:
        bundle = evidence.fetch_bundle(p["alert_id"])
        feats = features.compute(bundle, th)
        pack = evidence.build_pack(bundle, feats)
    except Exception as e:                                    # node 20
        return _exception(case_id, p["alert_id"], p["model"], p["prompt_version"],
                          f"evidence assembly failed: {e}", stage="fetch_evidence")

    audit.log(case_id, "evidence_pack_built", "orchestrator", p["model"],
              p["prompt_version"], inputs=p["alert_id"],
              output={"evidence_ids": pack["ids"], "features": feats})

    # --- node 5: injection scan -------------------------------------------
    injected, hits = guardrails.scan_injection(pack["untrusted"])
    if injected:
        audit.log(case_id, "injection_detected", "orchestrator",
                  output={"hits": hits})

    hard_flags = feats["hard_flags"]

    # --- nodes 8-11: the agents, in the order the brief specifies ----------
    prior, agent_latency = {}, 0
    for name in agents.AGENTS:
        r = agents.run(name, case_id, p["model"], p["prompt_version"],
                       pack, feats, prior, hard_flags)
        prior[name] = r
        agent_latency += r["latency_ms"] or 0
        if not r["ok"] and name == "recommendation":
            pass          # handled by the validator below
        elif not r["ok"]:
            audit.log(case_id, f"agent_degraded:{name}", "orchestrator",
                      output={"error": r["error"]})

    rec = prior["recommendation"]

    # --- node 12: output validator, one repair retry ------------------------
    ok, errors, cleaned = (False, ["recommendation agent returned nothing"], None)
    if rec["ok"]:
        ok, errors, cleaned = guardrails.validate_output(rec["output"], pack["ids"], feats)

    # A validator error naming an ungrounded ID or an invented number IS the
    # hallucination measurement — nothing else in the pipeline can see it.
    hallucinated = any("hallucinated evidence IDs" in e or "unknown evidence IDs" in e
                       or "amount not present in the evidence" in e for e in errors)

    repaired = False
    if not ok:
        audit.log(case_id, "output_validation_failed", "orchestrator",
                  p["model"], p["prompt_version"], output={"errors": errors})
        retry = agents.run("recommendation", case_id, p["model"], p["prompt_version"],
                           pack, feats, prior, hard_flags)
        agent_latency += retry["latency_ms"] or 0
        if retry["ok"]:
            ok, errors, cleaned = guardrails.validate_output(retry["output"], pack["ids"], feats)
            hallucinated = hallucinated or any(
                "hallucinated evidence IDs" in e or "unknown evidence IDs" in e
                or "amount not present in the evidence" in e for e in errors)
            repaired = ok
            if ok:
                prior["recommendation"] = retry
                rec = retry

    latency_ms = int((time.perf_counter() - t0) * 1000)

    if not ok:
        return _exception(case_id, p["alert_id"], p["model"], p["prompt_version"],
                          "model output failed validation after retry: " + "; ".join(errors),
                          stage="output_validator", features=feats, pack=pack,
                          agent_outputs=_outputs(prior), latency_ms=latency_ms,
                          hallucinated=hallucinated,
                          json_first_attempt=rec.get("json_first_attempt"),
                          simulated=rec.get("simulated"))

    raw_decision = cleaned["decision"]       # recorded BEFORE guardrails adjust it

    if injected:
        return _exception(case_id, p["alert_id"], p["model"], p["prompt_version"],
                          "Prompt injection detected in customer free text; quarantined "
                          "and referred for human review.",
                          stage="injection_scanner", features=feats, pack=pack,
                          agent_outputs=_outputs(prior), recommendation=cleaned,
                          raw_decision=raw_decision, latency_ms=latency_ms,
                          injection_hits=hits, hallucinated=hallucinated,
                          json_first_attempt=rec.get("json_first_attempt"),
                          simulated=rec.get("simulated"))

    # --- node 13: decision router ------------------------------------------
    decision, route, ahe, reason = decide_route(cleaned, feats, th)
    final = dict(cleaned, decision=decision)

    sampled = False
    if route == "AUTO_PROCEED" and random.random() < th["AUTO_SAMPLE_RATE"]:
        sampled, route, ahe = True, "HITL", "H"
        reason += " Selected by the random sample of automatic decisions."

    # --- node 14: persist ---------------------------------------------------
    _persist(case_id, p, feats, pack, prior, final, raw_decision, route, ahe,
             latency_ms, sampled, repaired=repaired)
    audit.log(case_id, "routed", "orchestrator", p["model"], p["prompt_version"],
              output={"route": route, "ahe": ahe, "decision": decision,
                      "raw_decision": raw_decision, "reason": reason,
                      "sampled": sampled}, latency_ms=latency_ms)

    # --- nodes 16-18 --------------------------------------------------------
    if p["mode"] == "benchmark":
        # Skip the Wait node and the core banking call. The route is recorded,
        # but no action is taken and no case enters an investigator's queue.
        db.ex("update cases set status='closed', human_reason='benchmark run' where id=?",
              (case_id,))
        audit.log(case_id, "benchmark_run", "system", p["model"], p["prompt_version"],
                  output={"route": route, "decision": decision})
    elif route == "AUTO_PROCEED":
        mock_cbs.execute(case_id, "PROCEED", "orchestrator",
                         "Automatic clearance within policy thresholds.")
        _close(case_id, "AUTO", "PROCEED", "Automatic clearance", "orchestrator", "system")
    elif route == "ESCALATE":
        # Deterministic hard flags hold the money immediately; this is the one
        # action the system takes without a human, and only ever a TEMPORARY hold.
        if hard_flags:
            mock_cbs.execute(case_id, "HOLD", "orchestrator",
                             "Deterministic hard flag: temporary hold pending L2 review.")
        db.ex("update cases set status='awaiting_human' where id=?", (case_id,))
    else:
        db.ex("update cases set status='awaiting_human' where id=?", (case_id,))

    db.ex("update alerts set status='investigating' where id=?", (p["alert_id"],))

    return {"case_id": case_id, "status": "processing", "route": route, "ahe": ahe,
            "decision": decision, "raw_decision": raw_decision, "risk_score": final["risk_score"],
            "confidence": final["confidence"], "reason": reason, "sampled": sampled,
            "hard_flags": [h["code"] for h in hard_flags], "latency_ms": latency_ms,
            "agent_latency_ms": agent_latency, "simulated": rec.get("simulated"),
            "hallucinated": hallucinated, "repaired": repaired,
            "json_first_attempt": rec.get("json_first_attempt"),
            "data_incomplete": feats["data_incomplete"]}


def decide_route(rec: dict, feats: dict, th: dict):
    """Node 13. Conditions are evaluated in the order given in guide 6.5.
    Returns (effective_decision, route, A/H/E, human-readable reason)."""
    hard = feats["hard_flags"]

    if hard:
        return ("ESCALATE", "ESCALATE", "E",
                "Hard flag(s) " + ", ".join(h["code"] for h in hard) +
                " fired. Temporary hold placed and the case escalated to L2. "
                "A model cannot downgrade a hard flag.")

    if feats["data_incomplete"]:
        decision = rec["decision"] if rec["decision"] != "PROCEED" else "VERIFY"
        return (decision, "HITL", "H",
                "Mandatory evidence is missing or stale (" +
                ", ".join(feats["missing_sources"]) + "), so automatic clearance is "
                "blocked and an investigator must decide.")

    if rec["risk_score"] >= th["ESCALATE_MIN"] or rec["decision"] == "ESCALATE":
        return ("ESCALATE", "ESCALATE", "E",
                f"Risk score {rec['risk_score']} is at or above the escalation "
                f"threshold of {th['ESCALATE_MIN']:.0f}, or the agent recommended "
                f"escalation. L2 approval is required.")

    if (rec["risk_score"] >= th["AUTO_PROCEED_MAX"]
            or rec["confidence"] < th["MIN_AUTO_CONFIDENCE"]
            or rec["decision"] in ("VERIFY", "HOLD")):
        return (rec["decision"], "HITL", "H",
                f"Risk score {rec['risk_score']} with confidence {rec['confidence']} "
                f"falls in the human review band, or the recommendation was "
                f"{rec['decision']}. An investigator must approve.")

    return ("PROCEED", "AUTO_PROCEED", "A",
            f"Risk score {rec['risk_score']} is below {th['AUTO_PROCEED_MAX']:.0f} "
            f"with confidence {rec['confidence']} and no flags, so the alert is "
            f"cleared automatically.")


def _outputs(prior):
    return {k: {"ok": v["ok"], "output": v["output"], "error": v["error"],
                "latency_ms": v["latency_ms"],
                "json_first_attempt": v["json_first_attempt"]}
            for k, v in prior.items()}


def _persist(case_id, p, feats, pack, prior, rec, raw_decision, route, ahe,
             latency_ms, sampled, repaired=False, exception_reason=None):
    db.ex("""insert into cases
             (id, alert_id, model, prompt_version, evidence, features, hard_flags,
              agent_outputs, recommendation, raw_decision, route, ahe, status,
              resume_token, sampled, latency_ms)
             values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
          (case_id, p["alert_id"], p["model"], p["prompt_version"],
           db.jdump(pack), db.jdump(feats), db.jdump(feats["hard_flags"]),
           db.jdump(_outputs(prior)),
           db.jdump(dict(rec, repaired=repaired, exception_reason=exception_reason)),
           raw_decision, route, ahe, "open",
           uuid.uuid4().hex, 1 if sampled else 0, latency_ms))


def _exception(case_id, alert_id, model, version, reason, stage,
               features=None, pack=None, agent_outputs=None, recommendation=None,
               raw_decision=None, latency_ms=None, injection_hits=None,
               hallucinated=False, json_first_attempt=False, simulated=None):
    """Node 20. Every failure path lands here: the case is parked for a human
    with the raw evidence, never silently dropped."""
    # An unknown alert_id must not break the foreign key — the exception is
    # still recorded, just without a link to a non-existent alert.
    if alert_id and not db.q1("select 1 from alerts where id=?", (alert_id,)):
        reason = f"{reason} (alert_id {alert_id!r} does not exist)"
        alert_id = None

    rec = recommendation or {"decision": None, "risk_score": None, "confidence": None,
                             "rationale": None, "evidence_ids": [],
                             "missing_information": [], "suspected_typology": "none"}
    db.ex("""insert into cases
             (id, alert_id, model, prompt_version, evidence, features, hard_flags,
              agent_outputs, recommendation, raw_decision, route, ahe, status,
              resume_token, latency_ms)
             values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
          (case_id, alert_id, model, version,
           db.jdump(pack or {}), db.jdump(features or {}),
           db.jdump((features or {}).get("hard_flags", [])),
           db.jdump(agent_outputs or {}),
           db.jdump(dict(rec, exception_reason=reason, exception_stage=stage,
                         injection_hits=injection_hits or [])),
           raw_decision, "EXCEPTION", "E", "awaiting_human",
           uuid.uuid4().hex, latency_ms))
    audit.log(case_id, "exception_raised", "orchestrator", model, version,
              output={"stage": stage, "reason": reason})

    # A hard flag is deterministic: it does not depend on a model producing valid
    # output. If the pipeline fails AFTER the flags were computed, the temporary
    # hold must still be placed — otherwise a model returning garbage on a
    # watchlist hit would let the money leave while a human reads the exception.
    flags = (features or {}).get("hard_flags", [])
    if flags:
        mock_cbs.execute(case_id, "HOLD", "orchestrator",
                         "Hard flag " + ", ".join(f["code"] for f in flags) +
                         " fired before the failure: temporary hold placed pending L2 review.")
        audit.log(case_id, "hard_flag_hold_on_exception", "orchestrator",
                  output={"flags": [f["code"] for f in flags], "stage": stage})
    if alert_id:
        db.ex("update alerts set status='investigating' where id=?", (alert_id,))
    return {"case_id": case_id, "status": "exception", "route": "EXCEPTION", "ahe": "E",
            "decision": None, "raw_decision": raw_decision, "reason": reason,
            "stage": stage, "latency_ms": latency_ms, "hallucinated": hallucinated,
            "json_first_attempt": json_first_attempt, "simulated": simulated,
            "hard_flags": [h["code"] for h in (features or {}).get("hard_flags", [])]}


# ---------------------------------------------------------------------------
# Node 16-18: the human decision resumes the paused case
# ---------------------------------------------------------------------------
class ApprovalError(PermissionError):
    pass


def decide(case_id: str, resume_token: str, decision: str, final_outcome: str,
           reason: str, investigator_id: str, role: str) -> dict:
    case = db.q1("select * from cases where id=?", (case_id,))
    if not case:
        raise KeyError(f"unknown case {case_id}")
    if case["status"] == "closed":
        raise ApprovalError("case is already closed")
    if case["resume_token"] != resume_token:
        raise ApprovalError("invalid resume token")

    decision = str(decision).upper()
    if decision not in ("APPROVE", "OVERRIDE", "ESCALATE"):
        raise ApprovalError(f"unknown decision {decision!r}")

    rec = db.jload(case["recommendation"], {}) or {}
    if decision == "APPROVE":
        outcome = (rec.get("decision") or final_outcome or "").upper()
        if not outcome:
            raise ApprovalError("nothing to approve: the case has no recommendation. "
                                "Use OVERRIDE and state the outcome.")
    elif decision == "ESCALATE":
        outcome = "ESCALATE"
        # A hand-off with no reason leaves the next investigator with nothing to
        # go on, so it is required here exactly as it is for an override.
        if not str(reason or "").strip():
            raise ApprovalError("ESCALATE requires a reason")
    else:
        outcome = str(final_outcome or "").upper()
        if not outcome:
            raise ApprovalError("OVERRIDE requires final_outcome")
        if not str(reason or "").strip():
            raise ApprovalError("OVERRIDE requires a reason")   # mandatory override reason

    allowed = APPROVAL_MATRIX.get(role)
    if allowed is None:
        raise ApprovalError(f"unknown role {role!r}")
    if outcome not in allowed:
        raise ApprovalError(
            f"role {role} may not authorise {outcome}. "
            f"{'Holding funds and closing an escalation require L2.' if role == 'L1' else ''}")

    # A hard flag can be escalated further but never cleared by a human at L1.
    hard = db.jload(case["hard_flags"], []) or []
    if hard and outcome in ("PROCEED", "VERIFY") and role != "L2" and role != "admin":
        raise ApprovalError(
            "this case carries deterministic hard flags (" +
            ", ".join(h["code"] for h in hard) + ") and cannot be cleared below L2")

    # Once a case sits in the escalation queue only L2 may resolve it; L1 can
    # still look at it and push it on, but must not be able to close it out.
    if (case["route"] == "ESCALATE" and outcome != "ESCALATE"
            and role not in ("L2", "admin")):
        raise ApprovalError(
            "this case is in the L2 escalation queue; only L2 may close it")

    audit.log(case_id, "human_decision", f"{role}:{investigator_id}",
              case["model"], case["prompt_version"],
              output={"decision": decision, "final_outcome": outcome, "reason": reason})

    response = mock_cbs.execute(case_id, outcome, f"{role}:{investigator_id}", reason)

    # Escalating below L2 is a HAND-OFF, not a closure: the case has to land in
    # L2's escalation queue. Closing it here was making it disappear from every
    # queue the moment an L1 pressed "Escalate to L2".
    if outcome == "ESCALATE" and role not in ("L2", "admin"):
        _handoff_to_l2(case_id, decision, reason, investigator_id, role)
        return {"case_id": case_id, "final_outcome": outcome, "status": "awaiting_human",
                "queue": "ESCALATE", "escalated": True, "cbs": response}

    _close(case_id, decision, outcome, reason, investigator_id, role)
    return {"case_id": case_id, "final_outcome": outcome, "status": "closed",
            "escalated": False, "cbs": response}


def _handoff_to_l2(case_id, human_decision, reason, investigator_id, role):
    """Move a case into the L2 escalation queue and leave it open.

    The resume token is rotated so the handing-over investigator's token cannot
    be replayed against the case now that it belongs to L2.
    """
    db.ex("""update cases set route='ESCALATE', ahe='E', status='awaiting_human',
             human_decision=?, final_outcome=null, human_reason=?, decided_by=?,
             decided_role=?, resume_token=?, closed_at=null where id=?""",
          (human_decision, reason, investigator_id, role, uuid.uuid4().hex, case_id))
    row = db.q1("select alert_id from cases where id=?", (case_id,))
    if row and row["alert_id"]:
        db.ex("update alerts set status='investigating' where id=?", (row["alert_id"],))
    audit.log(case_id, "escalated_to_l2", f"{role}:{investigator_id}",
              output={"reason": reason, "queue": "ESCALATE"})


def _close(case_id, human_decision, outcome, reason, investigator_id, role):
    db.ex("""update cases set status='closed', human_decision=?, final_outcome=?,
             human_reason=?, decided_by=?, decided_role=?,
             closed_at=datetime('now') where id=?""",
          (human_decision, outcome, reason, investigator_id, role, case_id))
    row = db.q1("select alert_id from cases where id=?", (case_id,))
    if row:
        db.ex("update alerts set status='closed' where id=?", (row["alert_id"],))
    audit.log(case_id, "case_closed", f"{role}:{investigator_id}",
              output={"final_outcome": outcome})


def sla_sweep():
    """Node 16's Wait timeout. Any case left un-decided past the SLA is
    auto-escalated rather than left to rot in the queue (guide 6.7)."""
    minutes = db.thresholds()["HITL_TIMEOUT_MIN"]
    stale = db.q("""select id, resume_token from cases
                    where status='awaiting_human' and route in ('HITL','AUTO_PROCEED')
                    and (julianday('now') - julianday(created_at)) * 1440 > ?""",
                 (minutes,))
    for c in stale:
        audit.log(c["id"], "sla_breach_auto_escalated", "system",
                  output={"timeout_minutes": minutes})
        mock_cbs.execute(c["id"], "ESCALATE", "system",
                         f"No investigator decision within {minutes:.0f} minutes.")
        db.ex("""update cases set route='ESCALATE', ahe='E',
                 human_reason='SLA breach: auto-escalated' where id=?""", (c["id"],))
    return len(stale)
