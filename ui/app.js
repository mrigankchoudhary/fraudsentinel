/* FraudSentinel dashboard (guide Section 6.9).
   No build step and no framework: open the server and it runs. Every screen
   maps to a requirement in the brief, noted above each render function. */
"use strict";

const S = {                       // app state
  token: sessionStorage.getItem("fs_token") || null,
  user: JSON.parse(sessionStorage.getItem("fs_user") || "null"),
  tab: "alerts", meta: null, caseId: null, busy: false, error: null, notice: null,
};

// Deep links: #/case/<id> and #/tab/<name>, so a case can be cited in the report.
function readHash() {
  const m = /^#\/(case|tab)\/(.+)$/.exec(location.hash || "");
  if (!m) return;
  if (m[1] === "case") S.caseId = m[2];
  else { S.tab = m[2]; S.caseId = null; }
}
function writeHash() {
  const want = S.caseId ? `#/case/${S.caseId}` : `#/tab/${S.tab}`;
  if (location.hash !== want) history.replaceState(null, "", want);
}
window.addEventListener("hashchange", () => { readHash(); render(); });

const $ = (h) => { const d = document.createElement("div"); d.innerHTML = h.trim(); return d.firstChild; };

// A wide table is the one thing on these screens that cannot be made narrow:
// the audit trail is eight columns and carries 64-character hashes. Left alone
// it stretches the whole page far past a phone's viewport and every other
// element gets dragged sideways with it. Giving each table its own scroller
// confines that to the table.
const scrollableTables = (el) => {
  el.querySelectorAll("table").forEach(t => {
    if (t.parentElement?.classList.contains("tscroll")) return;
    const w = document.createElement("div");
    w.className = "tscroll";
    t.replaceWith(w); w.appendChild(t);
  });
  return el;
};
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, c => (
  { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const money = (n) => n == null ? "—" : "₹" + Number(n).toLocaleString("en-IN", { maximumFractionDigits: 2 });
const when = (t) => !t ? "—" : new Date(t).toLocaleString("en-IN", { dateStyle: "medium", timeStyle: "short" });
const riskColour = (r) => r == null ? "#ccc" : r >= 60 ? "var(--e)" : r >= 30 ? "var(--warn)" : "var(--a)";

async function api(path, opts = {}) {
  const r = await fetch(path, {
    ...opts,
    headers: { "Content-Type": "application/json",
               ...(S.token ? { Authorization: "Bearer " + S.token } : {}),
               ...(opts.headers || {}) },
  });
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(body.error || `HTTP ${r.status}`);
  return body;
}

// ---------------------------------------------------------------- shell
async function render() {
  const root = document.getElementById("root");
  writeHash();
  if (!S.token) return root.replaceChildren(loginView());

  if (!S.meta) { try { S.meta = await api("/api/meta"); } catch (e) { S.error = e.message; } }
  const c = S.meta?.counts || {};
  const tabs = [
    ["alerts", "Alert queue", c.alerts_open],
    ["hitl", "My review queue", c.hitl],
    // Once a case is escalated it belongs to L2, so L1 does not see the queue
    // at all — it is not theirs to work and they cannot close anything in it.
    ...(S.user.role === "L1" ? [] : [["escalation", "Escalation queue", c.escalation]]),
    ["exception", "Exception queue", c.exception],
    ["audit", "Audit trail", null],
    ["benchmark", "Model benchmark", null],
    ...(S.user.role === "admin" ? [["settings", "Settings", null]] : []),
  ];

  root.replaceChildren($(`
    <div>
      <header>
        <div class="brand">FraudSentinel<span>Governed agentic fraud investigation</span></div>
        <div class="spacer"></div>
        <div class="who">${esc(S.user.name)} · <b>${esc(S.user.role)}</b></div>
        <button class="btn" id="out">Sign out</button>
      </header>
      <nav>${tabs.map(([k, label, n]) => `
        <button data-tab="${k}" class="${S.tab === k ? "on" : ""}">${label}${
          n ? `<span class="count">${n}</span>` : ""}</button>`).join("")}
      </nav>
      <main id="main"></main>
    </div>`));

  root.querySelector("#out").onclick = async () => {
    await api("/api/logout", { method: "POST" }).catch(() => {});
    sessionStorage.clear(); S.token = null; S.user = null; S.meta = null; render();
  };
  root.querySelectorAll("nav button").forEach(b => b.onclick = () => {
    S.tab = b.dataset.tab; S.caseId = null; S.error = S.notice = null; render();
  });

  if (S.tab === "escalation" && S.user.role === "L1") S.tab = "hitl";

  const main = root.querySelector("#main");
  if (S.meta?.simulated) main.appendChild($(`<div class="banner warn">
    <b>Simulated model provider.</b> The orchestration, guardrails, routing and HITL
    below are real, but agent reasoning comes from the offline stub. Benchmark figures
    produced in this mode are <b>not</b> a real open-model comparison — set
    <code>FS_LLM_PROVIDER</code> to <code>ollama</code> (local) or
    <code>openrouter</code> (hosted) before recording Stage 3 results.
  </div>`));
  if (S.error) main.appendChild($(`<div class="banner err">${esc(S.error)}</div>`));
  if (S.notice) main.appendChild($(`<div class="banner warn">${esc(S.notice)}</div>`));

  if (S.caseId) { main.appendChild(scrollableTables(await caseView(S.caseId))); return; }
  const views = { alerts: alertsView, hitl: queueView("HITL"), escalation: queueView("ESCALATE"),
                  exception: queueView("EXCEPTION"), audit: auditView,
                  benchmark: benchmarkView, settings: settingsView };
  main.appendChild(scrollableTables(await views[S.tab]()));
}

// ---------------------------------------------------------------- login
function loginView() {
  const v = $(`<div class="login">
    <h1 style="font-size:1.4rem;text-align:center;margin-bottom:.2rem">FraudSentinel</h1>
    <p class="muted small" style="text-align:center;margin-top:0">
      Governed agentic AI copilot for digital payment fraud alert investigation</p>
    <form class="card" id="login-form">
      <label for="em">Email</label>
      <input id="em" name="email" type="email" autocomplete="username"
        placeholder="l1@bank.test" autocapitalize="none" spellcheck="false" required autofocus>
      <label for="pw">Password</label>
      <input id="pw" name="password" type="password" autocomplete="current-password"
        placeholder="Enter password" required>
      <div style="margin-top:1rem"><button class="btn primary" id="go" type="submit" style="width:100%">Sign in</button></div>
      <p id="err" class="small" style="color:var(--e);min-height:1.2em"></p>
      <h3 style="margin-top:.5rem">Demo roles</h3>
      <table><tbody>
        <tr><td><button class="demo-login" type="button" data-email="l1@bank.test" data-password="l1">Use L1</button></td><td class="muted small">may clear and verify</td></tr>
        <tr><td><button class="demo-login" type="button" data-email="l2@bank.test" data-password="l2">Use L2</button></td><td class="muted small">may also hold and close escalations</td></tr>
        <tr><td><button class="demo-login" type="button" data-email="admin@bank.test" data-password="admin">Use admin</button></td><td class="muted small">may change thresholds</td></tr>
      </tbody></table>
    </form></div>`);
  const form = v.querySelector("#login-form");
  const email = v.querySelector("#em");
  const password = v.querySelector("#pw");
  const button = v.querySelector("#go");
  const error = v.querySelector("#err");
  const go = async () => {
    error.textContent = "";
    button.disabled = true;
    try {
      const r = await api("/api/login", { method: "POST", body: JSON.stringify({
        email: email.value, password: password.value }) });
      S.token = r.token; S.user = r;
      sessionStorage.setItem("fs_token", r.token);
      sessionStorage.setItem("fs_user", JSON.stringify(r));
      S.meta = null; render();
    } catch (e) {
      error.textContent = e.message;
      button.disabled = false;
      password.focus();
    }
  };
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    if (form.reportValidity()) go();
  });
  v.querySelectorAll("[data-email]").forEach(b => b.onclick = () => {
    email.value = b.dataset.email;
    password.value = b.dataset.password;
    email.focus();
    email.setSelectionRange(0, email.value.length);
  });
  return v;
}

