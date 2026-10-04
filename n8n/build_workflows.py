#!/usr/bin/env python3
"""Generates the importable n8n workflow JSON.

The workflow is generated rather than hand-written so the node list, the
connections and the Code-node logic stay in step with app/orchestrator.py.
Run this, then import n8n/main_workflow.json in the n8n editor.

    python3 n8n/build_workflows.py
"""
import json, os

HERE = os.path.dirname(os.path.abspath(__file__))

# --- Code-node bodies. These mirror app/features.py and app/guardrails.py. ----

ASSEMBLE_PACK = r"""
// Node 4 — Assemble Evidence Pack: assign E-IDs, mask PII, compute features.
// Mirrors app/evidence.py and app/features.py.
const d = $input.first().json;
const now = new Date(d.reference_now || Date.now());
const H = (t) => t ? (now - new Date(t)) / 3.6e6 : null;
const TH = d.thresholds;

const txn = d.txn, cust = d.customer;
const history = (d.transactions || []).filter(t => t.id !== txn.id);
const amounts = history.map(t => +t.amount);
const max90 = amounts.length ? Math.max(...amounts) : 0;
const mean90 = amounts.length ? amounts.reduce((a, b) => a + b, 0) / amounts.length : 0;
const txnTs = new Date(txn.ts);

const burst = history.filter(t => {
  const ts = new Date(t.ts);
  return ts <= txnTs && ts >= new Date(txnTs - 15 * 60000) && (t.direction || 'OUT') === 'OUT';
});
const samePayee = burst.filter(t => t.beneficiary_id === txn.beneficiary_id).length + 1;

const ben = (d.beneficiaries || []).find(b => b.id === txn.beneficiary_id) || null;
const benAge = ben ? H(ben.added_at) : null;
const watchHit = !!(ben && (d.watchlist || []).some(w => w.masked_account === ben.masked_account));

const devices = (d.devices || []).slice().sort((a, b) => new Date(b.ts) - new Date(a.ts));
const dev = devices[0] || null;
const devAge = dev ? H(dev.first_seen) : null;
const newDevice = devAge !== null && devAge < TH.NEW_DEVICE_HOURS;
const simH = dev ? H(dev.sim_changed_at) : null;
const feedAge = dev ? H(dev.ts) : null;

// Established city pattern excludes the last 24h, so a takeover's own transfers
// cannot redefine the baseline they are measured against.
const cutoff = new Date(txnTs - 24 * 3.6e6);
const cities = new Set(history
  .filter(t => t.city && new Date(t.ts) <= cutoff && new Date(t.ts) >= new Date(now - 30 * 864e5))
  .map(t => t.city));
const mismatch = !!(dev && dev.ip_city && dev.ip_city !== cust.home_city && !cities.has(dev.ip_city));

const missing = [];
if (!history.length) missing.push('transaction_history');
if (!dev) missing.push('device_login_feed');
else if (feedAge > 24) missing.push('device_login_feed_stale_' + Math.floor(feedAge) + 'h');
if (!ben) missing.push('beneficiary_record');
if (!cust.kyc_updated_at) missing.push('kyc_record');

const f = {
  amount: +txn.amount, channel: txn.channel,
  amount_ratio: max90 ? +( +txn.amount / max90).toFixed(2) : null,
  max_90d: +max90.toFixed(2), mean_90d: +mean90.toFixed(2),
  txn_count_90d: history.length,
  velocity_15m_count: burst.length + 1,
  velocity_15m_sum: +(burst.reduce((a, t) => a + +t.amount, 0) + +txn.amount).toFixed(2),
  same_payee_burst_15m: samePayee,
  beneficiary_age_hours: benAge === null ? null : +benAge.toFixed(2),
  beneficiary_masked: ben ? ben.masked_account : null,
  beneficiary_is_new: benAge !== null && benAge < TH.NEW_BENEFICIARY_HOURS,
  prior_payments_to_beneficiary: history.filter(t => t.beneficiary_id === txn.beneficiary_id).length,
  new_device: newDevice,
  device_first_seen_hours: devAge === null ? null : +devAge.toFixed(2),
  sim_change_hours: simH === null ? null : +simH.toFixed(2),
  sim_changed_recently: simH !== null && simH < TH.SIM_CHANGE_HOURS,
  login_city: dev ? dev.ip_city : null, home_city: cust.home_city,
  location_mismatch: mismatch,
  device_feed_age_hours: feedAge === null ? null : +feedAge.toFixed(2),
  watchlist_hit: watchHit,
  data_incomplete: missing.length > 0, missing_sources: missing,
};

// --- PII masking: nothing identifying may reach a model ---------------------
const mask = (s) => {
  if (!s) return s;
  let out = String(s);
  for (const part of String(cust.full_name || '').split(' ')) {
    if (part.length > 2) out = out.replace(new RegExp('\\b' + part + '\\b', 'gi'), '[NAME]');
  }
  return out.replace(/\b\d{9,18}\b/g, '[ACCOUNT]')
            .replace(/\b[\w.\-]+@[\w\-]+\.\w{2,}\b/g, '[EMAIL]');
};

let n = 0;
const items = [];
const add = (kind, text, extra) => { items.push(Object.assign(
  { id: 'E' + (++n), kind, text: mask(text) }, extra || {})); return 'E' + n; };

add('alert', `Alert ${d.alert.id} of type ${d.alert.alert_type} fired by rule "${d.alert.rule_fired}" at ${d.alert.created_at}.`);
add('transaction', `Alerted transaction: ${txn.channel} debit of Rs${(+txn.amount).toFixed(2)} at ${txn.ts} from city ${txn.city}.`);
add('customer', `Customer segment ${cust.segment}, home city ${cust.home_city}, income band ${cust.income_band}, KYC last updated ${cust.kyc_updated_at}.`);
add('history', `Over the last 90 days the customer made ${f.txn_count_90d} outgoing transfers, mean Rs${f.mean_90d}, maximum Rs${f.max_90d}. This transaction is ${f.amount_ratio}x the 90-day maximum.`);
add('velocity', `${f.velocity_15m_count} outgoing transfers totalling Rs${f.velocity_15m_sum} occurred in the 15 minutes up to and including this transaction; ${f.same_payee_burst_15m} of them went to the alerted beneficiary.`);
add('beneficiary', benAge === null ? 'No beneficiary record is available for this transfer.'
  : `Beneficiary ${f.beneficiary_masked} was added ${f.beneficiary_age_hours} hours ago and has received ${f.prior_payments_to_beneficiary} prior payments from this customer.`);
add('device', devAge === null ? 'No device or login record is available.'
  : `Most recent login came from a device first seen ${f.device_first_seen_hours} hours ago (${newDevice ? 'NEW device' : 'established device'}), from city ${f.login_city} against home city ${f.home_city} (${mismatch ? 'MISMATCH' : 'consistent'}).`);
add('sim', simH === null ? 'No SIM change record is available.'
  : `Last SIM change on the account was ${f.sim_change_hours} hours ago.`);
add('watchlist', `Mule watchlist check on ${f.beneficiary_masked}: ${watchHit ? 'MATCH — account is listed.' : 'no match.'}`);

// Node 5 — Injection Scanner. Customer free text is the one attacker-controlled
// field, so it is quarantined rather than inlined.
const PATTERNS = [
  /ignore\s+(all\s+|any\s+)?(previous|prior|above)\s+instructions?/i,
  /disregard\s+(the\s+)?(previous|prior|above|system)/i, /\bsystem\s*:/i,
  /\bassistant\s*:/i, /you\s+are\s+now\b/i, /new\s+instructions?\b/i,
  /mark\s+(this|it)\s+(as\s+)?(safe|genuine|legitimate|low.risk)/i,
  /return\s+decision\s+(proceed|approve)/i, /set\s+confidence\s+to/i,
  /override\s+(the\s+)?(rule|policy|guardrail)/i,
];
const hits = [];
if (txn.remarks) {
  const eid = 'E' + (n + 1);
  for (const rx of PATTERNS) {
    const m = rx.exec(String(txn.remarks));
    if (m) { hits.push({ field: eid, matched: m[0].slice(0, 80) }); break; }
  }
  const safe = String(txn.remarks).replace(/<\/?untrusted>/g, '[untrusted]');
  items.push({ id: eid, kind: 'remarks_untrusted', untrusted: true,
               text: `<untrusted>${mask(safe)}</untrusted>` });
  n++;
}
if (f.data_incomplete) add('data_gap', 'Mandatory evidence is missing or stale: ' + missing.join(', ') + '.');
for (const p of (d.policy_rules || [])) {
  if (['AUTO_PROCEED_MAX','ESCALATE_MIN','MIN_AUTO_CONFIDENCE','AUTO_SAMPLE_RATE','HITL_TIMEOUT_MIN'].includes(p.action)) continue;
  add('policy', `Policy ${p.id}: ${p.rule} (threshold ${p.threshold}).`, { policy_id: p.id });
}

// Node 6 — Hard flags. An LLM may escalate one further; it can never clear one.
const hard = [];
if (watchHit) hard.push({ code: 'HF1', name: 'watchlist_hit',
  detail: `Beneficiary ${f.beneficiary_masked} is on the mule watchlist`, action: 'HOLD+ESCALATE' });
if (f.sim_changed_recently && newDevice && benAge !== null && benAge < 1) hard.push({ code: 'HF2', name: 'ato_pattern',
  detail: `SIM changed ${f.sim_change_hours}h ago, device first seen ${f.device_first_seen_hours}h ago, beneficiary added ${f.beneficiary_age_hours}h ago`, action: 'HOLD+ESCALATE' });
if (samePayee >= TH.SPLIT_BURST_COUNT && f.beneficiary_is_new) hard.push({ code: 'HF3', name: 'split_burst',
  detail: `${samePayee} transfers to the same new beneficiary within 15 minutes totalling Rs${f.velocity_15m_sum}`, action: 'HOLD+ESCALATE' });

return [{ json: Object.assign({}, d, {
  features: f, hard_flags: hard,
  evidence: { items, ids: items.map(i => i.id) },
  evidence_text: items.map(i => i.id + '. ' + i.text).join('\n'),
  injection_flag: hits.length > 0, injection_hits: hits,
}) }];
"""

