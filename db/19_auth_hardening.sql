-- =====================================================================
-- Part 19 · Authentication, API keys, webhooks, audit verification (M7)
-- =====================================================================

-- A service user is the identity behind an API key. It cannot sign in with a code or a password.
ALTER TABLE users DROP CONSTRAINT IF EXISTS users_auth_provider_check;
ALTER TABLE users ADD CONSTRAINT users_auth_provider_check CHECK (auth_provider IN ('password', 'google', 'microsoft', 'saml', 'otp', 'service'));
ALTER TABLE users ADD COLUMN totp_secret_enc bytea;      -- encrypted TOTP secret; required when mfa_enabled

-- One-time codes. Only a keyed hash of the code is stored. Written and read by the auth service (biz_worker); no policy is
-- granted to biz_app, so a request handler can never read them.
CREATE TABLE auth_otps (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  identifier  text NOT NULL,                       -- lower-cased email or E.164 mobile
  code_hash   char(64) NOT NULL,
  attempts    smallint NOT NULL DEFAULT 0,
  expires_at  timestamptz NOT NULL,
  consumed_at timestamptz,
  created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX auth_otps_identifier_ix ON auth_otps (identifier, created_at DESC);

-- Refresh tokens rotate on every use. Presenting a token that was already used revokes its whole family.
CREATE TABLE refresh_tokens (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  family_id   uuid NOT NULL,
  user_id     uuid NOT NULL REFERENCES users(id),
  token_hash  char(64) NOT NULL UNIQUE,
  expires_at  timestamptz NOT NULL,
  used_at     timestamptz,
  revoked_at  timestamptz,
  created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX refresh_tokens_family_ix ON refresh_tokens (family_id);
CREATE INDEX refresh_tokens_user_ix ON refresh_tokens (user_id);

ALTER TABLE auth_otps ENABLE ROW LEVEL SECURITY;
ALTER TABLE auth_otps FORCE ROW LEVEL SECURITY;
ALTER TABLE refresh_tokens ENABLE ROW LEVEL SECURITY;
ALTER TABLE refresh_tokens FORCE ROW LEVEL SECURITY;
GRANT SELECT, INSERT, UPDATE, DELETE ON auth_otps, refresh_tokens TO biz_worker;
GRANT UPDATE ON users TO biz_worker;

-- API keys: a key is a service user with a permission list; look it up by prefix before any identity exists.
ALTER TABLE api_keys ADD COLUMN created_by uuid REFERENCES users(id);
CREATE UNIQUE INDEX api_keys_service_user_ix ON api_keys (acts_as_user);
GRANT DELETE ON api_keys TO biz_worker;

-- Webhook delivery: one row per attempt.
ALTER TABLE webhook_deliveries ADD COLUMN attempted_at timestamptz;
ALTER TABLE webhook_deliveries ADD COLUMN error text;
CREATE INDEX webhook_deliveries_due_ix ON webhook_deliveries (next_retry_at) WHERE attempted_at IS NULL;
GRANT SELECT, INSERT, UPDATE ON webhook_deliveries TO biz_worker;

-- Raised by the audit verification job and read by operators; never visible to a tenant.
CREATE TABLE system_alerts (
  id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  kind        text NOT NULL,                       -- 'audit_chain_broken'
  detail      jsonb NOT NULL DEFAULT '{}',
  raised_at   timestamptz NOT NULL DEFAULT now(),
  resolved_at timestamptz
);
ALTER TABLE system_alerts ENABLE ROW LEVEL SECURITY;
ALTER TABLE system_alerts FORCE ROW LEVEL SECURITY;
GRANT SELECT, INSERT, UPDATE ON system_alerts TO biz_worker;

-- The chain is global, so verification must see every tenant's rows. Run it with the owner's rights; it returns only an id.
CREATE OR REPLACE FUNCTION app.audit_verify() RETURNS bigint
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = public, app AS $$
DECLARE r audit_log; prev char(64) := NULL;
BEGIN
  FOR r IN SELECT * FROM audit_log ORDER BY id LOOP
    IF r.prev_hash IS DISTINCT FROM prev
       OR r.hash <> encode(digest(app.audit_payload(r, prev), 'sha256'), 'hex') THEN
      RETURN r.id;
    END IF;
    prev := r.hash;
  END LOOP;
  RETURN NULL;
END $$;
GRANT EXECUTE ON FUNCTION app.audit_verify() TO biz_app, biz_worker;

-- Creating a key creates its service user and membership, which a normal request cannot do under row-level security (users
-- may only be written by themselves). These two functions do exactly that and nothing else; both check that the caller can see
-- the entity, and the route checks settings:write before calling them.
CREATE OR REPLACE FUNCTION app.create_api_key(p_entity uuid, p_name text, p_prefix text, p_hash text, p_permissions text[], p_expires timestamptz)
RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, app AS $$
DECLARE v_user uuid; v_key uuid;
BEGIN
  IF p_entity NOT IN (SELECT app.visible_entity_ids()) THEN
    RAISE EXCEPTION 'entity not visible' USING ERRCODE = '42501';
  END IF;
  INSERT INTO users (email, full_name, auth_provider)
  VALUES ('svc-' || p_prefix || '@service.biz360x.invalid', left(p_name, 120), 'service') RETURNING id INTO v_user;
  INSERT INTO memberships (entity_id, user_id, role, modules, invited_by, accepted_at)
  VALUES (p_entity, v_user, 'maker',
          ARRAY(SELECT DISTINCT split_part(x, ':', 1) FROM unnest(p_permissions) x), app.current_user_id(), now());
  INSERT INTO api_keys (entity_id, name, prefix, secret_hash, permissions, acts_as_user, expires_at, created_by)
  VALUES (p_entity, p_name, p_prefix, p_hash, p_permissions, v_user, p_expires, app.current_user_id()) RETURNING id INTO v_key;
  RETURN v_key;
END $$;

CREATE OR REPLACE FUNCTION app.revoke_api_key(p_key uuid) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, app AS $$
DECLARE k api_keys;
BEGIN
  SELECT * INTO k FROM api_keys WHERE id = p_key AND entity_id IN (SELECT app.visible_entity_ids());
  IF NOT FOUND THEN RETURN false; END IF;
  UPDATE api_keys SET revoked_at = coalesce(revoked_at, now()) WHERE id = p_key;
  UPDATE memberships SET revoked_at = coalesce(revoked_at, now()) WHERE user_id = k.acts_as_user AND entity_id = k.entity_id;
  RETURN true;
END $$;
GRANT EXECUTE ON FUNCTION app.create_api_key(uuid, text, text, text, text[], timestamptz) TO biz_app;
GRANT EXECUTE ON FUNCTION app.revoke_api_key(uuid) TO biz_app;
