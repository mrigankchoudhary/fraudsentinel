"""Evidence pack assembly (guide Section 6.4, node 4).

Every fact a model is allowed to reason about becomes a numbered evidence item
E1, E2, ... Agents must cite these IDs, and the output validator rejects any ID
that does not appear here. That pairing is what makes hallucination measurable
rather than merely discouraged.
"""
from . import db, guardrails


def fetch_bundle(alert_id: str) -> dict:
    alert = db.q1("select * from alerts where id=?", (alert_id,))
    if not alert:
        raise KeyError(f"unknown alert {alert_id}")
    txn = db.q1("select * from transactions where id=?", (alert["txn_id"],))
    customer = db.q1("select * from customers where id=?", (alert["customer_id"],))
    transactions = db.q(
        "select * from transactions where customer_id=? order by ts desc limit 400",
        (alert["customer_id"],))
    devices = db.q("select * from devices_logins where customer_id=? order by ts desc",
                   (alert["customer_id"],))
    beneficiaries = db.q("select * from beneficiaries where customer_id=?",
                         (alert["customer_id"],))
    accounts = [b["masked_account"] for b in beneficiaries] or [""]
    watchlist = db.q(
        "select * from mule_watchlist where masked_account in (%s)"
        % ",".join("?" * len(accounts)), accounts)
    policy = db.q("select * from policy_rules where active=1 order by id")
    return {"alert": alert, "txn": txn, "customer": customer,
            "transactions": transactions, "devices": devices,
            "beneficiaries": beneficiaries, "watchlist": watchlist,
            "policy_rules": policy}


def build_pack(bundle: dict, features: dict) -> dict:
    """Returns {"items":[{id,kind,text,...}], "untrusted":{id:raw_text}}."""
    name = bundle["customer"].get("full_name")
    items, untrusted = [], {}
    n = [0]

    def item(kind, text, **extra):
        n[0] += 1
        eid = f"E{n[0]}"
        items.append({"id": eid, "kind": kind,
                      "text": guardrails.mask_pii(text, name), **extra})
        return eid

    a, t, c, f = bundle["alert"], bundle["txn"], bundle["customer"], features

    item("alert", f"Alert {a['id']} of type {a['alert_type']} fired by rule "
                  f"\"{a['rule_fired']}\" at {a['created_at']}.")
    item("transaction",
         f"Alerted transaction: {t['channel']} debit of Rs{float(t['amount']):,.2f} "
         f"at {t['ts']} from city {t['city']}.")
    item("customer",
         f"Customer segment {c['segment']}, home city {c['home_city']}, income band "
         f"{c['income_band']}, KYC last updated {c['kyc_updated_at']}.")
    item("history",
         f"Over the last 90 days the customer made {f['txn_count_90d']} outgoing "
         f"transfers, mean Rs{f['mean_90d']:,.2f}, maximum Rs{f['max_90d']:,.2f}. "
         f"This transaction is {f['amount_ratio']}x the 90-day maximum.")
    item("velocity",
         f"{f['velocity_15m_count']} outgoing transfers totalling "
         f"Rs{f['velocity_15m_sum']:,.2f} occurred in the 15 minutes up to and "
         f"including this transaction; {f['same_payee_burst_15m']} of them went to "
         f"the alerted beneficiary.")

    if f["beneficiary_age_hours"] is None:
        item("beneficiary", "No beneficiary record is available for this transfer.")
    else:
        item("beneficiary",
             f"Beneficiary {f['beneficiary_masked']} was added "
             f"{f['beneficiary_age_hours']} hours ago and has received "
             f"{f['prior_payments_to_beneficiary']} prior payments from this customer.")

    if f["device_first_seen_hours"] is None:
        item("device", "No device or login record is available.")
    else:
        item("device",
             f"Most recent login came from a device first seen "
             f"{f['device_first_seen_hours']} hours ago "
             f"({'NEW device' if f['new_device'] else 'established device'}), "
             f"from city {f['login_city']} against home city {f['home_city']} "
             f"({'MISMATCH' if f['location_mismatch'] else 'consistent'}).")
        item("sim",
             f"Last SIM change on the account was {f['sim_change_hours']} hours ago."
             if f["sim_change_hours"] is not None else
             "No SIM change record is available.")

    item("watchlist",
         f"Mule watchlist check on {f['beneficiary_masked']}: "
         + ("MATCH — account is listed." if f["watchlist_hit"] else "no match."))

    # Free text from the payment is the one field an attacker controls, so it is
    # quarantined rather than inlined.
    if t.get("remarks"):
        n[0] += 1
        eid = f"E{n[0]}"
        raw = str(t["remarks"])
        untrusted[eid] = raw
        items.append({"id": eid, "kind": "remarks_untrusted",
                      "text": guardrails.quarantine(guardrails.mask_pii(raw, name)),
                      "untrusted": True})

    if f["data_incomplete"]:
        item("data_gap",
             "Mandatory evidence is missing or stale: " + ", ".join(f["missing_sources"]) + ".")

    for p in bundle["policy_rules"]:
        if p["action"] in ("AUTO_PROCEED_MAX", "ESCALATE_MIN", "MIN_AUTO_CONFIDENCE",
                           "AUTO_SAMPLE_RATE", "HITL_TIMEOUT_MIN"):
            continue
        item("policy", f"Policy {p['id']}: {p['rule']} (threshold {p['threshold']}).",
             policy_id=p["id"])

    return {"items": items, "untrusted": untrusted,
            "ids": [i["id"] for i in items]}


def render(pack: dict) -> str:
    return "\n".join(f"{i['id']}. {i['text']}" for i in pack["items"])
