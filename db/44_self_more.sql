-- =====================================================================
-- Part 44 · Employee self-service, the rest: address and emergency contact, proofs, a manager who approves leave, tasks, emailed invitations
--
-- As in migrations 41 and 43 the employee's role `biz_self` reads no table; it calls the functions in the schema `self`, each starting with
-- `self.mine()`. What is new here:
--   * contact details are the employee's own to keep (no pay depends on them), so they save them directly; HR can read and correct them;
--   * a proof is a file the employee uploads (identity, address, education, bank, tax) that HR accepts or refuses;
--   * an employee can have a manager. The manager is an employee too, so they decide their team's leave from their own page, but only leave
--     that costs no pay: unpaid days change a payroll run, and pay is HR's;
--   * HR hands a person tasks, which the person marks done;
--   * the invitation to sign in is queued as an email (the sending is a worker job, as with every call to a provider).
-- =====================================================================

-- ---------------------------------------------------------------- manager
ALTER TABLE employees ADD COLUMN manager_id uuid REFERENCES employees(id) CHECK (manager_id <> id);
CREATE INDEX employees_manager_ix ON employees (manager_id) WHERE manager_id IS NOT NULL;
ALTER TABLE leave_requests ADD COLUMN decided_by_manager uuid REFERENCES employees(id);       -- set when the manager, not HR, decided

-- ---------------------------------------------------------------- address and emergency contact
CREATE TABLE employee_contacts (
  employee_id        uuid PRIMARY KEY REFERENCES employees(id),
  entity_id          uuid NOT NULL REFERENCES entities(id),
  address_line1      text CHECK (length(address_line1) BETWEEN 1 AND 120),
  address_line2      text CHECK (length(address_line2) <= 120),
  city               text CHECK (length(city) BETWEEN 1 AND 60),
  state_code         char(2) REFERENCES ref_states(gst_code),
  pincode            char(6) CHECK (pincode ~ '^[1-9][0-9]{5}$'),
  emergency_name     text CHECK (length(emergency_name) BETWEEN 1 AND 80),
  emergency_relation text CHECK (length(emergency_relation) BETWEEN 1 AND 40),
  emergency_mobile   text CHECK (emergency_mobile ~ '^\+[1-9][0-9]{7,14}$'),
  updated_at         timestamptz NOT NULL DEFAULT now(),
  updated_by         uuid REFERENCES users(id),
  CONSTRAINT contact_address_whole CHECK ((address_line1 IS NULL) = (city IS NULL) AND (city IS NULL) = (state_code IS NULL) AND (state_code IS NULL) = (pincode IS NULL)),
  CONSTRAINT contact_emergency_whole CHECK ((emergency_name IS NULL) = (emergency_relation IS NULL) AND (emergency_relation IS NULL) = (emergency_mobile IS NULL))
);

-- ---------------------------------------------------------------- proofs
CREATE TABLE employee_proofs (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  entity_id     uuid NOT NULL REFERENCES entities(id),
  employee_id   uuid NOT NULL REFERENCES employees(id),
  kind          text NOT NULL CHECK (kind IN ('identity', 'address', 'education', 'bank', 'tax', 'other')),
  label         text NOT NULL CHECK (length(label) BETWEEN 1 AND 80),
  file_name     text NOT NULL CHECK (length(file_name) BETWEEN 1 AND 200),
  mime_type     text NOT NULL CHECK (mime_type IN ('application/pdf', 'image/jpeg', 'image/png')),
  size_bytes    integer NOT NULL CHECK (size_bytes BETWEEN 1 AND 5242880),
  sha256        char(64) NOT NULL,
  storage_key   text NOT NULL,
  status        text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'accepted', 'rejected')),
  uploaded_by   uuid NOT NULL REFERENCES users(id),
  uploaded_at   timestamptz NOT NULL DEFAULT now(),
  decided_by    uuid REFERENCES users(id),
  decided_at    timestamptz,
  hr_note       text CHECK (length(hr_note) <= 300),
  CONSTRAINT proof_decided CHECK (status = 'pending' OR (decided_by IS NOT NULL AND decided_at IS NOT NULL))
);
CREATE INDEX employee_proofs_ix ON employee_proofs (employee_id, uploaded_at DESC);

-- ---------------------------------------------------------------- tasks for a person
CREATE TABLE employee_tasks (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  entity_id   uuid NOT NULL REFERENCES entities(id),
  employee_id uuid NOT NULL REFERENCES employees(id),
  title       text NOT NULL CHECK (length(title) BETWEEN 1 AND 120),
  detail      text CHECK (length(detail) <= 600),
  due_on      date,
  status      text NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'done', 'cancelled')),
  created_by  uuid NOT NULL REFERENCES users(id),
  created_at  timestamptz NOT NULL DEFAULT now(),
  done_at     timestamptz,
  CONSTRAINT task_done_has_time CHECK ((status = 'done') = (done_at IS NOT NULL))
);
CREATE INDEX employee_tasks_ix ON employee_tasks (employee_id, status, due_on);

