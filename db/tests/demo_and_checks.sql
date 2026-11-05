-- =====================================================================
-- Demo data (Nimbus Instruments, as in the designs) + behaviour checks.
-- Run after parts 01–10 on a scratch database. Every check raises on failure.
-- =====================================================================
\set ON_ERROR_STOP on

-- ---------- demo tenant ----------
INSERT INTO organizations (id, name, kind, plan) VALUES
  ('00000000-0000-0000-0000-0000000000a1', 'Nimbus group', 'business_group', 'grow'),
  ('00000000-0000-0000-0000-0000000000a2', 'Mehta & Co', 'ca_firm', 'group'),
  ('00000000-0000-0000-0000-0000000000a3', 'Unrelated Co', 'business_group', 'launch');

INSERT INTO entities (id, organization_id, legal_name, constitution, track, pan, cin, incorporated_on, registered_state) VALUES
  ('00000000-0000-0000-0000-0000000000e1', '00000000-0000-0000-0000-0000000000a1', 'Nimbus Instruments Private Limited',
   'private_limited', 'business', 'AAHCN1234Q', 'U26510KA2019PTC123456', '2019-08-14', '29'),
  ('00000000-0000-0000-0000-0000000000e9', '00000000-0000-0000-0000-0000000000a3', 'Someone Else Pvt Ltd',
   'private_limited', 'business', 'AAACS9999Z', 'U72900KA2020PTC111111', '2020-01-01', '29');

INSERT INTO entity_registrations (id, entity_id, kind, number, state_code, label, gst_filing_frequency) VALUES
  ('00000000-0000-0000-0000-000000000071', '00000000-0000-0000-0000-0000000000e1', 'gstin', '29AAHCN1234Q1Z8', '29', 'Bengaluru', 'monthly'),
  ('00000000-0000-0000-0000-000000000072', '00000000-0000-0000-0000-0000000000e1', 'gstin', '33AAHCN1234Q1Z4', '33', 'Chennai',   'monthly'),
  ('00000000-0000-0000-0000-000000000073', '00000000-0000-0000-0000-0000000000e1', 'tan',   'BLRN12345A',      '29', 'TAN', NULL);

INSERT INTO users (id, email, full_name) VALUES
  ('00000000-0000-0000-0000-000000000011', 'ananya@example.in', 'Ananya Rao'),
  ('00000000-0000-0000-0000-000000000012', 'priya@example.in',  'Priya S'),
  ('00000000-0000-0000-0000-000000000013', 'rohan@example.in',  'Rohan Mehta'),
  ('00000000-0000-0000-0000-000000000014', 'stranger@example.in','Stranger');

INSERT INTO memberships (entity_id, user_id, role) VALUES
  ('00000000-0000-0000-0000-0000000000e1', '00000000-0000-0000-0000-000000000011', 'owner'),
  ('00000000-0000-0000-0000-0000000000e1', '00000000-0000-0000-0000-000000000012', 'maker');
INSERT INTO org_members VALUES ('00000000-0000-0000-0000-0000000000a2', '00000000-0000-0000-0000-000000000013', 'partner');
INSERT INTO advisor_grants (entity_id, advisor_org_id, advisor_name, advisor_kind, scopes, status) VALUES
  ('00000000-0000-0000-0000-0000000000e1', '00000000-0000-0000-0000-0000000000a2', 'Mehta & Co', 'chartered_accountant',
   '{"finance":"read","gst":"prepare"}', 'active');

-- ---------- parties, items, series ----------
INSERT INTO parties (id, entity_id, legal_name, is_customer, deducts_tds_194q, payment_terms_days) VALUES
  ('00000000-0000-0000-0000-000000000021', '00000000-0000-0000-0000-0000000000e1', 'Coastal Grid Utilities Ltd', true, true, 30);
INSERT INTO party_gst_registrations (id, party_id, gstin, state_code, is_default) VALUES
  ('00000000-0000-0000-0000-000000000031', '00000000-0000-0000-0000-000000000021', '32AABCC4521R1ZR', '32', true);
INSERT INTO parties (id, entity_id, legal_name, is_vendor, msme_class, udyam_number) VALUES
  ('00000000-0000-0000-0000-000000000022', '00000000-0000-0000-0000-0000000000e1', 'Shakti Castings', true, 'small', 'UDYAM-KR-03-0012345');

