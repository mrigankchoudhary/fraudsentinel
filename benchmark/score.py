"""Scoring for the open-model comparison (guide 6.12).

Two rules matter here:

  * We score the RAW model decision, not the final system decision. Guardrails
    exist to catch model mistakes, so scoring after them would hide exactly what
    the comparison is supposed to measure.

  * A missed high-risk case costs far more than a slow or over-cautious one, so
    escalation recall and hallucination are weighted above latency when ranking.
"""
import collections, statistics

ESCALATING = {"HOLD", "ESCALATE"}
OUTCOMES = ["PROCEED", "VERIFY", "HOLD", "ESCALATE"]


def score_row(case, result):
    """case: {id, class, expected}; result: an orchestrator investigate() return."""
    raw = (result.get("raw_decision") or "").upper() or None
    return {
        "case_id": case["id"],
        "case_class": case["class"],
        "expected": case["expected"],
        "raw_decision": raw,
        "final_route": result.get("route"),
        "ahe": result.get("ahe"),
        "risk_score": result.get("risk_score"),
        "confidence": result.get("confidence"),
        "json_valid": bool(result.get("json_first_attempt")),
        # The validator rejects ungrounded evidence IDs and invented numbers, so
        # a validation failure naming one is our hallucination signal.
        "hallucinated": bool(result.get("hallucinated")),
        "completed": result.get("route") is not None and result.get("route") != "EXCEPTION",
        "latency_ms": result.get("latency_ms"),
        "simulated": bool(result.get("simulated")),
    }


def macro_f1(rows):
    f1s = []
    for label in OUTCOMES:
        tp = sum(1 for r in rows if r["raw_decision"] == label and r["expected"] == label)
        fp = sum(1 for r in rows if r["raw_decision"] == label and r["expected"] != label)
        fn = sum(1 for r in rows if r["raw_decision"] != label and r["expected"] == label)
        if tp + fn == 0:
            continue                      # label absent from the ground truth
        prec = tp / (tp + fp) if tp + fp else 0.0
        rec = tp / (tp + fn) if tp + fn else 0.0
        f1s.append(2 * prec * rec / (prec + rec) if prec + rec else 0.0)
    return sum(f1s) / len(f1s) if f1s else 0.0


def pct(num, den):
    return round(100.0 * num / den, 1) if den else None


def summarise(rows):
    """rows -> one dict of metrics per model."""
    out = {}
    for model, rs in group(rows, "model").items():
        high = [r for r in rs if r["case_class"] == "high_risk"]
        genuine = [r for r in rs if r["case_class"] == "normal"]
        lat = sorted(r["latency_ms"] for r in rs if r["latency_ms"])

        # Consistency: did the same case get the same decision on every run?
        per_case = group(rs, "case_id")
        stable = sum(1 for v in per_case.values()
                     if len({r["raw_decision"] for r in v}) == 1)

        out[model] = {
            "n": len(rs),
            "accuracy": pct(sum(1 for r in rs if r["raw_decision"] == r["expected"]), len(rs)),
            "macro_f1": round(macro_f1(rs), 3),
            "escalation_recall": pct(
                sum(1 for r in high if r["raw_decision"] in ESCALATING), len(high)),
            "false_positive_rate": pct(
                sum(1 for r in genuine if r["raw_decision"] in ESCALATING), len(genuine)),
            "hallucination_rate": pct(sum(1 for r in rs if r["hallucinated"]), len(rs)),
            "json_compliance": pct(sum(1 for r in rs if r["json_valid"]), len(rs)),
            "task_completion": pct(sum(1 for r in rs if r["completed"]), len(rs)),
            "consistency": pct(stable, len(per_case)),
            "latency_p50_ms": lat[int(len(lat) * .50)] if lat else None,
            "latency_p95_ms": lat[min(len(lat) - 1, int(len(lat) * .95))] if lat else None,
            "latency_mean_ms": round(statistics.mean(lat)) if lat else None,
            "simulated": any(r["simulated"] for r in rs),
        }
    return out


def group(rows, key):
    g = collections.OrderedDict()
    for r in rows:
        g.setdefault(r[key], []).append(r)
    return g


def rank(summary):
    """Deployment ranking. Recall and hallucination dominate; latency breaks ties."""
    def key(item):
        m = item[1]
        return (-(m["escalation_recall"] or 0),
                (m["hallucination_rate"] or 0),
                -(m["accuracy"] or 0),
                (m["false_positive_rate"] or 0),
                (m["latency_p50_ms"] or 0))
    return [m for m, _ in sorted(summary.items(), key=key)]
