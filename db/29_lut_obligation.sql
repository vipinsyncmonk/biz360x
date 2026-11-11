-- =====================================================================
-- Part 29 · The yearly LUT
-- An exporter who supplies without paying IGST needs a Letter of Undertaking (form RFD-11) for the financial year, filed before the first
-- export of that year. It is a compliance duty like a return: it appears when a year starts, stays open until a LUT that covers the whole
-- year is recorded, and turns overdue when the year has started without one (a LUT that has expired is the same thing: nothing covers
-- the new year). "Exporter" is derived: an IEC on record, a customer outside India, or an invoice in a foreign currency.
-- The figures and form are a starter entry and need a CA to confirm them.
-- =====================================================================

INSERT INTO compliance_rules (code, version, title, form_code, authority_code, statute_ref, frequency, per_registration, applicability, due_rule, penalty_rule, effective, change_note) VALUES
 ('GST.LUT.ANNUAL', 1, 'Letter of Undertaking for exports without IGST', 'RFD-11', 'GSTN', 'CGST Act s.16(3)(a); CGST Rules r.96A', 'annual', NULL,
   '{"all":[{"fact":"is_exporter","op":"=","value":true}]}',
   '{"type":"fixed_dates","dates":{"FY":"04-01"},"open_until_filed":true}',
   NULL,
   daterange('2017-07-01', NULL),
   'File a fresh LUT at the start of each financial year, before the first export of the year');
