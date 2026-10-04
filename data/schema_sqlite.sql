-- FraudSentinel — SQLite schema (local dev mirror of data/schema.sql)
-- Section 6.1 of the build guide. All data is SYNTHETIC.

create table if not exists customers (
  id            text primary key,
  full_name     text not null,
  segment       text not null,            -- retail | affluent | senior | student
  home_city     text not null,
  income_band   text not null,
  kyc_updated_at text,
  created_at    text default (datetime('now'))
);

create table if not exists beneficiaries (
  id             text primary key,
  customer_id    text references customers(id),
  masked_account text not null,
  nickname       text,
  added_at       text not null
);

create table if not exists transactions (
  id             text primary key,
  customer_id    text references customers(id),
  channel        text not null,           -- UPI | IMPS | NETBANKING
  amount         real not null,
  beneficiary_id text,
  remarks        text,
  city           text,
  direction      text default 'OUT',      -- OUT | IN
  ts             text not null
);

create table if not exists devices_logins (
  id            text primary key,
  customer_id   text references customers(id),
  device_id     text not null,
  first_seen    text not null,
  ip_city       text,
  sim_changed_at text,
  ts            text not null
);

create table if not exists mule_watchlist (
  masked_account text primary key,
  source         text not null,           -- internal | NPCI | 1930_helpline
  listed_at      text not null
);

create table if not exists policy_rules (
  id        text primary key,
  rule      text not null,
  threshold real,
  action    text not null,
  active    integer default true
);

create table if not exists alerts (
  id         text primary key,
  customer_id text references customers(id),
  txn_id     text references transactions(id),
  alert_type text not null,               -- NEW_BENEFICIARY | HIGH_AMOUNT | NEW_DEVICE | VELOCITY | GEO_ANOMALY
  rule_fired text not null,
  status     text default 'new',          -- new | investigating | closed
  created_at text default (datetime('now'))
);

create table if not exists cases (
  id             text primary key,
  alert_id       text references alerts(id),
  model          text,
  prompt_version text,
  evidence       text,
  features       text,
  hard_flags     text,
  agent_outputs  text,
  recommendation text,
  raw_decision   text,                    -- model decision BEFORE guardrails
  route          text,                    -- AUTO_PROCEED | HITL | ESCALATE | EXCEPTION
  ahe            text,                    -- A | H | E
  status         text default 'open',     -- open | awaiting_human | closed
  resume_token   text,
  human_decision text,                    -- APPROVE | OVERRIDE | ESCALATE
  final_outcome  text,                    -- PROCEED | VERIFY | HOLD | ESCALATE
  human_reason   text,
  decided_by     text,
  decided_role   text,
  sampled        integer default false,   -- picked by the 10% auto-decision sample
  latency_ms     integer,
  created_at     text default (datetime('now')),
  closed_at      text
);

create table if not exists actions (
  id          text primary key,
  case_id     text references cases(id),
  action      text not null,              -- release | verify | hold | escalate
  endpoint    text,
  request     text,
  response    text,
  executed_at text default (datetime('now'))
);

create table if not exists audit_log (
  id             integer primary key autoincrement,
  case_id        uuid,
  step           text not null,
  actor          text not null,           -- orchestrator | agent | human | system
  model          text,
  prompt_version text,
  input_hash     text,
  output         text,
  latency_ms     integer,
  ts             text default (datetime('now'))
);

create table if not exists benchmark_results (
  id            integer primary key autoincrement,
  run_id        text,
  model         text,
  case_id       text,
  run_index     int,
  expected      text,
  raw_decision  text,
  final_route   text,
  risk_score    real,
  confidence    real,
  json_valid    integer,
  hallucinated  integer,
  latency_ms    integer,
  ts            text default (datetime('now'))
);

create index if not exists idx_txn_cust_ts on transactions(customer_id, ts desc);
create index if not exists idx_dev_cust_ts on devices_logins(customer_id, ts desc);
create index if not exists idx_audit_case  on audit_log(case_id, ts);
create index if not exists idx_cases_route on cases(route, status);

-- Decision thresholds live in policy_rules so an admin can change them from the UI
-- without editing the workflow (guide Section 6.5). Changes are written to audit_log.
insert or ignore into policy_rules (id, rule, threshold, action) values
  ('PR-01','Auto-proceed only below this risk score',           30,  'AUTO_PROCEED_MAX'),
  ('PR-02','Escalate at or above this risk score',              60,  'ESCALATE_MIN'),
  ('PR-03','Minimum confidence required to auto-proceed',      0.8,  'MIN_AUTO_CONFIDENCE'),
  ('PR-04','Share of auto-decisions sampled for human review', 0.10, 'AUTO_SAMPLE_RATE'),
  ('PR-05','Beneficiary younger than this is high risk (h)',    24,  'NEW_BENEFICIARY_HOURS'),
  ('PR-06','SIM change within this window is high risk (h)',    24,  'SIM_CHANGE_HOURS'),
  ('PR-07','Device newer than this is a new device (h)',        72,  'NEW_DEVICE_HOURS'),
  ('PR-08','Transfers above this multiple of 90d max are high', 3.0, 'AMOUNT_RATIO_HIGH'),
  ('PR-09','Transfers to one new payee in 15m that is a burst',  3,  'SPLIT_BURST_COUNT'),
  ('PR-10','Investigator SLA before auto-escalation (minutes)', 30,  'HITL_TIMEOUT_MIN')
;
