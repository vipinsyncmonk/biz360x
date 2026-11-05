-- =====================================================================
-- Part 02 · Compliance engine, approvals, signatures, tasks
-- =====================================================================
-- The engine works on three things:
--   compliance_rules  — versioned, statute-linked rule definitions (global)
--   entity_facts      — time-stamped facts about an entity (turnover, headcount…)
--   obligations       — concrete instances the engine derives for a period
-- Applicability and due dates are expressed as JSON so a rule change ships
-- as data (a new rule version), not as a code release.
-- =====================================================================

CREATE TYPE rule_frequency AS ENUM ('one_time','monthly','quarterly','half_yearly','annual','per_event','per_document');

CREATE TABLE compliance_rules (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  code             text NOT NULL,                 -- 'GST.GSTR1.MONTHLY'
  version          int  NOT NULL,
  title            text NOT NULL,                 -- 'GSTR-1 · outward supplies'
  form_code        text,                          -- 'GSTR-1', 'AOC-4', 'ECR'
  authority_code   text NOT NULL REFERENCES ref_authorities(code),
  statute_ref      text NOT NULL,                 -- 'CGST Act s.37; CGST Rules r.59'
  frequency        rule_frequency NOT NULL,
  per_registration registration_kind,             -- one obligation per GSTIN/TAN… or NULL = per entity
  applicability    jsonb NOT NULL,                -- {"all":[{"fact":"constitution","in":["private_limited"]},{"fact":"headcount","gte":20}]}
  due_rule         jsonb NOT NULL,                -- {"type":"day_of_following_month","day":11}
  penalty_rule     jsonb,                         -- {"late_fee_per_day":100,"cap":null,"interest_pct_month":1.5}
  effective        daterange NOT NULL,            -- validity of this version
  source_url       text,
  change_note      text,                          -- what changed vs previous version
  published_at     timestamptz NOT NULL DEFAULT now(),
  UNIQUE (code, version),
  EXCLUDE USING gist (code WITH =, effective WITH &&)   -- one live version per day
);

-- Facts the engine evaluates. Append-only: a new value is a new row.
CREATE TABLE entity_facts (
  id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  entity_id    uuid NOT NULL REFERENCES entities(id),
  fact_key     text NOT NULL,                     -- 'aato','headcount','paid_up_capital','net_profit','cash_receipts_pct','has_foreign_investor'
  value_num    numeric,
  value_text   text,
  value_bool   boolean,
  as_of        date NOT NULL,
  source       text NOT NULL CHECK (source IN ('books','payroll','registry','user','engine','advisor')),
  recorded_at  timestamptz NOT NULL DEFAULT now(),
  recorded_by  uuid REFERENCES users(id),
  CHECK (num_nonnulls(value_num, value_text, value_bool) = 1)
);
CREATE INDEX entity_facts_lookup_ix ON entity_facts (entity_id, fact_key, as_of DESC, recorded_at DESC);

CREATE VIEW entity_facts_current AS
SELECT DISTINCT ON (entity_id, fact_key)
       entity_id, fact_key, value_num, value_text, value_bool, as_of, source
  FROM entity_facts
 ORDER BY entity_id, fact_key, as_of DESC, recorded_at DESC;

CREATE TABLE engine_runs (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  entity_id           uuid NOT NULL REFERENCES entities(id),
  trigger             text NOT NULL CHECK (trigger IN ('schedule','fact_change','rule_change','manual','onboarding')),
  started_at          timestamptz NOT NULL DEFAULT now(),
  finished_at         timestamptz,
  rules_evaluated     int,
  obligations_created int,
  obligations_changed int,
  error               text
);

CREATE TYPE obligation_status AS ENUM (
  'upcoming','in_preparation','awaiting_client','awaiting_approval',
  'ready_to_file','filed','overdue','not_applicable','waived');

