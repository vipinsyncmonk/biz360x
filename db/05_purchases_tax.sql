-- =====================================================================
-- Part 05 · Purchases & vendor payments (incl. MSME 45-day rule),
--           GST returns & 2A/2B matching, TDS and direct tax
-- =====================================================================

CREATE TABLE purchase_orders (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  entity_id     uuid NOT NULL REFERENCES entities(id),
  party_id      uuid NOT NULL REFERENCES parties(id),
  po_no         text NOT NULL,
  po_date       date NOT NULL,
  status        text NOT NULL DEFAULT 'open' CHECK (status IN ('draft','open','partially_received','received','closed','cancelled')),
  total         inr NOT NULL DEFAULT 0,
  UNIQUE (entity_id, po_no)
);

CREATE TABLE goods_receipts (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  entity_id     uuid NOT NULL REFERENCES entities(id),
  purchase_order_id uuid REFERENCES purchase_orders(id),
  grn_no        text NOT NULL,
  received_on   date NOT NULL,           -- "day of acceptance" for the MSMED Act
  inspected_ok  boolean,
  UNIQUE (entity_id, grn_no)
);

CREATE TYPE itc_eligibility AS ENUM ('eligible','blocked_17_5','ineligible','partial','reverse_charge');

CREATE TABLE purchase_bills (
  id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  entity_id          uuid NOT NULL REFERENCES entities(id),
  party_id           uuid NOT NULL REFERENCES parties(id),
  party_gst_id       uuid REFERENCES party_gst_registrations(id),
  supplier_gstin     text,                          -- snapshot
  registration_id    uuid NOT NULL REFERENCES entity_registrations(id),   -- our receiving GSTIN
  bill_no            text NOT NULL,                 -- supplier's invoice number
  bill_date          date NOT NULL,
  fy                 fy_label NOT NULL GENERATED ALWAYS AS (app.fy_of(bill_date)) STORED,
  goods_receipt_id   uuid REFERENCES goods_receipts(id),
  accepted_on        date NOT NULL,                 -- acceptance of goods/services
  supplier_state     char(2) NOT NULL REFERENCES ref_states(gst_code),
  place_of_supply    char(2) NOT NULL REFERENCES ref_states(gst_code),
  is_inter_state     boolean GENERATED ALWAYS AS (supplier_state <> place_of_supply) STORED,
  reverse_charge     boolean NOT NULL DEFAULT false,
  taxable_total      inr NOT NULL DEFAULT 0,
  cgst_total         inr NOT NULL DEFAULT 0,
  sgst_total         inr NOT NULL DEFAULT 0,
  igst_total         inr NOT NULL DEFAULT 0,
  cess_total         inr NOT NULL DEFAULT 0,
  grand_total        inr NOT NULL DEFAULT 0,
  tds_section        text,
  tds_amount         inr NOT NULL DEFAULT 0,
  itc_eligibility    itc_eligibility NOT NULL DEFAULT 'eligible',
  itc_claim_period   text,                          -- '2026-10' when claimed in 3B
  agreed_terms_days  smallint,
  due_date           date NOT NULL,
  -- MSMED Act s.15: pay a micro/small supplier within the agreed period, max 45 days from acceptance
  msme_applicable    boolean NOT NULL DEFAULT false,
  msme_pay_by        date,
  amount_paid        inr NOT NULL DEFAULT 0,
  balance_due        inr GENERATED ALWAYS AS (grand_total - tds_amount - amount_paid) STORED,
  status             text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','booked','approved','partially_paid','paid','disputed','cancelled')),
  document_id        uuid,                          -- FK added in part 08
  journal_entry_id   uuid REFERENCES journal_entries(id),
  created_at         timestamptz NOT NULL DEFAULT now(),
  UNIQUE (entity_id, party_id, bill_no, fy),
  CHECK (NOT msme_applicable OR msme_pay_by <= accepted_on + 45)
);
CREATE INDEX purchase_bills_msme_ix ON purchase_bills (entity_id, msme_pay_by) WHERE msme_applicable AND status NOT IN ('paid','cancelled');

