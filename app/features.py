"""Deterministic feature computation and hard flags (guide Section 6.2).

Everything here runs BEFORE any model call. Hard flags are rules an LLM can
never downgrade: a model may escalate further, but it cannot clear a
hard-flagged case. This is the backbone of the 'unsafe autonomous action'
guardrail in Stage 4.
"""
from datetime import datetime, timedelta

from . import config


def _dt(s):
    if not s:
        return None
    if isinstance(s, datetime):
        return s
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def _hours_since(ts, now):
    d = _dt(ts)
    return None if d is None else round((now - d).total_seconds() / 3600.0, 2)


def compute(bundle: dict, th: dict) -> dict:
    """bundle: raw rows from db (alert, txn, customer, transactions, devices,
    beneficiaries, watchlist). Returns the feature dict used by every agent and
    by the router."""
    now = config.reference_now()
    txn = bundle["txn"]
    cust = bundle["customer"]
    history = [t for t in bundle["transactions"] if t["id"] != txn["id"]]
    devices = bundle["devices"]
    bens = {b["id"]: b for b in bundle["beneficiaries"]}
    watch = {w["masked_account"] for w in bundle["watchlist"]}

    amount = float(txn["amount"])
    hist_amounts = [float(t["amount"]) for t in history]
    max_90d = max(hist_amounts) if hist_amounts else 0.0
    mean_90d = (sum(hist_amounts) / len(hist_amounts)) if hist_amounts else 0.0

    txn_ts = _dt(txn["ts"]) or now

    # --- velocity: outgoing transfers in the 15 minutes before this one -------
    window_start = txn_ts - timedelta(minutes=15)
    burst = [t for t in history
             if (_dt(t["ts"]) or now) >= window_start
             and (_dt(t["ts"]) or now) <= txn_ts
             and t.get("direction", "OUT") == "OUT"]
    velocity_15m_count = len(burst) + 1
    velocity_15m_sum = round(sum(float(t["amount"]) for t in burst) + amount, 2)

    # --- beneficiary ----------------------------------------------------------
    ben = bens.get(txn.get("beneficiary_id") or "")
    beneficiary_age_hours = _hours_since(ben["added_at"], now) if ben else None
    beneficiary_masked = ben["masked_account"] if ben else None
    watchlist_hit = bool(beneficiary_masked and beneficiary_masked in watch)

    # transfers to this same beneficiary inside the window (split-burst test)
    same_payee_burst = sum(
        1 for t in burst if t.get("beneficiary_id") == txn.get("beneficiary_id")) + 1

    # --- device / channel -----------------------------------------------------
    devices_sorted = sorted(devices, key=lambda d: _dt(d["ts"]) or now, reverse=True)
    latest_device = devices_sorted[0] if devices_sorted else None
    device_first_seen_hours = (
        _hours_since(latest_device["first_seen"], now) if latest_device else None)
    new_device = (device_first_seen_hours is not None
                  and device_first_seen_hours < th["NEW_DEVICE_HOURS"])
    sim_change_hours = (
        _hours_since(latest_device["sim_changed_at"], now) if latest_device else None)
    sim_changed_recently = (sim_change_hours is not None
                            and sim_change_hours < th["SIM_CHANGE_HOURS"])

    login_city = latest_device["ip_city"] if latest_device else None
    # The customer's ESTABLISHED city pattern. Transfers from the last 24 hours are
    # excluded on purpose: in a takeover the fraudulent transfers are themselves
    # recent, and counting them would let the attack define the baseline it is
    # then measured against — the location mismatch would silently disappear.
    baseline_cutoff = txn_ts - timedelta(hours=24)
    recent_cities = {t["city"] for t in history
                     if t.get("city")
                     and now - timedelta(days=30) <= (_dt(t["ts"]) or now) <= baseline_cutoff}
    location_mismatch = bool(
        login_city and login_city != cust["home_city"] and login_city not in recent_cities)

    # --- data completeness ----------------------------------------------------
    # The device/login feed is treated as stale (and therefore incomplete) if the
    # newest record predates the transaction by more than 24h — this is how
    # TC-02's "feed returns no data for the last 24h" surfaces.
    device_feed_age_hours = (
        _hours_since(latest_device["ts"], now) if latest_device else None)
    missing = []
    if not history:
        missing.append("transaction_history")
    if latest_device is None:
        missing.append("device_login_feed")
    elif device_feed_age_hours is not None and device_feed_age_hours > 24:
        missing.append("device_login_feed_stale_%sh" % int(device_feed_age_hours))
    if ben is None:
        missing.append("beneficiary_record")
    if not cust.get("kyc_updated_at"):
        missing.append("kyc_record")

    f = {
        "amount": amount,
        "channel": txn["channel"],
        "amount_ratio": round(amount / max_90d, 2) if max_90d else None,
        "amount_vs_mean": round(amount / mean_90d, 2) if mean_90d else None,
        "max_90d": round(max_90d, 2),
        "mean_90d": round(mean_90d, 2),
        "txn_count_90d": len(history),
        "velocity_15m_count": velocity_15m_count,
        "velocity_15m_sum": velocity_15m_sum,
        "same_payee_burst_15m": same_payee_burst,
        "beneficiary_age_hours": beneficiary_age_hours,
        "beneficiary_masked": beneficiary_masked,
        "beneficiary_is_new": (beneficiary_age_hours is not None
                               and beneficiary_age_hours < th["NEW_BENEFICIARY_HOURS"]),
        "prior_payments_to_beneficiary": sum(
            1 for t in history if t.get("beneficiary_id") == txn.get("beneficiary_id")),
        "new_device": new_device,
        "device_first_seen_hours": device_first_seen_hours,
        "sim_change_hours": sim_change_hours,
        "sim_changed_recently": sim_changed_recently,
        "login_city": login_city,
        "home_city": cust["home_city"],
        "location_mismatch": location_mismatch,
        "device_feed_age_hours": device_feed_age_hours,
        "txn_hour_utc": txn_ts.hour,
        "odd_hour": txn_ts.hour in (0, 1, 2, 3, 4),
        "watchlist_hit": watchlist_hit,
        "data_incomplete": bool(missing),
        "missing_sources": missing,
    }
    f["hard_flags"] = hard_flags(f, th)
    return f


def hard_flags(f: dict, th: dict) -> list:
    """HF1-HF3 from guide 6.2. Each returns Hold + Escalate and cannot be
    overridden downward by any model."""
    flags = []
    if f["watchlist_hit"]:
        flags.append({
            "code": "HF1", "name": "watchlist_hit",
            "detail": f"Beneficiary {f['beneficiary_masked']} is on the mule watchlist",
            "action": "HOLD+ESCALATE"})

    if (f["sim_changed_recently"] and f["new_device"]
            and f["beneficiary_age_hours"] is not None
            and f["beneficiary_age_hours"] < 1):
        flags.append({
            "code": "HF2", "name": "ato_pattern",
            "detail": (f"SIM changed {f['sim_change_hours']}h ago, device first seen "
                       f"{f['device_first_seen_hours']}h ago, beneficiary added "
                       f"{f['beneficiary_age_hours']}h ago"),
            "action": "HOLD+ESCALATE"})

    if (f["same_payee_burst_15m"] >= int(th["SPLIT_BURST_COUNT"])
            and f["beneficiary_is_new"]):
        flags.append({
            "code": "HF3", "name": "split_burst",
            "detail": (f"{f['same_payee_burst_15m']} transfers to the same new "
                       f"beneficiary within 15 minutes totalling "
                       f"Rs{f['velocity_15m_sum']:,.0f}"),
            "action": "HOLD+ESCALATE"})
    return flags
