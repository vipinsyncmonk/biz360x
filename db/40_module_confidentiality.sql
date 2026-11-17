-- =====================================================================
-- Part 40 · Module confidentiality in the database
-- Until now the database decided which *company* a person can see, and the API alone decided which *modules*. A GST consultant was
-- kept out of salaries by each handler's require() and by nothing else. For the modules where a leak hurts people, the database now
-- applies the module as well:
--
-- * pay and people        employees, salary structures, payslips and their lines, monthly inputs, leave, onboarding checklists,
--                         handbook acknowledgements, gratuity and bonus          -> `payroll` or `hr`
-- * the payroll run       its month, status and totals, no person in it          -> `payroll`, `hr` or `finance` (the bank match)
-- * the cap table         shareholders, classes, rounds, allotments, transfers, ESOP -> `capital`
-- * the bank statement    lines and their matches (they show each salary paid)   -> `finance`
-- * the vault             documents in the `payroll` folder, and their links     -> `payroll` or `hr`
--
-- Holding any level on a module lets a person read it, as in the API: a member through their scopes (`*` counts), a practitioner
-- through an active engagement that reaches them, an API key only for the modules its permissions name.
--
-- These are RESTRICTIVE policies on top of the company policy, and they cover every command, not only SELECT: PostgreSQL applies a
-- read policy to an UPDATE or DELETE only when the statement reads a column, so a read-only policy would still let a person who
-- cannot see a table blindly change or empty it. Someone outside the module can therefore neither read these rows nor write them.
-- The *level* (may this person write, approve, file) stays with require(): a payroll approval legitimately posts a finance journal,
-- and enforcing levels here would turn every posting helper into a privileged function.
--
-- Three narrow functions give other modules the little they need without the rows: the month's statutory total for a PF, ESI or PT
-- challan, a count of payslips with a given net pay for matching a bank line, and employees' names for the ESOP register.
-- =====================================================================

GRANT SELECT ON api_keys, employees, payroll_runs, payslips TO biz_security;

-- The companies in which the caller holds any level on at least one of the modules.
CREATE FUNCTION app.module_entity_ids(VARIADIC p_modules text[]) RETURNS SETOF uuid
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, app AS $$
  SELECT x.entity_id FROM (
    SELECT m.entity_id FROM memberships m
     WHERE m.user_id = app.current_user_id() AND m.revoked_at IS NULL AND (m.scopes ? '*' OR m.scopes ?| p_modules)
    UNION
    SELECT g.entity_id FROM advisor_grants g
     WHERE g.status = 'active' AND (g.expires_on IS NULL OR g.expires_on >= current_date) AND g.scopes ?| p_modules
       AND (g.practitioner_user_id = app.current_user_id()
            OR EXISTS (SELECT 1 FROM org_members om WHERE om.organization_id = g.advisor_org_id AND om.user_id = app.current_user_id()
                          AND (om.role IN ('admin', 'partner')
                               OR EXISTS (SELECT 1 FROM firm_client_staff s WHERE s.organization_id = om.organization_id AND s.entity_id = g.entity_id AND s.user_id = om.user_id))))
  ) x
  -- an API key is a service user: it reads a module only if its own permission list names that module
  WHERE NOT EXISTS (SELECT 1 FROM api_keys k WHERE k.acts_as_user = app.current_user_id())
     OR EXISTS (SELECT 1 FROM api_keys k, unnest(k.permissions) perm
                 WHERE k.acts_as_user = app.current_user_id() AND k.entity_id = x.entity_id AND split_part(perm, ':', 1) = ANY (p_modules))
$$;
ALTER FUNCTION app.module_entity_ids(text[]) OWNER TO biz_security;

-- Does the caller hold at least this level on the module in this company? Levels are the member's (read < comment < write < approve
-- < file). A practitioner's words map onto them: read, comment, prepare = write, certify = file; a practitioner never approves.
CREATE FUNCTION app.can(p_entity uuid, p_module text, p_level text DEFAULT 'read') RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, app AS $$
  WITH want AS (SELECT array_position(ARRAY['read', 'comment', 'write', 'approve', 'file'], p_level) AS n)
  SELECT p_entity IN (SELECT app.module_entity_ids(p_module)) AND (SELECT n FROM want) IS NOT NULL AND (
    EXISTS (SELECT 1 FROM memberships m
             WHERE m.entity_id = p_entity AND m.user_id = app.current_user_id() AND m.revoked_at IS NULL
               AND array_position(ARRAY['read', 'comment', 'write', 'approve', 'file'], coalesce(m.scopes ->> p_module, m.scopes ->> '*')) >= (SELECT n FROM want))
    OR (p_level <> 'approve' AND EXISTS (SELECT 1 FROM app.my_grant_scopes(p_entity) g
             WHERE array_position(ARRAY['read', 'comment', 'prepare', 'approve', 'certify'], g.scopes ->> p_module) >= (SELECT n FROM want))))
$$;
ALTER FUNCTION app.can(uuid, text, text) OWNER TO biz_security;
GRANT EXECUTE ON FUNCTION app.module_entity_ids(text[]), app.can(uuid, text, text) TO biz_app, biz_worker;