VALIDATE_OUTPUT = r"""
// Node 12 — Output Validator. Schema, evidence grounding and numeric sanity.
// Mirrors app/guardrails.py validate_output(). One repair retry downstream,
// then the case goes to the exception queue.
const d = $input.first().json;
const ids = new Set(d.evidence.ids);
const f = d.features;
const errors = [];

let obj = null, firstAttempt = true;
const raw = typeof d.model_output === 'string' ? d.model_output : JSON.stringify(d.model_output);
try { obj = JSON.parse(raw); } catch (e) {
  firstAttempt = false;
  const fence = /```(?:json)?\s*([\s\S]+?)```/.exec(raw);
  const brace = raw.indexOf('{');
  try { obj = JSON.parse(fence ? fence[1] : raw.slice(brace, raw.lastIndexOf('}') + 1)); }
  catch (e2) { obj = null; }
}
if (!obj) return [{ json: Object.assign({}, d, {
  validation_ok: false, validation_errors: ['model did not return parseable JSON'],
  json_first_attempt: false }) }];

const DEC = ['PROCEED', 'VERIFY', 'HOLD', 'ESCALATE'];
const decision = String(obj.decision || '').toUpperCase();
if (!DEC.includes(decision)) errors.push('decision must be one of ' + DEC.join('/'));

const risk = Number(obj.risk_score);
if (!isFinite(risk) || risk < 0 || risk > 100) errors.push('risk_score missing or out of range');
const conf = Number(obj.confidence);
if (!isFinite(conf) || conf < 0 || conf > 1) errors.push('confidence missing or out of range');

const rationale = String(obj.rationale || '').trim();
if (!rationale) errors.push('rationale is empty');
else if (rationale.split(/\s+/).length > 150) errors.push('rationale exceeds 120 words');

const cited = Array.isArray(obj.evidence_ids) ? obj.evidence_ids : [];
const unknown = cited.filter(e => !ids.has(e));
if (unknown.length) errors.push('hallucinated evidence IDs: ' + unknown.join(', '));
if (!cited.length && decision !== 'PROCEED') errors.push('non-PROCEED decision cites no evidence');

for (const m of rationale.matchAll(/\bE\d{1,3}\b/g))
  if (!ids.has(m[0])) errors.push('rationale cites unknown evidence ID ' + m[0]);

// Numbers quoted in the prose must exist in the evidence.
const allowed = [f.amount, f.velocity_15m_sum, f.max_90d, f.mean_90d].filter(Boolean);
for (const m of rationale.matchAll(/(?:Rs\.?|INR|₹)\s?([\d,]+(?:\.\d+)?)/g)) {
  const v = parseFloat(m[1].replace(/,/g, ''));
  if (!allowed.some(a => Math.abs(v - a) <= Math.max(1, a * 0.01)))
    errors.push('rationale quotes an amount not in the evidence: ' + v);
}
if (decision === 'PROCEED' && f.data_incomplete)
  errors.push('PROCEED is not permitted while mandatory evidence is missing');

return [{ json: Object.assign({}, d, {
  validation_ok: errors.length === 0, validation_errors: errors,
  json_first_attempt: firstAttempt,
  raw_decision: decision,
  recommendation: errors.length ? null : {
    decision, risk_score: +risk.toFixed(1), confidence: +conf.toFixed(2),
    rationale, evidence_ids: cited,
    missing_information: Array.isArray(obj.missing_information) ? obj.missing_information : [],
    suspected_typology: obj.suspected_typology || 'none' },
}) }];
"""

