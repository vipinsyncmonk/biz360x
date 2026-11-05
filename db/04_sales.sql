-- =====================================================================
-- Part 04 · Sales & Billing: quotes → orders → invoices / CN / DN →
--           e-invoice, e-way bill → payment links, receipts, dunning
-- =====================================================================

-- GST rule: invoice numbers ≤ 16 characters, unique per GSTIN per FY.
CREATE TYPE sales_doc_type AS ENUM ('tax_invoice','bill_of_supply','export_invoice','credit_note','debit_note','proforma');

CREATE TABLE document_series (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  entity_id       uuid NOT NULL REFERENCES entities(id),
  registration_id uuid NOT NULL REFERENCES entity_registrations(id),   -- supplier GSTIN
  doc_type        sales_doc_type NOT NULL,
  fy              fy_label NOT NULL,
  prefix          text NOT NULL,              -- 'NI/KA/' or 'NI/TN/CN-'
  pad_to          smallint NOT NULL DEFAULT 3,
  next_number     int NOT NULL DEFAULT 1 CHECK (next_number > 0),
  UNIQUE (registration_id, doc_type, fy),
  CHECK (length(prefix) + pad_to <= 16)
);

-- Atomically take the next number from a series
CREATE OR REPLACE FUNCTION app.next_document_number(p_series uuid) RETURNS text
LANGUAGE plpgsql AS $$
DECLARE s document_series;
BEGIN
  UPDATE document_series SET next_number = next_number + 1
   WHERE id = p_series RETURNING * INTO s;
  IF NOT FOUND THEN RAISE EXCEPTION 'Unknown series %', p_series; END IF;
  RETURN s.prefix || lpad((s.next_number - 1)::text, s.pad_to, '0');
END $$;

-- ---------------------------------------------------------------------
-- Quotes (versioned) and sales orders (support partial / lot invoicing)
-- ---------------------------------------------------------------------
CREATE TABLE quotes (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  entity_id        uuid NOT NULL REFERENCES entities(id),
  registration_id  uuid NOT NULL REFERENCES entity_registrations(id),
  party_id         uuid NOT NULL REFERENCES parties(id),
  quote_no         text NOT NULL,                   -- 'Q-0418'
  version          smallint NOT NULL DEFAULT 1,
  supersedes_id    uuid REFERENCES quotes(id),
  title            text,                            -- '40 pressure transmitters, 2 lots'
  status           text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','sent','viewed','negotiating','accepted','rejected','expired','lost')),
  place_of_supply  char(2) REFERENCES ref_states(gst_code),
  valid_until      date,
  advance_pct      numeric(5,2) CHECK (advance_pct BETWEEN 0 AND 100),
  payment_terms_days smallint,
  taxable_total    inr NOT NULL DEFAULT 0,
  tax_total        inr NOT NULL DEFAULT 0,
  grand_total      inr NOT NULL DEFAULT 0,
  sent_at          timestamptz,
  view_count       int NOT NULL DEFAULT 0,
  accepted_at      timestamptz,
  customer_po_ref  text,
  lost_reason      text,
  owner_user_id    uuid REFERENCES users(id),
  created_at       timestamptz NOT NULL DEFAULT now(),
  updated_at       timestamptz NOT NULL DEFAULT now(),
  UNIQUE (entity_id, quote_no, version),
  CHECK (status <> 'accepted' OR accepted_at IS NOT NULL)
);
CREATE TRIGGER quotes_touch BEFORE UPDATE ON quotes FOR EACH ROW EXECUTE FUNCTION app.touch_updated_at();

CREATE TABLE quote_lines (
  id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  quote_id      uuid NOT NULL REFERENCES quotes(id) ON DELETE CASCADE,
  line_no       smallint NOT NULL,
  item_id       uuid REFERENCES items(id),
  description   text NOT NULL,
  hsn_sac       hsn_sac NOT NULL,
  qty           qty NOT NULL CHECK (qty > 0),
  uqc           text NOT NULL DEFAULT 'NOS',
  rate          inr NOT NULL CHECK (rate >= 0),
  discount_pct  numeric(5,2) NOT NULL DEFAULT 0 CHECK (discount_pct BETWEEN 0 AND 100),
  gst_rate      numeric(5,2) NOT NULL,
  taxable_value inr GENERATED ALWAYS AS (round(qty * rate * (1 - discount_pct / 100), 2)) STORED,
  UNIQUE (quote_id, line_no)
);

