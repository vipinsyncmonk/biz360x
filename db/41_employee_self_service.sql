-- =====================================================================
-- Part 41 · Employee self-service
-- An employee signs in to see their own payslips, apply for leave, read the handbook and follow their joining checklist. They are not a
-- member of the company: they never appear in `memberships`, `app.visible_entity_ids()` does not change, and no company policy has to
-- learn to exclude them.
--
-- They come in by a separate door. Requests under /v1/self run as a new role, `biz_self`, which has:
--   * no privilege on any table, view or sequence, and no USAGE on the schema `app` (so none of its functions);
--   * USAGE on the schema `self`, and EXECUTE on the functions in it.
-- Every function in `self` takes the employee record it is about, checks that the record belongs to the signed-in person
-- (`employees.user_id`), and returns or changes that person's own data only. A bug in a self-service handler therefore cannot reach the
-- ledger, another employee or another company: the role cannot name those tables at all.
--
-- The design (docs/ROLES_DESIGN.md, 8.1) proposed table grants with a policy each. This is stricter and has nothing to keep in step
-- with the module policies of migration 40.
--
-- Linking a person to their record: HR invites the employee (their email must be on the record); the person signs in with that email
-- and accepts, which sets `employees.user_id`. HR can switch it off again.
-- =====================================================================

ALTER TABLE employees
  ADD COLUMN self_invited_at  timestamptz,                     -- HR opened self-service for this person
  ADD COLUMN self_invited_by  uuid REFERENCES users(id),
  ADD COLUMN self_accepted_at timestamptz,                     -- the person accepted; user_id is set from then on
  ADD CONSTRAINT employee_self_needs_invite CHECK (self_accepted_at IS NULL OR self_invited_at IS NOT NULL),
  ADD CONSTRAINT employee_self_needs_email CHECK (self_invited_at IS NULL OR email IS NOT NULL);
CREATE UNIQUE INDEX employees_user_per_entity_ix ON employees (entity_id, user_id) WHERE user_id IS NOT NULL;
CREATE INDEX employees_user_ix ON employees (user_id) WHERE user_id IS NOT NULL;
CREATE INDEX employees_self_invite_ix ON employees (lower(email::text)) WHERE self_invited_at IS NOT NULL AND user_id IS NULL;

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'biz_self') THEN
    CREATE ROLE biz_self NOLOGIN;
  END IF;
END $$;
CREATE SCHEMA self;
GRANT USAGE ON SCHEMA self TO biz_self, biz_security;
-- the functions run as biz_security: what they may touch is listed here and nowhere else
GRANT SELECT ON users, leave_types, leave_requests, leave_adjustments, holidays, hr_policies, hr_policy_documents, hr_policy_acks, onboarding_tasks,
                payslip_lines, salary_structures TO biz_security;
GRANT INSERT, UPDATE ON leave_requests TO biz_security;
GRANT INSERT ON hr_policy_acks, event_outbox TO biz_security;
GRANT SELECT, INSERT ON audit_log TO biz_security;            -- the hash chain reads the last row
GRANT UPDATE (user_id, self_accepted_at, self_invited_at, self_invited_by) ON employees TO biz_security;
GRANT INSERT ON users TO biz_security;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO biz_security;

-- ---------------------------------------------------------------- under biz_app: being invited and accepting
-- HR opens self-service for a person. They sign in with a one-time code sent to the email on their record, so an account for that
-- email is made here if there is none (an account with no workspace and no employment sees nothing). The caller must hold HR at
-- `write` in the employee's company.
CREATE FUNCTION app.invite_employee(p_employee uuid) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, app AS $$
DECLARE e employees;
BEGIN
  SELECT * INTO e FROM employees WHERE id = p_employee FOR UPDATE;
  IF e.id IS NULL OR e.email IS NULL OR e.status = 'exited' OR NOT app.can(e.entity_id, 'hr', 'write') THEN
    RAISE EXCEPTION 'cannot invite this employee' USING ERRCODE = '42501';
  END IF;
  INSERT INTO users (email, full_name, auth_provider) VALUES (e.email, left(e.full_name, 120), 'otp') ON CONFLICT (email) DO NOTHING;
  UPDATE employees SET self_invited_at = now(), self_invited_by = app.current_user_id() WHERE id = p_employee AND self_invited_at IS NULL;
END $$;

