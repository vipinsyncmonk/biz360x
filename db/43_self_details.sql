-- =====================================================================
-- Part 43 · Employee self-service: own details and own checklist items
-- An employee can propose their own PAN, salary bank account and mobile number, and tick the joining-checklist items that are theirs to do.
--
-- A change to PAN or bank account is never applied by the employee. It waits as a request and HR confirms it, because the bank account is
-- where salary is paid (a changed account is the classic payroll fraud) and the employee's own sign-in is the weaker door. The bank account
-- number is encrypted by the API before it reaches the database, as everywhere else (secretbox); the database never sees it in the clear.
-- The employee's role still holds no table: everything goes through functions in the schema `self`, each starting with `self.mine()`.
-- =====================================================================

ALTER TABLE onboarding_tasks ADD COLUMN by_employee boolean NOT NULL DEFAULT false;     -- an item the employee ticks themselves

CREATE TABLE employee_detail_requests (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  entity_id        uuid NOT NULL REFERENCES entities(id),
  employee_id      uuid NOT NULL REFERENCES employees(id),
  pan              pan_no,
  bank_account_enc bytea,
  bank_ifsc        char(11) CHECK (bank_ifsc ~ '^[A-Z]{4}0[A-Z0-9]{6}$'),
  mobile_e164      text CHECK (mobile_e164 ~ '^\+[1-9][0-9]{7,14}$'),
  status           text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'confirmed', 'rejected', 'withdrawn')),
  requested_by     uuid NOT NULL REFERENCES users(id),
  requested_at     timestamptz NOT NULL DEFAULT now(),
  decided_by       uuid REFERENCES users(id),
  decided_at       timestamptz,
  hr_note          text CHECK (length(hr_note) <= 300),
  CONSTRAINT detail_request_has_a_change CHECK (pan IS NOT NULL OR bank_account_enc IS NOT NULL OR mobile_e164 IS NOT NULL),
  CONSTRAINT detail_request_bank_whole CHECK ((bank_account_enc IS NULL) = (bank_ifsc IS NULL)),
  CONSTRAINT detail_request_decided CHECK (status = 'pending' OR decided_at IS NOT NULL),
  CONSTRAINT detail_request_confirmed_by_hr CHECK (status <> 'confirmed' OR decided_by IS NOT NULL)
);
CREATE UNIQUE INDEX one_pending_detail_request_ix ON employee_detail_requests (employee_id) WHERE status = 'pending';
CREATE INDEX detail_requests_entity_ix ON employee_detail_requests (entity_id, status);

-- Pay and people data: closed to anyone without payroll or HR, like migration 40's tables.
ALTER TABLE employee_detail_requests ENABLE ROW LEVEL SECURITY;
ALTER TABLE employee_detail_requests FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON employee_detail_requests USING (entity_id IN (SELECT app.visible_entity_ids())) WITH CHECK (entity_id IN (SELECT app.visible_entity_ids()));
CREATE POLICY module_access ON employee_detail_requests AS RESTRICTIVE USING (entity_id IN (SELECT app.module_entity_ids('payroll', 'hr')));
GRANT SELECT, INSERT, UPDATE, DELETE ON employee_detail_requests TO biz_app, biz_worker, biz_security;
GRANT UPDATE (done_at, done_by) ON onboarding_tasks TO biz_security;

-- ---------------------------------------------------------------- the checklist, now saying which items are the employee's
DROP FUNCTION self.onboarding_tasks(uuid);
CREATE FUNCTION self.onboarding_tasks(p_employee uuid) RETURNS TABLE (key text, title text, required boolean, auto text, done_at timestamptz, by_employee boolean)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, app AS $$
  SELECT t.key, t.title, t.required, t.auto, t.done_at, t.by_employee FROM onboarding_tasks t WHERE t.employee_id = (self.mine(p_employee)).id ORDER BY t.sort, t.key
$$;

