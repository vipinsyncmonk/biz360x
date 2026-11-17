-- =====================================================================
-- Part 39 · Practitioner engagements
-- An advisor grant becomes an engagement: the company invites a practitioner by email for a named service, and access starts only when
-- the practitioner accepts, as themselves or for the firm they are a partner of. Until now a grant was created as `invited` and nothing
-- ever made it active, and a practitioner could only be a firm.
--
-- * A sole practitioner is engaged as a person (practitioner_user_id); a firm through advisor_org_id. Never both.
-- * An audit engagement (statutory, tax or internal audit) can only read and raise queries, and must have an end date. A constraint,
--   not a convention.
-- * The same firm or person cannot hold the statutory audit and keep the books (or be the internal auditor or virtual CFO) for the
--   same company and year. Which services an auditor may not provide is set by the Companies Act and the ICAI rules: the list here is a
--   starter rule for a CA to confirm.
-- * A firm's staff see only the clients they are assigned to. Partners and admins see all. Everyone who is staff today is assigned to
--   every client their firm has today, so nobody loses access when this runs.
-- See docs/ROLES_DESIGN.md, phase 3.
-- =====================================================================
ALTER TABLE advisor_grants
  ADD COLUMN practitioner_user_id uuid REFERENCES users(id),
  ADD COLUMN service        text CHECK (service IN ('bookkeeping', 'gst', 'tds', 'pf_esi', 'payroll', 'secretarial', 'trade', 'statutory_audit', 'tax_audit', 'internal_audit', 'virtual_cfo', 'legal')),
  ADD COLUMN preset         text CHECK (preset IS NULL OR preset ~ '^[a-z_]{1,40}$'),      -- the starting point the scopes came from, if any
  ADD COLUMN period_fy      fy_label,                     -- an audit or a year's filing work belongs to a financial year
  ADD COLUMN invited_email  citext,
  ADD COLUMN membership_no  text CHECK (membership_no IS NULL OR length(btrim(membership_no)) BETWEEN 3 AND 20),      -- as the practitioner states it; not verified here
  ADD COLUMN invited_by     uuid REFERENCES users(id),
  ADD COLUMN accepted_at    timestamptz,
  ADD COLUMN accepted_by    uuid REFERENCES users(id);
ALTER TABLE advisor_grants DROP CONSTRAINT advisor_grants_status_check;
ALTER TABLE advisor_grants ADD CONSTRAINT advisor_grants_status_check CHECK (status IN ('invited', 'active', 'declined', 'revoked', 'expired'));
CREATE INDEX advisor_grants_practitioner_ix ON advisor_grants (practitioner_user_id) WHERE practitioner_user_id IS NOT NULL;

CREATE FUNCTION app.scopes_within(scopes jsonb, allowed text[]) RETURNS boolean LANGUAGE sql IMMUTABLE AS $$
  SELECT NOT EXISTS (SELECT 1 FROM jsonb_each_text(scopes) s WHERE NOT (s.value = ANY(allowed)))
$$;
ALTER TABLE advisor_grants
  ADD CONSTRAINT engagement_one_party CHECK (advisor_org_id IS NULL OR practitioner_user_id IS NULL),
  ADD CONSTRAINT audit_engagement_read_only CHECK (service IS NULL OR service NOT IN ('statutory_audit', 'tax_audit', 'internal_audit')
                                                   OR (app.scopes_within(scopes, ARRAY['read', 'comment']) AND expires_on IS NOT NULL));

-- Independence: the statutory auditor does not also keep the books, audit internally or act as CFO for the same company and year.
CREATE FUNCTION app.check_engagement_independence() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE clash text[];
BEGIN
  IF NEW.status NOT IN ('invited', 'active') OR NEW.service IS NULL OR (NEW.advisor_org_id IS NULL AND NEW.practitioner_user_id IS NULL) THEN
    RETURN NEW;
  END IF;
  clash := CASE WHEN NEW.service = 'statutory_audit' THEN ARRAY['bookkeeping', 'internal_audit', 'virtual_cfo']
                WHEN NEW.service IN ('bookkeeping', 'internal_audit', 'virtual_cfo') THEN ARRAY['statutory_audit'] END;
  IF clash IS NOT NULL AND EXISTS (
       SELECT 1 FROM advisor_grants g
        WHERE g.entity_id = NEW.entity_id AND g.id <> NEW.id AND g.status IN ('invited', 'active') AND g.service = ANY(clash)
          AND (g.advisor_org_id = NEW.advisor_org_id OR g.practitioner_user_id = NEW.practitioner_user_id)
          AND (g.period_fy IS NULL OR NEW.period_fy IS NULL OR g.period_fy = NEW.period_fy)) THEN
    RAISE EXCEPTION 'The same practitioner cannot hold the statutory audit and % for this company in the same year', replace(clash[1], '_', ' ')
      USING ERRCODE = '23514', CONSTRAINT = 'engagement_independence';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER advisor_grants_independence BEFORE INSERT OR UPDATE OF status, service, advisor_org_id, practitioner_user_id, period_fy ON advisor_grants
  FOR EACH ROW EXECUTE FUNCTION app.check_engagement_independence();

