-- =====================================================================
-- Part 07 · Legal & contracts, board & secretarial, cap table & ESOPs,
--           schemes & credit (MSME / startup benefits, TReDS, delayed payments)
-- =====================================================================

-- ---------------------------------------------------------------------
-- Contracts
-- ---------------------------------------------------------------------
CREATE TABLE contract_templates (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  entity_id     uuid REFERENCES entities(id),     -- NULL = Biz360X library template
  name          text NOT NULL,                     -- 'Mutual NDA'
  kind          text NOT NULL,
  version       text NOT NULL,                     -- 'v4'
  body_document_id uuid,                           -- FK added in part 08
  reviewed_by   text,                              -- counsel who approved it
  reviewed_on   date,
  is_active     boolean NOT NULL DEFAULT true
);

CREATE TABLE contracts (
  id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  entity_id            uuid NOT NULL REFERENCES entities(id),
  party_id             uuid REFERENCES parties(id),
  counterparty_name    text NOT NULL,
  title                text NOT NULL,              -- 'Supply agreement'
  kind                 text NOT NULL CHECK (kind IN ('customer','vendor','property','employment','nda','consultant','licence','loan','shareholder','other')),
  template_id          uuid REFERENCES contract_templates(id),
  start_date           date,
  end_date             date,
  auto_renews          boolean NOT NULL DEFAULT false,
  renewal_months       smallint,
  notice_days          smallint,
  notice_deadline      date GENERATED ALWAYS AS (end_date - notice_days) STORED,
  value_amount         inr,
  value_period         text CHECK (value_period IN ('one_time','monthly','annual','rate_card')),
  payment_terms_days   smallint,
  governing_law        text,
  dispute_forum        text,                       -- 'Arbitration, Bengaluru'
  stamp_state          char(2) REFERENCES ref_states(gst_code),
  stamp_duty_paid      inr,
  registration_required boolean NOT NULL DEFAULT false,
  registered_on        date,
  status               text NOT NULL DEFAULT 'active' CHECK (status IN ('draft','negotiating','out_for_signature','active','expired','terminated','renewed')),
  owner_user_id        uuid REFERENCES users(id),
  signed_document_id   uuid,                       -- FK added in part 08
  created_at           timestamptz NOT NULL DEFAULT now(),
  CHECK (end_date IS NULL OR start_date IS NULL OR end_date >= start_date)
);
CREATE INDEX contracts_notice_ix ON contracts (entity_id, notice_deadline) WHERE status = 'active' AND auto_renews;

-- Key terms extracted from the signed copy (by people or by the copilot)
CREATE TABLE contract_terms (
  id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  contract_id   uuid NOT NULL REFERENCES contracts(id) ON DELETE CASCADE,
  term_key      text NOT NULL,                     -- 'renewal','payment','liability_cap','pricing','escalation','lock_in','deposit'
  value_text    text NOT NULL,
  value_json    jsonb,
  source_page   smallint,
  extracted_by  text NOT NULL CHECK (extracted_by IN ('user','copilot')),
  confidence    numeric(4,3),
  verified_by   uuid REFERENCES users(id),
  UNIQUE (contract_id, term_key)
);

-- ---------------------------------------------------------------------
-- Board & secretarial
-- ---------------------------------------------------------------------
CREATE TABLE meetings (
  id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  entity_id            uuid NOT NULL REFERENCES entities(id),
  kind                 text NOT NULL CHECK (kind IN ('board','agm','egm','committee','partners')),
  scheduled_for        timestamptz NOT NULL,
  held_on              date,
  notice_sent_on       date,
  quorum_met           boolean,
  minutes_document_id  uuid,                       -- FK added in part 08
  minutes_signed_on    date
);
CREATE INDEX meetings_board_ix ON meetings (entity_id, held_on) WHERE kind = 'board';

CREATE TABLE resolutions (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  entity_id         uuid NOT NULL REFERENCES entities(id),
  meeting_id        uuid REFERENCES meetings(id),     -- NULL = by circulation
  number            text NOT NULL,
  title             text NOT NULL,
  kind              text NOT NULL CHECK (kind IN ('board','ordinary','special','circular')),
  passed_on         date,
  needs_mgt14       boolean NOT NULL DEFAULT false,
  mgt14_obligation_id uuid REFERENCES obligations(id),
  signature_request_id uuid REFERENCES signature_requests(id),
  status            text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','circulated','passed','failed','withdrawn')),
  UNIQUE (entity_id, number)
);

