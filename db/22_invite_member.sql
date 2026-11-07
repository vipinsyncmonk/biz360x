-- =====================================================================
-- Part 22 · Inviting a person
-- Inviting someone creates their user row when they have not signed up yet, which a normal request cannot do under row-level
-- security (a user row may only be written by its owner). This function does that and one thing more: it adds the membership.
-- It checks that the caller can see the entity; the route checks settings:write and the role rules before calling it.
-- The person signs in with a one-time code to the invited address, so no password is created here.
-- =====================================================================
CREATE OR REPLACE FUNCTION app.invite_member(p_entity uuid, p_email text, p_full_name text, p_role member_role, p_modules text[])
RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, app AS $$
DECLARE v_user uuid; v_member uuid;
BEGIN
  IF p_entity NOT IN (SELECT app.visible_entity_ids()) THEN
    RAISE EXCEPTION 'entity not visible' USING ERRCODE = '42501';
  END IF;
  SELECT id INTO v_user FROM users WHERE email = p_email;
  IF v_user IS NULL THEN
    INSERT INTO users (email, full_name, auth_provider) VALUES (p_email, left(coalesce(nullif(trim(p_full_name), ''), split_part(p_email, '@', 1)), 120), 'otp')
    RETURNING id INTO v_user;
  END IF;
  -- a returning person (revoked earlier) is re-invited on the same row
  INSERT INTO memberships (entity_id, user_id, role, modules, invited_by)
  VALUES (p_entity, v_user, p_role, p_modules, app.current_user_id())
  ON CONFLICT (entity_id, user_id) DO UPDATE
     SET role = EXCLUDED.role, modules = EXCLUDED.modules, invited_by = EXCLUDED.invited_by, revoked_at = NULL, accepted_at = NULL
   WHERE memberships.revoked_at IS NOT NULL
  RETURNING id INTO v_member;
  RETURN v_member;                                  -- NULL when the person is already a member
END $$;
GRANT EXECUTE ON FUNCTION app.invite_member(uuid, text, text, member_role, text[]) TO biz_app;
