-- =====================================================================
-- Biz360X — PostgreSQL 16 schema
-- Part 01 · Foundation: conventions, reference data, tenancy, identity
-- =====================================================================
-- Conventions
--   * Every business row belongs to exactly one legal ENTITY (entity_id).
--     Entities belong to one ORGANIZATION (the paying tenant: a business
--     group or a CA/CS firm). Row-level security filters on entity_id.
--   * Money: numeric(18,2) in INR. Quantities: numeric(18,3). Rates: numeric(7,4).
--   * Dates that the law cares about (invoice date, due date) are DATE;
--     moments (filed_at, approved_at) are TIMESTAMPTZ.
--   * Indian fiscal year is stored as text '2026-27' (fy) where needed.
--   * Nothing statutory is ever hard-deleted: rows carry status and the
--     audit log (part 08) is append-only and hash-chained.
-- =====================================================================

CREATE EXTENSION IF NOT EXISTS pgcrypto;   -- gen_random_uuid(), digest()
CREATE EXTENSION IF NOT EXISTS citext;     -- case-insensitive email
CREATE EXTENSION IF NOT EXISTS btree_gist; -- exclusion constraints on ranges

CREATE SCHEMA IF NOT EXISTS app;           -- helper functions, session context

-- ---------------------------------------------------------------------
-- Helpers
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION app.touch_updated_at() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  NEW.updated_at := now();
  RETURN NEW;
END $$;

-- Indian fiscal year label for a date: 2026-10-29 -> '2026-27'
CREATE OR REPLACE FUNCTION app.fy_of(d date) RETURNS text
LANGUAGE sql IMMUTABLE AS $$
  SELECT CASE WHEN extract(month FROM d) >= 4
              THEN extract(year FROM d)::int || '-' || lpad(((extract(year FROM d)::int + 1) % 100)::text, 2, '0')
              ELSE (extract(year FROM d)::int - 1) || '-' || lpad((extract(year FROM d)::int % 100)::text, 2, '0')
         END
$$;

-- Format validators (structure only; checksum validation happens in the app)
CREATE DOMAIN pan_no   AS text CHECK (VALUE ~ '^[A-Z]{5}[0-9]{4}[A-Z]$');
CREATE DOMAIN gstin_no AS text CHECK (VALUE ~ '^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$');
CREATE DOMAIN tan_no   AS text CHECK (VALUE ~ '^[A-Z]{4}[0-9]{5}[A-Z]$');
CREATE DOMAIN cin_no   AS text CHECK (VALUE ~ '^[LU][0-9]{5}[A-Z]{2}[0-9]{4}[A-Z]{3}[0-9]{6}$');
CREATE DOMAIN llpin_no AS text CHECK (VALUE ~ '^[A-Z]{3}-[0-9]{4}$');
CREATE DOMAIN hsn_sac  AS text CHECK (VALUE ~ '^[0-9]{4,8}$');
CREATE DOMAIN inr      AS numeric(18,2);
CREATE DOMAIN qty      AS numeric(18,3);
CREATE DOMAIN fy_label AS text CHECK (VALUE ~ '^[0-9]{4}-[0-9]{2}$');

-- ---------------------------------------------------------------------
-- Reference data (global, not tenant-scoped)
-- ---------------------------------------------------------------------
CREATE TABLE ref_states (
  gst_code     char(2) PRIMARY KEY,            -- '29' Karnataka
  name         text NOT NULL UNIQUE,
  iso_code     text,                           -- 'IN-KA'
  is_ut        boolean NOT NULL DEFAULT false
);

CREATE TABLE ref_authorities (
  code         text PRIMARY KEY,               -- 'MCA','GSTN','CBDT','EPFO','ESIC','PT-KA','RBI','DPIIT','MSME'
  name         text NOT NULL,
  level        text NOT NULL CHECK (level IN ('central','state','local','regulator')),
  state_code   char(2) REFERENCES ref_states(gst_code),
  portal_url   text
);

CREATE TABLE ref_hsn_sac (
  code         hsn_sac PRIMARY KEY,
  description  text NOT NULL,
  kind         text NOT NULL CHECK (kind IN ('goods','service')),
  default_gst_rate numeric(5,2)
);

-- ---------------------------------------------------------------------
-- Tenancy: organizations (the subscriber)
-- ---------------------------------------------------------------------
CREATE TYPE org_kind     AS ENUM ('business_group', 'ca_firm', 'cs_firm', 'law_firm');
CREATE TYPE plan_code    AS ENUM ('launch', 'grow', 'group');

