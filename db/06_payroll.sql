-- =====================================================================
-- Part 06 · Payroll & HR (PF, ESI, PT, TDS on salary, labour codes)
-- =====================================================================

CREATE TABLE employees (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  entity_id         uuid NOT NULL REFERENCES entities(id),
  user_id           uuid REFERENCES users(id),
  employee_code     text NOT NULL,
  full_name         text NOT NULL,
  pan               pan_no,
  uan               char(12) CHECK (uan ~ '^[0-9]{12}$'),          -- EPF universal account number
  esic_ip_no        char(10) CHECK (esic_ip_no ~ '^[0-9]{10}$'),   -- ESI insurance number
  email             citext,
  mobile_e164       text,
  date_of_birth     date,
  date_of_joining   date NOT NULL,
  date_of_leaving   date,
  designation       text,
  department        text,
  work_state        char(2) NOT NULL REFERENCES ref_states(gst_code),   -- drives professional tax
  employment_type   text NOT NULL DEFAULT 'permanent' CHECK (employment_type IN ('permanent','fixed_term','intern','apprentice','contract')),
  tax_regime        text NOT NULL DEFAULT 'new' CHECK (tax_regime IN ('new','old')),
  bank_account_enc  bytea,
  bank_ifsc         char(11),
  employment_terms_version text,              -- 'v4'
  status            text NOT NULL DEFAULT 'active' CHECK (status IN ('onboarding','active','notice','exited')),
  created_at        timestamptz NOT NULL DEFAULT now(),
  UNIQUE (entity_id, employee_code),
  CHECK (date_of_leaving IS NULL OR date_of_leaving >= date_of_joining)
);

ALTER TABLE tds_deductions ADD CONSTRAINT tds_deductions_employee_fk
  FOREIGN KEY (deductee_employee_id) REFERENCES employees(id);

-- Salary structure with effective dating; components flag what counts as "wages"
CREATE TABLE salary_structures (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  employee_id     uuid NOT NULL REFERENCES employees(id),
  effective       daterange NOT NULL,
  ctc_annual      inr NOT NULL CHECK (ctc_annual > 0),
  approved_by     uuid REFERENCES users(id),
  EXCLUDE USING gist (employee_id WITH =, effective WITH &&)
);

CREATE TABLE salary_components (
  id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  structure_id    uuid NOT NULL REFERENCES salary_structures(id) ON DELETE CASCADE,
  code            text NOT NULL,               -- 'BASIC','DA','HRA','SPECIAL','LTA','CONVEYANCE','EMPLOYER_PF'
  monthly_amount  inr NOT NULL CHECK (monthly_amount >= 0),
  kind            text NOT NULL CHECK (kind IN ('earning','employer_contribution','reimbursement')),
  in_wages        boolean NOT NULL,            -- counts as "wages" (basic + DA + retaining allowance)
  taxable         boolean NOT NULL DEFAULT true,
  UNIQUE (structure_id, code)
);

CREATE TABLE payroll_runs (
  id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  entity_id            uuid NOT NULL REFERENCES entities(id),
  period_month         date NOT NULL CHECK (extract(day FROM period_month) = 1),
  status               text NOT NULL DEFAULT 'inputs_open' CHECK (status IN ('inputs_open','inputs_locked','computed','approved','bank_file_sent','paid','statutory_done')),
  headcount            int,
  gross_total          inr,
  net_total            inr,
  employer_cost_total  inr,
  approval_request_id  uuid REFERENCES approval_requests(id),
  bank_file_document_id uuid,                   -- FK added in part 08
  paid_on              date,
  journal_entry_id     uuid REFERENCES journal_entries(id),
  created_at           timestamptz NOT NULL DEFAULT now(),
  UNIQUE (entity_id, period_month)
);

