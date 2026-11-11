-- =====================================================================
-- Part 33 · Fixed assets and depreciation
-- A register of what the company owns for more than a year (computers, furniture, plant, vehicles, software), the depreciation that is charged
-- on it each month under the Companies Act, and the sale or write-off of an asset. Depreciation is posted as one journal per month for all
-- assets. Nothing posts by itself: a person runs a month, and a locked period refuses it like any other journal.
-- Income-tax depreciation (block of assets) is a different calculation and is not here.
-- =====================================================================
ALTER TYPE journal_source ADD VALUE IF NOT EXISTS 'asset';

CREATE TABLE fixed_assets (
  id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  entity_id             uuid NOT NULL REFERENCES entities(id),
  asset_no              text NOT NULL,
  name                  text NOT NULL CHECK (length(btrim(name)) > 0),
  category              text NOT NULL CHECK (category IN ('building', 'plant_machinery', 'furniture', 'office_equipment', 'computers', 'servers_networks', 'vehicles', 'electrical', 'software', 'other')),
  location              text,
  serial_no             text,
  party_id              uuid REFERENCES parties(id),                          -- the supplier
  purchase_bill_id      uuid REFERENCES purchase_bills(id),
  bill_of_entry_id      uuid REFERENCES bills_of_entry(id),
  document_id           uuid REFERENCES documents(id),                        -- the invoice or other proof, kept in the vault
  acquired_on           date NOT NULL,
  put_to_use_on         date NOT NULL,                                        -- depreciation starts here
  cost                  numeric(18,2) NOT NULL CHECK (cost > 0),              -- what is capitalised: price + duty + freight + installation, without GST that is claimed as credit
  residual_value        numeric(18,2) NOT NULL DEFAULT 0 CHECK (residual_value >= 0),
  method                text NOT NULL CHECK (method IN ('slm', 'wdv')),
  life_months           int  CHECK (life_months BETWEEN 1 AND 1200),          -- straight line
  rate_pct              numeric(6,2) CHECK (rate_pct > 0 AND rate_pct < 100),-- written down value, per year
  booking               text NOT NULL CHECK (booking IN ('reclassify', 'already_booked', 'opening')),
  opening_accumulated   numeric(18,2) NOT NULL DEFAULT 0 CHECK (opening_accumulated >= 0),   -- depreciation already charged before this register (opening assets)
  opening_through       date,                                                 -- the month-end that figure is as of; depreciation here starts the month after
  acquisition_journal_id uuid REFERENCES journal_entries(id),
  status                text NOT NULL DEFAULT 'in_use' CHECK (status IN ('in_use', 'disposed')),
  disposed_on           date,
  disposal_proceeds     numeric(18,2) CHECK (disposal_proceeds >= 0),
  disposal_journal_id   uuid REFERENCES journal_entries(id),
  created_by            uuid REFERENCES users(id),
  created_at            timestamptz NOT NULL DEFAULT now(),
  UNIQUE (entity_id, asset_no),
  CONSTRAINT asset_method_fields CHECK ((method = 'slm' AND life_months IS NOT NULL AND rate_pct IS NULL) OR (method = 'wdv' AND rate_pct IS NOT NULL AND life_months IS NULL)),
  CONSTRAINT asset_residual_below_cost CHECK (residual_value < cost),
  CONSTRAINT asset_dates CHECK (put_to_use_on >= acquired_on),
  CONSTRAINT asset_opening_fields CHECK ((booking = 'opening') = (opening_through IS NOT NULL) AND opening_accumulated < cost - residual_value + 0.01),
  CONSTRAINT asset_disposal_fields CHECK ((status = 'disposed') = (disposed_on IS NOT NULL AND disposal_proceeds IS NOT NULL AND disposal_journal_id IS NOT NULL) AND (disposed_on IS NULL OR disposed_on >= put_to_use_on))
);
CREATE INDEX fixed_assets_entity_ix ON fixed_assets (entity_id, status);

-- One row per asset per month it was depreciated. The journal is shared by all assets of that month (a disposal month has its own).
CREATE TABLE asset_depreciation (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  entity_id         uuid NOT NULL REFERENCES entities(id),
  asset_id          uuid NOT NULL REFERENCES fixed_assets(id),
  period_month      date NOT NULL CHECK (period_month = date_trunc('month', period_month)::date),   -- the first day of the month
  amount            numeric(18,2) NOT NULL CHECK (amount >= 0),
  book_value_after  numeric(18,2) NOT NULL CHECK (book_value_after >= 0),
  journal_entry_id  uuid NOT NULL REFERENCES journal_entries(id),
  created_at        timestamptz NOT NULL DEFAULT now(),
  UNIQUE (asset_id, period_month)
);
CREATE INDEX asset_depreciation_period_ix ON asset_depreciation (entity_id, period_month);

DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['fixed_assets', 'asset_depreciation'] LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
    EXECUTE format($p$CREATE POLICY tenant_isolation ON %I USING (entity_id IN (SELECT app.visible_entity_ids())) WITH CHECK (entity_id IN (SELECT app.visible_entity_ids()))$p$, t);
    EXECUTE format('GRANT SELECT, INSERT, UPDATE ON %I TO biz_app, biz_worker', t);
  END LOOP;
END $$;
