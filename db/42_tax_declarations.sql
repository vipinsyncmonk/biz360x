-- =====================================================================
-- Part 42 · Year-end tax declarations (roles phase 5)
-- An employee states, for a financial year, the tax regime they choose and the deductions they will claim (80C, 80D, home-loan interest …),
-- sends it to HR, and HR verifies each figure. Payroll then deducts TDS on the verified figures instead of assuming only the employee's PF
-- under the old regime. Form 16 Part B is produced from the year's payslips and the verified declaration.
--
-- THE LIMITS BELOW ARE A STARTER LIBRARY. They are Indian law figures (Income-tax Act sections and their caps) and must be reviewed by a
-- CA before they drive a deduction. They are versioned data: change a limit by adding rows for the new year, never by editing a past one.
-- Not modelled: HRA exemption, senior-citizen limits, employer NPS under 80CCD(2), 80G, other income, surcharge, previous employer income.
-- =====================================================================

CREATE TABLE tax_deduction_sections (
  fy          text NOT NULL,                           -- '2026-27', as app.fy_of() writes it
  code        text NOT NULL,
  label       text NOT NULL,
  annual_cap  numeric(14,2),                           -- NULL: no cap
  old_regime_only boolean NOT NULL DEFAULT true,
  note        text,
  PRIMARY KEY (fy, code)
);
GRANT SELECT ON tax_deduction_sections TO biz_app, biz_worker, biz_security;

INSERT INTO tax_deduction_sections (fy, code, label, annual_cap, note)
SELECT fy, v.code, v.label, v.cap, 'Starter figure: a CA must review before it drives a deduction'
  FROM (VALUES ('2025-26'), ('2026-27'), ('2027-28')) f(fy),
       (VALUES ('80C',     'Section 80C: PPF, ELSS, life insurance, tuition fees, principal on a home loan (your PF counts here too)', 150000::numeric),
               ('80CCD1B', 'Section 80CCD(1B): your own NPS contribution, over and above 80C', 50000::numeric),
               ('80D',     'Section 80D: health insurance premium (limit for those under 60)', 25000::numeric),
               ('24B',     'Section 24(b): interest on a home loan for a house you live in', 200000::numeric),
               ('80E',     'Section 80E: interest on an education loan', NULL::numeric)) v(code, label, cap);

CREATE TABLE tax_declarations (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  entity_id    uuid NOT NULL REFERENCES entities(id),
  employee_id  uuid NOT NULL REFERENCES employees(id),
  fy           text NOT NULL CHECK (fy ~ '^[0-9]{4}-[0-9]{2}$'),
  regime       text NOT NULL CHECK (regime IN ('new', 'old')),
  status       text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'submitted', 'verified', 'returned')),
  submitted_at timestamptz,
  verified_at  timestamptz,
  verified_by  uuid REFERENCES users(id),
  hr_note      text,
  updated_at   timestamptz NOT NULL DEFAULT now(),
  UNIQUE (employee_id, fy),
  CONSTRAINT tax_declaration_verified_by_someone CHECK (status <> 'verified' OR (verified_at IS NOT NULL AND verified_by IS NOT NULL)),
  CONSTRAINT tax_declaration_submitted_when CHECK (status IN ('draft', 'returned') OR submitted_at IS NOT NULL)
);
CREATE TABLE tax_declaration_items (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  declaration_id  uuid NOT NULL REFERENCES tax_declarations(id) ON DELETE CASCADE,
  entity_id       uuid NOT NULL REFERENCES entities(id),
  section         text NOT NULL,
  description     text NOT NULL CHECK (length(description) BETWEEN 1 AND 200),
  declared_amount numeric(14,2) NOT NULL CHECK (declared_amount >= 0),
  proof_ref       text CHECK (length(proof_ref) <= 200),          -- what the proof is (policy number, receipt number); files are not uploaded here yet
  status          text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'verified', 'rejected')),
  verified_amount numeric(14,2) CHECK (verified_amount >= 0),
  hr_note         text,
  CONSTRAINT tax_item_verified_has_amount CHECK ((status = 'verified') = (verified_amount IS NOT NULL))
);
CREATE INDEX tax_declaration_items_ix ON tax_declaration_items (declaration_id);
CREATE INDEX tax_declarations_entity_ix ON tax_declarations (entity_id, fy);

-- Pay and people are confidential to payroll and HR (migration 40): same two policies as the other tables of that kind.
DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['tax_declarations', 'tax_declaration_items'] LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
    EXECUTE format($p$CREATE POLICY tenant_isolation ON %I USING (entity_id IN (SELECT app.visible_entity_ids())) WITH CHECK (entity_id IN (SELECT app.visible_entity_ids()))$p$, t);
    EXECUTE format('CREATE POLICY module_access ON %I AS RESTRICTIVE USING (entity_id IN (SELECT app.module_entity_ids(%L, %L)))', t, 'payroll', 'hr');
    EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON %I TO biz_app, biz_worker', t);
    EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON %I TO biz_security', t);
  END LOOP;
END $$;

-- ---------------------------------------------------------------- what the employee does (schema self, see migration 41)
CREATE FUNCTION self.tax_sections(p_employee uuid, p_fy text) RETURNS TABLE (code text, label text, annual_cap numeric, old_regime_only boolean, note text)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, app AS $$
  SELECT s.code, s.label, s.annual_cap, s.old_regime_only, s.note FROM self.mine(p_employee) e JOIN tax_deduction_sections s ON s.fy = p_fy ORDER BY s.code
$$;