CREATE TABLE organizations (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name          text NOT NULL,
  kind          org_kind NOT NULL DEFAULT 'business_group',
  plan          plan_code NOT NULL DEFAULT 'launch',
  billing_gstin gstin_no,
  data_region   text NOT NULL DEFAULT 'ap-south-1',   -- keep data in India
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------
-- Legal entities
-- ---------------------------------------------------------------------
CREATE TYPE entity_constitution AS ENUM (
  'private_limited', 'public_limited', 'opc', 'section8',
  'llp', 'partnership', 'proprietorship', 'trust', 'society', 'huf');
CREATE TYPE entity_track AS ENUM ('business', 'startup', 'msme');
CREATE TYPE udyam_class  AS ENUM ('micro', 'small', 'medium');

CREATE TABLE entities (
  id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id    uuid NOT NULL REFERENCES organizations(id),
  legal_name         text NOT NULL,
  trade_name         text,
  constitution       entity_constitution NOT NULL,
  track              entity_track NOT NULL DEFAULT 'business',
  pan                pan_no,
  cin                cin_no,
  llpin              llpin_no,
  incorporated_on    date,
  registered_state   char(2) REFERENCES ref_states(gst_code),
  registered_address text,
  nic_code           text,                          -- industry classification
  fy_start_month     smallint NOT NULL DEFAULT 4 CHECK (fy_start_month BETWEEN 1 AND 12),
  udyam_number       text CHECK (udyam_number ~ '^UDYAM-[A-Z]{2}-[0-9]{2}-[0-9]{7}$'),
  udyam_class        udyam_class,
  dpiit_number       text,                          -- startup recognition
  is_active          boolean NOT NULL DEFAULT true,
  created_at         timestamptz NOT NULL DEFAULT now(),
  updated_at         timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT company_needs_cin CHECK (
    constitution NOT IN ('private_limited','public_limited','opc','section8') OR cin IS NOT NULL OR incorporated_on IS NULL),
  CONSTRAINT llp_needs_llpin CHECK (constitution <> 'llp' OR llpin IS NOT NULL OR incorporated_on IS NULL)
);
CREATE UNIQUE INDEX entities_cin_uq   ON entities (cin)   WHERE cin IS NOT NULL;
CREATE UNIQUE INDEX entities_llpin_uq ON entities (llpin) WHERE llpin IS NOT NULL;
CREATE INDEX entities_org_ix ON entities (organization_id);

-- Registrations an entity holds: GSTIN per state, TAN, EPF, ESIC, PT, IEC, licences
CREATE TYPE registration_kind AS ENUM (
  'gstin', 'tan', 'epf', 'esic', 'professional_tax', 'iec',
  'shops_establishment', 'factory_licence', 'fssai', 'trade_licence', 'lut', 'other');

CREATE TABLE entity_registrations (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  entity_id     uuid NOT NULL REFERENCES entities(id),
  kind          registration_kind NOT NULL,
  number        text NOT NULL,
  state_code    char(2) REFERENCES ref_states(gst_code),
  label         text,                         -- 'Karnataka · principal place'
  gst_filing_frequency text CHECK (gst_filing_frequency IN ('monthly','qrmp','composition')),
  valid_from    date,
  valid_until   date,                         -- licences that expire
  status        text NOT NULL DEFAULT 'active' CHECK (status IN ('active','suspended','cancelled','applied')),
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT gstin_format CHECK (kind <> 'gstin' OR number ~ '^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$'),
  CONSTRAINT gstin_state  CHECK (kind <> 'gstin' OR state_code = substr(number, 1, 2)),
  UNIQUE (kind, number)
);
CREATE INDEX entity_registrations_entity_ix ON entity_registrations (entity_id, kind);

-- ---------------------------------------------------------------------
-- People & identity
-- ---------------------------------------------------------------------
CREATE TABLE users (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  email          citext NOT NULL UNIQUE,
  full_name      text NOT NULL,
  mobile_e164    text CHECK (mobile_e164 ~ '^\+[1-9][0-9]{7,14}$'),
  auth_provider  text NOT NULL DEFAULT 'password' CHECK (auth_provider IN ('password','google','microsoft','saml','otp')),
  mfa_enabled    boolean NOT NULL DEFAULT false,
  last_login_at  timestamptz,
  created_at     timestamptz NOT NULL DEFAULT now(),
  updated_at     timestamptz NOT NULL DEFAULT now()
);

-- Directors, partners, KMP, shareholders as legal persons (may or may not be users)
CREATE TABLE persons (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  entity_id     uuid NOT NULL REFERENCES entities(id),
  user_id       uuid REFERENCES users(id),
  full_name     text NOT NULL,
  pan           pan_no,
  din           text CHECK (din ~ '^[0-9]{8}$'),
  designation   text,                       -- 'Director', 'Designated partner', 'CFO'
  appointed_on  date,
  ceased_on     date,
  dir3_kyc_status text CHECK (dir3_kyc_status IN ('done','due','deactivated')),
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX persons_entity_ix ON persons (entity_id);

CREATE TYPE member_role AS ENUM ('owner', 'approver', 'maker', 'viewer');

-- Who can act inside an entity, and in which modules
CREATE TABLE memberships (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  entity_id    uuid NOT NULL REFERENCES entities(id),
  user_id      uuid NOT NULL REFERENCES users(id),
  role         member_role NOT NULL,
  modules      text[] NOT NULL DEFAULT '{*}',   -- '{gst,finance}' or '{*}'
  invited_by   uuid REFERENCES users(id),
  accepted_at  timestamptz,
  revoked_at   timestamptz,
  created_at   timestamptz NOT NULL DEFAULT now(),
  UNIQUE (entity_id, user_id)
);

-- Org-level staff (e.g. CA firm partners who see every client entity)
CREATE TABLE org_members (
  organization_id uuid NOT NULL REFERENCES organizations(id),
  user_id         uuid NOT NULL REFERENCES users(id),
  role            text NOT NULL CHECK (role IN ('admin','partner','staff')),
  PRIMARY KEY (organization_id, user_id)
);

-- External advisors given scoped access to an entity (CA, CS, counsel, lender)
CREATE TABLE advisor_grants (
  id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  entity_id            uuid NOT NULL REFERENCES entities(id),
  advisor_org_id       uuid REFERENCES organizations(id),   -- the CA firm, if on Biz360X
  advisor_name         text NOT NULL,
  advisor_kind         text NOT NULL CHECK (advisor_kind IN ('chartered_accountant','company_secretary','counsel','lender','investor','auditor')),
  scopes               jsonb NOT NULL,          -- {"finance":"read","gst":"prepare","mca_financial":"certify"}
  document_folders     text[],                  -- restrict vault access
  starts_on            date NOT NULL DEFAULT current_date,
  expires_on           date,
  status               text NOT NULL DEFAULT 'invited' CHECK (status IN ('invited','active','revoked','expired')),
  created_at           timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX advisor_grants_entity_ix  ON advisor_grants (entity_id);
CREATE INDEX advisor_grants_advorg_ix ON advisor_grants (advisor_org_id) WHERE advisor_org_id IS NOT NULL;

-- Digital signature certificates (metadata only — tokens never leave the signer)
CREATE TABLE dsc_certificates (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  entity_id     uuid NOT NULL REFERENCES entities(id),
  person_id     uuid NOT NULL REFERENCES persons(id),
  dsc_class     text NOT NULL DEFAULT 'class3',
  usage         text NOT NULL DEFAULT 'signing' CHECK (usage IN ('signing','encryption','both')),
  serial_hash   text NOT NULL,              -- hash of certificate serial
  issuer        text,
  valid_from    date NOT NULL,
  valid_until   date NOT NULL,
  registered_on text[] NOT NULL DEFAULT '{}',  -- '{MCA,GSTN,ITD}'
  created_at    timestamptz NOT NULL DEFAULT now(),
  CHECK (valid_until > valid_from)
);
CREATE INDEX dsc_expiry_ix ON dsc_certificates (entity_id, valid_until);

-- Session context used by RLS (set by the API on every request)
--   SET app.user_id = '<uuid>';
CREATE OR REPLACE FUNCTION app.current_user_id() RETURNS uuid
LANGUAGE sql STABLE AS $$
  SELECT nullif(current_setting('app.user_id', true), '')::uuid
$$;

-- Entities the current user may see: own memberships, org staff, advisor grants
CREATE OR REPLACE FUNCTION app.visible_entity_ids() RETURNS SETOF uuid
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, app AS $$
  SELECT m.entity_id FROM memberships m
   WHERE m.user_id = app.current_user_id() AND m.revoked_at IS NULL
  UNION
  SELECT e.id FROM entities e
    JOIN org_members om ON om.organization_id = e.organization_id
   WHERE om.user_id = app.current_user_id()
  UNION
  SELECT g.entity_id FROM advisor_grants g
    JOIN org_members om ON om.organization_id = g.advisor_org_id
   WHERE om.user_id = app.current_user_id()
     AND g.status = 'active' AND (g.expires_on IS NULL OR g.expires_on >= current_date)
$$;

CREATE TRIGGER organizations_touch BEFORE UPDATE ON organizations FOR EACH ROW EXECUTE FUNCTION app.touch_updated_at();
CREATE TRIGGER entities_touch      BEFORE UPDATE ON entities      FOR EACH ROW EXECUTE FUNCTION app.touch_updated_at();
CREATE TRIGGER entity_reg_touch    BEFORE UPDATE ON entity_registrations FOR EACH ROW EXECUTE FUNCTION app.touch_updated_at();
CREATE TRIGGER users_touch         BEFORE UPDATE ON users         FOR EACH ROW EXECUTE FUNCTION app.touch_updated_at();
CREATE TRIGGER persons_touch       BEFORE UPDATE ON persons       FOR EACH ROW EXECUTE FUNCTION app.touch_updated_at();
