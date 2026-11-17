-- =====================================================================
-- Part 43 · Registering an asset from a purchase bill line
-- An asset can now say which line of which purchase bill it was capitalised from, so the same line cannot be capitalised twice over
-- (the API checks what is left on the line) and the register can show where the cost came from.
-- =====================================================================
ALTER TABLE fixed_assets
  ADD COLUMN purchase_bill_line_no smallint,
  ADD CONSTRAINT asset_line_needs_bill CHECK (purchase_bill_line_no IS NULL OR purchase_bill_id IS NOT NULL),
  ADD CONSTRAINT asset_bill_line_fk FOREIGN KEY (purchase_bill_id, purchase_bill_line_no) REFERENCES purchase_bill_lines (bill_id, line_no);
CREATE INDEX fixed_assets_bill_line_ix ON fixed_assets (purchase_bill_id, purchase_bill_line_no) WHERE purchase_bill_id IS NOT NULL;
