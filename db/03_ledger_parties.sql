-- =====================================================================
-- Part 03 · Parties, items, unified ledger, banking, month-end close
-- =====================================================================

-- ---------------------------------------------------------------------
-- Parties: one table for customers and vendors (a party can be both)
-- ---------------------------------------------------------------------
CREATE TABLE parties (
  id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  entity_id            uuid NOT NULL REFERENCES entities(id),
  legal_name           text NOT NULL,
  display_name         text,
  is_customer          boolean NOT NULL DEFAULT false,
  is_vendor            boolean NOT NULL DEFAULT false,
  pan                  pan_no,
  msme_class           udyam_class,                 -- supplier's Udyam class (drives the 45-day rule)
  udyam_number         text,
  udyam_verified_at    timestamptz,
  payment_terms_days   smallint NOT NULL DEFAULT 30 CHECK (payment_terms_days BETWEEN 0 AND 365),
  credit_limit         inr,
  deducts_tds_194q     boolean NOT NULL DEFAULT false,   -- customer deducts 0.1% on purchase of goods
  default_tds_section  text,                        -- vendor: '194C','194J','194Q'…
  is_related_party     boolean NOT NULL DEFAULT false,
  status               text NOT NULL DEFAULT 'active' CHECK (status IN ('active','on_hold','blocked','archived')),
  created_at           timestamptz NOT NULL DEFAULT now(),
  updated_at           timestamptz NOT NULL DEFAULT now(),
  CHECK (is_customer OR is_vendor),
  CHECK (msme_class IS NULL OR udyam_number IS NOT NULL)
);
CREATE INDEX parties_entity_ix ON parties (entity_id, legal_name);
CREATE TRIGGER parties_touch BEFORE UPDATE ON parties FOR EACH ROW EXECUTE FUNCTION app.touch_updated_at();

CREATE TYPE taxpayer_type AS ENUM ('regular','composition','sez_unit','sez_developer','uin','non_resident','unregistered','overseas');

CREATE TABLE party_gst_registrations (
  id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  party_id           uuid NOT NULL REFERENCES parties(id) ON DELETE CASCADE,
  gstin              gstin_no,                     -- NULL for unregistered / overseas
  state_code         char(2) REFERENCES ref_states(gst_code),
  taxpayer_type      taxpayer_type NOT NULL DEFAULT 'regular',
  legal_name_on_gstn text,
  gstn_status        text CHECK (gstn_status IN ('active','suspended','cancelled')),
  verified_at        timestamptz,
  is_default         boolean NOT NULL DEFAULT false,
  CHECK (gstin IS NULL OR state_code = substr(gstin, 1, 2))
);
CREATE UNIQUE INDEX party_gstin_uq ON party_gst_registrations (party_id, gstin) WHERE gstin IS NOT NULL;

CREATE TABLE party_addresses (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  party_id     uuid NOT NULL REFERENCES parties(id) ON DELETE CASCADE,
  kind         text NOT NULL CHECK (kind IN ('billing','shipping','registered')),
  line1        text NOT NULL,
  line2        text,
  city         text NOT NULL,
  state_code   char(2) REFERENCES ref_states(gst_code),
  pincode      char(6) CHECK (pincode ~ '^[1-9][0-9]{5}$'),
  country      char(2) NOT NULL DEFAULT 'IN'
);

CREATE TABLE party_contacts (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  party_id     uuid NOT NULL REFERENCES parties(id) ON DELETE CASCADE,
  name         text NOT NULL,
  email        citext,
  mobile_e164  text,
  whatsapp_opt_in boolean NOT NULL DEFAULT false,
  is_billing_contact boolean NOT NULL DEFAULT false
);

-- ---------------------------------------------------------------------
-- Item catalogue
-- ---------------------------------------------------------------------
CREATE TABLE items (
  id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  entity_id            uuid NOT NULL REFERENCES entities(id),
  sku                  text NOT NULL,
  name                 text NOT NULL,
  kind                 text NOT NULL CHECK (kind IN ('goods','service')),
  hsn_sac              hsn_sac NOT NULL,
  uqc                  text NOT NULL DEFAULT 'NOS',   -- GST unit quantity code
  gst_rate             numeric(5,2) NOT NULL CHECK (gst_rate IN (0,0.1,0.25,1.5,3,5,12,18,28,40)),
  cess_rate            numeric(5,2) NOT NULL DEFAULT 0,
  sale_rate            inr,
  purchase_rate        inr,
  track_inventory      boolean NOT NULL DEFAULT false,
  is_active            boolean NOT NULL DEFAULT true,
  UNIQUE (entity_id, sku)
);