INSERT INTO document_series (id, entity_id, registration_id, doc_type, fy, prefix, next_number) VALUES
  ('00000000-0000-0000-0000-000000000041', '00000000-0000-0000-0000-0000000000e1', '00000000-0000-0000-0000-000000000071', 'tax_invoice', '2026-27', 'NI/KA/', 212),
  ('00000000-0000-0000-0000-000000000042', '00000000-0000-0000-0000-0000000000e1', '00000000-0000-0000-0000-000000000071', 'credit_note', '2026-27', 'NI/KA/CN-', 14);

-- ---------- invoice NI/KA/212 (KA → Kerala, so IGST) ----------
INSERT INTO sales_documents (id, entity_id, doc_type, series_id, number, doc_date, registration_id, supplier_state,
       party_id, party_gst_id, buyer_gstin, buyer_legal_name, bill_to, place_of_supply, supply_type,
       payment_terms_days, due_date, taxable_total, igst_total, grand_total, tds_expected, status,
       irn, irn_ack_no, irn_ack_at, irn_status, issued_at)
VALUES ('00000000-0000-0000-0000-000000000051', '00000000-0000-0000-0000-0000000000e1', 'tax_invoice',
       '00000000-0000-0000-0000-000000000041', app.next_document_number('00000000-0000-0000-0000-000000000041'),
       '2026-10-29', '00000000-0000-0000-0000-000000000071', '29',
       '00000000-0000-0000-0000-000000000021', '00000000-0000-0000-0000-000000000031', '32AABCC4521R1ZR',
       'Coastal Grid Utilities Ltd', '{"city":"Kochi","state":"32"}', '32', 'b2b',
       30, '2026-11-28', 400000, 72000, 472000, 400, 'issued',
       repeat('a', 64), '112610290001', now(), 'generated', now());

INSERT INTO sales_document_lines (document_id, line_no, description, hsn_sac, qty, rate, taxable_value, gst_rate, igst_amount) VALUES
  ('00000000-0000-0000-0000-000000000051', 1, 'Pressure transmitter PT-400', '902620', 25, 14500, 362500, 18, 65250),
  ('00000000-0000-0000-0000-000000000051', 2, 'NABL calibration certificate', '998346', 25, 1200, 30000, 18, 5400),
  ('00000000-0000-0000-0000-000000000051', 3, 'Freight and transit insurance', '996511', 1, 7500, 7500, 18, 1350);

DO $$ BEGIN
  ASSERT (SELECT number FROM sales_documents WHERE id = '00000000-0000-0000-0000-000000000051') = 'NI/KA/212', 'series numbering';
  ASSERT (SELECT is_inter_state FROM sales_documents WHERE id = '00000000-0000-0000-0000-000000000051'), 'KA→KL must be inter-state';
  ASSERT (SELECT fy FROM sales_documents WHERE id = '00000000-0000-0000-0000-000000000051') = '2026-27', 'fy derived';
END $$;

-- CHECK: an inter-state invoice cannot carry CGST/SGST
DO $$ BEGIN
  BEGIN
    UPDATE sales_documents SET cgst_total = 10, sgst_total = 10 WHERE id = '00000000-0000-0000-0000-000000000051';
    RAISE EXCEPTION 'tax_split check did not fire';
  EXCEPTION WHEN check_violation THEN NULL;
  END;
END $$;

-- ---------- receipt + credit note settle the invoice ----------
INSERT INTO receipts (id, entity_id, party_id, receipt_no, received_on, amount, method, utr) VALUES
  ('00000000-0000-0000-0000-000000000061', '00000000-0000-0000-0000-0000000000e1', '00000000-0000-0000-0000-000000000021', 'RCPT-0901', '2026-11-20', 200000, 'neft', 'UTR123');
INSERT INTO invoice_settlements (entity_id, invoice_id, source_kind, receipt_id, amount, settled_on) VALUES
  ('00000000-0000-0000-0000-0000000000e1', '00000000-0000-0000-0000-000000000051', 'receipt', '00000000-0000-0000-0000-000000000061', 200000, '2026-11-20');