-- ---------------------------------------------------------------- tables that carry entity_id
DO $$
DECLARE r record;
BEGIN
  FOR r IN SELECT * FROM (VALUES
      ('employees',           ARRAY['payroll', 'hr']),
      ('gratuity_valuations', ARRAY['payroll', 'hr']),
      ('statutory_bonus',     ARRAY['payroll', 'hr']),
      ('leave_adjustments',   ARRAY['payroll', 'hr']),
      ('onboarding_tasks',    ARRAY['payroll', 'hr']),
      ('hr_policy_acks',      ARRAY['payroll', 'hr']),
      ('payroll_runs',        ARRAY['payroll', 'hr', 'finance']),
      ('shareholders',        ARRAY['capital']),
      ('share_classes',       ARRAY['capital']),
      ('funding_rounds',      ARRAY['capital']),
      ('share_allotments',    ARRAY['capital']),
      ('share_transfers',     ARRAY['capital']),
      ('esop_pools',          ARRAY['capital']),
      ('bank_transactions',   ARRAY['finance'])
    ) AS v(tbl, modules)
  LOOP
    EXECUTE format('CREATE POLICY module_access ON %I AS RESTRICTIVE USING (entity_id IN (SELECT app.module_entity_ids(VARIADIC %L::text[])))', r.tbl, r.modules);
  END LOOP;
END $$;
-- Their children are reached through the parent (`via_parent`), so they follow: salary_structures, salary_components and
-- leave_requests through employees; esop_grants and esop_exercises through esop_pools; bank_matches through bank_transactions.

-- A payroll run is readable by finance, but what is inside it is not.
-- Written as `run_id IN (...)`, not as a second EXISTS beside `via_parent`: with two EXISTS policies on one table PostgreSQL 16 took
-- about 160 ms to read 20,000 payslips against 10 ms without the policy; in this form it takes about 16 ms.
CREATE POLICY module_access ON payslips AS RESTRICTIVE
  USING (run_id IN (SELECT r.id FROM payroll_runs r WHERE r.entity_id IN (SELECT app.module_entity_ids('payroll', 'hr'))));
CREATE POLICY module_access ON payroll_run_inputs AS RESTRICTIVE
  USING (run_id IN (SELECT r.id FROM payroll_runs r WHERE r.entity_id IN (SELECT app.module_entity_ids('payroll', 'hr'))));
-- payslip_lines follow payslips.

-- The vault's payroll folder: bank files, payslips, an employee's proofs. document_links follow documents.
CREATE POLICY module_access ON documents AS RESTRICTIVE
  USING (folder <> 'payroll' OR entity_id IN (SELECT app.module_entity_ids('payroll', 'hr')));

-- ---------------------------------------------------------------- the little that other modules need
-- These run as their owner, so they must decide for themselves who is asking. A worker (or a migration) is not subject to row-level
-- security and has no user; everyone else is held to what they may see. The caller's role is the one set on the session.
CREATE FUNCTION app.caller_bypasses_rls() RETURNS boolean
LANGUAGE sql STABLE SET search_path = pg_catalog AS $$
  SELECT coalesce((SELECT r.rolsuper OR r.rolbypassrls FROM pg_roles r
                    WHERE r.rolname = coalesce(nullif(nullif(current_setting('role', true), 'none'), ''), session_user::text)), false)
$$;

-- The month's statutory total from the approved payroll run, for the PF, ESI or professional tax challan. A total, never a person.
CREATE FUNCTION app.payroll_statutory_total(p_entity uuid, p_month date, p_what text) RETURNS TABLE (run_id uuid, amount numeric)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = public, app AS $$
BEGIN
  IF NOT app.caller_bypasses_rls() AND p_entity NOT IN (SELECT app.visible_entity_ids()) THEN
    RETURN;
  END IF;
  IF p_what NOT IN ('pf', 'esi', 'pt') THEN
    RAISE EXCEPTION 'unknown statutory total %', p_what;
  END IF;
  RETURN QUERY
    SELECT r.id, coalesce(sum(CASE p_what WHEN 'pf' THEN s.pf_employee + s.pf_employer_epf + s.pf_employer_eps
                                          WHEN 'esi' THEN s.esi_employee + s.esi_employer
                                          ELSE s.professional_tax END), 0)::numeric
      FROM payroll_runs r JOIN payslips s ON s.run_id = r.id
     WHERE r.entity_id = p_entity AND r.period_month = p_month AND r.status IN ('approved', 'bank_file_sent', 'paid', 'statutory_done')
     GROUP BY r.id;
END $$;

-- How many payslips in a run have exactly this net pay: what matching a salary debit on the bank statement needs. Only for someone who
-- reads the bank statement, where each salary paid is a line anyway.
CREATE FUNCTION app.payslips_with_net_pay(p_run uuid, p_amount numeric) RETURNS integer
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, app AS $$
  SELECT count(*)::int FROM payslips s JOIN payroll_runs r ON r.id = s.run_id
   WHERE r.id = p_run AND s.net_pay = p_amount AND (app.caller_bypasses_rls() OR r.entity_id IN (SELECT app.module_entity_ids('finance', 'payroll', 'hr')))
$$;

-- Employees by name, for the ESOP register: who holds a grant, and who can be given one. Nothing else about them.
CREATE FUNCTION app.employee_names(p_entity uuid) RETURNS TABLE (id uuid, full_name text, employee_code text, status text)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, app AS $$
  SELECT e.id, e.full_name, e.employee_code, e.status FROM employees e
   WHERE e.entity_id = p_entity AND (app.caller_bypasses_rls() OR p_entity IN (SELECT app.module_entity_ids('capital', 'payroll', 'hr')))
$$;
ALTER FUNCTION app.payroll_statutory_total(uuid, date, text) OWNER TO biz_security;
ALTER FUNCTION app.payslips_with_net_pay(uuid, numeric) OWNER TO biz_security;
ALTER FUNCTION app.employee_names(uuid) OWNER TO biz_security;
GRANT EXECUTE ON FUNCTION app.caller_bypasses_rls(), app.payroll_statutory_total(uuid, date, text), app.payslips_with_net_pay(uuid, numeric), app.employee_names(uuid) TO biz_app, biz_worker;