-- ---------------------------------------------------------------------
-- Ledger: chart of accounts, periods, double-entry journal
-- ---------------------------------------------------------------------
CREATE TYPE account_type AS ENUM ('asset','liability','equity','income','expense');

CREATE TABLE ledger_accounts (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  entity_id    uuid NOT NULL REFERENCES entities(id),
  code         text NOT NULL,
  name         text NOT NULL,
  type         account_type NOT NULL,
  subtype      text,                 -- 'bank','receivable','payable','gst_output_cgst','gst_input_igst','tds_payable','tds_receivable','salary_payable'
  schedule_iii_head text,            -- mapping for Companies Act financial statements
  parent_id    uuid REFERENCES ledger_accounts(id),
  is_system    boolean NOT NULL DEFAULT false,   -- created by modules; not deletable
  is_active    boolean NOT NULL DEFAULT true,
  UNIQUE (entity_id, code)
);

CREATE TABLE fiscal_periods (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  entity_id     uuid NOT NULL REFERENCES entities(id),
  fy            fy_label NOT NULL,
  period_start  date NOT NULL,
  period_end    date NOT NULL,
  status        text NOT NULL DEFAULT 'open' CHECK (status IN ('open','closing','locked')),
  locked_at     timestamptz,
  locked_by     uuid REFERENCES users(id),
  UNIQUE (entity_id, period_start),
  CHECK (period_end > period_start),
  CHECK (status <> 'locked' OR locked_at IS NOT NULL)
);

CREATE TYPE journal_source AS ENUM ('manual','sales_document','purchase_bill','receipt','vendor_payment','payroll_run','depreciation','bank','opening','gst_setoff','tds');

CREATE TABLE journal_entries (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  entity_id     uuid NOT NULL REFERENCES entities(id),
  entry_no      text NOT NULL,
  entry_date    date NOT NULL,
  fy            fy_label NOT NULL GENERATED ALWAYS AS (app.fy_of(entry_date)) STORED,
  source        journal_source NOT NULL,
  source_id     uuid,                     -- the sales document / bill / payroll run…
  narration     text,
  status        text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','posted','reversed')),
  reversal_of   uuid REFERENCES journal_entries(id),
  posted_at     timestamptz,
  posted_by     uuid REFERENCES users(id),
  created_at    timestamptz NOT NULL DEFAULT now(),
  UNIQUE (entity_id, entry_no)
);
CREATE INDEX journal_entries_date_ix   ON journal_entries (entity_id, entry_date);
CREATE INDEX journal_entries_source_ix ON journal_entries (source, source_id);

CREATE TABLE journal_lines (
  id               bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  entry_id         uuid NOT NULL REFERENCES journal_entries(id) ON DELETE CASCADE,
  line_no          smallint NOT NULL,
  account_id       uuid NOT NULL REFERENCES ledger_accounts(id),
  party_id         uuid REFERENCES parties(id),
  registration_id  uuid REFERENCES entity_registrations(id),   -- branch / GSTIN dimension
  cost_center      text,
  debit            inr NOT NULL DEFAULT 0 CHECK (debit  >= 0),
  credit           inr NOT NULL DEFAULT 0 CHECK (credit >= 0),
  memo             text,
  CHECK ((debit = 0) <> (credit = 0)),
  UNIQUE (entry_id, line_no)
);
CREATE INDEX journal_lines_account_ix ON journal_lines (account_id);
CREATE INDEX journal_lines_party_ix   ON journal_lines (party_id) WHERE party_id IS NOT NULL;

-- Posting guard: entry must balance and its period must be open
CREATE OR REPLACE FUNCTION app.check_journal_on_post() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE dr numeric; cr numeric; locked boolean;
BEGIN
  IF NEW.status = 'posted' AND (TG_OP = 'INSERT' OR OLD.status <> 'posted') THEN
    SELECT coalesce(sum(debit),0), coalesce(sum(credit),0) INTO dr, cr
      FROM journal_lines WHERE entry_id = NEW.id;
    IF dr = 0 OR dr <> cr THEN
      RAISE EXCEPTION 'Journal % does not balance (Dr % / Cr %)', NEW.entry_no, dr, cr;
    END IF;
    SELECT EXISTS (SELECT 1 FROM fiscal_periods p
                    WHERE p.entity_id = NEW.entity_id AND p.status = 'locked'
                      AND NEW.entry_date BETWEEN p.period_start AND p.period_end) INTO locked;
    IF locked THEN
      RAISE EXCEPTION 'Period for % is locked', NEW.entry_date;
    END IF;
    NEW.posted_at := coalesce(NEW.posted_at, now());
  END IF;
  IF TG_OP = 'UPDATE' AND OLD.status = 'posted' AND NEW.status = 'draft' THEN
    RAISE EXCEPTION 'Posted journals cannot return to draft; reverse them instead';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER journal_entries_post BEFORE INSERT OR UPDATE ON journal_entries
  FOR EACH ROW EXECUTE FUNCTION app.check_journal_on_post();