-- Fill MSME fields from the supplier's Udyam class on insert / change
CREATE OR REPLACE FUNCTION app.set_msme_pay_by() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE cls udyam_class;
BEGIN
  SELECT msme_class INTO cls FROM parties WHERE id = NEW.party_id;
  NEW.msme_applicable := cls IN ('micro','small');
  IF NEW.msme_applicable THEN
    NEW.msme_pay_by := NEW.accepted_on + least(coalesce(NEW.agreed_terms_days, 15), 45);
  ELSE
    NEW.msme_pay_by := NULL;
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER purchase_bills_msme BEFORE INSERT OR UPDATE OF party_id, accepted_on, agreed_terms_days ON purchase_bills
  FOR EACH ROW EXECUTE FUNCTION app.set_msme_pay_by();

CREATE TABLE purchase_bill_lines (
  id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  bill_id         uuid NOT NULL REFERENCES purchase_bills(id) ON DELETE CASCADE,
  line_no         smallint NOT NULL,
  item_id         uuid REFERENCES items(id),
  description     text NOT NULL,
  hsn_sac         hsn_sac,
  qty             qty NOT NULL DEFAULT 1,
  taxable_value   inr NOT NULL,
  gst_rate        numeric(5,2) NOT NULL,
  cgst_amount     inr NOT NULL DEFAULT 0,
  sgst_amount     inr NOT NULL DEFAULT 0,
  igst_amount     inr NOT NULL DEFAULT 0,
  cess_amount     inr NOT NULL DEFAULT 0,
  itc_eligibility itc_eligibility NOT NULL DEFAULT 'eligible',
  expense_account_id uuid REFERENCES ledger_accounts(id),
  UNIQUE (bill_id, line_no)
);

CREATE TABLE vendor_payments (
  id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  entity_id            uuid NOT NULL REFERENCES entities(id),
  party_id             uuid NOT NULL REFERENCES parties(id),
  payment_no           text NOT NULL,
  scheduled_on         date,
  paid_on              date,
  amount               inr NOT NULL CHECK (amount > 0),
  method               payment_method NOT NULL,
  bank_account_id      uuid REFERENCES bank_accounts(id),
  utr                  text,
  approval_request_id  uuid REFERENCES approval_requests(id),
  bank_transaction_id  uuid REFERENCES bank_transactions(id),
  status               text NOT NULL DEFAULT 'scheduled' CHECK (status IN ('draft','scheduled','approved','sent','paid','failed','cancelled')),
  journal_entry_id     uuid REFERENCES journal_entries(id),
  UNIQUE (entity_id, payment_no),
  CHECK (status <> 'paid' OR paid_on IS NOT NULL)
);

CREATE TABLE vendor_payment_allocations (
  payment_id  uuid NOT NULL REFERENCES vendor_payments(id) ON DELETE CASCADE,
  bill_id     uuid NOT NULL REFERENCES purchase_bills(id),
  amount      inr NOT NULL CHECK (amount > 0),
  PRIMARY KEY (payment_id, bill_id)
);

CREATE OR REPLACE FUNCTION app.refresh_bill_paid() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE b uuid := coalesce(NEW.bill_id, OLD.bill_id);
BEGIN
  UPDATE purchase_bills pb
     SET amount_paid = coalesce((SELECT sum(a.amount) FROM vendor_payment_allocations a
                                   JOIN vendor_payments p ON p.id = a.payment_id
                                  WHERE a.bill_id = b AND p.status = 'paid'), 0)
   WHERE pb.id = b;
  RETURN NULL;
END $$;
CREATE TRIGGER vendor_alloc_refresh AFTER INSERT OR UPDATE OR DELETE ON vendor_payment_allocations
  FOR EACH ROW EXECUTE FUNCTION app.refresh_bill_paid();

-- ---------------------------------------------------------------------
-- GST returns and portal data
-- ---------------------------------------------------------------------
CREATE TYPE gst_return_type AS ENUM ('GSTR1','IFF','GSTR3B','GSTR9','GSTR9C','CMP08','GSTR4','ITC04');

