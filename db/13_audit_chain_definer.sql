-- =====================================================================
-- Part 13 · Audit chain must see every tenant's rows
-- app.audit_chain() ran as the calling user, so row-level security hid other
-- tenants' audit rows and each writer chained to the last row *it could see*.
-- With two tenants the global chain broke (audit_verify() returned the first
-- row written after another tenant's row). Run the trigger with the owner's
-- rights so the chain is global, as audit_verify() expects.
-- =====================================================================
CREATE OR REPLACE FUNCTION app.audit_chain() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, app AS $$
DECLARE last char(64);
BEGIN
  PERFORM pg_advisory_xact_lock(hashtext('audit_log_chain'));   -- serialise writers
  SELECT hash INTO last FROM audit_log ORDER BY id DESC LIMIT 1;
  NEW.prev_hash := last;
  NEW.hash := encode(digest(app.audit_payload(NEW, last), 'sha256'), 'hex');
  RETURN NEW;
END $$;