-- Ticks or unticks one of the employee's own items. Items read from the record (`auto`) and items that are HR's are refused.
CREATE FUNCTION self.tick_task(p_employee uuid, p_key text, p_done boolean) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, app AS $$
DECLARE e employees := self.mine(p_employee); t onboarding_tasks;
BEGIN
  SELECT * INTO t FROM onboarding_tasks WHERE employee_id = e.id AND key = p_key FOR UPDATE;
  IF t.id IS NULL OR NOT t.by_employee OR t.auto IS NOT NULL OR e.status = 'exited' THEN
    RAISE EXCEPTION 'not an item you can tick' USING ERRCODE = '42501';
  END IF;
  IF (t.done_at IS NOT NULL) = p_done THEN
    RETURN false;                                              -- already so: nothing changes
  END IF;
  UPDATE onboarding_tasks SET done_at = CASE WHEN p_done THEN now() END, done_by = CASE WHEN p_done THEN app.current_user_id() END WHERE id = t.id;
  PERFORM self.note(e, 'onboarding_task.employee_ticked', 'onboarding_tasks', t.id, jsonb_build_object('key', p_key, 'done', p_done, 'by', 'employee'));
  RETURN true;
END $$;

-- ---------------------------------------------------------------- proposing own details
-- The latest request, whatever happened to it. The bank account comes back encrypted so the API can show its last four digits.
CREATE FUNCTION self.detail_request(p_employee uuid) RETURNS TABLE (
  id uuid, status text, pan text, bank_account_enc bytea, bank_ifsc text, mobile_e164 text, requested_at timestamptz, decided_at timestamptz, hr_note text)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, app AS $$
  SELECT r.id, r.status, r.pan::text, r.bank_account_enc, r.bank_ifsc::text, r.mobile_e164, r.requested_at, r.decided_at, r.hr_note
    FROM employee_detail_requests r WHERE r.employee_id = (self.mine(p_employee)).id ORDER BY r.requested_at DESC LIMIT 1
$$;

-- Proposes a change; it replaces any request of theirs that is still waiting. Nothing on the employee's record changes until HR confirms.
CREATE FUNCTION self.request_detail_change(p_employee uuid, p_pan text, p_bank_enc bytea, p_ifsc text, p_mobile text) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, app AS $$
DECLARE e employees := self.mine(p_employee); v_id uuid;
BEGIN
  IF e.status = 'exited' THEN
    RAISE EXCEPTION 'no longer an employee' USING ERRCODE = '42501';
  END IF;
  UPDATE employee_detail_requests SET status = 'withdrawn', decided_at = now() WHERE employee_id = e.id AND status = 'pending';
  INSERT INTO employee_detail_requests (entity_id, employee_id, pan, bank_account_enc, bank_ifsc, mobile_e164, requested_by)
  VALUES (e.entity_id, e.id, nullif(p_pan, ''), p_bank_enc, nullif(p_ifsc, ''), nullif(p_mobile, ''), app.current_user_id()) RETURNING id INTO v_id;
  PERFORM self.note(e, 'employee_details.change_requested', 'employee_detail_requests', v_id,
                    jsonb_build_object('pan', p_pan IS NOT NULL AND p_pan <> '', 'bank', p_bank_enc IS NOT NULL, 'mobile', p_mobile IS NOT NULL AND p_mobile <> '', 'by', 'employee'));
  RETURN v_id;
END $$;

CREATE FUNCTION self.withdraw_detail_request(p_employee uuid) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, app AS $$
DECLARE e employees := self.mine(p_employee); v_rows int;
BEGIN
  UPDATE employee_detail_requests SET status = 'withdrawn', decided_at = now() WHERE employee_id = e.id AND status = 'pending';
  GET DIAGNOSTICS v_rows = ROW_COUNT;
  RETURN v_rows > 0;
END $$;

DO $$
DECLARE f record;
BEGIN
  FOR f IN SELECT p.oid::regprocedure AS sig FROM pg_proc p WHERE p.pronamespace = 'self'::regnamespace
            AND p.proname IN ('onboarding_tasks', 'tick_task', 'detail_request', 'request_detail_change', 'withdraw_detail_request') LOOP
    EXECUTE format('ALTER FUNCTION %s OWNER TO biz_security', f.sig);
    EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC', f.sig);
    EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO biz_self', f.sig);
  END LOOP;
END $$;