-- Which of a firm's staff work on which client. A client is a company the firm holds an engagement for, or one it set up under itself.
CREATE TABLE firm_client_staff (
  organization_id uuid NOT NULL REFERENCES organizations(id),
  entity_id       uuid NOT NULL REFERENCES entities(id),
  user_id         uuid NOT NULL REFERENCES users(id),
  assigned_by     uuid REFERENCES users(id),
  assigned_at     timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (organization_id, entity_id, user_id)
);
CREATE INDEX firm_client_staff_user_ix ON firm_client_staff (user_id);
ALTER TABLE firm_client_staff ENABLE ROW LEVEL SECURITY;
ALTER TABLE firm_client_staff FORCE ROW LEVEL SECURITY;
CREATE POLICY firm_reads ON firm_client_staff FOR SELECT USING (organization_id IN (SELECT app.my_org_ids()));
CREATE POLICY partners_write ON firm_client_staff FOR ALL USING (organization_id IN (SELECT app.my_org_ids(true))) WITH CHECK (organization_id IN (SELECT app.my_org_ids(true)));
GRANT SELECT, INSERT, UPDATE, DELETE ON firm_client_staff TO biz_app, biz_worker;
GRANT SELECT ON firm_client_staff, organizations TO biz_security;

-- Nobody loses access today: every staff member is assigned to every client their firm has now.
INSERT INTO firm_client_staff (organization_id, entity_id, user_id)
SELECT om.organization_id, c.entity_id, om.user_id
  FROM org_members om
  JOIN organizations o ON o.id = om.organization_id AND o.kind IN ('ca_firm', 'cs_firm', 'law_firm')
  JOIN (SELECT organization_id, id AS entity_id FROM entities
        UNION SELECT advisor_org_id, entity_id FROM advisor_grants WHERE advisor_org_id IS NOT NULL AND status IN ('invited', 'active')) c ON c.organization_id = om.organization_id
 WHERE om.role = 'staff'
ON CONFLICT DO NOTHING;

-- Companies the current user may see: their own memberships; companies under an organisation they belong to; companies that engaged them
-- or their firm. In a firm, staff see only the clients they are assigned to.
CREATE OR REPLACE FUNCTION app.visible_entity_ids() RETURNS SETOF uuid
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, app AS $$
  SELECT m.entity_id FROM memberships m
   WHERE m.user_id = app.current_user_id() AND m.revoked_at IS NULL
  UNION
  SELECT e.id FROM entities e
    JOIN org_members om ON om.organization_id = e.organization_id
    JOIN organizations o ON o.id = om.organization_id
   WHERE om.user_id = app.current_user_id()
     AND (om.role IN ('admin', 'partner') OR o.kind = 'business_group'
          OR EXISTS (SELECT 1 FROM firm_client_staff s WHERE s.organization_id = om.organization_id AND s.entity_id = e.id AND s.user_id = om.user_id))
  UNION
  SELECT g.entity_id FROM advisor_grants g
    JOIN org_members om ON om.organization_id = g.advisor_org_id
   WHERE om.user_id = app.current_user_id()
     AND g.status = 'active' AND (g.expires_on IS NULL OR g.expires_on >= current_date)
     AND (om.role IN ('admin', 'partner')
          OR EXISTS (SELECT 1 FROM firm_client_staff s WHERE s.organization_id = om.organization_id AND s.entity_id = g.entity_id AND s.user_id = om.user_id))
  UNION
  SELECT g.entity_id FROM advisor_grants g
   WHERE g.practitioner_user_id = app.current_user_id()
     AND g.status = 'active' AND (g.expires_on IS NULL OR g.expires_on >= current_date)
$$;

-- The scopes the current user holds on a company through engagements: one row per engagement that reaches them.
CREATE FUNCTION app.my_grant_scopes(p_entity uuid) RETURNS TABLE (grant_id uuid, scopes jsonb, service text)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, app AS $$
  SELECT g.id, g.scopes, g.service FROM advisor_grants g
   WHERE g.entity_id = p_entity AND g.status = 'active' AND (g.expires_on IS NULL OR g.expires_on >= current_date)
     AND (g.practitioner_user_id = app.current_user_id()
          OR EXISTS (SELECT 1 FROM org_members om WHERE om.organization_id = g.advisor_org_id AND om.user_id = app.current_user_id()
                        AND (om.role IN ('admin', 'partner')
                             OR EXISTS (SELECT 1 FROM firm_client_staff s WHERE s.organization_id = om.organization_id AND s.entity_id = g.entity_id AND s.user_id = om.user_id))))
$$;

