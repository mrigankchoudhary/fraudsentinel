#!/usr/bin/env python3
"""FraudSentinel API + dashboard host.

Written on the standard library so the whole PoC runs with `python3 app/server.py`
and no install step. The routes mirror the n8n webhook contract in guide 6.10, so
the UI can be pointed at either this server or n8n without changing the front end.

  POST /webhook/investigate          trigger an investigation (node 1)
  POST /api/cases/<id>/decide        the investigator's decision (node 16 resume)

/api/cases/<id>/decide stands in for the Supabase Edge Function in the guide: the
browser never sees the resume token. It posts a decision, the server checks the
caller's role against the approval matrix and only then resumes the case.

AUTH NOTE: sessions here are in-memory and the passwords are fixed demo values.
That is deliberate for a PoC. The production path is Supabase Auth with
row-level security, as recorded in docs/governance/.
"""
import json, os, re, secrets, sys, threading, traceback
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import audit, config, db, llm, mock_cbs, orchestrator  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UI_DIR = os.path.join(ROOT, "ui")

# Demo users. Role drives the approval matrix in orchestrator.APPROVAL_MATRIX.
USERS = {
    "l1@bank.test":    {"password": "l1", "role": "L1", "id": "u-l1-12", "name": "L1 Investigator"},
    "l2@bank.test":    {"password": "l2", "role": "L2", "id": "u-l2-04", "name": "L2 Investigator"},
    "admin@bank.test": {"password": "admin", "role": "admin", "id": "u-ad-01", "name": "Fraud Ops Admin"},
}
SESSIONS = {}
_lock = threading.Lock()

# A deployed instance has no shell to type `python3 benchmark/run_benchmark.py`
# into, so an admin can start the same run from the dashboard. One run at a
# time, in a background thread, with progress the UI can poll.
BENCH = {"running": False, "done": 0, "total": 0, "model": None,
         "run_id": None, "error": None, "finished_at": None, "started_by": None}


def _bench_worker(models, runs, prompt_version):
    from benchmark import run_benchmark

    def progress(done, total, model):
        BENCH.update(done=done, total=total, model=model)
    try:
        meta = run_benchmark.run(models, runs=runs, prompt_version=prompt_version,
                                 progress=progress)
        BENCH.update(run_id=meta["run_id"], error=None)
    except Exception as e:
        traceback.print_exc()
        BENCH.update(error=f"{type(e).__name__}: {e}")
    finally:
        BENCH.update(running=False,
                     finished_at=datetime.now(timezone.utc).isoformat())


