-- =====================================================================
-- Part 27 · Export proceeds in the books
-- When money arrives for an export, the bank credits rupees at the day's rate while the invoice was booked at the invoice's rate. The difference
-- is the foreign exchange gain or loss. A realisation can now carry the rupees the bank credited, the charges it deducted, the receipt it created
-- and the gain or loss it posted.
-- =====================================================================
ALTER TABLE export_realisations
  ADD COLUMN receipt_id   uuid REFERENCES receipts(id),
  ADD COLUMN amount_inr   numeric(15,2) CHECK (amount_inr > 0),                       -- what the bank credited, net of its charges
  ADD COLUMN bank_charges numeric(15,2) NOT NULL DEFAULT 0 CHECK (bank_charges >= 0),
  ADD COLUMN booked_inr   numeric(15,2),                                              -- the part of the invoice this settled, at the invoice's rate
  ADD COLUMN forex_gain   numeric(15,2),                                              -- positive gain, negative loss
  ADD CONSTRAINT export_realisation_posted CHECK ((amount_inr IS NULL) = (receipt_id IS NULL) AND (amount_inr IS NULL) = (booked_inr IS NULL) AND (amount_inr IS NULL) = (forex_gain IS NULL));
