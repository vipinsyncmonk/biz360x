-- =====================================================================
-- Part 17 · Vault upload state, the template library, and the MGT-14 rule
-- =====================================================================

-- A document row is created when metadata is registered; the bytes arrive later at the signed upload URL.
ALTER TABLE documents ADD COLUMN uploaded_at timestamptz;
COMMENT ON COLUMN documents.uploaded_at IS 'NULL until the bytes have been received and checked against sha256 and size_bytes';

-- Library templates (entity_id NULL). The text of each lives in server/app/routes_legal.py (TEMPLATE_BODIES);
-- none has been reviewed by counsel yet, and reviewed_by says so by being empty.
INSERT INTO contract_templates (entity_id, name, kind, version) VALUES
  (NULL, 'Mutual NDA', 'nda', 'v1'),
  (NULL, 'Master services agreement', 'customer', 'v1'),
  (NULL, 'Supply agreement', 'customer', 'v1'),
  (NULL, 'Offer letter and employment', 'employment', 'v1'),
  (NULL, 'Consultant agreement', 'consultant', 'v1');

-- MGT-14: special resolutions (and some board resolutions) are filed with the ROC within 30 days of being passed.
-- It is an event-driven duty, so the engine never schedules it; passing the resolution creates the obligation.
INSERT INTO compliance_rules (code, version, title, form_code, authority_code, statute_ref, frequency, per_registration, applicability, due_rule, penalty_rule, effective, change_note)
VALUES ('MCA.MGT14', 1, 'MGT-14 · filing of resolutions', 'MGT-14', 'MCA', 'Companies Act 2013 s.117', 'per_event', NULL,
        '{"all":[{"fact":"constitution","op":"in","value":["private_limited","public_limited","opc","section8"]}]}',
        '{"type":"days_after_event","event":"resolution","days":30}', '{"additional_fee_per_day":100}', daterange('2014-04-01', NULL),
        'Starter rule; created by the resolution workflow');