CREATE TABLE sales_orders (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  entity_id        uuid NOT NULL REFERENCES entities(id),
  quote_id         uuid REFERENCES quotes(id),
  party_id         uuid NOT NULL REFERENCES parties(id),
  order_no         text NOT NULL,                   -- 'SO-0233'
  order_date       date NOT NULL,
  customer_po_ref  text,
  status           text NOT NULL DEFAULT 'open' CHECK (status IN ('open','partially_invoiced','invoiced','closed','cancelled')),
  UNIQUE (entity_id, order_no)
);

CREATE TABLE sales_order_lines (
  id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  order_id       uuid NOT NULL REFERENCES sales_orders(id) ON DELETE CASCADE,
  line_no        smallint NOT NULL,
  item_id        uuid REFERENCES items(id),
  description    text NOT NULL,
  hsn_sac        hsn_sac NOT NULL,
  ordered_qty    qty NOT NULL CHECK (ordered_qty > 0),
  invoiced_qty   qty NOT NULL DEFAULT 0,
  rate           inr NOT NULL,
  gst_rate       numeric(5,2) NOT NULL,
  UNIQUE (order_id, line_no),
  CHECK (invoiced_qty <= ordered_qty)
);

-- ---------------------------------------------------------------------
-- Sales documents: tax invoices, credit notes, debit notes (one table)
-- ---------------------------------------------------------------------
CREATE TYPE supply_type AS ENUM ('b2b','b2c_small','b2c_large','export_with_tax','export_under_lut','sez_with_tax','sez_under_lut','deemed_export');
CREATE TYPE cn_reason   AS ENUM ('sales_return','rate_revision','post_sale_discount','deficiency_in_service','correction','other');
CREATE TYPE irn_status  AS ENUM ('not_required','pending','generated','cancelled','failed');