// ------------------------------------------- alert queue (UI triggers workflow)
async function alertsView() {
  const alerts = await api("/api/alerts");
  const v = $(`<div>
    <div class="card">
      <h2>Alert queue</h2>
      <p class="muted small">Pick a model and press Investigate. That POSTs
      <code>{alert_id, model, mode, prompt_version}</code> to the orchestrator webhook —
      the same contract the n8n workflow exposes.</p>
      <div class="row" style="align-items:end">
        <div style="max-width:220px"><label>Open-weight model</label>
          <select id="model">${S.meta.models.map(m => `<option>${esc(m)}</option>`).join("")}</select></div>
        <div style="max-width:160px"><label>Prompt version</label>
          <select id="pv">${S.meta.prompt_versions.map(p =>
            `<option ${p === S.meta.default_prompt_version ? "selected" : ""}>${p}</option>`).join("")}</select></div>
        <div></div>
      </div>
    </div>
    <div class="card"><table>
      <thead><tr><th>Alert</th><th>Rule fired</th><th>Channel</th><th class="num">Amount</th>
        <th>Customer</th><th>Status</th><th></th></tr></thead>
      <tbody>${alerts.map(a => `<tr data-alert="${esc(a.id)}">
        <td><b>${esc(a.id)}</b><div class="muted small">${esc(a.alert_type)}</div></td>
        <td class="small">${esc(a.rule_fired)}</td>
        <td>${esc(a.channel)}</td>
        <td class="num">${money(a.amount)}</td>
        <td class="small">${esc(a.customer_id)}<div class="muted">${esc(a.home_city)} · ${esc(a.segment)}</div></td>
        <td><span class="badge ${a.status === "new" ? "new" : "H"}">${esc(a.status)}</span></td>
        <td><button class="btn primary" data-go="${esc(a.id)}">Investigate</button></td>
      </tr>`).join("")}</tbody></table></div>`);

  v.querySelectorAll("[data-go]").forEach(b => b.onclick = async () => {
    const all = v.querySelectorAll("[data-go]");
    all.forEach(x => x.disabled = true);
    b.innerHTML = '<span class="spin"></span> running';
    try {
      const r = await api("/webhook/investigate", { method: "POST", body: JSON.stringify({
        alert_id: b.dataset.go, model: v.querySelector("#model").value,
        mode: "live", prompt_version: v.querySelector("#pv").value }) });
      S.meta = null; S.caseId = r.case_id;
      S.notice = `Case routed to ${r.route} (${r.ahe}) in ${r.latency_ms} ms — ${r.reason || ""}`;
      render();
    } catch (e) {
      S.error = e.message; all.forEach(x => x.disabled = false);
      b.textContent = "Investigate"; render();
    }
  });
  return v;
}