-- Lines of a posted entry are frozen
CREATE OR REPLACE FUNCTION app.freeze_posted_lines() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF EXISTS (SELECT 1 FROM journal_entries e
              WHERE e.id = coalesce(NEW.entry_id, OLD.entry_id) AND e.status <> 'draft') THEN
    RAISE EXCEPTION 'Lines of a posted journal cannot change';
  END IF;
  RETURN coalesce(NEW, OLD);
END $$;
CREATE TRIGGER journal_lines_freeze BEFORE INSERT OR UPDATE OR DELETE ON journal_lines
  FOR EACH ROW EXECUTE FUNCTION app.freeze_posted_lines();

-- ---------------------------------------------------------------------
-- Banking
-- ---------------------------------------------------------------------
CREATE TABLE bank_accounts (
  id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  entity_id          uuid NOT NULL REFERENCES entities(id),
  ledger_account_id  uuid NOT NULL REFERENCES ledger_accounts(id),
  bank_name          text NOT NULL,
  account_last4      char(4) NOT NULL,
  account_no_enc     bytea,               -- full number, encrypted by the app (KMS)
  ifsc               char(11) CHECK (ifsc ~ '^[A-Z]{4}0[A-Z0-9]{6}$'),
  kind               text NOT NULL CHECK (kind IN ('current','savings','cash_credit','overdraft','fixed_deposit','escrow')),
  sanctioned_limit   inr,
  feed               text NOT NULL DEFAULT 'manual' CHECK (feed IN ('account_aggregator','bank_api','statement_upload','manual')),
  is_active          boolean NOT NULL DEFAULT true
);

CREATE TABLE bank_transactions (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  bank_account_id   uuid NOT NULL REFERENCES bank_accounts(id),
  entity_id         uuid NOT NULL REFERENCES entities(id),
  txn_date          date NOT NULL,
  value_date        date,
  amount            inr NOT NULL CHECK (amount <> 0),    -- + credit to us, − debit
  description       text NOT NULL,
  utr               text,                                 -- NEFT/RTGS/IMPS/UPI reference
  counterparty_name text,
  counterparty_va   text,                                 -- virtual account it was paid into
  balance_after     inr,
  external_id       text NOT NULL,                        -- dedupe key from the feed
  match_status      text NOT NULL DEFAULT 'unmatched' CHECK (match_status IN ('unmatched','suggested','matched','ignored')),
  imported_at       timestamptz NOT NULL DEFAULT now(),
  UNIQUE (bank_account_id, external_id)
);
CREATE INDEX bank_txn_unmatched_ix ON bank_transactions (entity_id, txn_date) WHERE match_status <> 'matched';

CREATE TABLE bank_matches (
  id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  bank_transaction_id  uuid NOT NULL REFERENCES bank_transactions(id),
  target_kind          text NOT NULL CHECK (target_kind IN ('receipt','vendor_payment','journal_entry','payroll_run','tax_challan')),
  target_id            uuid NOT NULL,
  amount               inr NOT NULL,
  confidence           numeric(4,3),                     -- 0..1 from the matcher
  rule                 text,                             -- 'exact_amount_ref','virtual_account','bank_charges'
  status               text NOT NULL DEFAULT 'suggested' CHECK (status IN ('suggested','accepted','rejected')),
  decided_by           uuid REFERENCES users(id),
  decided_at           timestamptz
);
CREATE INDEX bank_matches_txn_ix ON bank_matches (bank_transaction_id);

-- Month-end close checklist
CREATE TABLE close_tasks (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  entity_id     uuid NOT NULL REFERENCES entities(id),
  period_id     uuid NOT NULL REFERENCES fiscal_periods(id),
  task_key      text NOT NULL,            -- 'bank_feeds','sales_posted','gst_recon','depreciation','cfo_review'
  title         text NOT NULL,
  owner_user_id uuid REFERENCES users(id),
  is_automatic  boolean NOT NULL DEFAULT false,
  done_at       timestamptz,
  done_by       uuid REFERENCES users(id),
  sort_order    smallint NOT NULL DEFAULT 0,
  UNIQUE (period_id, task_key)
);