CREATE TABLE obligations (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  entity_id           uuid NOT NULL REFERENCES entities(id),
  rule_id             uuid NOT NULL REFERENCES compliance_rules(id),
  registration_id     uuid REFERENCES entity_registrations(id),
  fy                  fy_label NOT NULL,
  period_start        date NOT NULL,
  period_end          date NOT NULL,
  period_label        text NOT NULL,              -- 'Oct 2026', 'Q2 FY26-27', 'FY 2025-26'
  due_date            date NOT NULL,
  extended_due_date   date,                       -- government extensions
  status              obligation_status NOT NULL DEFAULT 'upcoming',
  owner_user_id       uuid REFERENCES users(id),
  approver_user_id    uuid REFERENCES users(id),
  advisor_grant_id    uuid REFERENCES advisor_grants(id),   -- prepared by the CA/CS
  draft_document_id   uuid,                       -- FK added in part 08
  filed_at            timestamptz,
  filed_by            uuid REFERENCES users(id),
  acknowledgement     text,                       -- ARN / SRN / token / TRRN
  late_fee_accrued    inr NOT NULL DEFAULT 0,
  interest_accrued    inr NOT NULL DEFAULT 0,
  reason_snapshot     jsonb NOT NULL,             -- facts + rule clause that made it apply
  engine_run_id       uuid REFERENCES engine_runs(id),
  created_at          timestamptz NOT NULL DEFAULT now(),
  updated_at          timestamptz NOT NULL DEFAULT now(),
  CHECK (period_end >= period_start),
  CHECK (status <> 'filed' OR filed_at IS NOT NULL)
);
CREATE UNIQUE INDEX obligations_instance_uq
  ON obligations (entity_id, rule_id, coalesce(registration_id, '00000000-0000-0000-0000-000000000000'::uuid), period_start);
CREATE INDEX obligations_due_ix ON obligations (entity_id, coalesce(extended_due_date, due_date)) WHERE status NOT IN ('filed','not_applicable','waived');
CREATE INDEX obligations_owner_ix ON obligations (owner_user_id) WHERE status NOT IN ('filed','not_applicable','waived');
CREATE TRIGGER obligations_touch BEFORE UPDATE ON obligations FOR EACH ROW EXECUTE FUNCTION app.touch_updated_at();

CREATE TABLE obligation_events (
  id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  obligation_id  uuid NOT NULL REFERENCES obligations(id),
  at             timestamptz NOT NULL DEFAULT now(),
  actor_user_id  uuid REFERENCES users(id),       -- NULL = engine
  kind           text NOT NULL,                   -- 'created','status_changed','reassigned','due_extended','filed'
  from_status    obligation_status,
  to_status      obligation_status,
  note           text
);
CREATE INDEX obligation_events_ix ON obligation_events (obligation_id, at);

-- Duties that switch on when a fact crosses a threshold (tax audit, CSR, e-invoice…)
CREATE TABLE threshold_watches (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  entity_id     uuid NOT NULL REFERENCES entities(id),
  rule_code     text NOT NULL,                    -- rule that switches on
  fact_key      text NOT NULL,
  threshold     numeric NOT NULL,
  direction     text NOT NULL DEFAULT 'above' CHECK (direction IN ('above','below')),
  alert_at_pct  numeric(5,2) NOT NULL DEFAULT 90,
  note          text,                              -- 'Applies when cash receipts and payments stay under 5%'
  UNIQUE (entity_id, rule_code, fact_key)
);

