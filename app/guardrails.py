"""Input and output guardrails (guide Sections 6.4 nodes 4/5/12, and Stage 4).

Three controls live here:
  * mask_pii        — no names or full account numbers ever reach a model
  * scan_injection  — free-text fields are treated as data, never instructions
  * validate_output — schema, evidence grounding and numeric sanity on the way back
"""
import re

DECISIONS = {"PROCEED", "VERIFY", "HOLD", "ESCALATE"}
TYPOLOGIES = {"none", "account_takeover", "mule", "social_engineering", "other"}

# Patterns that look like an instruction aimed at the model rather than a payment note.
INJECTION_PATTERNS = [
    r"ignore\s+(all\s+|any\s+)?(previous|prior|above)\s+instructions?",
    r"disregard\s+(the\s+)?(previous|prior|above|system)",
    r"\bsystem\s*:",
    r"\bassistant\s*:",
    r"you\s+are\s+now\b",
    r"new\s+instructions?\b",
    r"mark\s+(this|it)\s+(as\s+)?(safe|genuine|legitimate|low.risk)",
    r"return\s+decision\s+(proceed|approve)",
    r"set\s+confidence\s+to",
    r"override\s+(the\s+)?(rule|policy|guardrail)",
    r"<\s*/?\s*(system|untrusted|instructions?)\s*>",
    r"\bprompt\b.{0,20}\binjection\b",
]
_INJ = [re.compile(p, re.I) for p in INJECTION_PATTERNS]

ACCOUNT_RE = re.compile(r"\b\d{9,18}\b")
PHONE_RE = re.compile(r"\b(?:\+91[\-\s]?)?[6-9]\d{9}\b")
EMAIL_RE = re.compile(r"\b[\w.\-]+@[\w\-]+\.\w{2,}\b")
PAN_RE = re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")


def mask_pii(text, customer_name=None):
    """Mask anything that identifies a person before it reaches a model."""
    if not text:
        return text
    s = str(text)
    if customer_name:
        for part in str(customer_name).split():
            if len(part) > 2:
                s = re.sub(rf"\b{re.escape(part)}\b", "[NAME]", s, flags=re.I)
    s = ACCOUNT_RE.sub("[ACCOUNT]", s)
    s = PHONE_RE.sub("[PHONE]", s)
    s = EMAIL_RE.sub("[EMAIL]", s)
    s = PAN_RE.sub("[PAN]", s)
    return s


def scan_injection(fields: dict):
    """fields: {evidence_id: free_text}. Returns (flag, [hits])."""
    hits = []
    for key, value in fields.items():
        if not value:
            continue
        for rx in _INJ:
            m = rx.search(str(value))
            if m:
                hits.append({"field": key, "pattern": rx.pattern,
                             "matched": m.group(0)[:80]})
                break
    return bool(hits), hits


def quarantine(text):
    """Wrap untrusted free text so the prompt can tell the model it is data.
    Any closing tag inside the text itself is neutralised first."""
    if text is None:
        return ""
    s = str(text).replace("</untrusted>", "[/untrusted]").replace("<untrusted>", "[untrusted]")
    return f"<untrusted>{s}</untrusted>"


def validate_output(obj, evidence_ids, features):
    """Output guardrail. Returns (ok, errors, cleaned). Rejects malformed JSON,
    ungrounded evidence IDs and numbers the model invented."""
    errors = []
    if not isinstance(obj, dict):
        return False, ["output is not a JSON object"], None

    decision = str(obj.get("decision", "")).strip().upper()
    if decision not in DECISIONS:
        errors.append(f"decision must be one of {sorted(DECISIONS)}, got {obj.get('decision')!r}")

    try:
        risk = float(obj.get("risk_score"))
        if not 0 <= risk <= 100:
            errors.append(f"risk_score out of range: {risk}")
    except (TypeError, ValueError):
        errors.append("risk_score missing or not a number")
        risk = None

    try:
        conf = float(obj.get("confidence"))
        if not 0.0 <= conf <= 1.0:
            errors.append(f"confidence out of range: {conf}")
    except (TypeError, ValueError):
        errors.append("confidence missing or not a number")
        conf = None

    rationale = str(obj.get("rationale", "")).strip()
    if not rationale:
        errors.append("rationale is empty")
    elif len(rationale.split()) > 150:
        errors.append(f"rationale exceeds 120 words ({len(rationale.split())})")

    # --- grounding: every cited evidence ID must exist -----------------------
    cited = obj.get("evidence_ids") or []
    if not isinstance(cited, list):
        errors.append("evidence_ids must be a list")
        cited = []
    known = set(evidence_ids)
    unknown = [e for e in cited if e not in known]
    if unknown:
        errors.append(f"hallucinated evidence IDs: {unknown}")
    if not cited and decision != "PROCEED":
        errors.append("non-PROCEED decision cites no evidence")

    # IDs mentioned in the prose must also exist
    prose_ids = set(re.findall(r"\bE\d{1,3}\b", rationale))
    prose_unknown = sorted(prose_ids - known)
    if prose_unknown:
        errors.append(f"rationale cites unknown evidence IDs: {prose_unknown}")

    # --- numeric sanity: amounts quoted must match the evidence --------------
    for raw in re.findall(r"(?:Rs\.?|INR|₹)\s?([\d,]+(?:\.\d+)?)", rationale):
        try:
            val = float(raw.replace(",", ""))
        except ValueError:
            continue
        allowed = {round(features.get("amount") or 0, 2),
                   round(features.get("velocity_15m_sum") or 0, 2),
                   round(features.get("max_90d") or 0, 2),
                   round(features.get("mean_90d") or 0, 2)}
        if not any(abs(val - a) <= max(1.0, a * 0.01) for a in allowed if a):
            errors.append(f"rationale quotes an amount not present in the evidence: {val}")

    missing = obj.get("missing_information") or []
    if not isinstance(missing, list):
        errors.append("missing_information must be a list")
        missing = []

    # A model may not PROCEED while mandatory evidence is absent (guide 6.3 #3).
    if decision == "PROCEED" and features.get("data_incomplete"):
        errors.append("PROCEED is not permitted while mandatory evidence is missing")

    typology = str(obj.get("suspected_typology", "none")).strip().lower()
    if typology not in TYPOLOGIES:
        typology = "other"

    if errors:
        return False, errors, None

    return True, [], {
        "decision": decision,
        "risk_score": round(risk, 1),
        "confidence": round(conf, 2),
        "rationale": rationale,
        "evidence_ids": cited,
        "missing_information": missing,
        "suspected_typology": typology,
    }