// ------------------------------------------- HITL / escalation / exception queues
function queueView(route) {
  const titles = {
    HITL: ["Human review queue", "Medium risk, low confidence, missing evidence, or a sampled automatic decision. An investigator must decide before anything happens to the money."],
    ESCALATE: ["Escalation queue", "Hard flags and high risk. Funds are already on a temporary hold where a hard flag fired; L2 confirms or releases."],
    EXCEPTION: ["Exception queue", "The workflow could not complete safely: invalid model output, missing evidence, prompt injection or a model failure. Nothing is auto-decided here."],
  };
  return async function () {
    const rows = await api(`/api/cases?route=${route}&status=awaiting_human`);
    const [title, blurb] = titles[route];
    const v = $(`<div><div class="card">
      <h2>${title}</h2><p class="muted small">${blurb}</p></div>
      <div class="card">${rows.length === 0
        ? '<p class="muted">Nothing waiting. Investigate an alert to populate this queue.</p>'
        : `<table><thead><tr><th>Case</th><th>Alert</th><th>Recommendation</th>
            <th class="num">Risk</th><th class="num">Conf.</th><th>Flags</th><th>Model</th><th>Raised</th></tr></thead>
           <tbody>${rows.map(r => {
             const rec = r.recommendation || {};
             return `<tr class="clickable" data-case="${esc(r.id)}">
               <td><span class="badge ${esc(r.ahe)}">${esc(r.ahe)}</span>
                   <div class="muted small">${esc(r.id.slice(0, 8))}</div></td>
               <td>${esc(r.alert_id)}${r.sampled ? ' <span class="badge new">sampled</span>' : ""}</td>
               <td>${rec.decision ? `<b>${esc(rec.decision)}</b>` :
                   `<span class="badge E">exception</span>`}
                   <div class="muted small">${esc((rec.exception_reason || rec.rationale || "").slice(0, 90))}</div></td>
               <td class="num">${rec.risk_score ?? "—"}</td>
               <td class="num">${rec.confidence ?? "—"}</td>
               <td>${(r.hard_flags || []).map(f => `<span class="badge flag">${esc(f.code)}</span>`).join("") || "—"}</td>
               <td class="small">${esc(r.model || "—")}<div class="muted">${esc(r.prompt_version || "")}</div></td>
               <td class="small muted">${when(r.created_at)}</td></tr>`;
           }).join("")}</tbody></table>`}</div></div>`);
    v.querySelectorAll("[data-case]").forEach(tr => tr.onclick = () => {
      S.caseId = tr.dataset.case; S.notice = null; render();
    });
    return v;
  };
}

