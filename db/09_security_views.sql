-- =====================================================================
-- Part 09 · Row-level security and read models (views the screens use)
-- =====================================================================
-- The API connects as role biz_app (not the table owner) and runs
--   SET app.user_id = '<uuid>'
-- per request. Every row with an entity_id is visible only when that
-- entity is in app.visible_entity_ids(). Child tables inherit visibility
-- from their parent row.
-- =====================================================================

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'biz_app') THEN
    CREATE ROLE biz_app NOLOGIN;
  END IF;
END $$;

-- The visibility function must read memberships without being filtered by
-- the very policies it powers: it is owned by a BYPASSRLS role.
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'biz_security') THEN
    CREATE ROLE biz_security NOLOGIN BYPASSRLS;
  END IF;
END $$;
GRANT USAGE ON SCHEMA public, app TO biz_security;
GRANT SELECT ON memberships, entities, org_members, advisor_grants TO biz_security;
ALTER FUNCTION app.visible_entity_ids() OWNER TO biz_security;

GRANT USAGE ON SCHEMA public, app TO biz_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO biz_app;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO biz_app;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA app TO biz_app;
REVOKE UPDATE, DELETE ON audit_log FROM biz_app;

-- 1. Every table with an entity_id column: isolate by entity
DO $$
DECLARE t record;
BEGIN
  FOR t IN
    SELECT c.table_name
      FROM information_schema.columns c
      JOIN information_schema.tables tb ON tb.table_name = c.table_name AND tb.table_schema = c.table_schema
     WHERE c.table_schema = 'public' AND c.column_name = 'entity_id' AND tb.table_type = 'BASE TABLE'
  LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t.table_name);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t.table_name);
    EXECUTE format($p$CREATE POLICY tenant_isolation ON %I
                       USING (entity_id IN (SELECT app.visible_entity_ids()))
                       WITH CHECK (entity_id IN (SELECT app.visible_entity_ids()))$p$, t.table_name);
  END LOOP;
END $$;

-- contract_templates and audit_log allow NULL entity_id (global rows)
DROP POLICY tenant_isolation ON contract_templates;
CREATE POLICY tenant_isolation ON contract_templates
  USING (entity_id IS NULL OR entity_id IN (SELECT app.visible_entity_ids()))
  WITH CHECK (entity_id IN (SELECT app.visible_entity_ids()));
DROP POLICY tenant_isolation ON audit_log;
CREATE POLICY audit_read   ON audit_log FOR SELECT USING (entity_id IN (SELECT app.visible_entity_ids()));
CREATE POLICY audit_append ON audit_log FOR INSERT WITH CHECK (entity_id IS NULL OR entity_id IN (SELECT app.visible_entity_ids()));

-- 2. Child tables: visible when the parent is visible
DO $$
DECLARE r record;
BEGIN
  FOR r IN SELECT * FROM (VALUES
      ('party_gst_registrations','party_id','parties'),
      ('party_addresses','party_id','parties'),
      ('party_contacts','party_id','parties'),
      ('party_dunning','party_id','parties'),
      ('quote_lines','quote_id','quotes'),
      ('sales_order_lines','order_id','sales_orders'),
      ('sales_document_lines','document_id','sales_documents'),
      ('purchase_bill_lines','bill_id','purchase_bills'),
      ('vendor_payment_allocations','payment_id','vendor_payments'),
      ('journal_lines','entry_id','journal_entries'),
      ('obligation_events','obligation_id','obligations'),
      ('signature_signers','request_id','signature_requests'),
      ('dunning_steps','sequence_id','dunning_sequences'),
      ('dunning_events','sales_document_id','sales_documents'),
      ('salary_structures','employee_id','employees'),
      ('payslips','run_id','payroll_runs'),
      ('leave_requests','employee_id','employees'),
      ('committee_members','committee_id','committees'),
      ('contract_terms','contract_id','contracts'),
      ('esop_grants','pool_id','esop_pools'),
      ('delayed_payment_claim_invoices','claim_id','delayed_payment_claims'),
      ('document_links','document_id','documents'),
      ('document_request_items','request_id','document_requests'),
      ('data_room_items','room_id','data_rooms'),
      ('data_room_access','room_id','data_rooms'),
      ('sync_jobs','connection_id','portal_connections'),
      ('copilot_messages','thread_id','copilot_threads'),
      ('bank_matches','bank_transaction_id','bank_transactions'),
      ('gst_ledger_balances','registration_id','entity_registrations')
    ) AS v(child, fk, parent)
  LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', r.child);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', r.child);
    EXECUTE format($p$CREATE POLICY via_parent ON %I
                       USING (EXISTS (SELECT 1 FROM %I p WHERE p.id = %I.%I))
                       WITH CHECK (EXISTS (SELECT 1 FROM %I p WHERE p.id = %I.%I))$p$,
                   r.child, r.parent, r.child, r.fk, r.parent, r.child, r.fk);
  END LOOP;