-- The records the signed-in person is linked to, with the company's name (they cannot read `entities`).
CREATE FUNCTION app.my_employments() RETURNS TABLE (employee_id uuid, entity_id uuid, legal_name text, employee_code text, full_name text, designation text, status text)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, app AS $$
  SELECT e.id, e.entity_id, en.legal_name, e.employee_code, e.full_name, e.designation, e.status
    FROM employees e JOIN entities en ON en.id = e.entity_id
   WHERE e.user_id = app.current_user_id() ORDER BY en.legal_name
$$;

-- Invitations waiting for the signed-in person: records with their email that HR has opened and nobody has taken.
CREATE FUNCTION app.my_employment_invites() RETURNS TABLE (employee_id uuid, entity_id uuid, legal_name text, employee_code text, full_name text, designation text, invited_at timestamptz)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, app AS $$
  SELECT e.id, e.entity_id, en.legal_name, e.employee_code, e.full_name, e.designation, e.self_invited_at
    FROM employees e JOIN entities en ON en.id = e.entity_id JOIN users u ON u.id = app.current_user_id()
   WHERE e.user_id IS NULL AND e.self_invited_at IS NOT NULL AND e.status <> 'exited' AND lower(e.email::text) = lower(u.email::text)
   ORDER BY e.self_invited_at
$$;

CREATE FUNCTION app.accept_employment(p_employee uuid) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, app AS $$
DECLARE e employees; v_email text;
BEGIN
  SELECT email::text INTO v_email FROM users WHERE id = app.current_user_id();
  SELECT * INTO e FROM employees WHERE id = p_employee FOR UPDATE;
  IF e.id IS NULL OR v_email IS NULL OR e.user_id IS NOT NULL OR e.self_invited_at IS NULL OR e.status = 'exited' OR lower(e.email::text) <> lower(v_email) THEN
    RAISE EXCEPTION 'no such invitation' USING ERRCODE = '42501';
  END IF;
  UPDATE employees SET user_id = app.current_user_id(), self_accepted_at = now() WHERE id = p_employee;
  INSERT INTO audit_log (entity_id, actor_kind, actor_user_id, action, subject, subject_id, after)
  VALUES (e.entity_id, 'user', app.current_user_id(), 'employee.self_service_accepted', 'employees', p_employee, jsonb_build_object('email', v_email));
  INSERT INTO event_outbox (entity_id, type, subject, subject_id, data)
  VALUES (e.entity_id, 'employee.self_service_accepted', 'employees', p_employee, jsonb_build_object('employee', e.full_name));
  RETURN e.entity_id;
END $$;
ALTER FUNCTION app.invite_employee(uuid) OWNER TO biz_security;
ALTER FUNCTION app.my_employments() OWNER TO biz_security;
ALTER FUNCTION app.my_employment_invites() OWNER TO biz_security;
ALTER FUNCTION app.accept_employment(uuid) OWNER TO biz_security;
GRANT EXECUTE ON FUNCTION app.invite_employee(uuid), app.my_employments(), app.my_employment_invites(), app.accept_employment(uuid) TO biz_app;

-- ---------------------------------------------------------------- the door: schema `self`
-- The caller's own employee record, or an error. Every other function starts here. Not callable by biz_self: it returns the whole row.
CREATE FUNCTION self.mine(p_employee uuid) RETURNS employees
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = public, app AS $$
DECLARE e employees;
BEGIN
  SELECT * INTO e FROM employees WHERE id = p_employee AND user_id = app.current_user_id() AND app.current_user_id() IS NOT NULL;
  IF e.id IS NULL THEN
    RAISE EXCEPTION 'not your employee record' USING ERRCODE = '42501';
  END IF;
  RETURN e;
END $$;

CREATE FUNCTION self.note(e employees, p_action text, p_subject text, p_subject_id uuid, p_data jsonb) RETURNS void
LANGUAGE sql SECURITY DEFINER SET search_path = public, app AS $$
  INSERT INTO audit_log (entity_id, actor_kind, actor_user_id, action, subject, subject_id, after)
  VALUES (e.entity_id, 'user', app.current_user_id(), p_action, p_subject, p_subject_id, p_data);
  INSERT INTO event_outbox (entity_id, type, subject, subject_id, data) VALUES (e.entity_id, p_action, p_subject, p_subject_id, p_data || jsonb_build_object('employee', e.full_name));
$$;