// ---------------------------------------------------------------- case view
async function caseView(id) {
  const { case: c, alert, audit, actions } = await api("/api/cases/" + id);
  const rec = c.recommendation || {}, f = c.features || {}, ev = c.evidence || {};
  const cited = new Set(rec.evidence_ids || []);
  const ao = c.agent_outputs || {};
  const AGENTS = [["transaction_analysis", "Transaction Analysis Agent"],
                  ["customer_behaviour", "Customer Behaviour Agent"],
                  ["risk_policy", "Risk/Policy Agent"],
                  ["recommendation", "Recommendation Agent"]];
  // An escalated case belongs to L2: L1 does not get the queue, and following an
  // old link to one must not put a decision form in front of them either — the
  // approval matrix would refuse the submission anyway.
  const withL2 = c.route === "ESCALATE" && S.user.role === "L1";
  const open = c.status === "awaiting_human" && !withL2;

  const v = $(`<div>
    <button class="btn" id="back">← Back to queue</button>

    <div class="card" style="margin-top:1rem">
      <span class="badge ${esc(c.ahe)}" style="float:right">${esc(c.ahe)} · ${esc(c.route)}</span>
      <h2>Case ${esc(c.id.slice(0, 8))} · alert ${esc(c.alert_id)}</h2>
      <div class="row">
        <dl class="kv">
          <dt>Transaction</dt><dd>${money(alert?.amount)} ${esc(alert?.channel || "")}</dd>
          <dt>Rule fired</dt><dd>${esc(alert?.rule_fired || "")}</dd>
          <dt>Customer</dt><dd>${esc(alert?.customer_id || "")} · ${esc(alert?.home_city || "")}</dd>
          <dt>Model</dt><dd>${esc(c.model || "—")} · prompts ${esc(c.prompt_version || "")}</dd>
          <dt>End-to-end</dt><dd>${c.latency_ms ?? "—"} ms</dd>
          <dt>Status</dt><dd>${esc(c.status)}${c.final_outcome ? " → <b>" + esc(c.final_outcome) + "</b>" : ""}</dd>
        </dl>
        <div>
          <h3>Recommendation</h3>
          ${rec.decision ? `<div style="font-size:1.5rem;font-weight:650">${esc(rec.decision)}</div>` :
                           `<div class="badge E">no valid recommendation</div>`}
          <div class="gauge"><i style="width:${rec.risk_score ?? 0}%;background:${riskColour(rec.risk_score)}"></i></div>
          <div class="small muted">risk ${rec.risk_score ?? "—"} / 100 · confidence ${rec.confidence ?? "—"}
            ${c.raw_decision && c.raw_decision !== rec.decision
              ? ` · <b>model said ${esc(c.raw_decision)}</b>, guardrails adjusted it` : ""}</div>
          ${rec.rationale ? `<p class="small" style="margin-top:.5rem">${esc(rec.rationale)}</p>` : ""}
          ${rec.suspected_typology && rec.suspected_typology !== "none"
            ? `<div class="small muted">suspected typology: <b>${esc(rec.suspected_typology)}</b></div>` : ""}
        </div>
      </div>
      ${(c.hard_flags || []).length ? `<div class="banner err" style="margin-top:.8rem">
        <b>Hard flags fired.</b> ${(c.hard_flags || []).map(h =>
          `<div>${esc(h.code)} ${esc(h.name)} — ${esc(h.detail)}</div>`).join("")}
        A model may escalate a hard flag further but can never clear one.</div>` : ""}
      ${rec.exception_reason ? `<div class="banner err" style="margin-top:.8rem">
        <b>Exception (${esc(rec.exception_stage || "")}).</b> ${esc(rec.exception_reason)}</div>` : ""}
      ${(rec.injection_hits || []).length ? `<div class="banner warn">
        <b>Prompt injection quarantined.</b> ${(rec.injection_hits || []).map(h =>
          `${esc(h.field)}: “${esc(h.matched)}”`).join("; ")}. The text was passed to the
        models as data inside <code>&lt;untrusted&gt;</code> tags and the case was sent here
        for human review.</div>` : ""}
      ${f.data_incomplete ? `<div class="banner warn"><b>Mandatory evidence missing or stale:</b>
        ${esc((f.missing_sources || []).join(", "))}. Automatic clearance is blocked.</div>` : ""}
    </div>

    <div class="card"><h2>Agent findings</h2>
      <div class="row">${AGENTS.map(([k, title]) => {
        const r = ao[k] || {}, o = r.output || {};
        if (!r.ok) return `<div class="agent"><h4>${title}</h4>
          <div class="badge E">no valid output</div>
          <p class="small muted">${esc(r.error || "")}</p></div>`;
        const list = [...(o.findings || []),
          ...(o.policies_triggered || []).map(p => `Policy ${p.policy_id}: ${p.reason}`),
          ...(k === "recommendation" && o.decision
              ? [`Decision: ${o.decision} at risk ${o.risk_score}, confidence ${o.confidence}.`,
                 o.rationale,
                 ...(o.missing_information || []).map(x => `Missing: ${x}`)].filter(Boolean)
              : [])];
        return `<div class="agent">
          <span class="score">${o.sub_score != null ? o.sub_score + "/100" : ""}</span>
          <h4>${title}</h4>
          <ul class="small">${list.map(x => `<li>${esc(x)}</li>`).join("")}</ul>
          ${(o.risk_signals || o.deviations || []).length
            ? `<div class="small muted" style="margin-top:.4rem">${
                (o.risk_signals || o.deviations).map(s => `<code>${esc(s)}</code>`).join(" ")}</div>` : ""}
          <div class="small muted" style="margin-top:.4rem">${r.latency_ms ?? "—"} ms${
            r.json_first_attempt === false ? " · JSON needed recovery" : ""}</div>
        </div>`;
      }).join("")}</div>
    </div>

    <div class="card"><h2>Evidence pack</h2>
      <p class="muted small">PII is masked before any model call. Highlighted rows are the
      items the recommendation cites; the validator rejects any ID that is not in this list.</p>
      ${(ev.items || []).map(i => `<div class="ev ${i.untrusted ? "untrusted" : ""} ${cited.has(i.id) ? "cited" : ""}">
        <span class="id">${esc(i.id)}</span>
        <span>${esc(i.text)}${i.untrusted
          ? '<div class="small" style="color:var(--warn)"><b>Untrusted customer text</b> — passed to models as data, never as instructions.</div>' : ""}</span>
      </div>`).join("")}
    </div>

    ${withL2 && c.status === "awaiting_human" ? `<div class="banner warn">
      <b>This case is with L2.</b> It was escalated, so it is no longer in your queue and
      only L2 can close it. You are seeing it read-only.</div>` : ""}

    ${open && c.human_reason ? `<div class="card">
      <h2>Handed over by ${esc(c.decided_role || "")}</h2>
      <p class="small muted">This case was escalated rather than closed. Everything the
      previous investigator recorded is below — it is the only context you have from them.</p>
      <dl class="kv"><dt>Their decision</dt><dd>${esc(c.human_decision || "—")}</dd>
        <dt>Their reason</dt><dd>${esc(c.human_reason)}</dd>
        <dt>Investigator</dt><dd>${esc(c.decided_by || "—")} (${esc(c.decided_role || "—")})</dd></dl>
    </div>` : ""}

    ${open ? `<div class="card" id="decide"><h2>Your decision</h2>
      <p class="muted small">You are signed in as <b>${esc(S.user.role)}</b>.
      L1 may clear or verify, and may hand a case to L2; holding funds and closing
      an escalation require L2.
      An override always needs a written reason.</p>
      <div class="row">
        <div style="max-width:220px"><label>Action</label>
          <select id="dec"><option value="APPROVE">Approve the recommendation</option>
            <option value="OVERRIDE">Override</option>
            <option value="ESCALATE">${S.user.role === "L1"
              ? "Escalate to L2" : "Escalate to the AML queue"}</option></select></div>
        <div style="max-width:220px" id="outwrap" hidden><label>Final outcome</label>
          <select id="out"><option>PROCEED</option><option>VERIFY</option>
            <option>HOLD</option><option>ESCALATE</option></select></div>
        <div></div>
      </div>
      <label>Reason <span class="muted" id="reason-hint">(required when overriding)</span></label>
      <textarea id="reason" rows="2" placeholder="Why are you deciding this way?"></textarea>
      <div style="margin-top:.8rem"><button class="btn ok" id="submit">Submit decision</button></div>
    </div>` : `<div class="card"><h2>Outcome</h2>
      <dl class="kv"><dt>Human decision</dt><dd>${esc(c.human_decision || "—")}</dd>
        <dt>Final outcome</dt><dd><b>${esc(c.final_outcome || "—")}</b></dd>
        <dt>Reason</dt><dd>${esc(c.human_reason || "—")}</dd>
        <dt>Decided by</dt><dd>${esc(c.decided_by || "—")} (${esc(c.decided_role || "—")})</dd>
        <dt>Closed</dt><dd>${when(c.closed_at)}</dd></dl></div>`}

    <div class="card"><h2>Core banking actions</h2>
      ${actions.length ? `<table><thead><tr><th>Action</th><th>Endpoint</th><th>Result</th>
        <th>Reference</th><th>When</th></tr></thead><tbody>${actions.map(a => `<tr>
        <td><b>${esc(a.action)}</b></td><td><code>${esc(a.endpoint)}</code></td>
        <td class="small">${esc(a.response?.message || "")}</td>
        <td class="small"><code>${esc(a.response?.reference || "")}</code></td>
        <td class="small muted">${when(a.executed_at)}</td></tr>`).join("")}</tbody></table>`
        : '<p class="muted small">No action has touched the account yet.</p>'}
    </div>

    <div class="card"><h2>Audit trail</h2>
      <table><thead><tr><th>Step</th><th>Actor</th><th>Model</th><th>Prompts</th>
        <th class="num">ms</th><th>Input hash</th><th>When</th></tr></thead>
      <tbody>${audit.map(a => `<tr><td>${esc(a.step)}</td><td class="small">${esc(a.actor)}</td>
        <td class="small">${esc(a.model || "—")}</td><td class="small">${esc(a.prompt_version || "—")}</td>
        <td class="num">${a.latency_ms ?? ""}</td>
        <td class="small muted"><code>${esc(a.input_hash || "—")}</code></td>
        <td class="small muted">${when(a.ts)}</td></tr>`).join("")}</tbody></table>
    </div></div>`);

  v.querySelector("#back").onclick = () => { S.caseId = null; S.notice = null; render(); };
  if (open) {
    const dec = v.querySelector("#dec"), wrap = v.querySelector("#outwrap");
    const hint = v.querySelector("#reason-hint"), reason = v.querySelector("#reason");
    // Handing a case to L2 without saying why leaves L2 with no context, so the
    // reason is required for an escalation exactly as it is for an override.
    const needsReason = () => dec.value === "OVERRIDE" || dec.value === "ESCALATE";
    const sync = () => {
      wrap.hidden = dec.value !== "OVERRIDE";
      hint.textContent = needsReason() ? "(required)" : "(optional)";
      reason.placeholder = dec.value !== "ESCALATE"
        ? "Why are you deciding this way?"
        : S.user.role === "L1"
          ? "Why does this need L2? The next investigator sees only this."
          : "Why is this going to the AML queue?";
    };
    dec.onchange = sync; sync();
    v.querySelector("#submit").onclick = async (e) => {
      if (needsReason() && !reason.value.trim()) {
        S.error = dec.value === "ESCALATE"
          ? "Escalating to L2 requires a reason."
          : "An override requires a written reason.";
        render();
        return;
      }
      e.target.disabled = true;
      try {
        const r = await api(`/api/cases/${id}/decide`, { method: "POST", body: JSON.stringify({
          decision: dec.value,
          final_outcome: dec.value === "OVERRIDE" ? v.querySelector("#out").value : null,
          reason: v.querySelector("#reason").value }) });
        S.meta = null;
        S.notice = r.escalated
          ? `Case handed to L2 — it is now waiting in the escalation queue. Core banking: ${r.cbs.message} (${r.cbs.reference})`
          : `Case closed as ${r.final_outcome}. Core banking: ${r.cbs.message} (${r.cbs.reference})`;
        // A handed-off case is no longer this investigator's to act on, so drop
        // back to the queue instead of leaving a dead decision form on screen.
        if (r.escalated) S.caseId = null;
        render();
      } catch (err) { S.error = err.message; e.target.disabled = false; render(); }
    };
  }
  return v;
}