CREATE TABLE payslips (
  id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  run_id             uuid NOT NULL REFERENCES payroll_runs(id),
  employee_id        uuid NOT NULL REFERENCES employees(id),
  paid_days          numeric(5,2) NOT NULL,
  lop_days           numeric(5,2) NOT NULL DEFAULT 0,
  gross              inr NOT NULL,
  wages              inr NOT NULL,           -- statutory wage base after the 50% rule
  pf_wage            inr NOT NULL,           -- capped at the EPF ceiling unless opted higher
  pf_employee        inr NOT NULL DEFAULT 0,
  pf_employer_epf    inr NOT NULL DEFAULT 0,
  pf_employer_eps    inr NOT NULL DEFAULT 0,
  esi_applicable     boolean NOT NULL,
  esi_employee       inr NOT NULL DEFAULT 0,
  esi_employer       inr NOT NULL DEFAULT 0,
  professional_tax   inr NOT NULL DEFAULT 0,
  tds                inr NOT NULL DEFAULT 0,
  other_deductions   inr NOT NULL DEFAULT 0,
  net_pay            inr NOT NULL,
  emailed_at         timestamptz,
  UNIQUE (run_id, employee_id),
  CHECK (net_pay = gross - pf_employee - esi_employee - professional_tax - tds - other_deductions),
  CHECK (esi_applicable OR (esi_employee = 0 AND esi_employer = 0))
);

CREATE TABLE payslip_lines (
  id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  payslip_id  uuid NOT NULL REFERENCES payslips(id) ON DELETE CASCADE,
  code        text NOT NULL,
  label       text NOT NULL,
  kind        text NOT NULL CHECK (kind IN ('earning','deduction','employer_contribution')),
  amount      inr NOT NULL
);

-- Leave
CREATE TABLE leave_types (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  entity_id   uuid NOT NULL REFERENCES entities(id),
  code        text NOT NULL,                 -- 'EL','SL','CL','ML'
  name        text NOT NULL,
  annual_quota numeric(5,2),
  encashable  boolean NOT NULL DEFAULT false,
  UNIQUE (entity_id, code)
);

CREATE TABLE leave_requests (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  employee_id   uuid NOT NULL REFERENCES employees(id),
  leave_type_id uuid NOT NULL REFERENCES leave_types(id),
  dates         daterange NOT NULL,
  days          numeric(5,2) NOT NULL,
  status        text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','approved','rejected','cancelled')),
  approved_by   uuid REFERENCES users(id)
);

-- Labour-law provisions and committees
CREATE TABLE gratuity_valuations (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  entity_id     uuid NOT NULL REFERENCES entities(id),
  as_of         date NOT NULL,
  liability     inr NOT NULL,
  funded_amount inr NOT NULL DEFAULT 0,
  actuary       text,
  UNIQUE (entity_id, as_of)
);

CREATE TABLE statutory_bonus (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  entity_id     uuid NOT NULL REFERENCES entities(id),
  employee_id   uuid NOT NULL REFERENCES employees(id),
  fy            fy_label NOT NULL,
  rate_pct      numeric(5,2) NOT NULL CHECK (rate_pct BETWEEN 8.33 AND 20),
  amount        inr NOT NULL,
  pay_by        date NOT NULL,                -- within 8 months of FY close
  paid_on       date,
  UNIQUE (employee_id, fy)
);

CREATE TABLE committees (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  entity_id       uuid NOT NULL REFERENCES entities(id),
  kind            text NOT NULL CHECK (kind IN ('posh_internal','audit','nomination_remuneration','csr','grievance')),
  constituted_on  date NOT NULL,
  dissolved_on    date
);

CREATE TABLE committee_members (
  committee_id  uuid NOT NULL REFERENCES committees(id) ON DELETE CASCADE,
  name          text NOT NULL,
  role          text NOT NULL,              -- 'presiding_officer','member','external_member'
  employee_id   uuid REFERENCES employees(id),
  PRIMARY KEY (committee_id, name)
);
