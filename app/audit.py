"""Append-only audit log. Every orchestrator step, model call and human action
writes one row (guide 7.1 'Auditability', 7.3 NIST MANAGE/GOVERN)."""
import hashlib
from . import db

def input_hash(payload) -> str:
    return hashlib.sha256(db.jdump(payload).encode()).hexdigest()[:16]

def log(case_id, step, actor, model=None, prompt_version=None,
        inputs=None, output=None, latency_ms=None):
    db.ex(
        """insert into audit_log
           (case_id, step, actor, model, prompt_version, input_hash, output, latency_ms)
           values (?,?,?,?,?,?,?,?)""",
        (case_id, step, actor, model, prompt_version,
         input_hash(inputs) if inputs is not None else None,
         db.jdump(output) if output is not None else None, latency_ms))

def for_case(case_id):
    rows = db.q("select * from audit_log where case_id=? order by id", (case_id,))
    for r in rows:
        r["output"] = db.jload(r["output"])
    return rows