CREATE FUNCTION self.employments() RETURNS TABLE (employee_id uuid, entity_id uuid, legal_name text, employee_code text, full_name text, designation text, status text)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, app AS $$
  SELECT * FROM app.my_employments()
$$;

-- What a person sees about themselves. The PAN is its last four characters and the bank account only "on file or not".
CREATE FUNCTION self.profile(p_employee uuid) RETURNS TABLE (
  employee_id uuid, entity_id uuid, legal_name text, registered_address text, employee_code text, full_name text, email text, mobile_e164 text, designation text, department text,
  date_of_joining date, date_of_leaving date, status text, employment_type text, work_state text, tax_regime text, pan_last4 text, uan text, bank_ifsc text, bank_on_file boolean,
  has_salary boolean)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = public, app AS $$
DECLARE e employees := self.mine(p_employee);
BEGIN
  RETURN QUERY
    SELECT e.id, e.entity_id, en.legal_name, en.registered_address, e.employee_code, e.full_name, e.email::text, e.mobile_e164, e.designation, e.department, e.date_of_joining,
           e.date_of_leaving, e.status, e.employment_type, e.work_state::text, e.tax_regime, right(e.pan::text, 4), e.uan::text, e.bank_ifsc::text, e.bank_account_enc IS NOT NULL,
           EXISTS (SELECT 1 FROM salary_structures s WHERE s.employee_id = e.id)
      FROM entities en WHERE en.id = e.entity_id;
END $$;

-- ---- leave
CREATE FUNCTION self.leave_types(p_employee uuid) RETURNS TABLE (id uuid, code text, name text, annual_quota numeric, accrual text, paid boolean, carry_forward_max numeric, encashable boolean)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, app AS $$
  SELECT t.id, t.code, t.name, t.annual_quota, t.accrual, t.paid, t.carry_forward_max, t.encashable
    FROM leave_types t WHERE t.entity_id = (self.mine(p_employee)).entity_id AND t.is_active ORDER BY t.paid DESC, t.code
$$;

-- The leave part of the company's policy in force on a date (leave year, weekly off, what happens when leave runs out). Not the
-- salary or payroll parts.
CREATE FUNCTION self.leave_policy(p_employee uuid, p_as_of date) RETURNS jsonb
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, app AS $$
  SELECT p.leave FROM hr_policies p WHERE p.entity_id = (self.mine(p_employee)).entity_id AND p.effective_from <= p_as_of ORDER BY p.effective_from DESC LIMIT 1
$$;

CREATE FUNCTION self.leave_adjustments(p_employee uuid, p_year_start date) RETURNS TABLE (leave_type_id uuid, d numeric)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, app AS $$
  SELECT a.leave_type_id, coalesce(sum(a.days), 0) FROM leave_adjustments a
   WHERE a.employee_id = (self.mine(p_employee)).id AND a.leave_year_start = p_year_start GROUP BY a.leave_type_id
$$;

CREATE FUNCTION self.leave_used(p_employee uuid, p_start date, p_end date) RETURNS TABLE (leave_type_id uuid, status text, paid numeric, lop numeric)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, app AS $$
  SELECT r.leave_type_id, r.status, coalesce(sum(r.days - r.lop_days), 0), coalesce(sum(r.lop_days), 0) FROM leave_requests r
   WHERE r.employee_id = (self.mine(p_employee)).id AND r.status IN ('pending', 'approved') AND lower(r.dates) BETWEEN p_start AND p_end GROUP BY r.leave_type_id, r.status
$$;

CREATE FUNCTION self.holidays(p_employee uuid, p_from date, p_to date) RETURNS TABLE (holiday_date date, name text)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, app AS $$
  SELECT h.holiday_date, h.name FROM holidays h WHERE h.entity_id = (self.mine(p_employee)).entity_id AND h.holiday_date BETWEEN p_from AND p_to ORDER BY h.holiday_date
$$;

CREATE FUNCTION self.leave_requests(p_employee uuid, p_request uuid DEFAULT NULL) RETURNS TABLE (
  id uuid, leave_type text, leave_type_name text, d_from date, d_to date, days numeric, lop_days numeric, half_day boolean, reason text, status text, requested_at timestamptz,
  decided_at timestamptz, decision_note text)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, app AS $$
  SELECT r.id, t.code, t.name, lower(r.dates), upper(r.dates) - 1, r.days, r.lop_days, r.half_day, r.reason, r.status, r.requested_at, r.decided_at, r.decision_note
    FROM leave_requests r JOIN leave_types t ON t.id = r.leave_type_id
   WHERE r.employee_id = (self.mine(p_employee)).id AND (p_request IS NULL OR r.id = p_request)
   ORDER BY (r.status = 'pending') DESC, lower(r.dates) DESC, r.id
