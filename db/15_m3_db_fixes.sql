-- =====================================================================
-- Part 15 · Fixes found while building M3
-- 1. MSME trigger must cope with suppliers that have no Udyam class
-- 2. The matching worker re-runs a period, so it must be able to delete its own unactioned rows
-- app.set_msme_pay_by() assigned `cls IN ('micro','small')`, which is NULL (not false)
-- for a supplier with no msme_class, so every bill from an ordinary vendor failed
-- the NOT NULL constraint on purchase_bills.msme_applicable.
-- =====================================================================
CREATE OR REPLACE FUNCTION app.set_msme_pay_by() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE cls udyam_class;
BEGIN
  SELECT msme_class INTO cls FROM parties WHERE id = NEW.party_id;
  NEW.msme_applicable := coalesce(cls IN ('micro', 'small'), false);
  IF NEW.msme_applicable THEN
    NEW.msme_pay_by := NEW.accepted_on + least(coalesce(NEW.agreed_terms_days, 15), 45);
  ELSE
    NEW.msme_pay_by := NULL;
  END IF;
  RETURN NEW;
END $$;

-- Re-matching a period replaces the rows nobody has acted on yet.
GRANT DELETE ON itc_matches TO biz_worker;
