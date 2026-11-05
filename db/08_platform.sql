-- =====================================================================
-- Part 08 · Platform: document vault, data rooms, portal connections,
--           copilot, notifications, append-only audit log, subscriptions
-- =====================================================================

-- ---------------------------------------------------------------------
-- Document vault
-- ---------------------------------------------------------------------
CREATE TYPE doc_source AS ENUM ('gstn','mca','income_tax','traces','epfo','esic','irp','eway','udyam','bank','biz360x','upload','email');

CREATE TABLE documents (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  entity_id       uuid NOT NULL REFERENCES entities(id),
  folder          text NOT NULL CHECK (folder IN ('incorporation','tax','mca','payroll','contracts','board','bank_finance','sales','purchases','licences','other')),
  title           text NOT NULL,
  file_name       text NOT NULL,
  mime_type       text NOT NULL,
  size_bytes      bigint NOT NULL CHECK (size_bytes >= 0),
  sha256          char(64) NOT NULL,          -- integrity + dedupe
  storage_key     text NOT NULL,              -- object-store key (encrypted at rest)
  source          doc_source NOT NULL,
  period_label    text,                       -- 'Sep 2026', 'FY 2025-26'
  valid_until     date,                       -- certificates and licences
  extracted_text  text,                       -- OCR / text layer
  search          tsvector GENERATED ALWAYS AS (
                    setweight(to_tsvector('simple', coalesce(title,'')), 'A') ||
                    setweight(to_tsvector('simple', coalesce(extracted_text,'')), 'C')) STORED,
  supersedes_id   uuid REFERENCES documents(id),
  uploaded_by     uuid REFERENCES users(id),
  created_at      timestamptz NOT NULL DEFAULT now(),
  UNIQUE (entity_id, sha256)
);
CREATE INDEX documents_search_ix ON documents USING gin (search);
CREATE INDEX documents_folder_ix ON documents (entity_id, folder, created_at DESC);
CREATE INDEX documents_expiry_ix ON documents (entity_id, valid_until) WHERE valid_until IS NOT NULL;

-- Any record can point at documents (an obligation's acknowledgement, a contract's PDF…)
CREATE TABLE document_links (
  document_id   uuid NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  subject       text NOT NULL,     -- table name of the linked record
  subject_id    uuid NOT NULL,
  role          text NOT NULL DEFAULT 'attachment' CHECK (role IN ('attachment','acknowledgement','signed_copy','draft','evidence','certificate')),
  PRIMARY KEY (document_id, subject, subject_id, role)
);
CREATE INDEX document_links_subject_ix ON document_links (subject, subject_id);

-- Late-bound foreign keys from earlier parts
ALTER TABLE obligations          ADD CONSTRAINT obligations_draft_doc_fk     FOREIGN KEY (draft_document_id)     REFERENCES documents(id);
ALTER TABLE signature_requests   ADD CONSTRAINT signature_requests_doc_fk    FOREIGN KEY (document_id)           REFERENCES documents(id);
ALTER TABLE tds_receivables      ADD CONSTRAINT tds_receivables_16a_fk       FOREIGN KEY (form16a_document_id)   REFERENCES documents(id);
ALTER TABLE purchase_bills       ADD CONSTRAINT purchase_bills_doc_fk        FOREIGN KEY (document_id)           REFERENCES documents(id);
ALTER TABLE payroll_runs         ADD CONSTRAINT payroll_runs_bankfile_fk     FOREIGN KEY (bank_file_document_id) REFERENCES documents(id);
ALTER TABLE contract_templates   ADD CONSTRAINT contract_templates_body_fk   FOREIGN KEY (body_document_id)      REFERENCES documents(id);
ALTER TABLE contracts            ADD CONSTRAINT contracts_signed_doc_fk      FOREIGN KEY (signed_document_id)    REFERENCES documents(id);
ALTER TABLE meetings             ADD CONSTRAINT meetings_minutes_fk          FOREIGN KEY (minutes_document_id)   REFERENCES documents(id);