CREATE FUNCTION self.tax_declaration(p_employee uuid, p_fy text) RETURNS TABLE (
  id uuid, fy text, regime text, status text, submitted_at timestamptz, verified_at timestamptz, hr_note text)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, app AS $$
  SELECT d.id, d.fy, d.regime, d.status, d.submitted_at, d.verified_at, d.hr_note FROM tax_declarations d WHERE d.employee_id = (self.mine(p_employee)).id AND d.fy = p_fy
$$;

CREATE FUNCTION self.tax_declaration_items(p_employee uuid, p_fy text) RETURNS TABLE (
  id uuid, section text, description text, declared_amount numeric, proof_ref text, status text, verified_amount numeric, hr_note text)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, app AS $$
  SELECT i.id, i.section, i.description, i.declared_amount, i.proof_ref, i.status, i.verified_amount, i.hr_note
    FROM tax_declarations d JOIN tax_declaration_items i ON i.declaration_id = d.id
   WHERE d.employee_id = (self.mine(p_employee)).id AND d.fy = p_fy ORDER BY i.section, i.description, i.id
$$;

-- Saves the whole declaration as a draft: the regime and the list of items (a JSON array of {section, description, declared_amount, proof_ref}).
-- It cannot be changed while it waits with HR or once verified; HR returns it to unlock it.
CREATE FUNCTION self.save_tax_declaration(p_employee uuid, p_fy text, p_regime text, p_items jsonb) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, app AS $$
DECLARE e employees := self.mine(p_employee); d tax_declarations; it jsonb;
BEGIN
  IF e.status = 'exited' OR NOT EXISTS (SELECT 1 FROM tax_deduction_sections s WHERE s.fy = p_fy) THEN
    RAISE EXCEPTION 'no declaration can be made for this year' USING ERRCODE = '42501';
  END IF;
  SELECT * INTO d FROM tax_declarations WHERE employee_id = e.id AND fy = p_fy FOR UPDATE;
  IF d.id IS NOT NULL AND d.status IN ('submitted', 'verified') THEN
    RAISE EXCEPTION 'declaration is with HR' USING ERRCODE = 'P0001', CONSTRAINT = 'tax_declaration_locked';
  END IF;
  IF d.id IS NULL THEN
    INSERT INTO tax_declarations (entity_id, employee_id, fy, regime) VALUES (e.entity_id, e.id, p_fy, p_regime) RETURNING * INTO d;
  ELSE
    UPDATE tax_declarations SET regime = p_regime, updated_at = now() WHERE id = d.id;
    DELETE FROM tax_declaration_items WHERE declaration_id = d.id;
  END IF;
  FOR it IN SELECT * FROM jsonb_array_elements(coalesce(p_items, '[]'::jsonb)) LOOP
    IF NOT EXISTS (SELECT 1 FROM tax_deduction_sections s WHERE s.fy = p_fy AND s.code = it ->> 'section') THEN
      RAISE EXCEPTION 'unknown section' USING ERRCODE = 'P0001', CONSTRAINT = 'tax_item_section_unknown';
    END IF;
    INSERT INTO tax_declaration_items (declaration_id, entity_id, section, description, declared_amount, proof_ref)
    VALUES (d.id, e.entity_id, it ->> 'section', it ->> 'description', (it ->> 'declared_amount')::numeric, nullif(it ->> 'proof_ref', ''));
  END LOOP;
  RETURN d.id;
END $$;

CREATE FUNCTION self.submit_tax_declaration(p_employee uuid, p_fy text) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, app AS $$
DECLARE e employees := self.mine(p_employee); d tax_declarations;
BEGIN
  SELECT * INTO d FROM tax_declarations WHERE employee_id = e.id AND fy = p_fy FOR UPDATE;
  IF d.id IS NULL OR e.status = 'exited' THEN
    RAISE EXCEPTION 'nothing to submit' USING ERRCODE = '42501';
  END IF;
  IF d.status NOT IN ('draft', 'returned') THEN
    RETURN false;                                              -- already with HR: sending twice changes nothing
  END IF;
  UPDATE tax_declarations SET status = 'submitted', submitted_at = now(), hr_note = NULL, updated_at = now() WHERE id = d.id;
  PERFORM self.note(e, 'tax_declaration.submitted', 'tax_declarations', d.id, jsonb_build_object('fy', p_fy, 'regime', d.regime, 'by', 'employee'));
  RETURN true;
END $$;

-- The year's payslips with what Form 16 Part B needs, from approved runs only.
CREATE FUNCTION self.tax_year_payslips(p_employee uuid, p_fy text) RETURNS TABLE (period_month date, gross numeric, reimbursements numeric, pf_employee numeric, tds numeric)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, app AS $$
  SELECT p.period_month, p.gross, coalesce((SELECT sum(l.amount) FROM payslip_lines l WHERE l.payslip_id = p.id AND l.code = 'REIMBURSEMENT'), 0), p.pf_employee, p.tds
    FROM self.payslips(p_employee, NULL, p_fy) p ORDER BY p.period_month
$$;

DO $$
DECLARE f record;
BEGIN
  FOR f IN SELECT p.oid::regprocedure AS sig, p.proname FROM pg_proc p WHERE p.pronamespace = 'self'::regnamespace
            AND p.proname IN ('tax_sections', 'tax_declaration', 'tax_declaration_items', 'save_tax_declaration', 'submit_tax_declaration', 'tax_year_payslips') LOOP
    EXECUTE format('ALTER FUNCTION %s OWNER TO biz_security', f.sig);
    EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC', f.sig);
    EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO biz_self', f.sig);
  END LOOP;
END $$;