class Handler(BaseHTTPRequestHandler):
    server_version = "FraudSentinel/1.0"

    # ---------------- plumbing ----------------
    def log_message(self, fmt, *args):
        sys.stderr.write("%s %s\n" % (self.address_string(), fmt % args))

    def _send(self, code, payload=None, ctype="application/json"):
        body = b"" if payload is None else (
            json.dumps(payload, default=str).encode() if ctype == "application/json"
            else payload)
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n))
        except json.JSONDecodeError:
            return {}

    def _session(self):
        tok = (self.headers.get("Authorization") or "").replace("Bearer ", "").strip()
        return SESSIONS.get(tok)

    def _require(self, roles=None):
        s = self._session()
        if not s:
            self._send(401, {"error": "sign in required"})
            return None
        if roles and s["role"] not in roles:
            self._send(403, {"error": f"role {s['role']} is not permitted here"})
            return None
        return s

    # ---------------- routing ----------------
    def do_GET(self):
        u = urlparse(self.path)
        p, qs = u.path, parse_qs(u.query)
        try:
            if p.startswith("/api/"):
                return self._get_api(p, qs)
            return self._static(p)
        except Exception:
            traceback.print_exc()
            self._send(500, {"error": "internal error"})

    def do_POST(self):
        p = urlparse(self.path).path
        try:
            return self._post_api(p, self._body())
        except Exception:
            traceback.print_exc()
            self._send(500, {"error": "internal error"})

    # ---------------- GET ----------------
    def _get_api(self, p, qs):
        if p == "/api/meta":
            th = db.thresholds()
            return self._send(200, {
                "models": config.MODELS,
                "prompt_versions": ["v1", "v2", "v3"],
                "default_prompt_version": config.PROMPT_VERSION,
                "provider": config.LLM_PROVIDER,
                "endpoint": config.LLM_BASE_URL,
                "simulated": config.LLM_PROVIDER == "stub",
                "reference_now": config.reference_now().isoformat(),
                "thresholds": th,
                "counts": {
                    "alerts_open": db.q1("select count(*) c from alerts where status='new'")["c"],
                    "hitl": db.q1("select count(*) c from cases where route='HITL' and status='awaiting_human'")["c"],
                    "escalation": db.q1("select count(*) c from cases where route='ESCALATE' and status='awaiting_human'")["c"],
                    "exception": db.q1("select count(*) c from cases where route='EXCEPTION' and status='awaiting_human'")["c"],
                },
            })

        if p == "/api/alerts":
            if not self._require():
                return
            return self._send(200, db.q("""
                select a.*, t.amount, t.channel, t.ts as txn_ts, c.segment, c.home_city,
                       (select count(*) from cases k where k.alert_id=a.id) as case_count
                from alerts a
                join transactions t on t.id=a.txn_id
                join customers c on c.id=a.customer_id
                order by case when a.status='new' then 0 else 1 end, a.created_at desc"""))

        if p == "/api/cases":
            if not self._require():
                return
            where, args = [], []
            if qs.get("route"):
                where.append("route=?"); args.append(qs["route"][0])
            if qs.get("status"):
                where.append("status=?"); args.append(qs["status"][0])
            sql = "select * from cases"
            if where:
                sql += " where " + " and ".join(where)
            sql += " order by created_at desc limit 200"
            rows = db.q(sql, args)
            for r in rows:
                r["recommendation"] = db.jload(r["recommendation"], {})
                r["hard_flags"] = db.jload(r["hard_flags"], [])
                for k in ("evidence", "features", "agent_outputs"):
                    r.pop(k, None)
                r.pop("resume_token", None)
            return self._send(200, rows)

        m = re.fullmatch(r"/api/cases/([0-9a-f\-]{36})", p)
        if m:
            if not self._require():
                return
            c = db.q1("select * from cases where id=?", (m.group(1),))
            if not c:
                return self._send(404, {"error": "no such case"})
            for k in ("evidence", "features", "agent_outputs", "recommendation", "hard_flags"):
                c[k] = db.jload(c[k], {})
            c.pop("resume_token", None)          # never leaves the server
            alert = db.q1("""select a.*, t.amount, t.channel, t.ts as txn_ts,
                                    cu.segment, cu.home_city
                             from alerts a join transactions t on t.id=a.txn_id
                             join customers cu on cu.id=a.customer_id
                             where a.id=?""", (c["alert_id"],))
            return self._send(200, {"case": c, "alert": alert,
                                    "audit": audit.for_case(c["id"]),
                                    "actions": mock_cbs.for_case(c["id"])})

        if p == "/api/audit":
            if not self._require():
                return
            rows = db.q("select * from audit_log order by id desc limit 400")
            for r in rows:
                r["output"] = db.jload(r["output"])
            return self._send(200, rows)

        if p == "/api/benchmark":
            rows = db.q("select * from benchmark_results order by id desc limit 5000")
            return self._send(200, rows)

        if p == "/api/benchmark/status":
            if not self._require():
                return
            return self._send(200, dict(
                BENCH, provider=config.LLM_PROVIDER, simulated=config.LLM_PROVIDER == "stub",
                models=config.MODELS))

        return self._send(404, {"error": "no such endpoint"})

    # ---------------- POST ----------------
    def _post_api(self, p, body):
        if p == "/api/login":
            u = USERS.get(str(body.get("email", "")).lower().strip())
            if not u or u["password"] != body.get("password"):
                return self._send(401, {"error": "invalid credentials"})
            tok = secrets.token_urlsafe(24)
            with _lock:
                SESSIONS[tok] = {"role": u["role"], "id": u["id"], "name": u["name"],
                                 "email": body["email"]}
            audit.log(None, "login", f"{u['role']}:{u['id']}")
            return self._send(200, {"token": tok, **SESSIONS[tok]})

        if p == "/api/logout":
            tok = (self.headers.get("Authorization") or "").replace("Bearer ", "").strip()
            SESSIONS.pop(tok, None)
            return self._send(200, {"ok": True})

        if p == "/webhook/investigate":
            s = self._require()
            if not s:
                return
            result = orchestrator.investigate(body)
            return self._send(200, result)

        m = re.fullmatch(r"/api/cases/([0-9a-f\-]{36})/decide", p)
        if m:
            s = self._require()
            if not s:
                return
            case = db.q1("select resume_token from cases where id=?", (m.group(1),))
            if not case:
                return self._send(404, {"error": "no such case"})
            try:
                out = orchestrator.decide(
                    m.group(1), case["resume_token"],
                    body.get("decision"), body.get("final_outcome"),
                    body.get("reason"), s["id"], s["role"])
            except orchestrator.ApprovalError as e:
                return self._send(403, {"error": str(e)})
            except (KeyError, ValueError) as e:
                return self._send(400, {"error": str(e)})
            return self._send(200, out)

        if p == "/api/benchmark/run":
            s = self._require(["admin"])
            if not s:
                return
            with _lock:
                if BENCH["running"]:
                    return self._send(409, {"error": "a benchmark is already running"})
                models = [m.strip() for m in (body.get("models") or config.MODELS)
                          if str(m).strip()]
                unknown = [m for m in models if m not in config.MODELS]
                if unknown:
                    # Same allowlist the orchestrator enforces — the UI must not
                    # be a way around it.
                    return self._send(400, {"error": "not in the approved model list: "
                                                     + ", ".join(unknown)})
                if not models:
                    return self._send(400, {"error": "no models selected"})
                try:
                    runs = max(1, min(10, int(body.get("runs") or 3)))
                except (TypeError, ValueError):
                    return self._send(400, {"error": "runs must be a number"})
                version = str(body.get("prompt_version") or config.PROMPT_VERSION)
                if version not in ("v1", "v2", "v3"):
                    return self._send(400, {"error": "unknown prompt_version"})
                BENCH.update(running=True, done=0, total=0, model=None, run_id=None,
                             error=None, finished_at=None,
                             started_by=f"{s['role']}:{s['id']}")
            audit.log(None, "benchmark_started", f"{s['role']}:{s['id']}",
                      output={"models": models, "runs": runs, "prompt_version": version})
            threading.Thread(target=_bench_worker, args=(models, runs, version),
                             daemon=True).start()
            return self._send(202, dict(BENCH))

        if p == "/api/settings":
            s = self._require(["admin"])
            if not s:
                return
            changed = {}
            for k, v in (body.get("thresholds") or {}).items():
                if k in config.DEFAULT_THRESHOLDS:
                    db.set_threshold(k, float(v), f"admin:{s['id']}")
                    changed[k] = float(v)
            return self._send(200, {"changed": changed, "thresholds": db.thresholds()})

        if p == "/api/sla-sweep":
            if not self._require(["admin", "L2"]):
                return
            return self._send(200, {"escalated": orchestrator.sla_sweep()})

        return self._send(404, {"error": "no such endpoint"})

    # ---------------- static ----------------
    def _static(self, p):
        rel = "index.html" if p in ("/", "") else p.lstrip("/")
        full = os.path.normpath(os.path.join(UI_DIR, rel))
        if not full.startswith(UI_DIR) or not os.path.isfile(full):
            return self._send(404, b"not found", "text/plain")
        ctype = {".html": "text/html; charset=utf-8", ".js": "text/javascript",
                 ".css": "text/css", ".svg": "image/svg+xml",
                 ".png": "image/png", ".json": "application/json"}.get(
                     os.path.splitext(full)[1], "application/octet-stream")
        with open(full, "rb") as f:
            self._send(200, f.read(), ctype)