-- ---------------------------------------------------------------------
-- Cap table, funding rounds, ESOPs
-- ---------------------------------------------------------------------
CREATE TABLE share_classes (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  entity_id       uuid NOT NULL REFERENCES entities(id),
  name            text NOT NULL,                    -- 'Equity', 'Series Seed CCPS'
  instrument      text NOT NULL CHECK (instrument IN ('equity','ccps','ocps','ccd','safe','warrant')),
  face_value      numeric(12,4) NOT NULL,
  conversion_ratio numeric(12,6) NOT NULL DEFAULT 1,   -- to equity, for fully-diluted views
  liquidation_pref numeric(6,2),
  UNIQUE (entity_id, name)
);

CREATE TABLE shareholders (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  entity_id       uuid NOT NULL REFERENCES entities(id),
  name            text NOT NULL,
  kind            text NOT NULL CHECK (kind IN ('founder','investor','angel','employee_trust','company','other')),
  person_id       uuid REFERENCES persons(id),
  pan             pan_no,
  is_non_resident boolean NOT NULL DEFAULT false,  -- drives FEMA reporting
  country         char(2) NOT NULL DEFAULT 'IN'
);

CREATE TABLE funding_rounds (
  id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  entity_id             uuid NOT NULL REFERENCES entities(id),
  name                  text NOT NULL,             -- 'Seed extension'
  share_class_id        uuid REFERENCES share_classes(id),
  route                 text NOT NULL DEFAULT 'private_placement' CHECK (route IN ('private_placement','rights','preferential','bonus')),
  target_amount         inr,
  pre_money_valuation   inr,
  term_sheet_signed_on  date,
  shareholder_approval_on date,
  closed_on             date,
  status                text NOT NULL DEFAULT 'term_sheet' CHECK (status IN ('planning','term_sheet','diligence','documentation','allotment','closed','abandoned'))
);

CREATE TABLE share_allotments (
  id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  entity_id            uuid NOT NULL REFERENCES entities(id),
  round_id             uuid REFERENCES funding_rounds(id),
  share_class_id       uuid NOT NULL REFERENCES share_classes(id),
  shareholder_id       uuid NOT NULL REFERENCES shareholders(id),
  quantity             bigint NOT NULL CHECK (quantity > 0),
  issue_price          numeric(14,4) NOT NULL,
  money_received_on    date,                       -- allot within 60 days of receipt
  allotted_on          date,
  pas3_obligation_id   uuid REFERENCES obligations(id),    -- return of allotment, 15 days
  fcgpr_obligation_id  uuid REFERENCES obligations(id),    -- FEMA, 30 days, if non-resident
  certificate_no       text,
  CHECK (allotted_on IS NULL OR money_received_on IS NULL OR allotted_on <= money_received_on + 60)
);

CREATE TABLE share_transfers (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  entity_id        uuid NOT NULL REFERENCES entities(id),
  share_class_id   uuid NOT NULL REFERENCES share_classes(id),
  from_holder_id   uuid NOT NULL REFERENCES shareholders(id),
  to_holder_id     uuid NOT NULL REFERENCES shareholders(id),
  quantity         bigint NOT NULL CHECK (quantity > 0),
  price            numeric(14,4),
  transferred_on   date NOT NULL,
  CHECK (from_holder_id <> to_holder_id)
);

CREATE TABLE esop_pools (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  entity_id       uuid NOT NULL REFERENCES entities(id),
  name            text NOT NULL,                  -- 'ESOP 2024'
  approved_on     date NOT NULL,                  -- special resolution
  size_options    bigint NOT NULL CHECK (size_options > 0),
  resolution_id   uuid REFERENCES resolutions(id)
);

