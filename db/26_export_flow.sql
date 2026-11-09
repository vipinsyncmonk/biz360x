-- =====================================================================
-- Part 26 · The export flow
-- A customer outside India turns an invoice into an export invoice: foreign currency and the exchange rate on the document, the
-- foreign amount worked out by the database, the supply under LUT or with IGST, and a draft shipping bill opened when it is issued.
-- Nothing here talks to ICEGATE; a person still records what customs issued.
-- =====================================================================

-- Where the customer is, and what they are usually invoiced in
ALTER TABLE parties
  ADD COLUMN country  char(2) NOT NULL DEFAULT 'IN' CHECK (country ~ '^[A-Z]{2}$'),
  ADD COLUMN currency char(3) NOT NULL DEFAULT 'INR' CHECK (currency ~ '^[A-Z]{3}$');

-- The invoice stays in rupees (GST is declared in rupees); the foreign amount is derived from the grand total and the rate stated on it.
ALTER TABLE sales_documents
  ADD COLUMN currency      char(3) NOT NULL DEFAULT 'INR' CHECK (currency ~ '^[A-Z]{3}$'),
  ADD COLUMN exchange_rate numeric(12,4) CHECK (exchange_rate > 0),
  ADD COLUMN foreign_total numeric(15,2) GENERATED ALWAYS AS (
    CASE WHEN currency <> 'INR' AND exchange_rate IS NOT NULL THEN round(grand_total / exchange_rate, 2) END) STORED,
  ADD CONSTRAINT sales_document_currency_rate CHECK ((currency = 'INR' AND exchange_rate IS NULL) OR (currency <> 'INR' AND exchange_rate IS NOT NULL));

-- A shipping bill can be opened from the invoice before customs has issued its number, port and date
ALTER TABLE shipping_bills
  ALTER COLUMN sb_number DROP NOT NULL,
  ALTER COLUMN sb_date   DROP NOT NULL,
  ALTER COLUMN port_code DROP NOT NULL;
ALTER TABLE shipping_bills DROP CONSTRAINT shipping_bills_status_check;
ALTER TABLE shipping_bills ADD CONSTRAINT shipping_bills_status_check CHECK (status IN ('draft', 'filed', 'let_export', 'shipped', 'closed'));
ALTER TABLE shipping_bills ADD CONSTRAINT shipping_bill_filed_has_customs_details
  CHECK (status = 'draft' OR (sb_number IS NOT NULL AND sb_date IS NOT NULL AND port_code IS NOT NULL));
CREATE INDEX shipping_bills_invoice_ix ON shipping_bills (sales_document_id) WHERE sales_document_id IS NOT NULL;