// ---------------------------------------------------------------- audit
async function auditView() {
  const rows = await api("/api/audit");
  return $(`<div class="card"><h2>Audit trail</h2>
    <p class="muted small">Every orchestrator step, model call and human action, append-only.
    Each row carries the model, prompt version, a hash of the exact input and the actor —
    this is what makes a decision defensible months later.</p>
    <table><thead><tr><th>When</th><th>Case</th><th>Step</th><th>Actor</th>
      <th>Model</th><th>Prompts</th><th class="num">ms</th><th>Input hash</th></tr></thead>
    <tbody>${rows.map(a => `<tr><td class="small muted">${when(a.ts)}</td>
      <td class="small"><code>${esc((a.case_id || "—").slice(0, 8))}</code></td>
      <td>${esc(a.step)}</td><td class="small">${esc(a.actor)}</td>
      <td class="small">${esc(a.model || "—")}</td><td class="small">${esc(a.prompt_version || "—")}</td>
      <td class="num">${a.latency_ms ?? ""}</td>
      <td class="small muted"><code>${esc(a.input_hash || "—")}</code></td></tr>`).join("")}
    </tbody></table></div>`);
}

// ---------------------------------------------------------------- benchmark
// The runner panel is what makes the benchmark reproducible on a deployed
// instance, where there is no shell to type the CLI command into. Admin only,
// one run at a time, and it drives the same benchmark.run() the CLI does.
function benchRunner() {
  const st = S.bench || {};
  const admin = S.user.role === "admin";
  const models = (S.meta?.models || []);
  const pctDone = st.total ? Math.round(100 * st.done / st.total) : 0;

  const v = $(`<div class="card">
    <h2>Run a comparison</h2>
    ${S.meta?.simulated ? `<div class="banner warn">Provider is the offline stub, so a
      run started here produces <b>simulated</b> numbers, not a model comparison.</div>` : ""}
    <p class="muted small">Every model sees identical cases, evidence packs, prompts and
    schema at temperature 0. Results are written to this dashboard and to
    <code>benchmark/results/</code>.</p>
    ${st.running ? `<p class="small"><span class="spin"></span>
        Running ${esc(st.model || "")} — ${st.done}/${st.total} investigations (${pctDone}%)</p>
      <div class="gauge"><i style="width:${pctDone}%;background:var(--h)"></i></div>`
    : !admin ? `<p class="muted small">Signed in as <b>${esc(S.user.role)}</b>. Starting a
        benchmark is an admin action; it costs model calls and overwrites the dashboard
        figures. From a shell you can also run:</p>
        <pre class="small" style="overflow-x:auto"><code>python3 benchmark/run_benchmark.py --runs 3</code></pre>`
    : `<label for="bm">Models <span class="muted">(the approved list)</span></label>
       <select id="bm" multiple size="${Math.min(4, Math.max(2, models.length))}">${
         models.map(m => `<option value="${esc(m)}" selected>${esc(m)}</option>`).join("")}</select>
       <div class="row" style="margin-top:.4rem">
         <div><label for="br">Runs per case</label>
           <select id="br"><option>1</option><option selected>3</option><option>5</option></select></div>
         <div><label for="bv">Prompt version</label>
           <select id="bv"><option>v3</option><option>v2</option><option>v1</option></select></div>
       </div>
       <div style="margin-top:.8rem"><button class="btn primary" id="bgo">Start benchmark</button></div>
       <p class="small muted" style="margin-top:.5rem">${models.length} models x 30 cases x 3 runs
       = ${models.length * 90} investigations. On a hosted provider that is billed usage.</p>`}
    ${st.error ? `<div class="banner err" style="margin-top:.8rem">${esc(st.error)}</div>` : ""}
    ${st.run_id && !st.running && !st.error
      ? `<p class="small" style="margin-top:.6rem">Last run <code>${esc(st.run_id)}</code> finished.</p>` : ""}
  </div>`);

  const go = v.querySelector("#bgo");
  if (go) go.onclick = async () => {
    go.disabled = true;
    const picked = [...v.querySelectorAll("#bm option")].filter(o => o.selected).map(o => o.value);
    try {
      S.bench = await api("/api/benchmark/run", { method: "POST", body: JSON.stringify({
        models: picked,
        runs: Number(v.querySelector("#br").value),
        prompt_version: v.querySelector("#bv").value }) });
      pollBench();
      render();
    } catch (e) { S.error = e.message; go.disabled = false; render(); }
  };
  return v;
}

