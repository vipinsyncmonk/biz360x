-- =====================================================================
-- Part 12 · Compliance engine triggers
-- A changed fact, registration or rule queues one engine run per entity
-- (deduplicated while a run is still queued). The worker executes the job.
-- =====================================================================

CREATE OR REPLACE FUNCTION app.enqueue_engine_run(p_entity uuid, p_trigger text) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, app AS $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM jobs WHERE kind = 'compliance.run_engine' AND entity_id = p_entity AND status = 'queued') THEN
    INSERT INTO jobs (entity_id, kind, subject, subject_id, payload)
    VALUES (p_entity, 'compliance.run_engine', 'entities', p_entity, jsonb_build_object('trigger', p_trigger));
  END IF;
END $$;
GRANT EXECUTE ON FUNCTION app.enqueue_engine_run(uuid, text) TO biz_app, biz_worker;

CREATE OR REPLACE FUNCTION app.on_fact_change() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF TG_TABLE_NAME = 'entity_facts' THEN
    IF NEW.source = 'engine' THEN RETURN NEW; END IF;      -- the engine's own facts do not retrigger it
  END IF;
  PERFORM app.enqueue_engine_run(NEW.entity_id, 'fact_change');
  RETURN NEW;
END $$;

CREATE TRIGGER entity_facts_engine AFTER INSERT ON entity_facts
  FOR EACH ROW EXECUTE FUNCTION app.on_fact_change();
CREATE TRIGGER entity_registrations_engine AFTER INSERT OR UPDATE OF status, gst_filing_frequency, valid_from, valid_until ON entity_registrations
  FOR EACH ROW EXECUTE FUNCTION app.on_fact_change();

CREATE OR REPLACE FUNCTION app.on_rule_change() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, app AS $$
DECLARE e uuid;
BEGIN
  FOR e IN SELECT id FROM entities WHERE is_active LOOP
    PERFORM app.enqueue_engine_run(e, 'rule_change');
  END LOOP;
  RETURN NULL;
END $$;

CREATE TRIGGER compliance_rules_engine AFTER INSERT ON compliance_rules
  FOR EACH STATEMENT EXECUTE FUNCTION app.on_rule_change();