CREATE TABLE document_requests (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  entity_id         uuid NOT NULL REFERENCES entities(id),
  advisor_grant_id  uuid REFERENCES advisor_grants(id),
  requested_by_name text NOT NULL,                     -- 'Mehta & Co'
  title             text NOT NULL,                     -- 'Bank confirmations as of 31 Mar 2026'
  due_on            date,
  status            text NOT NULL DEFAULT 'open' CHECK (status IN ('open','partially_fulfilled','fulfilled','cancelled')),
  created_at        timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE document_request_items (
  request_id   uuid NOT NULL REFERENCES document_requests(id) ON DELETE CASCADE,
  document_id  uuid NOT NULL REFERENCES documents(id),
  shared_at    timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (request_id, document_id)
);

CREATE TABLE data_rooms (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  entity_id    uuid NOT NULL REFERENCES entities(id),
  name         text NOT NULL,                 -- 'Seed extension diligence'
  purpose      text CHECK (purpose IN ('fundraise','lender','audit','acquisition','other')),
  expires_at   timestamptz,
  watermark    boolean NOT NULL DEFAULT true,
  allow_download boolean NOT NULL DEFAULT false
);

CREATE TABLE data_room_items (
  room_id      uuid NOT NULL REFERENCES data_rooms(id) ON DELETE CASCADE,
  document_id  uuid NOT NULL REFERENCES documents(id),
  section      text,
  PRIMARY KEY (room_id, document_id)
);

CREATE TABLE data_room_access (
  id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  room_id       uuid NOT NULL REFERENCES data_rooms(id) ON DELETE CASCADE,
  email         citext NOT NULL,
  granted_at    timestamptz NOT NULL DEFAULT now(),
  revoked_at    timestamptz,
  last_viewed_at timestamptz,
  UNIQUE (room_id, email)
);

-- ---------------------------------------------------------------------
-- Government & financial portal connections
-- ---------------------------------------------------------------------
CREATE TYPE portal_kind AS ENUM ('gstn','irp','eway','mca','income_tax','traces','epfo','esic','professional_tax','udyam','account_aggregator','bank_api','treds','digilocker');

CREATE TABLE portal_connections (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  entity_id           uuid NOT NULL REFERENCES entities(id),
  portal              portal_kind NOT NULL,
  registration_id     uuid REFERENCES entity_registrations(id),
  via                 text,                         -- 'GSP', 'ASP', 'direct'
  owner_user_id       uuid REFERENCES users(id),    -- renews OTP sessions
  credential_ref      text,                         -- secret-manager path; secrets never stored here
  session_expires_at  timestamptz,
  status              text NOT NULL DEFAULT 'connected' CHECK (status IN ('not_connected','connected','expiring','expired','error','revoked')),
  last_sync_at        timestamptz,
  last_error          text,
  UNIQUE (entity_id, portal, registration_id)
);

CREATE TABLE aa_consents (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  entity_id         uuid NOT NULL REFERENCES entities(id),
  consent_handle    text NOT NULL UNIQUE,
  fi_types          text[] NOT NULL DEFAULT '{DEPOSIT}',
  bank_account_ids  uuid[] NOT NULL,
  status            text NOT NULL CHECK (status IN ('pending','active','paused','revoked','expired')),
  valid_until       timestamptz NOT NULL,
  approved_by       uuid REFERENCES users(id)
);

CREATE TABLE sync_jobs (
  id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  connection_id  uuid NOT NULL REFERENCES portal_connections(id),
  job            text NOT NULL,                  -- 'fetch_2a','file_gstr1','pull_mca_master','fetch_26as'
  started_at     timestamptz NOT NULL DEFAULT now(),
  finished_at    timestamptz,
  status         text NOT NULL DEFAULT 'running' CHECK (status IN ('queued','running','succeeded','failed')),
  records        int,
  error          text
);
CREATE INDEX sync_jobs_conn_ix ON sync_jobs (connection_id, started_at DESC);

-- ---------------------------------------------------------------------
-- Copilot: threads, messages, citations, proposed actions
-- ---------------------------------------------------------------------
CREATE TABLE copilot_threads (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  entity_id   uuid NOT NULL REFERENCES entities(id),
  user_id     uuid NOT NULL REFERENCES users(id),
  title       text,
  module      text,                               -- 'gst','vendors','legal'…
  created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE copilot_messages (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  thread_id    uuid NOT NULL REFERENCES copilot_threads(id) ON DELETE CASCADE,
  role         text NOT NULL CHECK (role IN ('user','assistant','tool')),
  content      text NOT NULL,
  model        text,
  input_tokens  int,
  output_tokens int,
  created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX copilot_messages_thread_ix ON copilot_messages (thread_id, created_at);

-- Every factual claim in an answer points at the record it came from
CREATE TABLE copilot_citations (
  message_id   uuid NOT NULL REFERENCES copilot_messages(id) ON DELETE CASCADE,
  ordinal      smallint NOT NULL,                 -- [1], [2]…
  subject      text NOT NULL,                     -- 'sales_documents','gst_inward_docs','compliance_rules'
  subject_id   uuid,
  label        text NOT NULL,                     -- 'GSTR-2A · live view'
  PRIMARY KEY (message_id, ordinal)
);

-- Copilot drafts, people approve: actions never execute without an approval
CREATE TABLE copilot_actions (
  id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  message_id           uuid NOT NULL REFERENCES copilot_messages(id),
  entity_id            uuid NOT NULL REFERENCES entities(id),
  kind                 text NOT NULL,              -- 'send_supplier_reminders','schedule_payments','draft_notice_reply'
  payload              jsonb NOT NULL,
  status               text NOT NULL DEFAULT 'proposed' CHECK (status IN ('proposed','approved','executed','rejected','failed')),
  approval_request_id  uuid REFERENCES approval_requests(id),
  executed_at          timestamptz,
  CHECK (status NOT IN ('approved','executed') OR approval_request_id IS NOT NULL)
);

-- ---------------------------------------------------------------------
-- Notifications
-- ---------------------------------------------------------------------
CREATE TABLE notification_preferences (
  user_id     uuid NOT NULL REFERENCES users(id),
  entity_id   uuid NOT NULL REFERENCES entities(id),
  event_type  text NOT NULL,     -- 'due_in_7_days','due_tomorrow','approval_requested','notice_received','threshold_crossed','copilot_suggestion'
  channel     text NOT NULL CHECK (channel IN ('email','whatsapp','push','sms')),
  enabled     boolean NOT NULL DEFAULT true,
  PRIMARY KEY (user_id, entity_id, event_type, channel)
);

CREATE TABLE notifications (
  id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  user_id      uuid NOT NULL REFERENCES users(id),
  entity_id    uuid REFERENCES entities(id),
  event_type   text NOT NULL,
  channel      text NOT NULL,
  subject      text,
  subject_id   uuid,
  payload      jsonb NOT NULL,
  status       text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','sent','delivered','read','failed','suppressed')),
  created_at   timestamptz NOT NULL DEFAULT now(),
  sent_at      timestamptz
);
CREATE INDEX notifications_queue_ix ON notifications (status, created_at) WHERE status = 'queued';

-- ---------------------------------------------------------------------
-- Append-only, hash-chained audit log (the "your auditor trusts it" table)
-- ---------------------------------------------------------------------
CREATE TABLE audit_log (
  id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  entity_id     uuid REFERENCES entities(id),
  at            timestamptz NOT NULL DEFAULT clock_timestamp(),
  actor_kind    text NOT NULL CHECK (actor_kind IN ('user','engine','copilot','integration','advisor','system')),
  actor_user_id uuid REFERENCES users(id),
  action        text NOT NULL,          -- 'obligation.filed','sales_document.issued','approval.decided'
  subject       text NOT NULL,
  subject_id    uuid,
  before        jsonb,
  after         jsonb,
  ip            inet,
  prev_hash     char(64),
  hash          char(64) NOT NULL
);
CREATE INDEX audit_log_subject_ix ON audit_log (subject, subject_id);
CREATE INDEX audit_log_entity_ix  ON audit_log (entity_id, at DESC);

-- Canonical text that is hashed for one row (timezone-independent)
CREATE OR REPLACE FUNCTION app.audit_payload(r audit_log, prev text) RETURNS text
LANGUAGE sql STABLE AS $$
  SELECT concat_ws('|', coalesce(prev, ''),
                   to_char(r.at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US'),
                   r.actor_kind, coalesce(r.actor_user_id::text, ''), r.action, r.subject,
                   coalesce(r.subject_id::text, ''), coalesce(r.before::text, ''), coalesce(r.after::text, ''))
$$;

CREATE OR REPLACE FUNCTION app.audit_chain() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE last char(64);
BEGIN
  PERFORM pg_advisory_xact_lock(hashtext('audit_log_chain'));   -- serialise writers
  SELECT hash INTO last FROM audit_log ORDER BY id DESC LIMIT 1;
  NEW.prev_hash := last;
  NEW.hash := encode(digest(app.audit_payload(NEW, last), 'sha256'), 'hex');
  RETURN NEW;
END $$;
CREATE TRIGGER audit_log_chain BEFORE INSERT ON audit_log FOR EACH ROW EXECUTE FUNCTION app.audit_chain();

CREATE OR REPLACE FUNCTION app.audit_immutable() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'audit_log is append-only';
END $$;
CREATE TRIGGER audit_log_no_update BEFORE UPDATE OR DELETE ON audit_log FOR EACH ROW EXECUTE FUNCTION app.audit_immutable();
CREATE TRIGGER audit_log_no_truncate BEFORE TRUNCATE ON audit_log FOR EACH STATEMENT EXECUTE FUNCTION app.audit_immutable();

-- Verify the chain: returns the first broken row id, or NULL if intact
CREATE OR REPLACE FUNCTION app.audit_verify() RETURNS bigint
LANGUAGE plpgsql STABLE AS $$
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

-- ---------------------------------------------------------------------
-- Biz360X's own subscription billing (per entity, not per seat)
-- ---------------------------------------------------------------------
CREATE TABLE subscriptions (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id   uuid NOT NULL REFERENCES organizations(id),
  plan              plan_code NOT NULL,
  entities_included int NOT NULL CHECK (entities_included > 0),
  price_per_entity  inr,
  billing_cycle     text NOT NULL DEFAULT 'monthly' CHECK (billing_cycle IN ('monthly','annual')),
  current_period    daterange NOT NULL,
  status            text NOT NULL DEFAULT 'active' CHECK (status IN ('trialing','active','past_due','cancelled')),
  mandate_ref       text                                   -- UPI AutoPay / e-mandate reference
);
