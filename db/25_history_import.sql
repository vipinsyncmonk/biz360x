-- Opening position imported from the company's earlier books: an opening trial balance, and the invoices and bills still open on that date.
-- Each import is a batch, so it can be seen and undone as a whole. Rows created by a batch carry its id.
CREATE TABLE import_batches (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  entity_id   uuid NOT NULL REFERENCES entities(id),
  kind        text NOT NULL CHECK (kind IN ('opening_balances', 'open_invoices', 'open_bills')),
  label       text,
  as_of       date NOT NULL,
  row_count   integer NOT NULL CHECK (row_count > 0),
  summary     jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_by  uuid REFERENCES users(id),
  created_at  timestamptz NOT NULL DEFAULT now(),
  undone_at   timestamptz,
  undone_by   uuid REFERENCES users(id)
);
CREATE INDEX import_batches_entity_ix ON import_batches (entity_id, created_at DESC);

ALTER TABLE sales_documents  ADD COLUMN import_batch_id uuid REFERENCES import_batches(id);
ALTER TABLE purchase_bills   ADD COLUMN import_batch_id uuid REFERENCES import_batches(id);
ALTER TABLE journal_entries  ADD COLUMN import_batch_id uuid REFERENCES import_batches(id);

DO $$
BEGIN
  ALTER TABLE import_batches ENABLE ROW LEVEL SECURITY;
  ALTER TABLE import_batches FORCE ROW LEVEL SECURITY;
  CREATE POLICY tenant_isolation ON import_batches USING (entity_id IN (SELECT app.visible_entity_ids())) WITH CHECK (entity_id IN (SELECT app.visible_entity_ids()));
  GRANT SELECT, INSERT, UPDATE ON import_batches TO biz_app;
  GRANT SELECT ON import_batches TO biz_worker;
END $$;
