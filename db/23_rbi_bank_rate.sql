-- =====================================================================
-- Part 23 · RBI bank rate history
-- Interest on delayed payments to micro and small suppliers (MSMED Act Sec 16) is three times the bank rate RBI notifies, so the
-- calculation needs the rate that applied in each month. This is a STARTER LIBRARY: the entries below must be checked against RBI's
-- notifications by a CA or CS before anyone relies on them, and a new row has to be added whenever RBI changes the rate (a rate takes
-- effect from `effective_from` until the next row). Nothing here edits an earlier row.
-- =====================================================================
INSERT INTO ref_rbi_bank_rate (effective_from, rate_pct) VALUES
  ('2023-02-08', 6.75),
  ('2025-02-07', 6.50),
  ('2025-04-09', 6.25),
  ('2025-06-06', 5.75),
  ('2025-12-05', 5.50)
ON CONFLICT (effective_from) DO NOTHING;