DECISION_ROUTER = r"""
// Node 13 — Decision Router. Conditions are evaluated in the order given in
// build guide 6.5. Mirrors app/orchestrator.decide_route().
const d = $input.first().json;
const f = d.features, TH = d.thresholds, rec = d.recommendation;
let route, ahe, decision, reason;

if (d.injection_flag) {
  route = 'EXCEPTION'; ahe = 'E'; decision = null;
  reason = 'Prompt injection detected in customer free text; quarantined and referred for human review.';
} else if (!d.validation_ok) {
  route = 'EXCEPTION'; ahe = 'E'; decision = null;
  reason = 'Model output failed validation after retry: ' + (d.validation_errors || []).join('; ');
} else if ((d.hard_flags || []).length) {
  route = 'ESCALATE'; ahe = 'E'; decision = 'ESCALATE';
  reason = 'Hard flag(s) ' + d.hard_flags.map(h => h.code).join(', ') +
           ' fired. Temporary hold placed and the case escalated to L2. A model cannot downgrade a hard flag.';
} else if (f.data_incomplete) {
  route = 'HITL'; ahe = 'H'; decision = rec.decision === 'PROCEED' ? 'VERIFY' : rec.decision;
  reason = 'Mandatory evidence is missing or stale (' + f.missing_sources.join(', ') +
           '), so automatic clearance is blocked and an investigator must decide.';
} else if (rec.risk_score >= TH.ESCALATE_MIN || rec.decision === 'ESCALATE') {
  route = 'ESCALATE'; ahe = 'E'; decision = 'ESCALATE';
  reason = 'Risk score ' + rec.risk_score + ' is at or above the escalation threshold of ' +
           TH.ESCALATE_MIN + ', or the agent recommended escalation. L2 approval is required.';
} else if (rec.risk_score >= TH.AUTO_PROCEED_MAX || rec.confidence < TH.MIN_AUTO_CONFIDENCE
           || ['VERIFY', 'HOLD'].includes(rec.decision)) {
  route = 'HITL'; ahe = 'H'; decision = rec.decision;
  reason = 'Risk score ' + rec.risk_score + ' with confidence ' + rec.confidence +
           ' falls in the human review band, or the recommendation was ' + rec.decision +
           '. An investigator must approve.';
} else {
  route = 'AUTO_PROCEED'; ahe = 'A'; decision = 'PROCEED';
  reason = 'Risk score ' + rec.risk_score + ' is below ' + TH.AUTO_PROCEED_MAX +
           ' with confidence ' + rec.confidence + ' and no flags, so the alert is cleared automatically.';
  // A sampled share of automatic decisions is reviewed anyway, so automation
  // bias has something to correct against.
  if (Math.random() < TH.AUTO_SAMPLE_RATE) {
    route = 'HITL'; ahe = 'H'; d.sampled = true;
    reason += ' Selected by the random sample of automatic decisions.';
  }
}
return [{ json: Object.assign({}, d, { route, ahe, effective_decision: decision, route_reason: reason }) }];
"""

