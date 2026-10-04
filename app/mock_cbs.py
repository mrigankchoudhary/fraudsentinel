"""Mock core banking API (guide Section 6.8).

Four endpoints — release, verify, hold, escalate — that write to the actions
table. Only the post-approval branch and the deterministic hard-flag branch of
the orchestrator may call this module. No agent can reach it.
"""
import uuid

from . import audit, db

ACTION_FOR = {"PROCEED": "release", "VERIFY": "verify",
              "HOLD": "hold", "ESCALATE": "escalate"}


def execute(case_id: str, outcome: str, actor: str, reason: str = None) -> dict:
    action = ACTION_FOR.get(str(outcome).upper())
    if action is None:
        raise ValueError(f"no core banking action maps to outcome {outcome!r}")

    request = {"case_id": case_id, "outcome": outcome, "actor": actor, "reason": reason}
    response = {
        "release":  {"status": "released", "message": "Debit released to the beneficiary."},
        "verify":   {"status": "step_up_sent", "message": "OTP / call-back verification raised."},
        "hold":     {"status": "held", "message": "Temporary hold placed. Not an account closure."},
        "escalate": {"status": "escalated", "message": "Referred to L2 and the AML queue."},
    }[action]
    response["reference"] = f"CBS-{uuid.uuid4().hex[:10].upper()}"

    db.ex("""insert into actions (id, case_id, action, endpoint, request, response)
             values (?,?,?,?,?,?)""",
          (str(uuid.uuid4()), case_id, action, f"/cbs/{action}",
           db.jdump(request), db.jdump(response)))
    audit.log(case_id, f"action:{action}", actor, output=response)
    return response


def for_case(case_id):
    rows = db.q("select * from actions where case_id=? order by executed_at", (case_id,))
    for r in rows:
        r["request"] = db.jload(r["request"])
        r["response"] = db.jload(r["response"])
    return rows
