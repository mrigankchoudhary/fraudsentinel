"""Thin SQLite data-access layer.

Mirrors the Supabase/Postgres schema in data/schema.sql. The orchestrator talks
to this module only, so swapping SQLite for Supabase means replacing this file
and nothing else.
"""
import json, os, sqlite3, threading
from . import config

_local = threading.local()

def conn() -> sqlite3.Connection:
    c = getattr(_local, "c", None)
    if c is None:
        if not os.path.exists(config.DB_PATH):
            raise RuntimeError(
                f"database not found at {config.DB_PATH} — run: python3 data/seed.py")
        c = sqlite3.connect(config.DB_PATH, check_same_thread=False)
        c.row_factory = sqlite3.Row
        c.execute("pragma journal_mode=WAL")
        c.execute("pragma foreign_keys=on")
        # The dashboard is threaded, so a writer can meet another writer. WAL
        # lets readers through; this makes writers queue instead of failing.
        c.execute("pragma busy_timeout=10000")
        _local.c = c
    return c

def q(sql, args=()):
    return [dict(r) for r in conn().execute(sql, args).fetchall()]

def q1(sql, args=()):
    r = conn().execute(sql, args).fetchone()
    return dict(r) if r else None

_write = threading.Lock()

def ex(sql, args=()):
    """All writes serialise through one lock. SQLite allows a single writer;
    doing this explicitly turns a race into a short wait."""
    with _write:
        c = conn()
        cur = c.execute(sql, args)
        c.commit()
        return cur

def thresholds() -> dict:
    """Read decision thresholds from policy_rules, falling back to defaults."""
    t = dict(config.DEFAULT_THRESHOLDS)
    for r in q("select action, threshold from policy_rules where active=1"):
        if r["action"] in t and r["threshold"] is not None:
            t[r["action"]] = float(r["threshold"])
    return t

def set_threshold(action: str, value: float, actor: str):
    ex("update policy_rules set threshold=? where action=?", (value, action))
    from .audit import log
    log(None, "threshold_changed", actor,
        output={"action": action, "new_value": value})

def jload(v, default=None):
    if v in (None, ""):
        return default
    if isinstance(v, (dict, list)):
        return v
    try:
        return json.loads(v)
    except Exception:
        return default

def jdump(v):
    return json.dumps(v, default=str)
