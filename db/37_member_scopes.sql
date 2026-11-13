-- =====================================================================
-- Part 37 · A level per module for team members, titles and presets
-- Until now a member had one level (owner, approver, maker, viewer) for every module they could open. A CFO who approves finance but only
-- reads HR, or an HR manager who approves leave but only prepares payroll, could not be expressed. A member now has `scopes`: a level for
-- each module, on the same scale practitioners' grants use.
--
--   read < comment < write < approve < file        ("*" stands for every module not named)
--
-- `role` and `modules` stay, kept in step by the trigger below, because the rest of the schema reads them (who the owners are, who may be
-- asked to approve). `role = 'owner'` remains the one flag that lets a person manage people and settings; an owner has every module.
-- Two modules are split out so they can be granted on their own: `hr` (onboarding, leave, policies) from `payroll`, and `inventory` from
-- `finance`. Everyone who had the wider module keeps the narrower one.
-- See docs/ROLES_DESIGN.md.
-- =====================================================================
ALTER TABLE memberships
  ADD COLUMN scopes jsonb,
  ADD COLUMN title  text CHECK (title IS NULL OR length(btrim(title)) BETWEEN 1 AND 80),      -- what the company calls the person: "CFO", "Accounts executive"
  ADD COLUMN preset text CHECK (preset IS NULL OR preset ~ '^[a-z_]{1,40}$'),                  -- the starting point they were invited with, if any
  ADD COLUMN home   text CHECK (home IS NULL OR home IN ('leadership', 'finance', 'people', 'general'));

CREATE FUNCTION app.scope_rank(level text) RETURNS int LANGUAGE sql IMMUTABLE AS $$
  SELECT CASE level WHEN 'read' THEN 1 WHEN 'comment' THEN 2 WHEN 'write' THEN 3 WHEN 'approve' THEN 4 WHEN 'file' THEN 5 END
$$;

-- Keeps scopes, role and modules telling the same story whichever of them a statement writes.
CREATE FUNCTION app.sync_member_scopes() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE k text; v text; top int := 0;
BEGIN
  IF NEW.scopes IS NULL
     OR (TG_OP = 'UPDATE' AND NEW.scopes IS NOT DISTINCT FROM OLD.scopes AND (NEW.role <> OLD.role OR NEW.modules <> OLD.modules)) THEN
    -- written the old way (a role and a module list): work the scopes out from them
    SELECT jsonb_object_agg(m, CASE NEW.role WHEN 'owner' THEN 'file' WHEN 'approver' THEN 'file' WHEN 'maker' THEN 'write' ELSE 'read' END)
      INTO NEW.scopes FROM unnest(NEW.modules) m;
  END IF;
  IF NEW.role = 'owner' THEN
    NEW.scopes := '{"*": "file"}'::jsonb;                    -- an owner has everything; that is what being the owner means
  END IF;
  IF NEW.scopes IS NULL OR jsonb_typeof(NEW.scopes) <> 'object' OR NEW.scopes = '{}'::jsonb THEN
    RAISE EXCEPTION 'A member needs at least one module' USING ERRCODE = '23514', CONSTRAINT = 'member_scopes_valid';
  END IF;
  FOR k, v IN SELECT key, value #>> '{}' FROM jsonb_each(NEW.scopes) LOOP
    IF app.scope_rank(v) IS NULL OR k !~ '^(\*|[a-z_]{1,40})$' THEN
      RAISE EXCEPTION 'Scope % = % is not a module and a level', k, v USING ERRCODE = '23514', CONSTRAINT = 'member_scopes_valid';
    END IF;
    top := greatest(top, app.scope_rank(v));
  END LOOP;
  IF NEW.role <> 'owner' THEN
    NEW.role := CASE WHEN top >= 4 THEN 'approver' WHEN top = 3 THEN 'maker' ELSE 'viewer' END::member_role;
  END IF;
  NEW.modules := CASE WHEN NEW.scopes ? '*' THEN ARRAY['*'] ELSE ARRAY(SELECT jsonb_object_keys(NEW.scopes) ORDER BY 1) END;
  RETURN NEW;
