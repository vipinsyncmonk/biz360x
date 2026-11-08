-- =====================================================================
-- Part 20 · Creating a workspace (onboarding)
-- A person with no workspace has no membership, so row-level security would hide, and refuse, everything they are about to
-- create. This function does the whole creation as the owner and nothing else: the organization (or an existing one the caller
-- administers), the entity, its registrations, the owner membership and the first facts. The route validates the answers, takes
-- the Idempotency-Key, runs the engine and writes the audit entry in the same transaction.
-- =====================================================================
CREATE OR REPLACE FUNCTION app.create_workspace(
  p_org uuid, p_org_name text, p_entity jsonb, p_gstins jsonb, p_facts jsonb
) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, app AS $$
DECLARE
  v_user uuid := app.current_user_id();
  v_org uuid := p_org;
  v_entity uuid;
  g jsonb;
  f jsonb;
BEGIN
  IF v_user IS NULL THEN
    RAISE EXCEPTION 'not signed in' USING ERRCODE = '42501';
  END IF;
  IF v_org IS NULL THEN
    INSERT INTO organizations (name) VALUES (left(p_org_name, 200)) RETURNING id INTO v_org;
    INSERT INTO org_members (organization_id, user_id, role) VALUES (v_org, v_user, 'admin');
  ELSIF v_org NOT IN (SELECT app.my_org_ids(true)) THEN
    RAISE EXCEPTION 'organization not administered by the caller' USING ERRCODE = '42501';
  END IF;

  INSERT INTO entities (organization_id, legal_name, constitution, track, pan, cin, llpin, incorporated_on, registered_state,
                        registered_address, nic_code, udyam_number, udyam_class)
  VALUES (v_org, p_entity->>'legal_name', (p_entity->>'constitution')::entity_constitution, (p_entity->>'track')::entity_track,
          p_entity->>'pan', p_entity->>'cin', p_entity->>'llpin', (p_entity->>'incorporated_on')::date, p_entity->>'registered_state',
          p_entity->>'registered_address', p_entity->>'nic_code', p_entity->>'udyam_number', (p_entity->>'udyam_class')::udyam_class)
  RETURNING id INTO v_entity;

  INSERT INTO memberships (entity_id, user_id, role, modules, accepted_at) VALUES (v_entity, v_user, 'owner', '{*}', now());

  FOR g IN SELECT * FROM jsonb_array_elements(coalesce(p_gstins, '[]'::jsonb)) LOOP
    INSERT INTO entity_registrations (entity_id, kind, number, state_code, label, gst_filing_frequency)
    VALUES (v_entity, 'gstin', g->>'gstin', g->>'state_code', g->>'label', coalesce(g->>'filing_frequency', 'monthly'));
  END LOOP;

  FOR f IN SELECT * FROM jsonb_array_elements(coalesce(p_facts, '[]'::jsonb)) LOOP
    INSERT INTO entity_facts (entity_id, fact_key, value_num, value_text, value_bool, as_of, source, recorded_by)
    VALUES (v_entity, f->>'key', (f->>'num')::numeric, f->>'text', (f->>'bool')::boolean, current_date, f->>'source', v_user);
  END LOOP;
  RETURN v_entity;
END $$;
GRANT EXECUTE ON FUNCTION app.create_workspace(uuid, text, jsonb, jsonb, jsonb) TO biz_app;
