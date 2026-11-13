-- =====================================================================
-- Part 38 · Who does what: the responsibility matrix, and work that finds its owner
-- A company says once who prepares and who approves each kind of work. From then on a new obligation gets its owner and approver, and a
-- request for approval goes to the named approver, without anyone assigning them by hand. With no row for a kind nothing changes:
-- the work is unowned and any approver may decide it, as before.
--
-- Only steps that change something are here: `prepare` (who owns a filing) and `approve` (who is asked to decide). Payroll runs, vendor
-- payments and leave have only an approver, because whoever starts them is the preparer.
-- The routing lives in triggers so every path that creates work (the engine, the API, a worker) is covered by one rule.
-- See docs/ROLES_DESIGN.md, phase 2.
-- =====================================================================
CREATE TABLE responsibilities (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  entity_id   uuid NOT NULL REFERENCES entities(id),
  work_kind   text NOT NULL CHECK (work_kind IN ('gst', 'tds', 'pf_esi', 'professional_tax', 'mca', 'income_tax', 'other_filing', 'payroll_run', 'vendor_payment', 'leave')),
  step        text NOT NULL CHECK (step IN ('prepare', 'approve')),
  user_id     uuid NOT NULL REFERENCES users(id),
  set_by      uuid REFERENCES users(id),
  created_at  timestamptz NOT NULL DEFAULT now(),
  UNIQUE (entity_id, work_kind, step),
  CONSTRAINT responsibility_step_fits CHECK (step = 'approve' OR work_kind NOT IN ('payroll_run', 'vendor_payment', 'leave'))
);
CREATE INDEX responsibilities_user_ix ON responsibilities (user_id);

ALTER TABLE responsibilities ENABLE ROW LEVEL SECURITY;
ALTER TABLE responsibilities FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON responsibilities USING (entity_id IN (SELECT app.visible_entity_ids())) WITH CHECK (entity_id IN (SELECT app.visible_entity_ids()));
GRANT SELECT, INSERT, UPDATE, DELETE ON responsibilities TO biz_app, biz_worker;

-- Which kind of work a compliance rule is, from its code and the authority it is filed with.
CREATE FUNCTION app.work_kind_of_rule(code text, authority text) RETURNS text LANGUAGE sql IMMUTABLE AS $$
  SELECT CASE
    WHEN code LIKE 'GST.%' OR authority = 'GSTN' THEN 'gst'
    WHEN code LIKE 'TDS.%' THEN 'tds'
    WHEN code LIKE 'EPF.%' OR code LIKE 'ESI.%' OR authority IN ('EPFO', 'ESIC') THEN 'pf_esi'
    WHEN code LIKE 'PT.%' OR authority LIKE 'PT-%' THEN 'professional_tax'
    WHEN code LIKE 'MCA.%' OR authority = 'MCA' THEN 'mca'
    WHEN code LIKE 'IT.%' OR authority = 'CBDT' THEN 'income_tax'
    ELSE 'other_filing' END
$$;

CREATE FUNCTION app.responsible(p_entity uuid, p_kind text, p_step text) RETURNS uuid LANGUAGE sql STABLE AS $$
  SELECT user_id FROM responsibilities WHERE entity_id = p_entity AND work_kind = p_kind AND step = p_step
$$;

-- A new obligation takes its owner and approver from the matrix, unless whoever created it named them.
CREATE FUNCTION app.route_obligation() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE kind text;
BEGIN
  IF NEW.owner_user_id IS NOT NULL AND NEW.approver_user_id IS NOT NULL THEN
    RETURN NEW;
  END IF;
  SELECT app.work_kind_of_rule(r.code, r.authority_code) INTO kind FROM compliance_rules r WHERE r.id = NEW.rule_id;
  NEW.owner_user_id := coalesce(NEW.owner_user_id, app.responsible(NEW.entity_id, kind, 'prepare'));
  NEW.approver_user_id := coalesce(NEW.approver_user_id, app.responsible(NEW.entity_id, kind, 'approve'));
  RETURN NEW;
END $$;
CREATE TRIGGER obligations_route BEFORE INSERT ON obligations FOR EACH ROW EXECUTE FUNCTION app.route_obligation();

-- A request for approval goes to the approver the matrix names for that kind of work. Never to the person who raised it: the two-person
-- rule would stop them deciding it, so it stays open to the other approvers instead.
CREATE FUNCTION app.route_approval() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE kind text; who uuid;
BEGIN
  IF NEW.assigned_to IS NOT NULL THEN
    RETURN NEW;
  END IF;
  kind := CASE NEW.subject
            WHEN 'obligation' THEN (SELECT app.work_kind_of_rule(r.code, r.authority_code) FROM obligations o JOIN compliance_rules r ON r.id = o.rule_id WHERE o.id = NEW.subject_id)
            WHEN 'vendor_payment' THEN 'vendor_payment'
            WHEN 'payroll_run' THEN 'payroll_run'
          END;
  IF kind IS NULL THEN
    RETURN NEW;
  END IF;
  who := app.responsible(NEW.entity_id, kind, 'approve');
  IF who IS DISTINCT FROM NEW.requested_by THEN
    NEW.assigned_to := who;
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER approval_requests_route BEFORE INSERT ON approval_requests FOR EACH ROW EXECUTE FUNCTION app.route_approval();
GRANT EXECUTE ON FUNCTION app.work_kind_of_rule(text, text), app.responsible(uuid, text, text), app.route_obligation(), app.route_approval() TO biz_app, biz_worker;

-- Tasks a person gives to another: what it is about, and who asked.
ALTER TABLE tasks
  ADD COLUMN description text,
  ADD COLUMN created_by  uuid REFERENCES users(id);