APPROVAL_MATRIX = r"""
// Node 17 — Apply Decision. Enforces the approval matrix AFTER the human
// responds, so a forged resume call cannot bypass it.
const d = $input.first().json;
const body = d.body || d;
const MATRIX = { L1: ['PROCEED','VERIFY','ESCALATE'], L2: ['PROCEED','VERIFY','HOLD','ESCALATE'],
                 admin: ['PROCEED','VERIFY','HOLD','ESCALATE'] };
const role = body.role, decision = String(body.decision || '').toUpperCase();
let outcome = decision === 'ESCALATE' ? 'ESCALATE'
            : decision === 'APPROVE' ? (d.effective_decision || '').toUpperCase()
            : String(body.final_outcome || '').toUpperCase();

const fail = (m) => { throw new Error(m); };
if (!MATRIX[role]) fail('unknown role ' + role);
if (decision === 'OVERRIDE' && !String(body.reason || '').trim())
  fail('OVERRIDE requires a reason');
if (!MATRIX[role].includes(outcome))
  fail('role ' + role + ' may not authorise ' + outcome + '. Holding funds and closing an escalation require L2.');
if ((d.hard_flags || []).length && ['PROCEED','VERIFY'].includes(outcome) && role === 'L1')
  fail('this case carries deterministic hard flags and cannot be cleared below L2');

return [{ json: Object.assign({}, d, {
  final_outcome: outcome, human_decision: decision,
  human_reason: body.reason || null, decided_by: body.investigator_id, decided_role: role,
  cbs_action: { PROCEED: 'release', VERIFY: 'verify', HOLD: 'hold', ESCALATE: 'escalate' }[outcome],
}) }];
"""

AGENT_PROMPTS = {}
for agent in ("transaction_analysis", "customer_behaviour", "risk_policy", "recommendation"):
    with open(os.path.join(os.path.dirname(HERE), "prompts", "v3", agent + ".txt")) as fh:
        AGENT_PROMPTS[agent] = fh.read().strip()


def node(name, ntype, pos, params, tv=1, extra=None):
    n = {"parameters": params, "id": name.lower().replace(" ", "-").replace("/", "-"),
         "name": name, "type": ntype, "typeVersion": tv, "position": pos}
    if extra:
        n.update(extra)
    return n


def code_node(name, pos, js):
    return node(name, "n8n-nodes-base.code", pos, {"jsCode": js.strip()}, tv=2)


def sb(name, pos, table, filters):
    """Supabase REST read via HTTP Request, so no extra credential type is needed."""
    return node(name, "n8n-nodes-base.httpRequest", pos, {
        "method": "GET",
        "url": "={{ $env.SUPABASE_URL }}/rest/v1/" + table,
        "sendQuery": True,
        "queryParameters": {"parameters": filters},
        "sendHeaders": True,
        "headerParameters": {"parameters": [
            {"name": "apikey", "value": "={{ $env.SUPABASE_SERVICE_KEY }}"},
            {"name": "Authorization", "value": "=Bearer {{ $env.SUPABASE_SERVICE_KEY }}"}]},
        "options": {}}, tv=4.2)


def llm_node(name, pos, agent):
    """One HTTP node per agent. The model name comes from the webhook payload, so
    a single workflow serves every model and the comparison stays identical."""
    return node(name, "n8n-nodes-base.httpRequest", pos, {
        "method": "POST",
        "url": "={{ $env.LLM_BASE_URL }}/chat/completions",
        "sendBody": True,
        "specifyBody": "json",
        "jsonBody": "={{ JSON.stringify({\n"
                    "  model: $('Investigate Alert').first().json.body.model,\n"
                    "  temperature: 0,\n"
                    "  max_tokens: 900,\n"
                    "  messages: [\n"
                    "    { role: 'system', content: $json.system_prompt_" + agent + " },\n"
                    "    { role: 'user',   content: $json.user_message_" + agent + " }\n"
                    "  ]\n"
                    "}) }}",
        "sendHeaders": True,
        # The last two are OpenRouter attribution headers — ignored by Ollama and
        # Groq, so one node serves every provider.
        "headerParameters": {"parameters": [
            {"name": "Authorization", "value": "=Bearer {{ $env.LLM_API_KEY }}"},
            {"name": "X-Title", "value": "={{ $env.OPENROUTER_APP_NAME || 'FraudSentinel' }}"},
            {"name": "HTTP-Referer", "value": "={{ $env.OPENROUTER_SITE_URL || 'http://localhost:8000' }}"}]},
        "options": {"timeout": 120000, "response": {"response": {}}}}, tv=4.2)