END $$;

-- Grandchildren
ALTER TABLE salary_components ENABLE ROW LEVEL SECURITY;
ALTER TABLE salary_components FORCE ROW LEVEL SECURITY;
CREATE POLICY via_parent ON salary_components USING (EXISTS (SELECT 1 FROM salary_structures s WHERE s.id = salary_components.structure_id));
ALTER TABLE payslip_lines ENABLE ROW LEVEL SECURITY;
ALTER TABLE payslip_lines FORCE ROW LEVEL SECURITY;
CREATE POLICY via_parent ON payslip_lines USING (EXISTS (SELECT 1 FROM payslips s WHERE s.id = payslip_lines.payslip_id));
ALTER TABLE esop_exercises ENABLE ROW LEVEL SECURITY;
ALTER TABLE esop_exercises FORCE ROW LEVEL SECURITY;
CREATE POLICY via_parent ON esop_exercises USING (EXISTS (SELECT 1 FROM esop_grants g WHERE g.id = esop_exercises.grant_id));
ALTER TABLE copilot_citations ENABLE ROW LEVEL SECURITY;
ALTER TABLE copilot_citations FORCE ROW LEVEL SECURITY;
CREATE POLICY via_parent ON copilot_citations USING (EXISTS (SELECT 1 FROM copilot_messages m WHERE m.id = copilot_citations.message_id));

-- Top-level tables keyed by id rather than entity_id
ALTER TABLE entities ENABLE ROW LEVEL SECURITY;
ALTER TABLE entities FORCE ROW LEVEL SECURITY;
CREATE POLICY visible_entities ON entities
  USING (id IN (SELECT app.visible_entity_ids()));

-- Org ids the current user belongs to (definer: avoids recursive policies on org_members)
CREATE OR REPLACE FUNCTION app.my_org_ids(admin_only boolean DEFAULT false) RETURNS SETOF uuid
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, app AS $$
  SELECT organization_id FROM org_members
   WHERE user_id = app.current_user_id() AND (NOT admin_only OR role IN ('admin','partner'))
$$;
ALTER FUNCTION app.my_org_ids(boolean) OWNER TO biz_security;
GRANT EXECUTE ON FUNCTION app.my_org_ids(boolean) TO biz_app;

ALTER TABLE organizations ENABLE ROW LEVEL SECURITY;
ALTER TABLE organizations FORCE ROW LEVEL SECURITY;
CREATE POLICY visible_orgs ON organizations
  USING (id IN (SELECT organization_id FROM entities)          -- orgs of visible entities
         OR id IN (SELECT app.my_org_ids()));

ALTER TABLE org_members ENABLE ROW LEVEL SECURITY;
ALTER TABLE org_members FORCE ROW LEVEL SECURITY;
CREATE POLICY own_org_rows ON org_members
  USING (user_id = app.current_user_id() OR organization_id IN (SELECT app.my_org_ids(true)));

ALTER TABLE users ENABLE ROW LEVEL SECURITY;
ALTER TABLE users FORCE ROW LEVEL SECURITY;
CREATE POLICY colleagues ON users
  USING (id = app.current_user_id()
         OR id IN (SELECT user_id FROM memberships))          -- memberships is itself entity-filtered
  WITH CHECK (id = app.current_user_id());