CREATE TABLE sales_documents (
  id                     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  entity_id              uuid NOT NULL REFERENCES entities(id),
  doc_type               sales_doc_type NOT NULL,
  series_id              uuid REFERENCES document_series(id),
  number                 text,                         -- NULL while draft
  doc_date               date NOT NULL,
  fy                     fy_label NOT NULL GENERATED ALWAYS AS (app.fy_of(doc_date)) STORED,
  registration_id        uuid NOT NULL REFERENCES entity_registrations(id),   -- supplier GSTIN
  supplier_state         char(2) NOT NULL REFERENCES ref_states(gst_code),
  party_id               uuid NOT NULL REFERENCES parties(id),
  party_gst_id           uuid REFERENCES party_gst_registrations(id),
  buyer_gstin            text,                         -- snapshot at issue
  buyer_legal_name       text NOT NULL,                -- snapshot at issue
  bill_to                jsonb NOT NULL,               -- address snapshot
  ship_to                jsonb,
  place_of_supply        char(2) NOT NULL REFERENCES ref_states(gst_code),
  is_inter_state         boolean GENERATED ALWAYS AS (supplier_state <> place_of_supply) STORED,
  supply_type            supply_type NOT NULL DEFAULT 'b2b',
  reverse_charge         boolean NOT NULL DEFAULT false,
  quote_id               uuid REFERENCES quotes(id),
  sales_order_id         uuid REFERENCES sales_orders(id),
  customer_po_ref        text,
  -- credit / debit notes
  original_document_id   uuid REFERENCES sales_documents(id),
  cn_reason              cn_reason,
  gst_adjusted           boolean NOT NULL DEFAULT true,  -- false = commercial credit note (no GST change)
  -- money
  payment_terms_days     smallint,
  due_date               date,
  taxable_total          inr NOT NULL DEFAULT 0,
  cgst_total             inr NOT NULL DEFAULT 0,
  sgst_total             inr NOT NULL DEFAULT 0,
  igst_total             inr NOT NULL DEFAULT 0,
  cess_total             inr NOT NULL DEFAULT 0,
  round_off              inr NOT NULL DEFAULT 0 CHECK (abs(round_off) < 1),
  grand_total            inr NOT NULL DEFAULT 0,
  tds_expected           inr NOT NULL DEFAULT 0,       -- e.g. 194Q at 0.1% deducted by buyer
  amount_settled         inr NOT NULL DEFAULT 0,       -- maintained from settlements
  balance_due            inr GENERATED ALWAYS AS (grand_total - amount_settled) STORED,
  status                 text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','issued','cancelled')),
  payment_status         text NOT NULL DEFAULT 'unpaid' CHECK (payment_status IN ('unpaid','partially_paid','paid','written_off','not_applicable')),
  -- e-invoice (IRP)
  irn                    char(64),
  irn_ack_no             text,
  irn_ack_at             timestamptz,
  irn_signed_qr          text,
  irn_status             irn_status NOT NULL DEFAULT 'pending',
  irn_cancelled_at       timestamptz,
  -- reporting
  gstr1_return_id        uuid,                         -- FK added in part 05
  journal_entry_id       uuid REFERENCES journal_entries(id),
  issued_at              timestamptz,
  issued_by              uuid REFERENCES users(id),
  created_by             uuid REFERENCES users(id),
  created_at             timestamptz NOT NULL DEFAULT now(),
  updated_at             timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT number_len         CHECK (number IS NULL OR length(number) <= 16),
  CONSTRAINT issued_has_number  CHECK (status = 'draft' OR number IS NOT NULL),
  CONSTRAINT notes_link_invoice CHECK (doc_type NOT IN ('credit_note','debit_note') OR original_document_id IS NOT NULL),
  CONSTRAINT cn_has_reason      CHECK (doc_type <> 'credit_note' OR cn_reason IS NOT NULL),
  CONSTRAINT b2b_has_gstin      CHECK (supply_type <> 'b2b' OR doc_type = 'proforma' OR buyer_gstin IS NOT NULL),
  CONSTRAINT tax_split          CHECK ((is_inter_state AND cgst_total = 0 AND sgst_total = 0)
                                    OR (NOT is_inter_state AND igst_total = 0 AND cgst_total = sgst_total)),
  CONSTRAINT irn_when_generated CHECK (irn_status <> 'generated' OR (irn IS NOT NULL AND irn_ack_no IS NOT NULL))
);
CREATE UNIQUE INDEX sales_documents_number_uq ON sales_documents (registration_id, fy, number) WHERE number IS NOT NULL;
CREATE INDEX sales_documents_party_ix ON sales_documents (entity_id, party_id, doc_date);
CREATE INDEX sales_documents_open_ix  ON sales_documents (entity_id, due_date)
  WHERE status = 'issued' AND payment_status IN ('unpaid','partially_paid');
CREATE INDEX sales_documents_original_ix ON sales_documents (original_document_id) WHERE original_document_id IS NOT NULL;
CREATE TRIGGER sales_documents_touch BEFORE UPDATE ON sales_documents FOR EACH ROW EXECUTE FUNCTION app.touch_updated_at();

CREATE TABLE sales_document_lines (
  id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  document_id     uuid NOT NULL REFERENCES sales_documents(id) ON DELETE CASCADE,
  line_no         smallint NOT NULL,
  item_id         uuid REFERENCES items(id),
  so_line_id      bigint REFERENCES sales_order_lines(id),
  original_line_id bigint REFERENCES sales_document_lines(id),   -- for CN/DN lines
  description     text NOT NULL,
  hsn_sac         hsn_sac NOT NULL,
  is_service      boolean NOT NULL DEFAULT false,
  qty             qty NOT NULL CHECK (qty > 0),
  uqc             text NOT NULL DEFAULT 'NOS',
  rate            inr NOT NULL CHECK (rate >= 0),
  discount        inr NOT NULL DEFAULT 0 CHECK (discount >= 0),
  taxable_value   inr NOT NULL,
  gst_rate        numeric(5,2) NOT NULL,
  cgst_amount     inr NOT NULL DEFAULT 0,
  sgst_amount     inr NOT NULL DEFAULT 0,
  igst_amount     inr NOT NULL DEFAULT 0,
  cess_amount     inr NOT NULL DEFAULT 0,
  UNIQUE (document_id, line_no),
  CHECK (taxable_value = round(qty * rate - discount, 2))
);

