-- =====================================================================
-- Part 21 · Colleagues inside an organization
-- Row-level security shows a person only themselves and the members of entities they can see, so a firm partner could not read
-- the names of their own staff (who have no entity membership). This returns the name and role of the people in an organization
-- the caller belongs to, and nothing else: no email, no other organization, and nothing at all for someone who is not a member.
-- =====================================================================
CREATE OR REPLACE FUNCTION app.org_people(p_org uuid) RETURNS TABLE (user_id uuid, full_name text, role text)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, app AS $$
  SELECT u.id, u.full_name, om.role
    FROM org_members om JOIN users u ON u.id = om.user_id
   WHERE om.organization_id = p_org AND p_org IN (SELECT app.my_org_ids())
   ORDER BY u.full_name
$$;
GRANT EXECUTE ON FUNCTION app.org_people(uuid) TO biz_app;

-- A person's notification choices are theirs alone. The generic tenant policy on every table with an entity_id would also show
-- them to colleagues who can see the entity, so add a restrictive policy: both must hold.
CREATE POLICY own_choices_only ON notification_preferences AS RESTRICTIVE
  USING (user_id = app.current_user_id()) WITH CHECK (user_id = app.current_user_id());
