-- =====================================================================
-- Part 36 · Imports that are free of charge, and whether the IGST on them is claimed
-- A sample arrives with nothing to pay the supplier, but the duty and the IGST are still paid at the port. Two things follow:
--   * the bill of entry has no purchase bill and no supplier payable (free_of_charge);
--   * the IGST is credit only if the law lets it be claimed. Section 17(5)(h) blocks credit on goods given away as free samples; samples
--     kept for the company's own use can carry it. That is a decision for the company and its CA, so it is recorded, not assumed.
-- Existing bills keep today's meaning: the IGST is credit once the duty is paid.
-- =====================================================================
ALTER TABLE bills_of_entry
  ADD COLUMN free_of_charge     boolean NOT NULL DEFAULT false,
  ADD COLUMN igst_credit_status text    NOT NULL DEFAULT 'eligible' CHECK (igst_credit_status IN ('eligible', 'blocked', 'undecided')),
  ADD CONSTRAINT boe_free_has_no_bill CHECK (NOT free_of_charge OR purchase_bill_id IS NULL);