-- People data: closed to anyone without payroll or HR, like migration 40's tables.
DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['employee_contacts', 'employee_proofs', 'employee_tasks'] LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
    EXECUTE format('CREATE POLICY tenant_isolation ON %I USING (entity_id IN (SELECT app.visible_entity_ids())) WITH CHECK (entity_id IN (SELECT app.visible_entity_ids()))', t);
    EXECUTE format($p$CREATE POLICY module_access ON %I AS RESTRICTIVE USING (entity_id IN (SELECT app.module_entity_ids('payroll', 'hr')))$p$, t);
    EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON %I TO biz_app, biz_worker, biz_security', t);
  END LOOP;
END $$;
GRANT UPDATE (status, decided_at, decision_note, decided_by_manager) ON leave_requests TO biz_security;

-- ---------------------------------------------------------------- the employee's contact details
CREATE FUNCTION self.contact(p_employee uuid) RETURNS TABLE (
  address_line1 text, address_line2 text, city text, state_code text, pincode text, emergency_name text, emergency_relation text, emergency_mobile text, updated_at timestamptz)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, app AS $$
  SELECT c.address_line1, c.address_line2, c.city, c.state_code::text, c.pincode::text, c.emergency_name, c.emergency_relation, c.emergency_mobile, c.updated_at
    FROM employee_contacts c WHERE c.employee_id = (self.mine(p_employee)).id
$$;

-- Saves both parts at once; a part left empty clears it. What is audited is which parts were set, not the values.
CREATE FUNCTION self.save_contact(p_employee uuid, p_line1 text, p_line2 text, p_city text, p_state text, p_pin text, p_ename text, p_erelation text, p_emobile text) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, app AS $$
DECLARE e employees := self.mine(p_employee);
BEGIN
  IF e.status = 'exited' THEN
    RAISE EXCEPTION 'no longer an employee' USING ERRCODE = '42501';
  END IF;
  INSERT INTO employee_contacts (employee_id, entity_id, address_line1, address_line2, city, state_code, pincode, emergency_name, emergency_relation, emergency_mobile, updated_by)
  VALUES (e.id, e.entity_id, nullif(p_line1, ''), nullif(p_line2, ''), nullif(p_city, ''), nullif(p_state, ''), nullif(p_pin, ''), nullif(p_ename, ''), nullif(p_erelation, ''), nullif(p_emobile, ''),
          app.current_user_id())
  ON CONFLICT (employee_id) DO UPDATE SET address_line1 = EXCLUDED.address_line1, address_line2 = EXCLUDED.address_line2, city = EXCLUDED.city, state_code = EXCLUDED.state_code,
    pincode = EXCLUDED.pincode, emergency_name = EXCLUDED.emergency_name, emergency_relation = EXCLUDED.emergency_relation, emergency_mobile = EXCLUDED.emergency_mobile,
    updated_at = now(), updated_by = EXCLUDED.updated_by;
  PERFORM self.note(e, 'employee_contact.saved', 'employees', e.id, jsonb_build_object('address', nullif(p_line1, '') IS NOT NULL, 'emergency', nullif(p_ename, '') IS NOT NULL, 'by', 'employee'));
END $$;

-- ---------------------------------------------------------------- proofs
CREATE FUNCTION self.proofs(p_employee uuid) RETURNS TABLE (
  id uuid, kind text, label text, file_name text, mime_type text, size_bytes integer, status text, uploaded_at timestamptz, decided_at timestamptz, hr_note text)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, app AS $$
  SELECT p.id, p.kind, p.label, p.file_name, p.mime_type, p.size_bytes, p.status, p.uploaded_at, p.decided_at, p.hr_note
    FROM employee_proofs p WHERE p.employee_id = (self.mine(p_employee)).id ORDER BY p.uploaded_at DESC
$$;

