-- =====================================================================
-- Part 31 · An export invoice priced in the customer's currency
-- Until now an export invoice was entered in rupees and the foreign amount was worked out from the rupee total (the column was generated). A
-- supplier often has to invoice in dollars: the customer's PO and the contract are in dollars, so the dollar amounts on the invoice must be exactly
-- what the person typed, with the rupee value worked from them at the rate for the books and GST.
--   * the line keeps the rate, discount and amount in the foreign currency (foreign_rate, foreign_discount, foreign_taxable); the rupee columns stay the
--     GST value, converted at the document's rate;
--   * the document's foreign_total becomes a stored figure the application sets (it can no longer come from the rupee total, which GST rounds to whole
--     rupees), and priced_in_foreign says which way round the document was entered.
-- =====================================================================

ALTER TABLE sales_documents DROP COLUMN foreign_total;
ALTER TABLE sales_documents
  ADD COLUMN foreign_total      numeric(15,2),
  ADD COLUMN priced_in_foreign  boolean NOT NULL DEFAULT false;
UPDATE sales_documents SET foreign_total = round(grand_total / exchange_rate, 2) WHERE currency <> 'INR' AND exchange_rate IS NOT NULL;
ALTER TABLE sales_documents
  ADD CONSTRAINT sales_document_foreign_pricing_needs_currency CHECK (NOT priced_in_foreign OR (currency <> 'INR' AND exchange_rate IS NOT NULL));

ALTER TABLE sales_document_lines
  ADD COLUMN foreign_rate     numeric(18,4) CHECK (foreign_rate >= 0),
  ADD COLUMN foreign_discount numeric(18,2) CHECK (foreign_discount >= 0),
  ADD COLUMN foreign_taxable  numeric(18,2),
  ADD CONSTRAINT sales_document_line_foreign_complete CHECK ((foreign_rate IS NULL) = (foreign_taxable IS NULL));

-- The rupee taxable value of a foreign-priced line is the foreign amount converted at the document's rate, so it is no longer qty x rate - discount in rupees
DO $$
DECLARE c record;
BEGIN
  FOR c IN SELECT conname FROM pg_constraint
            WHERE conrelid = 'sales_document_lines'::regclass AND contype = 'c' AND pg_get_constraintdef(oid) LIKE '%taxable_value = round(%'
  LOOP
    EXECUTE format('ALTER TABLE sales_document_lines DROP CONSTRAINT %I', c.conname);
  END LOOP;
END $$;
ALTER TABLE sales_document_lines
  ADD CONSTRAINT sales_document_line_taxable CHECK (foreign_taxable IS NOT NULL OR taxable_value = round(qty * rate - discount, 2));