$$;

-- Apply for leave. The working days and the unpaid part are worked out by the API from the policy, the holidays and the balance, as
-- they are when HR records a request; this checks that the person, the type and the dates belong together. Overlap is a constraint.
CREATE FUNCTION self.request_leave(p_employee uuid, p_type uuid, p_from date, p_to date, p_days numeric, p_lop numeric, p_half boolean, p_reason text) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, app AS $$
DECLARE e employees := self.mine(p_employee); v_id uuid;
BEGIN
  IF e.status = 'exited' OR p_to < p_from OR p_from < e.date_of_joining OR (e.date_of_leaving IS NOT NULL AND p_to > e.date_of_leaving) THEN
    RAISE EXCEPTION 'The leave falls outside your time with the company' USING ERRCODE = '23514', CONSTRAINT = 'leave_within_employment';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM leave_types t WHERE t.id = p_type AND t.entity_id = e.entity_id AND t.is_active) THEN
    RAISE EXCEPTION 'not a leave type of this company' USING ERRCODE = '42501';
  END IF;
  INSERT INTO leave_requests (employee_id, leave_type_id, dates, days, lop_days, half_day, reason, requested_by)
  VALUES (e.id, p_type, daterange(p_from, p_to, '[]'), p_days, p_lop, p_half, p_reason, app.current_user_id()) RETURNING id INTO v_id;
  PERFORM self.note(e, 'leave.requested', 'leave_requests', v_id, jsonb_build_object('from', p_from, 'to', p_to, 'days', p_days, 'by', 'employee'));
  RETURN v_id;
END $$;

-- Withdraw one's own request while nobody has decided it. An approved leave is cancelled by HR, because pay may already depend on it.
CREATE FUNCTION self.cancel_leave(p_employee uuid, p_request uuid) RETURNS text
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, app AS $$
DECLARE e employees := self.mine(p_employee); v_status text;
BEGIN
  SELECT r.status INTO v_status FROM leave_requests r WHERE r.id = p_request AND r.employee_id = e.id FOR UPDATE;
  IF v_status IS NULL THEN
    RAISE EXCEPTION 'not your request' USING ERRCODE = '42501';
  END IF;
  IF v_status = 'pending' THEN
    UPDATE leave_requests SET status = 'cancelled', decided_at = now(), decision_note = 'Withdrawn by the employee' WHERE id = p_request;
    PERFORM self.note(e, 'leave.cancelled', 'leave_requests', p_request, jsonb_build_object('by', 'employee'));
  END IF;
  RETURN v_status;                                              -- what it was: the API refuses anything but pending
END $$;

-- ---- payslips, from approved payroll runs only
CREATE FUNCTION self.payslips(p_employee uuid, p_slip uuid DEFAULT NULL, p_fy text DEFAULT NULL) RETURNS TABLE (
  id uuid, run_id uuid, period_month date, run_status text, paid_on date, employee_id uuid, employee_code text, employee_name text, designation text, department text, pan text,
  uan text, date_of_joining date, paid_days numeric, lop_days numeric, gross numeric, wages numeric, pf_wage numeric, pf_employee numeric, pf_employer_epf numeric,
  pf_employer_eps numeric, esi_applicable boolean, esi_employee numeric, esi_employer numeric, professional_tax numeric, tds numeric, other_deductions numeric, net_pay numeric)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, app AS $$
  SELECT s.id, s.run_id, r.period_month, r.status, r.paid_on, s.employee_id, e.employee_code, e.full_name, e.designation, e.department, e.pan::text, e.uan::text, e.date_of_joining,
         s.paid_days, s.lop_days, s.gross, s.wages, s.pf_wage, s.pf_employee, s.pf_employer_epf, s.pf_employer_eps, s.esi_applicable, s.esi_employee, s.esi_employer,
         s.professional_tax, s.tds, s.other_deductions, s.net_pay
    FROM self.mine(p_employee) e JOIN payslips s ON s.employee_id = e.id JOIN payroll_runs r ON r.id = s.run_id
   WHERE r.status IN ('approved', 'bank_file_sent', 'paid', 'statutory_done') AND (p_slip IS NULL OR s.id = p_slip) AND (p_fy IS NULL OR app.fy_of(r.period_month) = p_fy)
   ORDER BY r.period_month DESC
