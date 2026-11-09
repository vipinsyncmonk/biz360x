-- =====================================================================
-- Part 24 · Exports and imports
-- Shipping bills (exports), the realisation of export proceeds, and bills of entry (imports). A person records what customs and the
-- bank issued; nothing here talks to ICEGATE or DGFT. Rupee values are worked out from the foreign amount and the exchange rate on
-- the document, so they can never disagree with it.
-- =====================================================================
CREATE TABLE shipping_bills (
  id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  entity_id          uuid NOT NULL REFERENCES entities(id),
  sales_document_id  uuid REFERENCES sales_documents(id),            -- the export invoice, when there is one
  buyer_party_id     uuid REFERENCES parties(id),
  sb_number          text NOT NULL CHECK (sb_number ~ '^[0-9A-Z/-]{1,20}$'),
  sb_date            date NOT NULL,
  port_code          text NOT NULL CHECK (port_code ~ '^[A-Z]{2}[A-Z0-9]{4}$'),           -- ICEGATE location code, e.g. INMAA1
  currency           char(3) NOT NULL CHECK (currency ~ '^[A-Z]{3}$'),
  fob_foreign        numeric(15,2) NOT NULL CHECK (fob_foreign > 0),
  exchange_rate      numeric(12,4) NOT NULL CHECK (exchange_rate > 0),                    -- the customs notified rate on the shipping bill
  fob_inr            numeric(15,2) GENERATED ALWAYS AS (round(fob_foreign * exchange_rate, 2)) STORED,
  status             text NOT NULL DEFAULT 'filed' CHECK (status IN ('filed', 'let_export', 'shipped', 'closed')),
  exported_on        date,                                                                  -- the date of export: starts the realisation clock
  rodtep_claimed     numeric(15,2) NOT NULL DEFAULT 0 CHECK (rodtep_claimed >= 0),
  rodtep_credited    numeric(15,2) NOT NULL DEFAULT 0 CHECK (rodtep_credited >= 0),
  drawback_claimed   numeric(15,2) NOT NULL DEFAULT 0 CHECK (drawback_claimed >= 0),
  drawback_received  numeric(15,2) NOT NULL DEFAULT 0 CHECK (drawback_received >= 0),
  brc_ref            text,                                                                  -- e-BRC reference once DGFT issues it
  created_at         timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT shipping_bill_dates CHECK (exported_on IS NULL OR exported_on >= sb_date),
  CONSTRAINT shipping_bill_claims CHECK (rodtep_credited <= rodtep_claimed AND drawback_received <= drawback_claimed),
  UNIQUE (entity_id, port_code, sb_number, sb_date)
);

CREATE TABLE export_realisations (
  id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  entity_id          uuid NOT NULL REFERENCES entities(id),
  shipping_bill_id   uuid NOT NULL REFERENCES shipping_bills(id) ON DELETE CASCADE,
  received_on        date NOT NULL,
  amount_foreign     numeric(15,2) NOT NULL CHECK (amount_foreign > 0),
  bank_ref           text,
  firc_no            text,                                                                  -- foreign inward remittance certificate
  created_at         timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX export_realisations_sb_ix ON export_realisations (shipping_bill_id);

CREATE TABLE bills_of_entry (
  id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  entity_id          uuid NOT NULL REFERENCES entities(id),
  supplier_party_id  uuid REFERENCES parties(id),
  purchase_bill_id   uuid REFERENCES purchase_bills(id),
  boe_number         text NOT NULL CHECK (boe_number ~ '^[0-9A-Z/-]{1,20}$'),
  boe_date           date NOT NULL,
  port_code          text NOT NULL CHECK (port_code ~ '^[A-Z]{2}[A-Z0-9]{4}$'),
  currency           char(3) NOT NULL CHECK (currency ~ '^[A-Z]{3}$'),
  cif_foreign        numeric(15,2) NOT NULL CHECK (cif_foreign > 0),
  exchange_rate      numeric(12,4) NOT NULL CHECK (exchange_rate > 0),
  cif_inr            numeric(15,2) GENERATED ALWAYS AS (round(cif_foreign * exchange_rate, 2)) STORED,
  bcd                numeric(15,2) NOT NULL DEFAULT 0 CHECK (bcd >= 0),                     -- basic customs duty
  sws                numeric(15,2) NOT NULL DEFAULT 0 CHECK (sws >= 0),                     -- social welfare surcharge
  igst               numeric(15,2) NOT NULL DEFAULT 0 CHECK (igst >= 0),                    -- IGST on imports: input tax credit once paid
  other_duty         numeric(15,2) NOT NULL DEFAULT 0 CHECK (other_duty >= 0),
  duty_total         numeric(15,2) GENERATED ALWAYS AS (bcd + sws + igst + other_duty) STORED,
  status             text NOT NULL DEFAULT 'filed' CHECK (status IN ('filed', 'assessed', 'duty_paid', 'cleared')),
  duty_paid_on       date,
  created_at         timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT boe_paid_has_date CHECK (status NOT IN ('duty_paid', 'cleared') OR duty_paid_on IS NOT NULL),
  UNIQUE (entity_id, port_code, boe_number, boe_date)
);

-- Row-level security and grants, as for every table that holds a company's own records
DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['shipping_bills', 'export_realisations', 'bills_of_entry'] LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
    EXECUTE format($p$CREATE POLICY tenant_isolation ON %I USING (entity_id IN (SELECT app.visible_entity_ids())) WITH CHECK (entity_id IN (SELECT app.visible_entity_ids()))$p$, t);
    EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON %I TO biz_app', t);
    EXECUTE format('GRANT SELECT, INSERT, UPDATE ON %I TO biz_worker', t);
  END LOOP;
END $$;