def preflight():
    """Check the model endpoint before serving.

    Without this, an unreachable provider turns every investigation into an
    exception case, which looks like a bug in the pipeline rather than a model
    server that is not running.
    """
    if config.LLM_PROVIDER == "stub":
        return
    import json as _json, urllib.error, urllib.request
    url = config.LLM_BASE_URL.rstrip("/") + "/models"
    req = urllib.request.Request(url)
    if config.LLM_API_KEY:
        req.add_header("Authorization", f"Bearer {config.LLM_API_KEY}")
    if config.LLM_PROVIDER == "openrouter":
        for k, v in llm._openrouter_headers().items():
            req.add_header(k, v)
        if not config.LLM_API_KEY:
            print("\n  !! FS_LLM_PROVIDER=openrouter but no API key.\n"
                  "     echo 'FS_OPENROUTER_API_KEY=sk-or-v1-...' >> .env.local\n"
                  "     Create one at https://openrouter.ai/keys\n")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            available = {m.get("id") for m in _json.loads(r.read()).get("data", [])}
    except Exception as e:
        print(f"\n  !! cannot reach {config.LLM_PROVIDER} at {config.LLM_BASE_URL}\n"
              f"     {e}\n"
              f"     {_fix_hint()}\n"
              f"     Or run with FS_LLM_PROVIDER=stub to demo the pipeline offline.\n")
        return
    missing = [m for m in config.MODELS if m not in available]
    if missing:
        if config.LLM_PROVIDER == "openrouter":
            print(f"\n  !! not served by OpenRouter under this slug: {', '.join(missing)}\n"
                  f"     Slugs are `vendor/model` and change as models are retired —\n"
                  f"     check https://openrouter.ai/models and fix FS_MODELS.\n")
        else:
            print(f"\n  !! configured but not pulled: {', '.join(missing)}\n"
                  f"     Fix with: {' && '.join('ollama pull ' + m for m in missing)}\n")


def _fix_hint() -> str:
    return {
        "ollama": "Start the model server (`ollama serve`).",
        "openrouter": "Check the key and that openrouter.ai is reachable from here.",
    }.get(config.LLM_PROVIDER, "Check FS_LLM_BASE_URL and FS_LLM_API_KEY.")


def main():
    port = int(os.environ.get("FS_PORT", "8000"))
    if not os.path.exists(config.DB_PATH):
        sys.exit(f"database not found at {config.DB_PATH}\nrun: python3 data/seed.py")
    banner = "SIMULATED (stub reasoner)" if config.LLM_PROVIDER == "stub" else config.LLM_PROVIDER
    print(f"""
FraudSentinel  http://localhost:{port}
  model provider : {banner}
  endpoint       : {config.LLM_BASE_URL or '(offline stub)'}
  models         : {', '.join(config.MODELS)}
  prompt version : {config.PROMPT_VERSION}
  database       : {config.DB_PATH}
  sign in        : l1@bank.test / l1   l2@bank.test / l2   admin@bank.test / admin
""".rstrip())
    if config.LLM_PROVIDER == "stub":
        print("  ! stub provider: benchmark output is SIMULATED, not a real model comparison\n")
    preflight()
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