CREATE TABLE gst_returns (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  entity_id        uuid NOT NULL REFERENCES entities(id),
  registration_id  uuid NOT NULL REFERENCES entity_registrations(id),
  return_type      gst_return_type NOT NULL,
  period           text NOT NULL CHECK (period ~ '^[0-9]{4}-[0-9]{2}$'),   -- '2026-10' (annual returns: FY label)
  obligation_id    uuid REFERENCES obligations(id),
  status           text NOT NULL DEFAULT 'drafting' CHECK (status IN ('drafting','ready','submitted','filed')),
  summary          jsonb NOT NULL DEFAULT '{}',    -- table-wise figures (3.1, 4A, 4B…)
  output_tax       inr,
  itc_claimed      inr,
  cash_paid        inr,
  late_fee_paid    inr NOT NULL DEFAULT 0,
  arn              text,
  filed_at         timestamptz,
  filed_by         uuid REFERENCES users(id),
  UNIQUE (registration_id, return_type, period),
  CHECK (status <> 'filed' OR (arn IS NOT NULL AND filed_at IS NOT NULL))
);

ALTER TABLE sales_documents ADD CONSTRAINT sales_documents_gstr1_fk
  FOREIGN KEY (gstr1_return_id) REFERENCES gst_returns(id);

-- Supplier documents as reported to us on the portal (GSTR-2A live / 2B monthly)
CREATE TABLE gst_inward_docs (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  entity_id           uuid NOT NULL REFERENCES entities(id),
  registration_id     uuid NOT NULL REFERENCES entity_registrations(id),
  source              text NOT NULL CHECK (source IN ('2A','2B','IMS')),
  return_period       text NOT NULL,               -- '2026-10'
  supplier_gstin      gstin_no NOT NULL,
  supplier_name       text,
  doc_kind            text NOT NULL CHECK (doc_kind IN ('invoice','credit_note','debit_note','import_boe','isd')),
  doc_number          text NOT NULL,
  doc_date            date NOT NULL,
  taxable_value       inr NOT NULL,
  igst                inr NOT NULL DEFAULT 0,
  cgst                inr NOT NULL DEFAULT 0,
  sgst                inr NOT NULL DEFAULT 0,
  cess                inr NOT NULL DEFAULT 0,
  itc_available       boolean,
  supplier_filed_on   date,
  fetched_at          timestamptz NOT NULL DEFAULT now(),
  UNIQUE (registration_id, source, return_period, supplier_gstin, doc_kind, doc_number)
);

CREATE TABLE itc_matches (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  entity_id         uuid NOT NULL REFERENCES entities(id),
  purchase_bill_id  uuid REFERENCES purchase_bills(id),
  inward_doc_id     uuid REFERENCES gst_inward_docs(id),
  status            text NOT NULL CHECK (status IN ('matched','amount_differs','not_in_portal','not_in_books')),
  tax_difference    inr NOT NULL DEFAULT 0,
  action            text CHECK (action IN ('claim','claim_lower','defer','remind_supplier','ask_to_amend','book_bill','investigate','reject')),
  action_taken_at   timestamptz,
  action_by         uuid REFERENCES users(id),
  run_at            timestamptz NOT NULL DEFAULT now(),
  CHECK (purchase_bill_id IS NOT NULL OR inward_doc_id IS NOT NULL),
  CHECK (status <> 'matched'       OR (purchase_bill_id IS NOT NULL AND inward_doc_id IS NOT NULL)),
  CHECK (status <> 'not_in_portal' OR inward_doc_id IS NULL),
  CHECK (status <> 'not_in_books'  OR purchase_bill_id IS NULL)
);
CREATE INDEX itc_matches_open_ix ON itc_matches (entity_id, status) WHERE action_taken_at IS NULL;

CREATE TABLE gst_ledger_balances (
  registration_id  uuid NOT NULL REFERENCES entity_registrations(id),
  ledger           text NOT NULL CHECK (ledger IN ('cash','credit','liability')),
  head             text NOT NULL CHECK (head IN ('igst','cgst','sgst','cess')),
  as_of            date NOT NULL,
  balance          inr NOT NULL,
  PRIMARY KEY (registration_id, ledger, head, as_of)
);

