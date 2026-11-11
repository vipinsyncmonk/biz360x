-- =====================================================================
-- Part 32 · Booking the bank statement
-- A bank line is half of a journal entry; the other half is an account (rent, a card, a deposit), a party, or a document the app already holds (a
-- payroll run, a vendor payment, a challan). This part adds what is needed to find and post that other half:
--   * what the narration says, read into parts (how the money moved, who it went to, the remark), so a rule can key on it;
--   * rules a company teaches ("lines to this UPI handle go to Rent");
--   * a suggestion that is an account, and the journal a line posted for itself.
-- A rule only ever suggests, or applies when the person says "apply to similar lines". Nothing posts by itself.
-- =====================================================================

ALTER TABLE bank_transactions
  ADD COLUMN mode             text,                                  -- upi, neft, rtgs, imps, auto_debit, transfer, cheque, card, cash, charges, interest, other
  ADD COLUMN counterparty_key text,                                  -- what identifies the other side for a rule: a UPI handle, a beneficiary, a card or deposit number
  ADD COLUMN remark           text,                                  -- what the sender typed (SalaryMarch26, Rent)
  ADD COLUMN journal_entry_id uuid REFERENCES journal_entries(id);   -- the journal this line posted for itself (not one it was matched to)
CREATE INDEX bank_txn_counterparty_ix ON bank_transactions (entity_id, counterparty_key) WHERE counterparty_key IS NOT NULL;

ALTER TABLE bank_matches DROP CONSTRAINT bank_matches_target_kind_check;
ALTER TABLE bank_matches ADD CONSTRAINT bank_matches_target_kind_check
  CHECK (target_kind IN ('receipt','vendor_payment','journal_entry','payroll_run','tax_challan','shipping_bill','ledger_account'));

CREATE TABLE bank_rules (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  entity_id        uuid NOT NULL REFERENCES entities(id),
  direction        text NOT NULL DEFAULT 'any' CHECK (direction IN ('debit', 'credit', 'any')),
  counterparty_key text,                                             -- an exact match on the parsed counterparty
  keyword          text,                                             -- a word or phrase in the narration (whole words, so "rent" does not match "current")
  mode             text,                                             -- how the money moved
  account_id       uuid NOT NULL REFERENCES ledger_accounts(id),     -- where lines that match are booked
  party_id         uuid REFERENCES parties(id),
  memo             text,
  hits             int  NOT NULL DEFAULT 0,
  last_hit_on      date,
  is_active        boolean NOT NULL DEFAULT true,
  created_by       uuid REFERENCES users(id),
  created_at       timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT bank_rule_needs_a_condition CHECK (num_nonnulls(counterparty_key, keyword, mode) >= 1)
);
CREATE UNIQUE INDEX bank_rules_one_live_uq ON bank_rules (entity_id, direction, coalesce(counterparty_key, ''), coalesce(keyword, ''), coalesce(mode, '')) WHERE is_active;

DO $$
BEGIN
  EXECUTE 'ALTER TABLE bank_rules ENABLE ROW LEVEL SECURITY';
  EXECUTE 'ALTER TABLE bank_rules FORCE ROW LEVEL SECURITY';
  EXECUTE $p$CREATE POLICY tenant_isolation ON bank_rules USING (entity_id IN (SELECT app.visible_entity_ids())) WITH CHECK (entity_id IN (SELECT app.visible_entity_ids()))$p$;
  EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON bank_rules TO biz_app';
  EXECUTE 'GRANT SELECT, INSERT, UPDATE ON bank_rules TO biz_worker';
END $$;
