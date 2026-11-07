-- =====================================================================
-- Part 18 · Registry lookups (onboarding)
-- A person types a CIN, LLPIN, GSTIN or PAN before any workspace exists, so a lookup belongs to the user, not to an
-- entity. The results are public registry data and double as a 24-hour cache.
-- =====================================================================
CREATE TABLE registry_lookups (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id      uuid NOT NULL REFERENCES users(id),
  identifier   text NOT NULL,
  kind         text NOT NULL CHECK (kind IN ('cin', 'llpin', 'gstin', 'pan')),
  status       text NOT NULL DEFAULT 'running' CHECK (status IN ('running', 'found', 'not_found', 'failed')),
  result       jsonb,
  error        text,
  created_at   timestamptz NOT NULL DEFAULT now(),
  finished_at  timestamptz
);
CREATE INDEX registry_lookups_cache_ix ON registry_lookups (identifier, finished_at DESC) WHERE status IN ('found', 'not_found');

ALTER TABLE registry_lookups ENABLE ROW LEVEL SECURITY;
ALTER TABLE registry_lookups FORCE ROW LEVEL SECURITY;
CREATE POLICY own_lookups ON registry_lookups USING (user_id = app.current_user_id()) WITH CHECK (user_id = app.current_user_id());

GRANT SELECT, INSERT, UPDATE ON registry_lookups TO biz_app;
GRANT SELECT, INSERT, UPDATE ON registry_lookups TO biz_worker;

-- A job that is not tied to an entity (a registry lookup) must still be created and read by the person who asked for it.
-- jobs.entity_id may be NULL; the tenant policy hides such rows, so add creator policies.
CREATE POLICY own_jobs_read ON jobs FOR SELECT USING (entity_id IS NULL AND created_by = app.current_user_id());
CREATE POLICY own_jobs_insert ON jobs FOR INSERT WITH CHECK (entity_id IS NULL AND created_by = app.current_user_id());

-- Registry data is public, so one person's lookup answers the next person's. Row-level security hides other users' rows,
-- hence a SECURITY DEFINER function that returns only the finished status and result for an identifier.
CREATE OR REPLACE FUNCTION app.cached_lookup(p_identifier text) RETURNS TABLE (status text, result jsonb)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, app AS $$
  SELECT r.status, r.result FROM registry_lookups r
   WHERE r.identifier = p_identifier AND r.status IN ('found', 'not_found') AND r.finished_at > now() - interval '24 hours'
   ORDER BY r.finished_at DESC LIMIT 1
$$;
GRANT EXECUTE ON FUNCTION app.cached_lookup(text) TO biz_app;