// Poll while a run is in flight, then refresh the table once it lands.
let _benchTimer = null;
function pollBench() {
  clearTimeout(_benchTimer);
  _benchTimer = setTimeout(async () => {
    try {
      const st = await api("/api/benchmark/status");
      const wasRunning = S.bench?.running;
      S.bench = st;
      if (st.running) { pollBench(); if (S.tab === "benchmark") render(); }
      else if (wasRunning && S.tab === "benchmark") render();   // results are in
    } catch { /* a transient failure must not kill the poll loop */ }
  }, 2000);
}

async function benchmarkView() {
  const [rows, st] = await Promise.all([
    api("/api/benchmark"),
    api("/api/benchmark/status").catch(() => ({})),
  ]);
  S.bench = st;
  if (st.running) pollBench();

  if (!rows.length) {
    const v = $(`<div></div>`);
    v.appendChild($(`<div class="card"><h2>Model benchmark</h2>
      <p class="muted">No benchmark has been run yet.</p></div>`));
    v.appendChild(benchRunner());
    return v;
  }

  const byModel = {};
  for (const r of rows) (byModel[r.model] ||= []).push(r);
  const pct = (n, d) => d ? (100 * n / d) : 0;
  const stats = Object.entries(byModel).map(([model, rs]) => {
    const hi = rs.filter(r => r.expected === "ESCALATE" || r.expected === "HOLD");
    const gen = rs.filter(r => r.expected === "PROCEED");
    const lat = rs.map(r => r.latency_ms).filter(Boolean).sort((a, b) => a - b);
    return {
      model, n: rs.length,
      accuracy: pct(rs.filter(r => r.raw_decision === r.expected).length, rs.length),
      recall: pct(hi.filter(r => ["HOLD", "ESCALATE"].includes(r.raw_decision)).length, hi.length),
      fp: pct(gen.filter(r => ["HOLD", "ESCALATE"].includes(r.raw_decision)).length, gen.length),
      halluc: pct(rs.filter(r => r.hallucinated).length, rs.length),
      json: pct(rs.filter(r => r.json_valid).length, rs.length),
      p50: lat.length ? lat[Math.floor(lat.length * .5)] : 0,
      p95: lat.length ? lat[Math.floor(lat.length * .95)] : 0,
    };
  });
  const col = [["accuracy", "Accuracy %", 1], ["recall", "Escalation recall %", 1],
               ["fp", "False positive %", -1], ["halluc", "Hallucination %", -1],
               ["json", "JSON compliance %", 1]];
  const best = {}; for (const [k, , dir] of col)
    best[k] = stats.reduce((a, b) => (dir > 0 ? b[k] > a[k] : b[k] < a[k]) ? b : a).model;

  const view = $(`<div><div class="card"><h2>Open-model comparison</h2>
    <p class="muted small">Identical cases, evidence packs, prompts and output schema for every
    model, temperature 0, same host. Decisions scored are the <b>raw model decision</b>, before
    guardrails — otherwise the guardrails would hide the models' mistakes.</p></div>
    <div class="card"><table>
      <thead><tr><th>Model</th><th class="num">n</th>${col.map(c =>
        `<th class="num">${c[1]}</th>`).join("")}<th class="num">p50 ms</th><th class="num">p95 ms</th></tr></thead>
      <tbody>${stats.map(s => `<tr><td><b>${esc(s.model)}</b></td><td class="num">${s.n}</td>
        ${col.map(([k]) => `<td class="num">${s[k].toFixed(1)}${
          best[k] === s.model ? ' <span class="badge A">best</span>' : ""}</td>`).join("")}
        <td class="num">${s.p50}</td><td class="num">${s.p95}</td></tr>`).join("")}
      </tbody></table>
      <h3 style="margin-top:1rem">Escalation recall — the metric that matters most</h3>
      ${stats.map(s => `<div style="margin:.3rem 0">
        <span class="small" style="display:inline-block;min-width:9rem">${esc(s.model)}</span>
        <span class="bar" style="width:${Math.max(2, s.recall * 2)}px;background:${
          s.recall >= 99 ? "var(--a)" : s.recall >= 90 ? "var(--warn)" : "var(--e)"}"></span>
        <span class="small">${s.recall.toFixed(1)}%</span></div>`).join("")}
      <p class="small muted" style="margin-top:.8rem">A missed high-risk case costs far more than a
      slow one, so weight escalation recall and hallucination above latency when you choose.</p>
    </div></div>`);
  view.appendChild(benchRunner());
  return view;
}

