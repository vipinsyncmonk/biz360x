-- =====================================================================
-- Part 35 · HR: company policy, onboarding, leave, holidays, the handbook
-- The company states its policy once (how a CTC is split, what leave people get, the weekly off, the onboarding checklist) and the rest
-- follows from it: a new hire is entered with a CTC and a joining date, leave is counted against the policy, and unpaid days reach the
-- payroll run by themselves. A policy is versioned by the date it takes effect; a published handbook document is never edited, a new
-- version replaces it. Leave quotas, the CTC split and the checklist are a starter set for the company and its CA to review.
-- =====================================================================
CREATE TABLE hr_policies (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  entity_id       uuid NOT NULL REFERENCES entities(id),
  effective_from  date NOT NULL,
  salary          jsonb NOT NULL,          -- how a CTC becomes components
  leave           jsonb NOT NULL,          -- leave year, weekly off, what happens when leave runs out
  payroll         jsonb NOT NULL,          -- cut-off day, pay day, probation and notice (for people to read)
  onboarding      jsonb NOT NULL,          -- the checklist a new hire goes through
  created_by      uuid REFERENCES users(id),
  created_at      timestamptz NOT NULL DEFAULT now(),
  UNIQUE (entity_id, effective_from)
);

ALTER TABLE leave_types
  ADD COLUMN accrual           text NOT NULL DEFAULT 'yearly' CHECK (accrual IN ('yearly', 'monthly')),   -- granted at the start of the leave year, or earned month by month
  ADD COLUMN paid              boolean NOT NULL DEFAULT true,                                               -- false: every day of it is loss of pay
  ADD COLUMN carry_forward_max numeric(5,2) NOT NULL DEFAULT 0 CHECK (carry_forward_max >= 0),
  ADD COLUMN is_active         boolean NOT NULL DEFAULT true;

ALTER TABLE leave_requests
  ADD COLUMN lop_days      numeric(5,2) NOT NULL DEFAULT 0,       -- the days of this request that are not paid (the last days of it)
  ADD COLUMN half_day      boolean NOT NULL DEFAULT false,
  ADD COLUMN reason        text,
  ADD COLUMN requested_by  uuid REFERENCES users(id),
  ADD COLUMN requested_at  timestamptz NOT NULL DEFAULT now(),
  ADD COLUMN decided_at    timestamptz,
  ADD COLUMN decision_note text,
  ADD CONSTRAINT leave_lop_within_days CHECK (lop_days >= 0 AND lop_days <= days),
  ADD CONSTRAINT leave_days_positive CHECK (days > 0),
  ADD CONSTRAINT leave_no_overlap EXCLUDE USING gist (employee_id WITH =, dates WITH &&) WHERE (status IN ('pending', 'approved'));

-- Days added to or taken from a person's balance by hand: what they carried in from earlier books, a carry-forward, a correction.
CREATE TABLE leave_adjustments (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  entity_id        uuid NOT NULL REFERENCES entities(id),
  employee_id      uuid NOT NULL REFERENCES employees(id),
  leave_type_id    uuid NOT NULL REFERENCES leave_types(id),
  leave_year_start date NOT NULL,
  days             numeric(6,2) NOT NULL CHECK (days <> 0),
  kind             text NOT NULL CHECK (kind IN ('opening', 'carry_forward', 'correction')),
  note             text,
  created_by       uuid REFERENCES users(id),
  created_at       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX leave_adjustments_employee_ix ON leave_adjustments (employee_id, leave_year_start);

CREATE TABLE holidays (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  entity_id     uuid NOT NULL REFERENCES entities(id),
  holiday_date  date NOT NULL,
  name          text NOT NULL CHECK (length(btrim(name)) > 0),
  UNIQUE (entity_id, holiday_date)
);

-- One row per checklist item for a new hire, copied from the policy on the day they were entered. `auto` items are read from the record
-- (PAN on file, bank account on file, salary set, handbook acknowledged); the others a person ticks.
CREATE TABLE onboarding_tasks (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  entity_id    uuid NOT NULL REFERENCES entities(id),
  employee_id  uuid NOT NULL REFERENCES employees(id),
  key          text NOT NULL,
  title        text NOT NULL,
  required     boolean NOT NULL DEFAULT true,
  auto         text CHECK (auto IN ('pan', 'bank', 'salary', 'handbook')),
  sort         int NOT NULL DEFAULT 0,
  done_at      timestamptz,
  done_by      uuid REFERENCES users(id),
  UNIQUE (employee_id, key)
);

-- The handbook: leave policy, code of conduct, POSH policy and so on, as text people can read. Publishing again adds a version.
CREATE TABLE hr_policy_documents (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  entity_id     uuid NOT NULL REFERENCES entities(id),
  slug          text NOT NULL CHECK (slug ~ '^[a-z0-9-]{1,60}$'),
  title         text NOT NULL CHECK (length(btrim(title)) > 0),
  category      text NOT NULL DEFAULT 'general' CHECK (category IN ('general', 'leave', 'conduct', 'posh', 'payroll', 'it_security', 'travel', 'exit')),
  body          text NOT NULL CHECK (length(btrim(body)) > 0),
  version       int NOT NULL CHECK (version >= 1),
  requires_ack  boolean NOT NULL DEFAULT true,
  is_current    boolean NOT NULL DEFAULT true,
  published_by  uuid REFERENCES users(id),
  published_at  timestamptz NOT NULL DEFAULT now(),
  UNIQUE (entity_id, slug, version)
);
CREATE UNIQUE INDEX hr_policy_documents_current_ix ON hr_policy_documents (entity_id, slug) WHERE is_current;

CREATE TABLE hr_policy_acks (
  document_id      uuid NOT NULL REFERENCES hr_policy_documents(id),
  employee_id      uuid NOT NULL REFERENCES employees(id),
  entity_id        uuid NOT NULL REFERENCES entities(id),
  acknowledged_on  date NOT NULL,
  recorded_by      uuid REFERENCES users(id),
  PRIMARY KEY (document_id, employee_id)
);

DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['hr_policies', 'leave_adjustments', 'holidays', 'onboarding_tasks', 'hr_policy_documents', 'hr_policy_acks'] LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
    EXECUTE format($p$CREATE POLICY tenant_isolation ON %I USING (entity_id IN (SELECT app.visible_entity_ids())) WITH CHECK (entity_id IN (SELECT app.visible_entity_ids()))$p$, t);
    EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON %I TO biz_app, biz_worker', t);
  END LOOP;
END $$;