-- Notices from authorities
CREATE TABLE notices (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  entity_id         uuid NOT NULL REFERENCES entities(id),
  authority_code    text NOT NULL REFERENCES ref_authorities(code),
  registration_id   uuid REFERENCES entity_registrations(id),
  form_code         text NOT NULL,                -- 'ASMT-10','DRC-01','143(1)','142(1)'
  reference_no      text,
  period_label      text,                         -- 'FY 2023-24'
  received_on       date NOT NULL,
  reply_due_on      date,
  hearing_on        date,
  demand_amount     inr,
  status            text NOT NULL DEFAULT 'open' CHECK (status IN ('open','draft_reply','replied','hearing','closed','appealed')),
  summary           text,
  linked_obligation_id uuid REFERENCES obligations(id),
  owner_user_id     uuid REFERENCES users(id),
  created_at        timestamptz NOT NULL DEFAULT now(),
  updated_at        timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX notices_open_ix ON notices (entity_id, reply_due_on) WHERE status NOT IN ('closed');
CREATE TRIGGER notices_touch BEFORE UPDATE ON notices FOR EACH ROW EXECUTE FUNCTION app.touch_updated_at();

-- ---------------------------------------------------------------------
-- Approvals (maker–checker), signatures, tasks — polymorphic over subjects
-- ---------------------------------------------------------------------
CREATE TYPE subject_kind AS ENUM (
  'obligation','sales_document','purchase_bill','vendor_payment','payroll_run',
  'journal_entry','contract','board_resolution','copilot_action','credit_note','quote','notice_reply');

CREATE TABLE approval_policies (
  id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  entity_id             uuid NOT NULL REFERENCES entities(id),
  subject               subject_kind NOT NULL,
  condition             jsonb,                     -- {"amount_gt": 500000}
  approvals_required    smallint NOT NULL DEFAULT 1 CHECK (approvals_required BETWEEN 1 AND 3),
  maker_cannot_approve  boolean NOT NULL DEFAULT true,
  approver_roles        member_role[] NOT NULL DEFAULT '{owner,approver}',
  is_active             boolean NOT NULL DEFAULT true
);

CREATE TABLE approval_requests (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  entity_id       uuid NOT NULL REFERENCES entities(id),
  subject         subject_kind NOT NULL,
  subject_id      uuid NOT NULL,
  policy_id       uuid REFERENCES approval_policies(id),
  summary         text NOT NULL,                  -- 'Approve TDS return Q2 (24Q, 26Q)'
  amount          inr,
  requested_by    uuid NOT NULL REFERENCES users(id),
  assigned_to     uuid REFERENCES users(id),
  due_on          date,
  status          text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','approved','rejected','withdrawn','expired')),
  decided_by      uuid REFERENCES users(id),
  decided_at      timestamptz,
  decision_note   text,
  created_at      timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT two_person_rule CHECK (decided_by IS NULL OR decided_by <> requested_by),
  CONSTRAINT decided_consistency CHECK ((status IN ('approved','rejected')) = (decided_at IS NOT NULL))
);
CREATE INDEX approval_requests_inbox_ix ON approval_requests (assigned_to, status) WHERE status = 'pending';
CREATE INDEX approval_requests_subject_ix ON approval_requests (subject, subject_id);

CREATE TABLE signature_requests (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  entity_id     uuid NOT NULL REFERENCES entities(id),
  subject       subject_kind NOT NULL,
  subject_id    uuid NOT NULL,
  document_id   uuid,                              -- FK added in part 08
  method        text NOT NULL CHECK (method IN ('dsc','aadhaar_esign','click_to_sign')),
  status        text NOT NULL DEFAULT 'sent' CHECK (status IN ('draft','sent','partially_signed','completed','declined','expired')),
  sent_at       timestamptz,
  completed_at  timestamptz,
  created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE signature_signers (
  id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  request_id         uuid NOT NULL REFERENCES signature_requests(id) ON DELETE CASCADE,
  sign_order         smallint NOT NULL DEFAULT 1,
  person_id          uuid REFERENCES persons(id),
  external_name      text,                         -- counterparty signer
  external_email     citext,
  status             text NOT NULL DEFAULT 'waiting' CHECK (status IN ('queued','waiting','signed','declined')),
  signed_at          timestamptz,
  dsc_certificate_id uuid REFERENCES dsc_certificates(id),
  CHECK (person_id IS NOT NULL OR external_email IS NOT NULL)
);

CREATE TABLE tasks (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  entity_id     uuid NOT NULL REFERENCES entities(id),
  title         text NOT NULL,
  subject       subject_kind,
  subject_id    uuid,
  assignee_id   uuid REFERENCES users(id),
  due_on        date,
  status        text NOT NULL DEFAULT 'open' CHECK (status IN ('open','done','cancelled')),
  source        text NOT NULL DEFAULT 'user' CHECK (source IN ('user','engine','dunning','copilot','advisor')),
  created_at    timestamptz NOT NULL DEFAULT now(),
  completed_at  timestamptz
);
CREATE INDEX tasks_assignee_ix ON tasks (assignee_id, due_on) WHERE status = 'open';