def build_main():
    P = AGENT_PROMPTS
    nodes = [
        node("Investigate Alert", "n8n-nodes-base.webhook", [-1120, 300], {
            "httpMethod": "POST", "path": "investigate",
            "responseMode": "responseNode", "options": {}}, tv=2,
            extra={"webhookId": "fraudsentinel-investigate"}),

        code_node("Validate Payload", [-920, 300], r"""
// Node 2 — schema check. Unknown models or alert IDs are rejected here rather
// than being passed to a model.
const b = $input.first().json.body || {};
// Allowlist. Set FS_MODELS in n8n to the exact tags or slugs you are running;
// the fallback covers both the local Ollama trio and their OpenRouter slugs.
const MODELS = (($env.FS_MODELS) || 'mistral:7b,llama3.1:8b,qwen2.5:7b,'
  + 'mistralai/mistral-nemo,meta-llama/llama-3.1-8b-instruct,qwen/qwen-2.5-7b-instruct'
  ).split(',').map(m => m.trim());
const errors = [];
if (!b.alert_id) errors.push('alert_id is required');
if (!b.model) errors.push('model is required');
else if (!MODELS.includes(b.model)) errors.push('model ' + b.model + ' is not in the approved list');
if (b.mode && !['live','benchmark'].includes(b.mode)) errors.push('mode must be live or benchmark');
if (b.prompt_version && !['v1','v2','v3'].includes(b.prompt_version)) errors.push('unknown prompt_version');
if (errors.length) throw new Error('payload rejected: ' + errors.join('; '));
return [{ json: { alert_id: b.alert_id, model: b.model, mode: b.mode || 'live',
                  prompt_version: b.prompt_version || 'v3',
                  case_id: crypto.randomUUID(), reference_now: new Date().toISOString() } }];
"""),

        sb("Fetch Alert", [-720, 120], "alerts",
           [{"name": "id", "value": "=eq.{{ $json.alert_id }}"}, {"name": "select", "value": "*"}]),
        sb("Fetch Transactions", [-720, 240], "transactions",
           [{"name": "customer_id", "value": "=eq.{{ $json.customer_id }}"},
            {"name": "order", "value": "ts.desc"}, {"name": "limit", "value": "400"}]),
        sb("Fetch Customer", [-720, 360], "customers",
           [{"name": "id", "value": "=eq.{{ $json.customer_id }}"}]),
        sb("Fetch Devices", [-720, 480], "devices_logins",
           [{"name": "customer_id", "value": "=eq.{{ $json.customer_id }}"},
            {"name": "order", "value": "ts.desc"}]),
        sb("Fetch Beneficiaries & Watchlist", [-720, 600], "beneficiaries",
           [{"name": "customer_id", "value": "=eq.{{ $json.customer_id }}"},
            {"name": "select", "value": "*,mule_watchlist(*)"}]),
        sb("Fetch Policy Rules", [-720, 720], "policy_rules",
           [{"name": "active", "value": "eq.true"}]),

        code_node("Assemble Evidence Pack", [-500, 300], ASSEMBLE_PACK),

        code_node("Orchestrator Router", [-300, 300], r"""
// Node 7 — sequences the agents and attaches each agent's prompt. Routing by
// alert type and data completeness happens here; the agents themselves are a
// fixed chain, as the brief's illustrative flow specifies.
const d = $input.first().json;
const PROMPTS = __PROMPTS__;
const v = d.prompt_version || 'v3';
const out = Object.assign({}, d);
for (const [k, byVersion] of Object.entries(PROMPTS)) out['system_prompt_' + k] = byVersion[v] || byVersion.v3;
out.user_message_transaction_analysis =
  'EVIDENCE PACK\n' + d.evidence_text + '\n\nReturn only the JSON described in your instructions.';
return [{ json: out }];
""".replace("__PROMPTS__", json.dumps({k: {"v3": v} for k, v in P.items()}))),

        llm_node("Transaction Analysis Agent", [-100, 300], "transaction_analysis"),
        code_node("Carry Transaction Findings", [60, 300], r"""
const d = $('Orchestrator Router').first().json;
const txt = $input.first().json.choices[0].message.content;
let o = null; try { o = JSON.parse(txt); } catch (e) {
  const m = /\{[\s\S]*\}/.exec(txt); if (m) { try { o = JSON.parse(m[0]); } catch (e2) {} } }
const sum = (x) => !x ? '(unavailable)' :
  [...(x.findings||[]).map(s=>'- '+s), x.sub_score!=null?'- sub_score: '+x.sub_score:''].filter(Boolean).join('\n');
return [{ json: Object.assign({}, d, { out_transaction_analysis: o,
  user_message_customer_behaviour: 'EVIDENCE PACK\n' + d.evidence_text +
    '\n\nTRANSACTION ANALYSIS AGENT FINDINGS\n' + sum(o) +
    '\n\nReturn only the JSON described in your instructions.' }) }];
"""),

        llm_node("Customer Behaviour Agent", [260, 300], "customer_behaviour"),
        code_node("Carry Behaviour Findings", [420, 300], r"""
const d = $('Carry Transaction Findings').first().json;
const txt = $input.first().json.choices[0].message.content;
let o = null; try { o = JSON.parse(txt); } catch (e) {
  const m = /\{[\s\S]*\}/.exec(txt); if (m) { try { o = JSON.parse(m[0]); } catch (e2) {} } }
const sum = (x) => !x ? '(unavailable)' :
  [...(x.findings||[]).map(s=>'- '+s), x.sub_score!=null?'- sub_score: '+x.sub_score:''].filter(Boolean).join('\n');
return [{ json: Object.assign({}, d, { out_customer_behaviour: o,
  user_message_risk_policy: 'EVIDENCE PACK\n' + d.evidence_text +
    '\n\nTRANSACTION ANALYSIS AGENT FINDINGS\n' + sum(d.out_transaction_analysis) +
    '\n\nCUSTOMER BEHAVIOUR AGENT FINDINGS\n' + sum(o) +
    '\n\nDETERMINISTIC HARD FLAGS (already decided, cannot be downgraded)\n' +
      ((d.hard_flags||[]).map(h=>h.code+' '+h.detail).join(', ') || 'none') +
    '\n\nReturn only the JSON described in your instructions.' }) }];
"""),

        llm_node("Risk/Policy Agent", [620, 300], "risk_policy"),
        code_node("Carry Policy Findings", [780, 300], r"""
const d = $('Carry Behaviour Findings').first().json;
const txt = $input.first().json.choices[0].message.content;
let o = null; try { o = JSON.parse(txt); } catch (e) {
  const m = /\{[\s\S]*\}/.exec(txt); if (m) { try { o = JSON.parse(m[0]); } catch (e2) {} } }
const sum = (x) => !x ? '(unavailable)' : [...(x.findings||[]).map(s=>'- '+s),
  ...(x.policies_triggered||[]).map(p=>'- policy '+p.policy_id+': '+p.reason),
  x.sub_score!=null?'- sub_score: '+x.sub_score:''].filter(Boolean).join('\n');
let msg = 'EVIDENCE PACK\n' + d.evidence_text +
  '\n\nTRANSACTION ANALYSIS AGENT FINDINGS\n' + sum(d.out_transaction_analysis) +
  '\n\nCUSTOMER BEHAVIOUR AGENT FINDINGS\n' + sum(d.out_customer_behaviour) +
  '\n\nRISK/POLICY AGENT FINDINGS\n' + sum(o) +
  '\n\nDETERMINISTIC HARD FLAGS (cannot be downgraded)\n' +
    ((d.hard_flags||[]).map(h=>h.code+' '+h.detail).join(', ') || 'none');
if (d.features.data_incomplete) msg += '\n\nMANDATORY EVIDENCE MISSING: ' + d.features.missing_sources.join(', ');
msg += '\n\nReturn only the JSON described in your instructions.';
return [{ json: Object.assign({}, d, { out_risk_policy: o, user_message_recommendation: msg }) }];
"""),

        llm_node("Recommendation Agent", [980, 300], "recommendation"),
        code_node("Collect Recommendation", [1140, 300], r"""
const d = $('Carry Policy Findings').first().json;
return [{ json: Object.assign({}, d, { model_output: $input.first().json.choices[0].message.content }) }];
"""),

        code_node("Output Validator", [1320, 300], VALIDATE_OUTPUT),

        node("Valid?", "n8n-nodes-base.if", [1500, 300], {
            "conditions": {"options": {"caseSensitive": True, "version": 2},
                           "combinator": "and",
                           "conditions": [{"operator": {"type": "boolean", "operation": "true"},
                                           "leftValue": "={{ $json.validation_ok }}",
                                           "rightValue": ""}]},
            "options": {}}, tv=2),

        code_node("Repair Prompt", [1500, 500], r"""
// One repair retry, then the exception queue. A model gets exactly one chance
// to fix its own malformed output.
const d = $input.first().json;
return [{ json: Object.assign({}, d, {
  user_message_recommendation: d.user_message_recommendation +
    '\n\nYOUR PREVIOUS REPLY WAS REJECTED FOR: ' + (d.validation_errors || []).join('; ') +
    '\nReturn ONLY valid JSON matching the schema. Cite only evidence IDs that appear above.',
  repair_attempt: true }) }];
"""),
        llm_node("Recommendation Agent (retry)", [1700, 500], "recommendation"),
        code_node("Collect Retry", [1880, 500], r"""
const d = $('Repair Prompt').first().json;
return [{ json: Object.assign({}, d, { model_output: $input.first().json.choices[0].message.content }) }];
"""),
        code_node("Output Validator (retry)", [2060, 500], VALIDATE_OUTPUT),

        code_node("Decision Router", [1700, 300], DECISION_ROUTER),

        node("Route", "n8n-nodes-base.switch", [1880, 300], {
            "rules": {"values": [
                {"conditions": {"options": {"version": 2}, "combinator": "and", "conditions": [
                    {"leftValue": "={{ $json.route }}", "rightValue": "AUTO_PROCEED",
                     "operator": {"type": "string", "operation": "equals"}}]},
                 "outputKey": "AUTO_PROCEED (A)"},
                {"conditions": {"options": {"version": 2}, "combinator": "and", "conditions": [
                    {"leftValue": "={{ $json.route }}", "rightValue": "HITL",
                     "operator": {"type": "string", "operation": "equals"}}]},
                 "outputKey": "HITL (H)"},
                {"conditions": {"options": {"version": 2}, "combinator": "and", "conditions": [
                    {"leftValue": "={{ $json.route }}", "rightValue": "ESCALATE",
                     "operator": {"type": "string", "operation": "equals"}}]},
                 "outputKey": "ESCALATE (E)"},
                {"conditions": {"options": {"version": 2}, "combinator": "and", "conditions": [
                    {"leftValue": "={{ $json.route }}", "rightValue": "EXCEPTION",
                     "operator": {"type": "string", "operation": "equals"}}]},
                 "outputKey": "EXCEPTION (E)"}]},
            "options": {"allMatchingOutputs": False}}, tv=3),

        node("Create Case", "n8n-nodes-base.httpRequest", [2080, 180], {
            "method": "POST", "url": "={{ $env.SUPABASE_URL }}/rest/v1/cases",
            "sendBody": True, "specifyBody": "json",
            "jsonBody": "={{ JSON.stringify({\n"
                        "  id: $json.case_id, alert_id: $json.alert_id, model: $json.model,\n"
                        "  prompt_version: $json.prompt_version,\n"
                        "  evidence: $json.evidence, features: $json.features,\n"
                        "  hard_flags: $json.hard_flags,\n"
                        "  agent_outputs: { transaction_analysis: $json.out_transaction_analysis,\n"
                        "                   customer_behaviour: $json.out_customer_behaviour,\n"
                        "                   risk_policy: $json.out_risk_policy,\n"
                        "                   recommendation: $json.recommendation },\n"
                        "  recommendation: $json.recommendation, raw_decision: $json.raw_decision,\n"
                        "  route: $json.route, ahe: $json.ahe, sampled: !!$json.sampled,\n"
                        "  status: $json.route === 'AUTO_PROCEED' ? 'open' : 'awaiting_human',\n"
                        "  resume_url: $execution.resumeUrl\n"
                        "}) }}",
            "sendHeaders": True,
            "headerParameters": {"parameters": [
                {"name": "apikey", "value": "={{ $env.SUPABASE_SERVICE_KEY }}"},
                {"name": "Authorization", "value": "=Bearer {{ $env.SUPABASE_SERVICE_KEY }}"},
                {"name": "Prefer", "value": "return=representation"}]},
            "options": {}}, tv=4.2),

        node("Respond to UI", "n8n-nodes-base.respondToWebhook", [2280, 180], {
            "respondWith": "json",
            "responseBody": "={{ JSON.stringify({ case_id: $json.case_id, status: 'processing',"
                            " route: $json.route, ahe: $json.ahe,"
                            " decision: $json.effective_decision, reason: $json.route_reason }) }}",
            "options": {}}, tv=1.1),

        # A hard flag is deterministic and does not depend on the model producing
        # valid output. An exception raised AFTER the flags were computed must
        # still place the temporary hold, or a model returning garbage on a
        # watchlist hit would let the money leave while a human reads the queue.
        node("Hard flags present?", "n8n-nodes-base.if", [2080, 620], {
            "conditions": {"options": {"caseSensitive": True, "version": 2},
                           "combinator": "and",
                           "conditions": [{"operator": {"type": "number", "operation": "gt"},
                                           "leftValue": "={{ ($json.hard_flags || []).length }}",
                                           "rightValue": 0}]},
            "options": {}}, tv=2),

        node("Hard Flag Temporary Hold", "n8n-nodes-base.executeWorkflow", [2080, 420], {
            "workflowId": "={{ $env.MOCK_CBS_WORKFLOW_ID }}",
            "workflowInputs": {"value": {
                "case_id": "={{ $json.case_id }}", "action": "hold",
                "reason": "Deterministic hard flag: temporary hold pending L2 review."}},
            "options": {}}, tv=1.2),

        node("Await Investigator", "n8n-nodes-base.wait", [2280, 420], {
            "resume": "webhook", "limitWaitTime": True, "resumeAmount": 30,
            "resumeUnit": "minutes", "options": {}}, tv=1.1,
             extra={"webhookId": "fraudsentinel-resume"}),

        code_node("Apply Decision", [2480, 420], APPROVAL_MATRIX),

        node("Execute Action", "n8n-nodes-base.executeWorkflow", [2680, 420], {
            "workflowId": "={{ $env.MOCK_CBS_WORKFLOW_ID }}",
            "workflowInputs": {"value": {
                "case_id": "={{ $json.case_id }}", "action": "={{ $json.cbs_action }}",
                "reason": "={{ $json.human_reason }}"}},
            "options": {}}, tv=1.2),

        node("Close Case", "n8n-nodes-base.httpRequest", [2880, 420], {
            "method": "PATCH",
            "url": "={{ $env.SUPABASE_URL }}/rest/v1/cases?id=eq.{{ $json.case_id }}",
            "sendBody": True, "specifyBody": "json",
            "jsonBody": "={{ JSON.stringify({ status:'closed', human_decision: $json.human_decision,"
                        " final_outcome: $json.final_outcome, human_reason: $json.human_reason,"
                        " decided_by: $json.decided_by, decided_role: $json.decided_role,"
                        " closed_at: new Date().toISOString() }) }}",
            "sendHeaders": True,
            "headerParameters": {"parameters": [
                {"name": "apikey", "value": "={{ $env.SUPABASE_SERVICE_KEY }}"},
                {"name": "Authorization", "value": "=Bearer {{ $env.SUPABASE_SERVICE_KEY }}"}]},
            "options": {}}, tv=4.2),

        node("Auto Proceed — Release", "n8n-nodes-base.executeWorkflow", [2480, 180], {
            "workflowId": "={{ $env.MOCK_CBS_WORKFLOW_ID }}",
            "workflowInputs": {"value": {
                "case_id": "={{ $json.case_id }}", "action": "release",
                "reason": "Automatic clearance within policy thresholds."}},
            "options": {}}, tv=1.2),

        node("Audit Logger", "n8n-nodes-base.executeWorkflow", [2880, 180], {
            "workflowId": "={{ $env.AUDIT_WORKFLOW_ID }}",
            "workflowInputs": {"value": {
                "case_id": "={{ $json.case_id }}", "step": "={{ $json.route }}",
                "actor": "orchestrator", "model": "={{ $json.model }}",
                "prompt_version": "={{ $json.prompt_version }}",
                "output": "={{ JSON.stringify($json.recommendation) }}"}},
            "options": {}}, tv=1.2),
    ]

    c = lambda *names: {"main": [[{"node": n, "type": "main", "index": 0} for n in names]]}
    connections = {
        "Investigate Alert": c("Validate Payload"),
        "Validate Payload": {"main": [[{"node": n, "type": "main", "index": 0} for n in (
            "Fetch Alert", "Fetch Transactions", "Fetch Customer", "Fetch Devices",
            "Fetch Beneficiaries & Watchlist", "Fetch Policy Rules")]]},
        "Fetch Alert": c("Assemble Evidence Pack"),
        "Fetch Transactions": c("Assemble Evidence Pack"),
        "Fetch Customer": c("Assemble Evidence Pack"),
        "Fetch Devices": c("Assemble Evidence Pack"),
        "Fetch Beneficiaries & Watchlist": c("Assemble Evidence Pack"),
        "Fetch Policy Rules": c("Assemble Evidence Pack"),
        "Assemble Evidence Pack": c("Orchestrator Router"),
        "Orchestrator Router": c("Transaction Analysis Agent"),
        "Transaction Analysis Agent": c("Carry Transaction Findings"),
        "Carry Transaction Findings": c("Customer Behaviour Agent"),
        "Customer Behaviour Agent": c("Carry Behaviour Findings"),
        "Carry Behaviour Findings": c("Risk/Policy Agent"),
        "Risk/Policy Agent": c("Carry Policy Findings"),
        "Carry Policy Findings": c("Recommendation Agent"),
        "Recommendation Agent": c("Collect Recommendation"),
        "Collect Recommendation": c("Output Validator"),
        "Output Validator": c("Valid?"),
        "Valid?": {"main": [
            [{"node": "Decision Router", "type": "main", "index": 0}],
            [{"node": "Repair Prompt", "type": "main", "index": 0}]]},
        "Repair Prompt": c("Recommendation Agent (retry)"),
        "Recommendation Agent (retry)": c("Collect Retry"),
        "Collect Retry": c("Output Validator (retry)"),
        "Output Validator (retry)": c("Decision Router"),
        "Decision Router": c("Route"),
        "Route": {"main": [
            [{"node": "Create Case", "type": "main", "index": 0}],                    # AUTO_PROCEED
            [{"node": "Create Case", "type": "main", "index": 0}],                    # HITL
            [{"node": "Hard Flag Temporary Hold", "type": "main", "index": 0}],       # ESCALATE
            [{"node": "Hard flags present?", "type": "main", "index": 0}]]},          # EXCEPTION
        "Hard flags present?": {"main": [
            [{"node": "Hard Flag Temporary Hold", "type": "main", "index": 0}],       # true  -> hold first
            [{"node": "Create Case", "type": "main", "index": 0}]]},                  # false -> straight to the queue
        "Hard Flag Temporary Hold": c("Create Case"),
        "Create Case": {"main": [[
            {"node": "Respond to UI", "type": "main", "index": 0}]]},
        "Respond to UI": {"main": [[
            {"node": "Auto Proceed — Release", "type": "main", "index": 0},
            {"node": "Await Investigator", "type": "main", "index": 0}]]},
        "Auto Proceed — Release": c("Audit Logger"),
        "Await Investigator": c("Apply Decision"),
        "Apply Decision": c("Execute Action"),
        "Execute Action": c("Close Case"),
        "Close Case": c("Audit Logger"),
    }

    return {"name": "FraudSentinel — Main Investigation Workflow",
            "nodes": nodes, "connections": connections,
            "settings": {"executionOrder": "v1",
                         "errorWorkflow": "={{ $env.ERROR_WORKFLOW_ID }}",
                         "saveDataErrorExecution": "all",
                         "saveDataSuccessExecution": "all",
                         "saveExecutionProgress": True},
            "pinData": {}, "active": False,
            "tags": [{"name": "fraudsentinel"}],
            "meta": {"description":
                     "Fraud alert -> orchestrator -> 4 agents -> guardrails -> "
                     "route (A/H/E) -> human approval -> mock core banking -> audit. "
                     "Node numbering follows Section 6.4 of the build guide."}}