DO $$ BEGIN
  ASSERT (SELECT payment_status FROM sales_documents WHERE id = '00000000-0000-0000-0000-000000000051') = 'partially_paid', 'partial payment status';
  ASSERT (SELECT balance_due FROM sales_documents WHERE id = '00000000-0000-0000-0000-000000000051') = 272000, 'balance due';
END $$;

-- a credit note without its original invoice is rejected
DO $$ BEGIN
  BEGIN
    INSERT INTO sales_documents (entity_id, doc_type, doc_date, registration_id, supplier_state, party_id, buyer_gstin,
                                 buyer_legal_name, bill_to, place_of_supply, cn_reason)
    VALUES ('00000000-0000-0000-0000-0000000000e1', 'credit_note', '2026-10-30', '00000000-0000-0000-0000-000000000071', '29',
            '00000000-0000-0000-0000-000000000021', '32AABCC4521R1ZR', 'Coastal Grid', '{}', '32', 'sales_return');
    RAISE EXCEPTION 'credit note without invoice accepted';
  EXCEPTION WHEN check_violation THEN NULL;
  END;
END $$;

-- over-settlement is rejected
DO $$ BEGIN
  BEGIN
    INSERT INTO invoice_settlements (entity_id, invoice_id, source_kind, amount, settled_on)
    VALUES ('00000000-0000-0000-0000-0000000000e1', '00000000-0000-0000-0000-000000000051', 'write_off', 300000, '2026-11-21');
    RAISE EXCEPTION 'over-settlement accepted';
  EXCEPTION WHEN raise_exception THEN
    IF SQLERRM NOT LIKE 'Settlements%' THEN RAISE; END IF;
  END;
END $$;

-- ---------- MSME 45-day clock on a supplier bill ----------
INSERT INTO purchase_bills (id, entity_id, party_id, registration_id, bill_no, bill_date, accepted_on, supplier_state,
                            place_of_supply, taxable_total, cgst_total, sgst_total, grand_total, agreed_terms_days, due_date, status)
VALUES ('00000000-0000-0000-0000-000000000081', '00000000-0000-0000-0000-0000000000e1', '00000000-0000-0000-0000-000000000022',
        '00000000-0000-0000-0000-000000000071', 'SC/2026/118', '2026-09-18', '2026-09-20', '29', '29',
        325593, 29303, 29304, 384200, 60, '2026-11-19', 'booked');
DO $$ BEGIN
  ASSERT (SELECT msme_applicable FROM purchase_bills WHERE id = '00000000-0000-0000-0000-000000000081'), 'MSME flag from supplier class';
  ASSERT (SELECT msme_pay_by FROM purchase_bills WHERE id = '00000000-0000-0000-0000-000000000081') = date '2026-11-04', '45-day cap applied over 60-day terms';
END $$;

-- ---------- journal must balance before posting ----------
INSERT INTO ledger_accounts (id, entity_id, code, name, type, subtype) VALUES
  ('00000000-0000-0000-0000-000000000091', '00000000-0000-0000-0000-0000000000e1', '1200', 'Trade receivables', 'asset', 'receivable'),
  ('00000000-0000-0000-0000-000000000092', '00000000-0000-0000-0000-0000000000e1', '4000', 'Sales', 'income', NULL);
INSERT INTO journal_entries (id, entity_id, entry_no, entry_date, source) VALUES
  ('00000000-0000-0000-0000-000000000093', '00000000-0000-0000-0000-0000000000e1', 'JV-1', '2026-10-29', 'manual');
INSERT INTO journal_lines (entry_id, line_no, account_id, debit) VALUES ('00000000-0000-0000-0000-000000000093', 1, '00000000-0000-0000-0000-000000000091', 1000);
DO $$ BEGIN
  BEGIN
    UPDATE journal_entries SET status = 'posted' WHERE id = '00000000-0000-0000-0000-000000000093';
    RAISE EXCEPTION 'unbalanced journal posted';
  EXCEPTION WHEN raise_exception THEN
    IF SQLERRM NOT LIKE '%does not balance%' THEN RAISE; END IF;
  END;
END $$;
INSERT INTO journal_lines (entry_id, line_no, account_id, credit) VALUES ('00000000-0000-0000-0000-000000000093', 2, '00000000-0000-0000-0000-000000000092', 1000);
UPDATE journal_entries SET status = 'posted' WHERE id = '00000000-0000-0000-0000-000000000093';