$$;

CREATE FUNCTION self.payslip_lines(p_employee uuid, p_slip uuid) RETURNS TABLE (code text, label text, kind text, amount numeric)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, app AS $$
  SELECT l.code, l.label, l.kind, l.amount FROM payslip_lines l
   WHERE l.payslip_id = (SELECT p.id FROM self.payslips(p_employee, p_slip) p) ORDER BY l.id
$$;

-- The year so far, up to and including the month of one payslip.
CREATE FUNCTION self.payslip_year_to_date(p_employee uuid, p_slip uuid) RETURNS TABLE (gross numeric, tds numeric, pf numeric, net numeric)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, app AS $$
  SELECT coalesce(sum(p.gross), 0), coalesce(sum(p.tds), 0), coalesce(sum(p.pf_employee), 0), coalesce(sum(p.net_pay), 0)
    FROM self.payslips(p_employee, p_slip) one JOIN self.payslips(p_employee) p ON app.fy_of(p.period_month) = app.fy_of(one.period_month) AND p.period_month <= one.period_month
$$;

-- ---- the handbook
CREATE FUNCTION self.policy_documents(p_employee uuid, p_document uuid DEFAULT NULL) RETURNS TABLE (
  id uuid, slug text, title text, category text, version int, requires_ack boolean, published_at timestamptz, body text, acknowledged_on date)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, app AS $$
  SELECT d.id, d.slug, d.title, d.category, d.version, d.requires_ack, d.published_at, CASE WHEN p_document IS NOT NULL THEN d.body END, a.acknowledged_on
    FROM self.mine(p_employee) e JOIN hr_policy_documents d ON d.entity_id = e.entity_id AND d.is_current
    LEFT JOIN hr_policy_acks a ON a.document_id = d.id AND a.employee_id = e.id
   WHERE p_document IS NULL OR d.id = p_document ORDER BY d.category, d.title
$$;

-- The person's own act: they have read and accept the current version. Doing it twice changes nothing.
CREATE FUNCTION self.acknowledge(p_employee uuid, p_document uuid) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, app AS $$
DECLARE e employees := self.mine(p_employee); v_rows int;
BEGIN
  IF e.status = 'exited' OR NOT EXISTS (SELECT 1 FROM hr_policy_documents d WHERE d.id = p_document AND d.entity_id = e.entity_id AND d.is_current) THEN
    RAISE EXCEPTION 'not a current document of this company' USING ERRCODE = '42501';
  END IF;
  INSERT INTO hr_policy_acks (document_id, employee_id, entity_id, acknowledged_on, recorded_by) VALUES (p_document, e.id, e.entity_id, current_date, app.current_user_id())
  ON CONFLICT (document_id, employee_id) DO NOTHING;
  GET DIAGNOSTICS v_rows = ROW_COUNT;
  IF v_rows > 0 THEN
    PERFORM self.note(e, 'hr_policy_document.acknowledged', 'hr_policy_documents', p_document, jsonb_build_object('employee_id', e.id, 'on', current_date, 'by', 'employee'));
  END IF;
  RETURN v_rows > 0;
END $$;

-- ---- the joining checklist, to read
CREATE FUNCTION self.onboarding_tasks(p_employee uuid) RETURNS TABLE (key text, title text, required boolean, auto text, done_at timestamptz)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, app AS $$
  SELECT t.key, t.title, t.required, t.auto, t.done_at FROM onboarding_tasks t WHERE t.employee_id = (self.mine(p_employee)).id ORDER BY t.sort, t.key
$$;

DO $$
DECLARE f record;
BEGIN
  FOR f IN SELECT p.oid::regprocedure AS sig, p.proname FROM pg_proc p WHERE p.pronamespace = 'self'::regnamespace LOOP
    EXECUTE format('ALTER FUNCTION %s OWNER TO biz_security', f.sig);
    EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC', f.sig);
    IF f.proname NOT IN ('mine', 'note') THEN                  -- the two helpers stay private to the functions that use them
      EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO biz_self', f.sig);
    END IF;
  END LOOP;
END $$;