END $$;
CREATE TRIGGER memberships_sync_scopes BEFORE INSERT OR UPDATE OF scopes, role, modules ON memberships
  FOR EACH ROW EXECUTE FUNCTION app.sync_member_scopes();

-- Existing members: the trigger derives their scopes from the role and modules they have.
UPDATE memberships SET role = role;
ALTER TABLE memberships ALTER COLUMN scopes SET NOT NULL;

-- The two modules that are split out: whoever had the wider one keeps the narrower one at the same level.
UPDATE memberships SET scopes = scopes || jsonb_build_object('hr', scopes -> 'payroll') WHERE scopes ? 'payroll' AND NOT scopes ? 'hr' AND NOT scopes ? '*';
UPDATE memberships SET scopes = scopes || jsonb_build_object('inventory', scopes -> 'finance') WHERE scopes ? 'finance' AND NOT scopes ? 'inventory' AND NOT scopes ? '*';
UPDATE advisor_grants SET scopes = scopes || jsonb_build_object('hr', scopes -> 'payroll') WHERE scopes ? 'payroll' AND NOT scopes ? 'hr';
UPDATE advisor_grants SET scopes = scopes || jsonb_build_object('inventory', scopes -> 'finance') WHERE scopes ? 'finance' AND NOT scopes ? 'inventory';
UPDATE api_keys SET permissions = permissions || ARRAY(SELECT 'hr:' || split_part(p, ':', 2) FROM unnest(permissions) p WHERE p LIKE 'payroll:%'
                                                         AND NOT ('hr:' || split_part(p, ':', 2)) = ANY(permissions))
 WHERE EXISTS (SELECT 1 FROM unnest(permissions) p WHERE p LIKE 'payroll:%');
UPDATE api_keys SET permissions = permissions || ARRAY(SELECT 'inventory:' || split_part(p, ':', 2) FROM unnest(permissions) p WHERE p LIKE 'finance:%'
                                                         AND NOT ('inventory:' || split_part(p, ':', 2)) = ANY(permissions))
 WHERE EXISTS (SELECT 1 FROM unnest(permissions) p WHERE p LIKE 'finance:%');

-- Invite a person with a level per module. Like app.invite_member (part 22), it may create the user row, so it runs as its owner; the
-- caller must be an owner of the company, which the function checks itself.
CREATE FUNCTION app.invite_member_scoped(p_entity uuid, p_email text, p_full_name text, p_owner boolean, p_scopes jsonb, p_title text, p_preset text, p_home text)
RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, app AS $$
DECLARE v_user uuid; v_member uuid;
BEGIN
  IF NOT EXISTS (SELECT 1 FROM memberships WHERE entity_id = p_entity AND user_id = app.current_user_id() AND role = 'owner' AND revoked_at IS NULL) THEN
    RAISE EXCEPTION 'only an owner can invite people' USING ERRCODE = '42501';
  END IF;
  SELECT id INTO v_user FROM users WHERE email = p_email;
  IF v_user IS NULL THEN
    INSERT INTO users (email, full_name, auth_provider) VALUES (p_email, left(coalesce(nullif(trim(p_full_name), ''), split_part(p_email, '@', 1)), 120), 'otp')
    RETURNING id INTO v_user;
  END IF;
  -- a returning person (revoked earlier) is re-invited on the same row
  INSERT INTO memberships (entity_id, user_id, role, scopes, title, preset, home, invited_by)
  VALUES (p_entity, v_user, CASE WHEN p_owner THEN 'owner' ELSE 'viewer' END::member_role, p_scopes, p_title, p_preset, p_home, app.current_user_id())
  ON CONFLICT (entity_id, user_id) DO UPDATE
     SET role = EXCLUDED.role, scopes = EXCLUDED.scopes, title = EXCLUDED.title, preset = EXCLUDED.preset, home = EXCLUDED.home,
         invited_by = EXCLUDED.invited_by, revoked_at = NULL, accepted_at = NULL
   WHERE memberships.revoked_at IS NOT NULL
  RETURNING id INTO v_member;
  RETURN v_member;                                  -- NULL when the person is already a member
END $$;
GRANT EXECUTE ON FUNCTION app.invite_member_scoped(uuid, text, text, boolean, jsonb, text, text, text) TO biz_app;