CREATE TABLE esop_grants (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  pool_id          uuid NOT NULL REFERENCES esop_pools(id),
  employee_id      uuid NOT NULL REFERENCES employees(id),
  granted_on       date NOT NULL,
  options          bigint NOT NULL CHECK (options > 0),
  strike_price     numeric(14,4) NOT NULL,
  cliff_months     smallint NOT NULL DEFAULT 12,
  vesting_months   smallint NOT NULL DEFAULT 48,
  vesting_frequency text NOT NULL DEFAULT 'monthly' CHECK (vesting_frequency IN ('monthly','quarterly','annual')),
  status           text NOT NULL DEFAULT 'active' CHECK (status IN ('active','lapsed','fully_vested','cancelled'))
);

CREATE TABLE esop_exercises (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  grant_id          uuid NOT NULL REFERENCES esop_grants(id),
  exercised_on      date NOT NULL,
  options           bigint NOT NULL CHECK (options > 0),
  fmv_per_share     numeric(14,4) NOT NULL,
  perquisite_value  inr GENERATED ALWAYS AS (round(options * fmv_per_share, 2)) STORED,   -- less strike in the app
  allotment_id      uuid REFERENCES share_allotments(id)
);

-- ---------------------------------------------------------------------
-- Schemes, registrations-as-benefits, TReDS, delayed payment claims
-- ---------------------------------------------------------------------
CREATE TABLE ref_schemes (
  code          text PRIMARY KEY,                -- 'DPIIT','S80IAC','CGTMSE','ZED','PMEGP','TREDS','KA-IND-POLICY'
  name          text NOT NULL,
  authority_code text REFERENCES ref_authorities(code),
  kind          text NOT NULL CHECK (kind IN ('recognition','tax_benefit','credit_guarantee','subsidy','certification','platform','grievance')),
  state_code    char(2) REFERENCES ref_states(gst_code),
  for_tracks    entity_track[] NOT NULL,
  eligibility   jsonb NOT NULL,                   -- same expression language as compliance rules
  info_url      text
);

CREATE TABLE entity_benefits (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  entity_id     uuid NOT NULL REFERENCES entities(id),
  scheme_code   text NOT NULL REFERENCES ref_schemes(code),
  status        text NOT NULL CHECK (status IN ('not_eligible','eligible','applied','in_progress','granted','rejected','expired')),
  reason        text,                             -- why eligible / not
  reference_no  text,
  applied_on    date,
  granted_on    date,
  valid_until   date,
  checked_at    timestamptz NOT NULL DEFAULT now(),
  UNIQUE (entity_id, scheme_code)
);

CREATE TABLE treds_invoices (
  id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  entity_id          uuid NOT NULL REFERENCES entities(id),
  sales_document_id  uuid NOT NULL REFERENCES sales_documents(id),
  platform           text NOT NULL,
  uploaded_on        date NOT NULL,
  buyer_accepted_on  date,
  financed_on        date,
  discount_rate_pct  numeric(6,3),
  amount_received    inr,
  status             text NOT NULL DEFAULT 'uploaded' CHECK (status IN ('uploaded','accepted','auctioned','financed','settled','rejected'))
);

-- RBI bank rate history (MSMED Act interest = 3× bank rate, compounded monthly)
CREATE TABLE ref_rbi_bank_rate (
  effective_from date PRIMARY KEY,
  rate_pct       numeric(5,2) NOT NULL
);

-- An MSME supplier's claim against a late-paying buyer (MSME Samadhaan / MSEFC)
CREATE TABLE delayed_payment_claims (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  entity_id        uuid NOT NULL REFERENCES entities(id),   -- the supplier (us)
  buyer_party_id   uuid NOT NULL REFERENCES parties(id),
  principal        inr NOT NULL,
  interest_claimed inr NOT NULL,
  filed_on         date,
  forum            text NOT NULL DEFAULT 'msme_samadhaan' CHECK (forum IN ('msme_samadhaan','msefc','court')),
  case_no          text,
  status           text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','filed','conciliation','arbitration','settled','closed'))
);

CREATE TABLE delayed_payment_claim_invoices (
  claim_id           uuid NOT NULL REFERENCES delayed_payment_claims(id) ON DELETE CASCADE,
  sales_document_id  uuid NOT NULL REFERENCES sales_documents(id),
  PRIMARY KEY (claim_id, sales_document_id)
);