def build_error():
    return {"name": "FraudSentinel — Error Workflow",
            "nodes": [
                node("Error Trigger", "n8n-nodes-base.errorTrigger", [-200, 300], {}),
                code_node("Shape Exception", [0, 300], r"""
// Node 20 — any node failure becomes an exception case for a human, never a
// silently dropped alert.
const e = $input.first().json;
return [{ json: {
  case_id: e.execution?.id || crypto.randomUUID(),
  alert_id: e.execution?.data?.alert_id || null,
  route: 'EXCEPTION', ahe: 'E', status: 'awaiting_human',
  recommendation: { exception_stage: e.execution?.lastNodeExecuted || 'unknown',
                    exception_reason: e.execution?.error?.message || 'workflow failure' },
} }];
"""),
                node("Create Exception Case", "n8n-nodes-base.httpRequest", [200, 300], {
                    "method": "POST", "url": "={{ $env.SUPABASE_URL }}/rest/v1/cases",
                    "sendBody": True, "specifyBody": "json",
                    "jsonBody": "={{ JSON.stringify($json) }}",
                    "sendHeaders": True,
                    "headerParameters": {"parameters": [
                        {"name": "apikey", "value": "={{ $env.SUPABASE_SERVICE_KEY }}"},
                        {"name": "Authorization", "value": "=Bearer {{ $env.SUPABASE_SERVICE_KEY }}"}]},
                    "options": {}}, tv=4.2)],
            "connections": {
                "Error Trigger": {"main": [[{"node": "Shape Exception", "type": "main", "index": 0}]]},
                "Shape Exception": {"main": [[{"node": "Create Exception Case", "type": "main", "index": 0}]]}},
            "settings": {"executionOrder": "v1"}, "active": False,
            "tags": [{"name": "fraudsentinel"}]}