-- Biz360X subscription: org admins and partners only
ALTER TABLE subscriptions ENABLE ROW LEVEL SECURITY;
ALTER TABLE subscriptions FORCE ROW LEVEL SECURITY;
CREATE POLICY org_admins ON subscriptions USING (organization_id IN (SELECT app.my_org_ids(true)));

-- Global reference data is read-only for the application role
REVOKE INSERT, UPDATE, DELETE ON ref_states, ref_authorities, ref_hsn_sac, ref_schemes, ref_rbi_bank_rate, compliance_rules FROM biz_app;

-- Users see their own profile and preferences
ALTER TABLE notification_preferences ENABLE ROW LEVEL SECURITY;
ALTER TABLE notification_preferences FORCE ROW LEVEL SECURITY;
CREATE POLICY own_rows ON notification_preferences USING (user_id = app.current_user_id()) WITH CHECK (user_id = app.current_user_id());

-- =====================================================================
-- Read models
-- =====================================================================

-- Compliance calendar (Command Center "Next 30 days", Compliance Engine table)
CREATE VIEW v_compliance_calendar WITH (security_invoker = true) AS
SELECT o.id, o.entity_id, r.code AS rule_code, r.title, r.form_code, r.authority_code,
       er.number AS registration_no, o.period_label,
       coalesce(o.extended_due_date, o.due_date) AS due_on,
       coalesce(o.extended_due_date, o.due_date) - current_date AS days_left,
       o.status, o.owner_user_id, o.late_fee_accrued + o.interest_accrued AS exposure,
       o.reason_snapshot
  FROM obligations o
  JOIN compliance_rules r ON r.id = o.rule_id
  LEFT JOIN entity_registrations er ON er.id = o.registration_id
 WHERE o.status NOT IN ('not_applicable','waived');

-- Standing with each authority (dashboard table, group view matrix)
CREATE VIEW v_authority_standing WITH (security_invoker = true) AS
SELECT o.entity_id, r.authority_code,
       count(*) FILTER (WHERE o.status NOT IN ('filed','not_applicable','waived')
                          AND coalesce(o.extended_due_date, o.due_date) < current_date) AS overdue,
       count(*) FILTER (WHERE o.status = 'awaiting_client') AS waiting_on_client,
       count(*) FILTER (WHERE o.status NOT IN ('filed','not_applicable','waived')
                          AND coalesce(o.extended_due_date, o.due_date) BETWEEN current_date AND current_date + 7) AS due_7_days,
       max(o.filed_at) AS last_filed_at
  FROM obligations o
  JOIN compliance_rules r ON r.id = o.rule_id
 GROUP BY o.entity_id, r.authority_code;

-- Receivables ageing (Sales & Billing, MSME workspace)
CREATE VIEW v_receivables_ageing WITH (security_invoker = true) AS
SELECT d.entity_id, d.party_id, p.legal_name, p.msme_class,
       d.id AS document_id, d.number, d.doc_date, d.due_date, d.balance_due,
       current_date - d.doc_date AS age_days,
       greatest(current_date - d.due_date, 0) AS days_overdue,
       CASE WHEN current_date - d.doc_date <= 30 THEN '0-30'
            WHEN current_date - d.doc_date <= 45 THEN '31-45'
            WHEN current_date - d.doc_date <= 60 THEN '46-60'
            WHEN current_date - d.doc_date <= 90 THEN '61-90'
            ELSE '90+' END AS bucket
  FROM sales_documents d
  JOIN parties p ON p.id = d.party_id
 WHERE d.status = 'issued' AND d.doc_type IN ('tax_invoice','debit_note','export_invoice')
   AND d.payment_status IN ('unpaid','partially_paid');