-- Find or create the person a company is inviting. Only an owner of the company may; it runs as its owner because it may create the user.
CREATE FUNCTION app.invite_practitioner(p_entity uuid, p_email text, p_name text) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, app AS $$
DECLARE v_user uuid;
BEGIN
  IF NOT EXISTS (SELECT 1 FROM memberships WHERE entity_id = p_entity AND user_id = app.current_user_id() AND role = 'owner' AND revoked_at IS NULL) THEN
    RAISE EXCEPTION 'only an owner can engage a practitioner' USING ERRCODE = '42501';
  END IF;
  SELECT id INTO v_user FROM users WHERE email = p_email;
  IF v_user IS NULL THEN
    INSERT INTO users (email, full_name, auth_provider) VALUES (p_email, left(coalesce(nullif(trim(p_name), ''), split_part(p_email, '@', 1)), 120), 'otp') RETURNING id INTO v_user;
  END IF;
  RETURN v_user;
END $$;

-- The invitations waiting for the current user. They cannot see the company yet, so this reads past row-level security for exactly these rows.
CREATE FUNCTION app.my_engagement_invites() RETURNS TABLE (id uuid, entity_id uuid, entity_name text, advisor_name text, advisor_kind text, service text, scopes jsonb,
                                                           period_fy text, expires_on date, invited_by_name text, created_at timestamptz)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, app AS $$
  SELECT g.id, g.entity_id, e.legal_name, g.advisor_name, g.advisor_kind, g.service, g.scopes, g.period_fy::text, g.expires_on, u.full_name, g.created_at
    FROM advisor_grants g JOIN entities e ON e.id = g.entity_id LEFT JOIN users u ON u.id = g.invited_by
   WHERE g.practitioner_user_id = app.current_user_id() AND g.status = 'invited' AND (g.expires_on IS NULL OR g.expires_on >= current_date)
   ORDER BY g.created_at
$$;

-- Accept an invitation as oneself, or for a firm one is a partner or admin of (the engagement then belongs to the firm). Or decline it.
CREATE FUNCTION app.answer_engagement(p_grant uuid, p_accept boolean, p_org uuid) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, app AS $$
DECLARE v_entity uuid;
BEGIN
  SELECT entity_id INTO v_entity FROM advisor_grants
   WHERE id = p_grant AND practitioner_user_id = app.current_user_id() AND status = 'invited' AND (expires_on IS NULL OR expires_on >= current_date) FOR UPDATE;
  IF v_entity IS NULL THEN
    RETURN NULL;
  END IF;
  IF NOT p_accept THEN
    UPDATE advisor_grants SET status = 'declined', accepted_by = app.current_user_id(), accepted_at = now() WHERE id = p_grant;
  ELSIF p_org IS NULL THEN
    UPDATE advisor_grants SET status = 'active', accepted_by = app.current_user_id(), accepted_at = now() WHERE id = p_grant;
  ELSE
    IF p_org NOT IN (SELECT app.my_org_ids(true)) OR NOT EXISTS (SELECT 1 FROM organizations WHERE id = p_org AND kind IN ('ca_firm', 'cs_firm', 'law_firm')) THEN
      RAISE EXCEPTION 'not a partner of that firm' USING ERRCODE = '42501';
    END IF;
    UPDATE advisor_grants SET status = 'active', advisor_org_id = p_org, practitioner_user_id = NULL, accepted_by = app.current_user_id(), accepted_at = now() WHERE id = p_grant;
  END IF;
  -- the audit entry and the event are written here: someone who declines never gets to see the company, so the API could not write them
  INSERT INTO audit_log (entity_id, actor_kind, actor_user_id, action, subject, subject_id, after)
  VALUES (v_entity, 'user', app.current_user_id(), CASE WHEN p_accept THEN 'engagement.accepted' ELSE 'engagement.declined' END, 'advisor_grants', p_grant,
          jsonb_build_object('for_firm', p_org));
  INSERT INTO event_outbox (entity_id, type, subject, subject_id, data)
  VALUES (v_entity, CASE WHEN p_accept THEN 'engagement.accepted' ELSE 'engagement.declined' END, 'advisor_grants', p_grant, '{}'::jsonb);
  RETURN v_entity;
END $$;
GRANT EXECUTE ON FUNCTION app.scopes_within(jsonb, text[]), app.check_engagement_independence(), app.my_grant_scopes(uuid), app.invite_practitioner(uuid, text, text),
                          app.my_engagement_invites(), app.answer_engagement(uuid, boolean, uuid) TO biz_app, biz_worker;

-- A company can see the name of a practitioner it has invited or engaged as a person (to name them for work and give them tasks).
-- advisor_grants is itself filtered by company, so this shows exactly those people and no one else's practitioners.
CREATE POLICY engaged_practitioners ON users FOR SELECT
  USING (id IN (SELECT practitioner_user_id FROM advisor_grants WHERE practitioner_user_id IS NOT NULL));