def build_cbs():
    return {"name": "FraudSentinel — Mock Core Banking API",
            "nodes": [
                node("When Called", "n8n-nodes-base.executeWorkflowTrigger", [-200, 300],
                     {"workflowInputs": {"values": [
                         {"name": "case_id"}, {"name": "action"}, {"name": "reason"}]}}, tv=1.1),
                code_node("Perform Action", [0, 300], r"""
// Mock core banking. Agents cannot reach this workflow: only the post-approval
// branch and the deterministic hard-flag branch call it (guide 6.8).
const { case_id, action, reason } = $input.first().json;
const RESULTS = {
  release:  { status: 'released',     message: 'Debit released to the beneficiary.' },
  verify:   { status: 'step_up_sent', message: 'OTP / call-back verification raised.' },
  hold:     { status: 'held',         message: 'Temporary hold placed. Not an account closure.' },
  escalate: { status: 'escalated',    message: 'Referred to L2 and the AML queue.' },
};
if (!RESULTS[action]) throw new Error('unknown core banking action: ' + action);
return [{ json: { case_id, action, reason,
  response: Object.assign({ reference: 'CBS-' + crypto.randomUUID().slice(0, 10).toUpperCase() },
                          RESULTS[action]) } }];
"""),
                node("Write Action Row", "n8n-nodes-base.httpRequest", [200, 300], {
                    "method": "POST", "url": "={{ $env.SUPABASE_URL }}/rest/v1/actions",
                    "sendBody": True, "specifyBody": "json",
                    "jsonBody": "={{ JSON.stringify({ case_id: $json.case_id, action: $json.action,"
                                " endpoint: '/cbs/' + $json.action, request: { reason: $json.reason },"
                                " response: $json.response }) }}",
                    "sendHeaders": True,
                    "headerParameters": {"parameters": [
                        {"name": "apikey", "value": "={{ $env.SUPABASE_SERVICE_KEY }}"},
                        {"name": "Authorization", "value": "=Bearer {{ $env.SUPABASE_SERVICE_KEY }}"}]},
                    "options": {}}, tv=4.2)],
            "connections": {
                "When Called": {"main": [[{"node": "Perform Action", "type": "main", "index": 0}]]},
                "Perform Action": {"main": [[{"node": "Write Action Row", "type": "main", "index": 0}]]}},
            "settings": {"executionOrder": "v1"}, "active": False,
            "tags": [{"name": "fraudsentinel"}]}


if __name__ == "__main__":
    for fname, wf in [("main_workflow.json", build_main()),
                      ("error_workflow.json", build_error()),
                      ("mock_cbs.json", build_cbs())]:
        with open(os.path.join(HERE, fname), "w") as fh:
            json.dump(wf, fh, indent=2, ensure_ascii=False)
        print(f"{fname:<22} {len(wf['nodes']):>2} nodes")
