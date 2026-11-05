-- =====================================================================
-- Part 11 · Tables the API layer needs: jobs, idempotency keys,
--           API keys, webhook endpoints and deliveries, event outbox
-- =====================================================================

-- Long-running operations (portal filings, IRN, OCR, syncs)
CREATE TABLE jobs (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  entity_id      uuid REFERENCES entities(id),
  kind           text NOT NULL,                 -- 'einvoice.generate_irn','gst.file_gstr1','portal.renew_session'
  status         text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','running','waiting_for_otp','succeeded','failed','cancelled')),
  subject        text,
  subject_id     uuid,
  payload        jsonb NOT NULL DEFAULT '{}',
  result         jsonb,
  error          jsonb,                         -- RFC 9457 problem
  progress       numeric(4,3) NOT NULL DEFAULT 0,
  attempts       smallint NOT NULL DEFAULT 0,
  run_after      timestamptz NOT NULL DEFAULT now(),
  locked_by      text,                          -- worker id while running
  locked_until   timestamptz,
  created_by     uuid REFERENCES users(id),
  created_at     timestamptz NOT NULL DEFAULT now(),
  finished_at    timestamptz
);
CREATE INDEX jobs_queue_ix ON jobs (run_after) WHERE status = 'queued';
CREATE INDEX jobs_subject_ix ON jobs (subject, subject_id);

-- Worker claim: SELECT … FOR UPDATE SKIP LOCKED keeps workers from colliding
CREATE OR REPLACE FUNCTION app.claim_job(p_worker text, p_kinds text[]) RETURNS jobs
LANGUAGE plpgsql AS $$
DECLARE j jobs;
BEGIN
  SELECT * INTO j FROM jobs
   WHERE status = 'queued' AND run_after <= now() AND kind = ANY (p_kinds)
   ORDER BY run_after
   FOR UPDATE SKIP LOCKED LIMIT 1;
  IF NOT FOUND THEN RETURN NULL; END IF;
  UPDATE jobs SET status = 'running', locked_by = p_worker, locked_until = now() + interval '5 minutes',
                  attempts = attempts + 1
   WHERE id = j.id RETURNING * INTO j;
  RETURN j;
END $$;

-- Idempotency: the first response to a key is replayed for 24 h
CREATE TABLE api_idempotency_keys (
  user_id        uuid NOT NULL REFERENCES users(id),
  idem_key       text NOT NULL,
  method         text NOT NULL,
  path           text NOT NULL,
  request_hash   char(64) NOT NULL,
  status_code    smallint,
  response_body  jsonb,
  created_at     timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, idem_key)
);
CREATE INDEX api_idem_expiry_ix ON api_idempotency_keys (created_at);

-- Server-to-server keys, scoped to one entity
CREATE TABLE api_keys (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  entity_id     uuid NOT NULL REFERENCES entities(id),
  name          text NOT NULL,
  prefix        char(8) NOT NULL UNIQUE,        -- shown in the UI: 'bzx_live_ab12cd34…'
  secret_hash   char(64) NOT NULL,              -- sha256 of the full key; the key itself is never stored
  permissions   text[] NOT NULL,                -- '{sales:read,sales:write}'
  acts_as_user  uuid NOT NULL REFERENCES users(id),  -- RLS identity (a service user)
  last_used_at  timestamptz,
  expires_at    timestamptz,
  revoked_at    timestamptz,
  created_at    timestamptz NOT NULL DEFAULT now()
);

-- Webhooks
CREATE TABLE webhook_endpoints (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  entity_id     uuid NOT NULL REFERENCES entities(id),
  url           text NOT NULL CHECK (url ~ '^https://'),
  events        text[] NOT NULL,
  secret_enc    bytea NOT NULL,                 -- encrypted HMAC secret
  is_active     boolean NOT NULL DEFAULT true,
  created_at    timestamptz NOT NULL DEFAULT now()
);

-- Transactional outbox: written in the same transaction as the change,
-- fanned out to SSE clients and webhooks by a relay worker
CREATE TABLE event_outbox (
  id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  entity_id     uuid NOT NULL REFERENCES entities(id),
  type          text NOT NULL,                  -- 'sales_document.paid'
  subject       text NOT NULL,
  subject_id    uuid,
  data          jsonb NOT NULL DEFAULT '{}',
  occurred_at   timestamptz NOT NULL DEFAULT now(),
  relayed_at    timestamptz
);
CREATE INDEX event_outbox_pending_ix ON event_outbox (id) WHERE relayed_at IS NULL;

CREATE TABLE webhook_deliveries (
  id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  endpoint_id    uuid NOT NULL REFERENCES webhook_endpoints(id) ON DELETE CASCADE,
  event_id       bigint NOT NULL REFERENCES event_outbox(id),
  attempt        smallint NOT NULL DEFAULT 1,
  status_code    smallint,
  next_retry_at  timestamptz,
  delivered_at   timestamptz,
  UNIQUE (endpoint_id, event_id, attempt)
);

-- RLS for the new entity-scoped tables
DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['jobs','api_keys','webhook_endpoints','event_outbox'] LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
  END LOOP;
END $$;
CREATE POLICY tenant_isolation ON jobs
  USING (entity_id IN (SELECT app.visible_entity_ids()) OR (entity_id IS NULL AND created_by = app.current_user_id()))
  WITH CHECK (entity_id IN (SELECT app.visible_entity_ids()) OR (entity_id IS NULL AND created_by = app.current_user_id()));
CREATE POLICY tenant_isolation ON api_keys          USING (entity_id IN (SELECT app.visible_entity_ids())) WITH CHECK (entity_id IN (SELECT app.visible_entity_ids()));
CREATE POLICY tenant_isolation ON webhook_endpoints USING (entity_id IN (SELECT app.visible_entity_ids())) WITH CHECK (entity_id IN (SELECT app.visible_entity_ids()));
CREATE POLICY tenant_isolation ON event_outbox      USING (entity_id IN (SELECT app.visible_entity_ids())) WITH CHECK (entity_id IN (SELECT app.visible_entity_ids()));
ALTER TABLE api_idempotency_keys ENABLE ROW LEVEL SECURITY;
ALTER TABLE api_idempotency_keys FORCE ROW LEVEL SECURITY;
CREATE POLICY own_keys ON api_idempotency_keys USING (user_id = app.current_user_id()) WITH CHECK (user_id = app.current_user_id());
ALTER TABLE webhook_deliveries ENABLE ROW LEVEL SECURITY;
ALTER TABLE webhook_deliveries FORCE ROW LEVEL SECURITY;
CREATE POLICY via_parent ON webhook_deliveries USING (EXISTS (SELECT 1 FROM webhook_endpoints w WHERE w.id = webhook_deliveries.endpoint_id));

GRANT SELECT, INSERT, UPDATE, DELETE ON jobs, api_idempotency_keys, api_keys, webhook_endpoints, event_outbox, webhook_deliveries TO biz_app;
GRANT EXECUTE ON FUNCTION app.claim_job(text, text[]) TO biz_app;

-- Workers run as their own role and see every queued job (no RLS)
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'biz_worker') THEN
    CREATE ROLE biz_worker NOLOGIN BYPASSRLS;
  END IF;
END $$;
GRANT USAGE ON SCHEMA public, app TO biz_worker;
GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA public TO biz_worker;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO biz_worker;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA app TO biz_worker;
REVOKE UPDATE ON audit_log FROM biz_worker;