// ---------------------------------------------------------------- settings
async function settingsView() {
  const th = S.meta.thresholds;
  const labels = {
    AUTO_PROCEED_MAX: "Auto-proceed only below this risk score",
    ESCALATE_MIN: "Escalate at or above this risk score",
    MIN_AUTO_CONFIDENCE: "Minimum confidence to auto-proceed",
    AUTO_SAMPLE_RATE: "Share of automatic decisions sampled for human review",
    NEW_BENEFICIARY_HOURS: "Beneficiary younger than this is high risk (hours)",
    SIM_CHANGE_HOURS: "SIM change within this window is high risk (hours)",
    NEW_DEVICE_HOURS: "Device newer than this counts as new (hours)",
    AMOUNT_RATIO_HIGH: "High amount as a multiple of the 90-day maximum",
    SPLIT_BURST_COUNT: "Transfers to one new payee in 15 min that count as a burst",
    HITL_TIMEOUT_MIN: "Investigator SLA before auto-escalation (minutes)",
  };
  const v = $(`<div class="card"><h2>Decision thresholds</h2>
    <p class="muted small">Thresholds live in the <code>policy_rules</code> table, not in the
    workflow, so they can be changed without editing the pipeline. Every change is written to
    the audit log.</p>
    ${Object.entries(th).map(([k, val]) => `<label>${labels[k] || k} <code>${k}</code></label>
      <input data-th="${k}" value="${val}" type="number" step="0.01">`).join("")}
    <div style="margin-top:1rem"><button class="btn primary" id="save">Save</button>
      <button class="btn" id="sweep">Run SLA sweep now</button></div>`);
  v.querySelector("#save").onclick = async (e) => {
    e.target.disabled = true;
    const body = { thresholds: {} };
    v.querySelectorAll("[data-th]").forEach(i => body.thresholds[i.dataset.th] = i.value);
    try { await api("/api/settings", { method: "POST", body: JSON.stringify(body) });
          S.meta = null; S.notice = "Thresholds saved and logged."; }
    catch (err) { S.error = err.message; }
    render();
  };
  v.querySelector("#sweep").onclick = async () => {
    try { const r = await api("/api/sla-sweep", { method: "POST" });
          S.notice = `SLA sweep: ${r.escalated} case(s) auto-escalated.`; }
    catch (e) { S.error = e.message; }
    S.meta = null; render();
  };
  return v;
}

readHash();
render();