-- ---------------------------------------------------------------------
-- Direct tax: TDS we deduct, challans, TDS returns, advance tax, ITR
-- ---------------------------------------------------------------------
CREATE TABLE tax_challans (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  entity_id           uuid NOT NULL REFERENCES entities(id),
  kind                text NOT NULL CHECK (kind IN ('itns281_tds','itns280_income_tax','gst_pmt06','epf_ecr','esic','professional_tax','other')),
  registration_id     uuid REFERENCES entity_registrations(id),     -- TAN / GSTIN / EPF code
  period_label        text NOT NULL,
  amount              inr NOT NULL CHECK (amount > 0),
  interest            inr NOT NULL DEFAULT 0,
  penalty             inr NOT NULL DEFAULT 0,
  paid_on             date,
  challan_ref         text,          -- CIN / CPIN / TRRN
  bsr_code            text,
  obligation_id       uuid REFERENCES obligations(id),
  bank_transaction_id uuid REFERENCES bank_transactions(id),
  status              text NOT NULL DEFAULT 'scheduled' CHECK (status IN ('draft','scheduled','paid','failed'))
);

CREATE TABLE tds_returns (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  entity_id        uuid NOT NULL REFERENCES entities(id),
  registration_id  uuid NOT NULL REFERENCES entity_registrations(id),   -- TAN
  form             text NOT NULL CHECK (form IN ('24Q','26Q','27Q','27EQ')),
  fy               fy_label NOT NULL,
  quarter          smallint NOT NULL CHECK (quarter BETWEEN 1 AND 4),
  fvu_validated_at timestamptz,
  token_no         text,
  filed_at         timestamptz,
  obligation_id    uuid REFERENCES obligations(id),
  status           text NOT NULL DEFAULT 'drafting' CHECK (status IN ('drafting','ready','filed','revised')),
  UNIQUE (registration_id, form, fy, quarter)
);

CREATE TABLE tds_deductions (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  entity_id           uuid NOT NULL REFERENCES entities(id),
  deductee_party_id   uuid REFERENCES parties(id),
  deductee_employee_id uuid,                          -- FK added in part 06
  deductee_pan        pan_no,
  section             text NOT NULL,                  -- '192','194C','194J','194Q','195'
  deducted_on         date NOT NULL,
  base_amount         inr NOT NULL,
  rate_pct            numeric(7,4) NOT NULL,
  tds_amount          inr NOT NULL CHECK (tds_amount >= 0),
  source_kind         text NOT NULL CHECK (source_kind IN ('purchase_bill','vendor_payment','payroll_run','manual')),
  source_id           uuid,
  fy                  fy_label NOT NULL GENERATED ALWAYS AS (app.fy_of(deducted_on)) STORED,
  challan_id          uuid REFERENCES tax_challans(id),
  tds_return_id       uuid REFERENCES tds_returns(id),
  certificate_issued_at timestamptz,                  -- Form 16A / 16
  CHECK (num_nonnulls(deductee_party_id, deductee_employee_id) = 1)
);
CREATE INDEX tds_deductions_unpaid_ix ON tds_deductions (entity_id, deducted_on) WHERE challan_id IS NULL;

CREATE TABLE advance_tax_instalments (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  entity_id       uuid NOT NULL REFERENCES entities(id),
  fy              fy_label NOT NULL,
  instalment_no   smallint NOT NULL CHECK (instalment_no BETWEEN 1 AND 4),
  due_date        date NOT NULL,                  -- 15 Jun / 15 Sep / 15 Dec / 15 Mar
  cumulative_pct  numeric(5,2) NOT NULL,          -- 15 / 45 / 75 / 100
  estimated_tax   inr,
  challan_id      uuid REFERENCES tax_challans(id),
  UNIQUE (entity_id, fy, instalment_no)
);

CREATE TABLE income_tax_returns (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  entity_id        uuid NOT NULL REFERENCES entities(id),
  assessment_year  text NOT NULL CHECK (assessment_year ~ '^[0-9]{4}-[0-9]{2}$'),
  form             text NOT NULL,                 -- 'ITR-6','ITR-5'
  audit_required   boolean NOT NULL DEFAULT false,
  audit_report_filed_at timestamptz,              -- Form 3CA/3CD
  filed_at         timestamptz,
  ack_no           text,
  intimation_143_1 jsonb,
  obligation_id    uuid REFERENCES obligations(id),
  UNIQUE (entity_id, assessment_year)
);