-- E-way bills (outward sales, returns, branch transfers)
CREATE TABLE eway_bills (
  id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  entity_id          uuid NOT NULL REFERENCES entities(id),
  sales_document_id  uuid REFERENCES sales_documents(id),
  purpose            text NOT NULL DEFAULT 'supply' CHECK (purpose IN ('supply','sales_return','branch_transfer','job_work','export','other')),
  ewb_no             char(12) UNIQUE,
  goods_value        inr NOT NULL,
  from_pincode       char(6) NOT NULL,
  to_pincode         char(6) NOT NULL,
  distance_km        int,
  transport_mode     text CHECK (transport_mode IN ('road','rail','air','ship')),
  transporter_gstin  text,
  vehicle_no         text,                    -- Part B, at dispatch
  generated_at       timestamptz,
  valid_until        timestamptz,
  status             text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','generated','extended','cancelled','expired'))
);

-- ---------------------------------------------------------------------
-- Getting paid: virtual accounts, payment links, receipts, settlements
-- ---------------------------------------------------------------------
CREATE TABLE virtual_accounts (
  id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  entity_id          uuid NOT NULL REFERENCES entities(id),
  bank_account_id    uuid NOT NULL REFERENCES bank_accounts(id),
  va_number          text NOT NULL UNIQUE,
  ifsc               char(11) NOT NULL,
  sales_document_id  uuid REFERENCES sales_documents(id),   -- per invoice…
  party_id           uuid REFERENCES parties(id),           -- …or per customer
  status             text NOT NULL DEFAULT 'active' CHECK (status IN ('active','closed')),
  CHECK (num_nonnulls(sales_document_id, party_id) = 1)
);

CREATE TABLE payment_links (
  id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  entity_id          uuid NOT NULL REFERENCES entities(id),
  sales_document_id  uuid NOT NULL REFERENCES sales_documents(id),
  short_code         text NOT NULL UNIQUE,          -- pay.domain/<short_code>
  amount             inr NOT NULL CHECK (amount > 0),
  methods            text[] NOT NULL DEFAULT '{bank_transfer,upi,netbanking}',
  provider           text,                          -- payment gateway
  provider_ref       text,
  status             text NOT NULL DEFAULT 'active' CHECK (status IN ('active','paid','expired','cancelled')),
  first_viewed_at    timestamptz,
  expires_at         timestamptz,
  created_at         timestamptz NOT NULL DEFAULT now()
);

CREATE TYPE payment_method AS ENUM ('neft','rtgs','imps','upi','cheque','card','netbanking','treds','cash','adjustment');

CREATE TABLE receipts (
  id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  entity_id            uuid NOT NULL REFERENCES entities(id),
  party_id             uuid NOT NULL REFERENCES parties(id),
  receipt_no           text NOT NULL,
  received_on          date NOT NULL,
  amount               inr NOT NULL CHECK (amount > 0),
  method               payment_method NOT NULL,
  utr                  text,
  bank_account_id      uuid REFERENCES bank_accounts(id),
  bank_transaction_id  uuid REFERENCES bank_transactions(id),
  payment_link_id      uuid REFERENCES payment_links(id),
  is_advance           boolean NOT NULL DEFAULT false,   -- receipt voucher before supply
  journal_entry_id     uuid REFERENCES journal_entries(id),
  status               text NOT NULL DEFAULT 'confirmed' CHECK (status IN ('pending','confirmed','bounced','reversed')),
  created_at           timestamptz NOT NULL DEFAULT now(),
  UNIQUE (entity_id, receipt_no)
);

-- What reduces an invoice's balance: receipts, credit notes, TDS, write-offs
CREATE TABLE invoice_settlements (
  id                 bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  entity_id          uuid NOT NULL REFERENCES entities(id),
  invoice_id         uuid NOT NULL REFERENCES sales_documents(id),
  source_kind        text NOT NULL CHECK (source_kind IN ('receipt','credit_note','tds','write_off')),
  receipt_id         uuid REFERENCES receipts(id),
  credit_note_id     uuid REFERENCES sales_documents(id),
  amount             inr NOT NULL CHECK (amount > 0),
  settled_on         date NOT NULL,
  CHECK ((source_kind = 'receipt')     = (receipt_id IS NOT NULL)),
  CHECK ((source_kind = 'credit_note') = (credit_note_id IS NOT NULL))
);
CREATE INDEX invoice_settlements_invoice_ix ON invoice_settlements (invoice_id);

