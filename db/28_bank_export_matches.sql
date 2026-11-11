-- =====================================================================
-- Part 28 · Bank credits matched to export proceeds
-- A credit that looks like an inward remittance can be suggested as the proceeds of a shipping bill. The statement shows the rupees after the bank's
-- charges and its own rate, so the suggestion is never exact: a person confirms the foreign amount and the charges when accepting it.
-- =====================================================================
ALTER TABLE bank_matches DROP CONSTRAINT bank_matches_target_kind_check;
ALTER TABLE bank_matches ADD CONSTRAINT bank_matches_target_kind_check
  CHECK (target_kind IN ('receipt','vendor_payment','journal_entry','payroll_run','tax_challan','shipping_bill'));