-- Bills to micro/small suppliers and the MSMED 45-day clock (Sec 43B(h) risk)
CREATE VIEW v_msme_payables WITH (security_invoker = true) AS
SELECT b.entity_id, b.party_id, p.legal_name, p.msme_class, b.id AS bill_id, b.bill_no,
       b.accepted_on, b.msme_pay_by, b.balance_due,
       b.msme_pay_by - current_date AS days_left,
       (b.msme_pay_by < current_date) AS breached,
       app.fy_of(b.accepted_on) AS fy
  FROM purchase_bills b
  JOIN parties p ON p.id = b.party_id
 WHERE b.msme_applicable AND b.balance_due > 0 AND b.status NOT IN ('cancelled','draft');

-- Labour codes: allowances above 50% of remuneration are added back to wages
CREATE VIEW v_wage_code_check WITH (security_invoker = true) AS
SELECT e.entity_id, e.id AS employee_id, e.full_name, s.id AS structure_id,
       sum(c.monthly_amount) FILTER (WHERE c.kind = 'earning') AS gross_monthly,
       sum(c.monthly_amount) FILTER (WHERE c.kind = 'earning' AND c.in_wages) AS wages_monthly,
       round(100.0 * sum(c.monthly_amount) FILTER (WHERE c.kind = 'earning' AND c.in_wages)
             / nullif(sum(c.monthly_amount) FILTER (WHERE c.kind = 'earning'), 0), 1) AS wage_pct,
       greatest(0.5 * sum(c.monthly_amount) FILTER (WHERE c.kind = 'earning'),
                sum(c.monthly_amount) FILTER (WHERE c.kind = 'earning' AND c.in_wages)) AS statutory_wage
  FROM employees e
  JOIN salary_structures s ON s.employee_id = e.id AND s.effective @> current_date
  JOIN salary_components c ON c.structure_id = s.id
 WHERE e.status IN ('active','notice')
 GROUP BY e.entity_id, e.id, e.full_name, s.id;

-- Fully diluted cap table (allotments − transfers out + transfers in + ESOP pool)
CREATE VIEW v_cap_table WITH (security_invoker = true) AS
WITH holdings AS (
  SELECT entity_id, shareholder_id, share_class_id, quantity FROM share_allotments WHERE allotted_on IS NOT NULL
  UNION ALL
  SELECT entity_id, to_holder_id, share_class_id, quantity FROM share_transfers
  UNION ALL
  SELECT entity_id, from_holder_id, share_class_id, -quantity FROM share_transfers
), by_holder AS (
  SELECT h.entity_id, s.name AS holder, sc.name AS class_name,
         sum(h.quantity * sc.conversion_ratio) AS fd_shares
    FROM holdings h
    JOIN shareholders s ON s.id = h.shareholder_id
    JOIN share_classes sc ON sc.id = h.share_class_id
   GROUP BY h.entity_id, s.name, sc.name
  UNION ALL
  SELECT entity_id, 'ESOP pool · ' || name, 'Options', size_options FROM esop_pools
)
SELECT entity_id, holder, class_name, fd_shares,
       round(100 * fd_shares / sum(fd_shares) OVER (PARTITION BY entity_id), 2) AS fd_pct
  FROM by_holder;

-- Open ITC at risk (GST screen: "not in 2A yet")
CREATE VIEW v_itc_at_risk WITH (security_invoker = true) AS
SELECT m.entity_id, b.party_id, p.legal_name, count(*) AS invoices,
       sum(b.cgst_total + b.sgst_total + b.igst_total) AS itc_amount
  FROM itc_matches m
  JOIN purchase_bills b ON b.id = m.purchase_bill_id
  JOIN parties p ON p.id = b.party_id
 WHERE m.status = 'not_in_portal' AND m.action_taken_at IS NULL
 GROUP BY m.entity_id, b.party_id, p.legal_name;

-- Contracts whose notice window closes soon (Legal screen headline)
CREATE VIEW v_contract_notice_windows WITH (security_invoker = true) AS
SELECT c.entity_id, c.id, c.title, c.counterparty_name, c.end_date, c.notice_deadline,
       c.notice_deadline - current_date AS days_to_act
  FROM contracts c
 WHERE c.status = 'active' AND c.auto_renews AND c.notice_deadline >= current_date;

GRANT SELECT ON ALL TABLES IN SCHEMA public TO biz_app;   -- includes the views