CREATE OR REPLACE FUNCTION app.refresh_invoice_settled() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE inv uuid := coalesce(NEW.invoice_id, OLD.invoice_id); settled numeric; total numeric;
BEGIN
  SELECT coalesce(sum(amount), 0) INTO settled FROM invoice_settlements WHERE invoice_id = inv;
  SELECT grand_total INTO total FROM sales_documents WHERE id = inv;
  IF settled > total THEN
    RAISE EXCEPTION 'Settlements (%) exceed invoice total (%)', settled, total;
  END IF;
  UPDATE sales_documents
     SET amount_settled = settled,
         payment_status = CASE WHEN settled = 0 THEN 'unpaid'
                               WHEN settled < total THEN 'partially_paid'
                               ELSE 'paid' END
   WHERE id = inv;
  RETURN NULL;
END $$;
CREATE TRIGGER invoice_settlements_refresh AFTER INSERT OR UPDATE OR DELETE ON invoice_settlements
  FOR EACH ROW EXECUTE FUNCTION app.refresh_invoice_settled();

-- TDS deducted by customers (claimed via Form 26AS / 16A)
CREATE TABLE tds_receivables (
  id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  entity_id          uuid NOT NULL REFERENCES entities(id),
  party_id           uuid NOT NULL REFERENCES parties(id),
  sales_document_id  uuid REFERENCES sales_documents(id),
  section            text NOT NULL,           -- '194Q','194C','194J'
  amount             inr NOT NULL CHECK (amount > 0),
  fy                 fy_label NOT NULL,
  quarter            smallint NOT NULL CHECK (quarter BETWEEN 1 AND 4),
  seen_in_26as_at    timestamptz,
  form16a_document_id uuid                    -- FK added in part 08
);

-- ---------------------------------------------------------------------
-- Collections (dunning) and recurring invoices
-- ---------------------------------------------------------------------
CREATE TABLE dunning_sequences (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  entity_id   uuid NOT NULL REFERENCES entities(id),
  name        text NOT NULL,
  is_default  boolean NOT NULL DEFAULT false
);
CREATE UNIQUE INDEX dunning_default_uq ON dunning_sequences (entity_id) WHERE is_default;

CREATE TABLE dunning_steps (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  sequence_id    uuid NOT NULL REFERENCES dunning_sequences(id) ON DELETE CASCADE,
  offset_days    smallint NOT NULL,                 -- −3 = three days before due
  action         text NOT NULL CHECK (action IN ('message','task','escalate','hold_orders')),
  channels       text[] NOT NULL DEFAULT '{email}', -- email, whatsapp, sms
  template_key   text,
  attach_statement boolean NOT NULL DEFAULT false,
  UNIQUE (sequence_id, offset_days, action)
);

CREATE TABLE party_dunning (
  party_id      uuid PRIMARY KEY REFERENCES parties(id) ON DELETE CASCADE,
  sequence_id   uuid REFERENCES dunning_sequences(id),
  paused_until  date,
  pause_reason  text
);

CREATE TABLE dunning_events (
  id                 bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  sales_document_id  uuid NOT NULL REFERENCES sales_documents(id),
  step_id            uuid REFERENCES dunning_steps(id),
  channel            text NOT NULL,
  sent_at            timestamptz NOT NULL DEFAULT now(),
  delivery_status    text CHECK (delivery_status IN ('queued','sent','delivered','read','failed')),
  task_id            uuid REFERENCES tasks(id)
);
CREATE INDEX dunning_events_doc_ix ON dunning_events (sales_document_id, sent_at);

CREATE TABLE recurring_invoices (
  id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  entity_id             uuid NOT NULL REFERENCES entities(id),
  party_id              uuid NOT NULL REFERENCES parties(id),
  registration_id       uuid NOT NULL REFERENCES entity_registrations(id),
  name                  text NOT NULL,                     -- 'Calibration AMC'
  template_document_id  uuid NOT NULL REFERENCES sales_documents(id),
  frequency             text NOT NULL CHECK (frequency IN ('monthly','quarterly','half_yearly','annual')),
  bill_in_advance       boolean NOT NULL DEFAULT true,
  next_run_on           date NOT NULL,
  ends_on               date,
  auto_issue            boolean NOT NULL DEFAULT false,
  is_active             boolean NOT NULL DEFAULT true
);