-- ---------- obligations from rules ----------
INSERT INTO obligations (entity_id, rule_id, registration_id, fy, period_start, period_end, period_label, due_date, status, reason_snapshot)
SELECT '00000000-0000-0000-0000-0000000000e1', r.id, '00000000-0000-0000-0000-000000000071', '2026-27', '2026-10-01', '2026-10-31',
       'Oct 2026', '2026-11-11', 'ready_to_file', '{"gst_frequency":"monthly"}'
  FROM compliance_rules r WHERE r.code = 'GST.GSTR1.MONTHLY';

-- ---------- two-person rule ----------
DO $$ BEGIN
  BEGIN
    INSERT INTO approval_requests (entity_id, subject, subject_id, summary, requested_by, status, decided_by, decided_at)
    VALUES ('00000000-0000-0000-0000-0000000000e1', 'obligation', gen_random_uuid(), 'self approve',
            '00000000-0000-0000-0000-000000000012', 'approved', '00000000-0000-0000-0000-000000000012', now());
    RAISE EXCEPTION 'maker approved own request';
  EXCEPTION WHEN check_violation THEN NULL;
  END;
END $$;

-- ---------- audit log is append-only and verifiable ----------
INSERT INTO audit_log (entity_id, actor_kind, actor_user_id, action, subject, subject_id, after)
VALUES ('00000000-0000-0000-0000-0000000000e1', 'user', '00000000-0000-0000-0000-000000000011', 'sales_document.issued', 'sales_documents', '00000000-0000-0000-0000-000000000051', '{"number":"NI/KA/212"}'),
       ('00000000-0000-0000-0000-0000000000e1', 'engine', NULL, 'obligation.created', 'obligations', NULL, '{"rule":"GST.GSTR1.MONTHLY"}');
DO $$ BEGIN
  ASSERT app.audit_verify() IS NULL, 'audit chain intact';
  BEGIN
    UPDATE audit_log SET action = 'tampered' WHERE id = 1;
    RAISE EXCEPTION 'audit row updated';
  EXCEPTION WHEN raise_exception THEN
    IF SQLERRM <> 'audit_log is append-only' THEN RAISE; END IF;
  END;
END $$;

-- ---------- row-level security ----------
SET ROLE biz_app;
SET app.user_id = '00000000-0000-0000-0000-000000000011';     -- Nimbus owner
DO $$ BEGIN
  ASSERT (SELECT count(*) FROM entities) = 1, 'owner sees only Nimbus';
  ASSERT (SELECT count(*) FROM sales_document_lines) = 3, 'owner sees invoice lines';
END $$;
SET app.user_id = '00000000-0000-0000-0000-000000000013';     -- CA partner via advisor grant
DO $$ BEGIN
  ASSERT (SELECT count(*) FROM sales_documents) = 1, 'CA sees client invoices';
  ASSERT (SELECT count(*) FROM org_members) = 1, 'CA sees own firm membership';
  ASSERT (SELECT count(*) FROM organizations) = 2, 'CA sees own firm and client org';
  ASSERT (SELECT count(*) FROM users) >= 1, 'users readable without recursion';
END $$;
SET app.user_id = '00000000-0000-0000-0000-000000000014';     -- stranger
DO $$ BEGIN
  ASSERT (SELECT count(*) FROM entities) = 0, 'stranger sees nothing';
  ASSERT (SELECT count(*) FROM sales_document_lines) = 0, 'stranger sees no lines';
  BEGIN
    INSERT INTO parties (entity_id, legal_name, is_customer) VALUES ('00000000-0000-0000-0000-0000000000e1', 'Injected', true);
    RAISE EXCEPTION 'stranger wrote into Nimbus';
  EXCEPTION WHEN insufficient_privilege THEN NULL;
  END;
END $$;
RESET app.user_id;
RESET ROLE;

-- ---------- read models ----------
SELECT legal_name, bucket, balance_due FROM v_receivables_ageing;
SELECT legal_name, msme_pay_by, breached FROM v_msme_payables;
SELECT title, due_on, status FROM v_compliance_calendar;
\echo 'ALL CHECKS PASSED'