CREATE FUNCTION self.add_proof(p_employee uuid, p_kind text, p_label text, p_file text, p_mime text, p_size integer, p_sha text, p_key text) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, app AS $$
DECLARE e employees := self.mine(p_employee); v_id uuid;
BEGIN
  IF e.status = 'exited' THEN
    RAISE EXCEPTION 'no longer an employee' USING ERRCODE = '42501';
  END IF;
  IF (SELECT count(*) FROM employee_proofs WHERE employee_id = e.id) >= 30 THEN
    RAISE EXCEPTION 'too many files' USING ERRCODE = '23514', CONSTRAINT = 'proofs_limit';
  END IF;
  INSERT INTO employee_proofs (entity_id, employee_id, kind, label, file_name, mime_type, size_bytes, sha256, storage_key, uploaded_by)
  VALUES (e.entity_id, e.id, p_kind, p_label, p_file, p_mime, p_size, p_sha, p_key, app.current_user_id()) RETURNING id INTO v_id;
  PERFORM self.note(e, 'employee_proof.uploaded', 'employee_proofs', v_id, jsonb_build_object('kind', p_kind, 'label', p_label, 'by', 'employee'));
  RETURN v_id;
END $$;

-- Takes back a file nobody has decided on. An accepted or refused file stays: HR's decision is part of the record.
CREATE FUNCTION self.remove_proof(p_employee uuid, p_proof uuid) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, app AS $$
DECLARE e employees := self.mine(p_employee); v_rows int;
BEGIN
  DELETE FROM employee_proofs WHERE id = p_proof AND employee_id = e.id AND status = 'pending';
  GET DIAGNOSTICS v_rows = ROW_COUNT;
  IF v_rows > 0 THEN
    PERFORM self.note(e, 'employee_proof.removed', 'employee_proofs', p_proof, jsonb_build_object('by', 'employee'));
  END IF;
  RETURN v_rows > 0;
END $$;

CREATE FUNCTION self.proof_file(p_employee uuid, p_proof uuid) RETURNS TABLE (storage_key text, file_name text, mime_type text)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, app AS $$
  SELECT p.storage_key, p.file_name, p.mime_type FROM employee_proofs p WHERE p.id = p_proof AND p.employee_id = (self.mine(p_employee)).id
$$;

-- ---------------------------------------------------------------- tasks
CREATE FUNCTION self.tasks(p_employee uuid) RETURNS TABLE (id uuid, title text, detail text, due_on date, status text, created_at timestamptz, done_at timestamptz)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, app AS $$
  SELECT t.id, t.title, t.detail, t.due_on, t.status, t.created_at, t.done_at FROM employee_tasks t
   WHERE t.employee_id = (self.mine(p_employee)).id AND t.status <> 'cancelled' ORDER BY (t.status = 'done'), t.due_on NULLS LAST, t.created_at
$$;

CREATE FUNCTION self.set_task_done(p_employee uuid, p_task uuid, p_done boolean) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, app AS $$
DECLARE e employees := self.mine(p_employee); t employee_tasks;
BEGIN
  SELECT * INTO t FROM employee_tasks WHERE id = p_task AND employee_id = e.id AND status <> 'cancelled' FOR UPDATE;
  IF t.id IS NULL OR e.status = 'exited' THEN
    RAISE EXCEPTION 'not your task' USING ERRCODE = '42501';
  END IF;
  IF (t.status = 'done') = p_done THEN
    RETURN false;
  END IF;
  UPDATE employee_tasks SET status = CASE WHEN p_done THEN 'done' ELSE 'open' END, done_at = CASE WHEN p_done THEN now() END WHERE id = p_task;
  PERFORM self.note(e, CASE WHEN p_done THEN 'employee_task.done' ELSE 'employee_task.reopened' END, 'employee_tasks', p_task, jsonb_build_object('title', t.title, 'by', 'employee'));
  RETURN true;
END $$;

-- ---------------------------------------------------------------- manager and team
CREATE FUNCTION self.manager(p_employee uuid) RETURNS TABLE (id uuid, full_name text, designation text)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, app AS $$
  SELECT m.id, m.full_name, m.designation FROM employees m WHERE m.id = (self.mine(p_employee)).manager_id
$$;

CREATE FUNCTION self.reports(p_employee uuid) RETURNS TABLE (id uuid, employee_code text, full_name text, designation text)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, app AS $$
  SELECT r.id, r.employee_code, r.full_name, r.designation FROM employees r WHERE r.manager_id = (self.mine(p_employee)).id AND r.status <> 'exited' ORDER BY r.full_name
$$;

-- Leave requests of the people who report to me: the undecided first, then the last two months of decided ones.
CREATE FUNCTION self.team_leave(p_employee uuid) RETURNS TABLE (
  id uuid, employee_id uuid, employee_name text, leave_type text, leave_type_name text, d_from date, d_to date, days numeric, lop_days numeric, half_day boolean, reason text,
  status text, requested_at timestamptz, decided_at timestamptz, decision_note text, by_me boolean)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, app AS $$
  SELECT r.id, r.employee_id, e.full_name, t.code, t.name, lower(r.dates), upper(r.dates) - 1, r.days, r.lop_days, r.half_day, r.reason, r.status, r.requested_at, r.decided_at, r.decision_note,
         r.decided_by_manager = (self.mine(p_employee)).id
    FROM leave_requests r JOIN employees e ON e.id = r.employee_id JOIN leave_types t ON t.id = r.leave_type_id
   WHERE e.manager_id = (self.mine(p_employee)).id AND e.status <> 'exited' AND (r.status = 'pending' OR r.decided_at > now() - interval '60 days')
   ORDER BY (r.status <> 'pending'), lower(r.dates), r.requested_at
$$;

-- A manager decides leave of their own reports only, and approves only leave that costs no pay. Unpaid days change a payroll run, which is HR's.
CREATE FUNCTION self.decide_team_leave(p_employee uuid, p_request uuid, p_approve boolean, p_note text) RETURNS text
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, app AS $$
DECLARE me employees := self.mine(p_employee); r leave_requests; rep employees;
BEGIN
  IF me.status = 'exited' THEN
    RAISE EXCEPTION 'no longer an employee' USING ERRCODE = '42501';
  END IF;
  SELECT * INTO r FROM leave_requests WHERE id = p_request FOR UPDATE;
  SELECT * INTO rep FROM employees WHERE id = r.employee_id;
  IF r.id IS NULL OR rep.manager_id IS DISTINCT FROM me.id OR rep.id = me.id THEN
    RAISE EXCEPTION 'not a request of your team' USING ERRCODE = '42501';
  END IF;
  IF r.status <> 'pending' THEN
    RAISE EXCEPTION 'already decided' USING ERRCODE = '23514', CONSTRAINT = 'leave_already_decided';
  END IF;
  IF p_approve AND r.lop_days > 0 THEN
    RAISE EXCEPTION 'unpaid days are for HR' USING ERRCODE = '23514', CONSTRAINT = 'leave_unpaid_is_hr';
  END IF;
  UPDATE leave_requests SET status = CASE WHEN p_approve THEN 'approved' ELSE 'rejected' END, decided_at = now(), decision_note = nullif(p_note, ''),
         decided_by_manager = me.id, approved_by = CASE WHEN p_approve THEN app.current_user_id() ELSE approved_by END WHERE id = p_request;
  PERFORM self.note(rep, CASE WHEN p_approve THEN 'leave.approved' ELSE 'leave.rejected' END, 'leave_requests', p_request,
                    jsonb_build_object('status', CASE WHEN p_approve THEN 'approved' ELSE 'rejected' END, 'by', 'manager', 'manager', me.full_name));
  RETURN CASE WHEN p_approve THEN 'approved' ELSE 'rejected' END;
END $$;

-- ---------------------------------------------------------------- the invitation, now giving back the account it made so an email can be addressed to it
DROP FUNCTION app.invite_employee(uuid);
CREATE FUNCTION app.invite_employee(p_employee uuid) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, app AS $$
DECLARE e employees; v_user uuid;
BEGIN
  SELECT * INTO e FROM employees WHERE id = p_employee FOR UPDATE;
  IF e.id IS NULL OR e.email IS NULL OR e.status = 'exited' OR NOT app.can(e.entity_id, 'hr', 'write') THEN
    RAISE EXCEPTION 'cannot invite this employee' USING ERRCODE = '42501';
  END IF;
  INSERT INTO users (email, full_name, auth_provider) VALUES (e.email, left(e.full_name, 120), 'otp') ON CONFLICT (email) DO NOTHING;
  UPDATE employees SET self_invited_at = coalesce(self_invited_at, now()), self_invited_by = coalesce(self_invited_by, app.current_user_id()) WHERE id = p_employee;
  SELECT id INTO v_user FROM users WHERE email = e.email AND auth_provider <> 'service';
  RETURN v_user;
END $$;
ALTER FUNCTION app.invite_employee(uuid) OWNER TO biz_security;
GRANT EXECUTE ON FUNCTION app.invite_employee(uuid) TO biz_app;

DO $$
DECLARE f record;
BEGIN
  FOR f IN SELECT p.oid::regprocedure AS sig FROM pg_proc p WHERE p.pronamespace = 'self'::regnamespace
            AND p.proname IN ('contact', 'save_contact', 'proofs', 'add_proof', 'remove_proof', 'proof_file', 'tasks', 'set_task_done', 'manager', 'reports', 'team_leave', 'decide_team_leave') LOOP
    EXECUTE format('ALTER FUNCTION %s OWNER TO biz_security', f.sig);
    EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC', f.sig);
    EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO biz_self', f.sig);
  END LOOP;
END $$;
