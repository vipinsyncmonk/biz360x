-- =====================================================================
-- Part 34 · Inventory
-- Stock for the items a company marks as tracked (items.track_inventory, goods only): where it is kept, every movement in and out with the
-- cost it carried, and the quantity on hand. A booked purchase bill brings stock in, an issued invoice takes it out at the average cost and
-- a credit note for returned goods puts it back; counts, write-offs, opening stock and a move between warehouses are recorded by a person.
--
-- Valuation is weighted average cost, per item across all warehouses, worked out by the API in the order movements are recorded
-- (server/app/inventory.py). The value of an item's stock is the sum of stock_movements.value, so it always agrees with what was posted to
-- the Inventory account. FIFO, batches, serial numbers and expiry are not here.
-- =====================================================================
ALTER TYPE journal_source ADD VALUE IF NOT EXISTS 'inventory';

-- A company may let stock go below zero (selling before the purchase bill is entered). Off unless it says so.
ALTER TABLE entities ADD COLUMN allow_negative_stock boolean NOT NULL DEFAULT false;

CREATE TABLE warehouses (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  entity_id   uuid NOT NULL REFERENCES entities(id),
  code        text NOT NULL CHECK (code ~ '^[A-Z0-9-]{1,12}$'),
  name        text NOT NULL CHECK (length(btrim(name)) > 0),
  is_default  boolean NOT NULL DEFAULT false,                -- bills receive into it and invoices ship from it
  is_active   boolean NOT NULL DEFAULT true,
  created_at  timestamptz NOT NULL DEFAULT now(),
  UNIQUE (entity_id, code)
);
CREATE UNIQUE INDEX warehouses_one_default_ix ON warehouses (entity_id) WHERE is_default;

-- Append-only. The id is the order of recording, which is the order the average cost was worked out in.
CREATE TABLE stock_movements (
  id                bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  entity_id         uuid NOT NULL REFERENCES entities(id),
  item_id           uuid NOT NULL REFERENCES items(id),
  warehouse_id      uuid NOT NULL REFERENCES warehouses(id),
  movement_date     date NOT NULL,
  qty_in            qty NOT NULL DEFAULT 0 CHECK (qty_in >= 0),
  qty_out           qty NOT NULL DEFAULT 0 CHECK (qty_out >= 0),
  unit_cost         numeric(18,4) NOT NULL CHECK (unit_cost >= 0),
  value             inr NOT NULL,                             -- signed: what this movement added to (+) or took from (−) the value of the item's stock
  source_kind       text NOT NULL CHECK (source_kind IN ('purchase_bill', 'sales_document', 'adjustment', 'opening', 'transfer', 'bill_of_entry')),
  source_id         uuid,                                     -- the bill, sales document or bill of entry; a transfer's two rows share one id
  source_line_id    bigint,                                   -- the bill or document line
  reason            text,                                     -- adjustments: count, write_off, opening
  note              text,
  journal_entry_id  uuid REFERENCES journal_entries(id),
  created_by        uuid REFERENCES users(id),
  created_at        timestamptz NOT NULL DEFAULT now(),
  -- stock comes in with a value of zero or more, goes out with zero or less; a bill of entry's duty adds value without quantity
  CONSTRAINT stock_movement_direction CHECK ((qty_in > 0 AND qty_out = 0 AND value >= 0) OR (qty_out > 0 AND qty_in = 0 AND value <= 0)
                                             OR (qty_in = 0 AND qty_out = 0 AND value > 0 AND source_kind = 'bill_of_entry'))
);
CREATE INDEX stock_movements_item_ix   ON stock_movements (item_id, id);
CREATE INDEX stock_movements_entity_ix ON stock_movements (entity_id, movement_date);
CREATE INDEX stock_movements_source_ix ON stock_movements (source_kind, source_id);

-- Quantity on hand per item and warehouse, kept by the trigger below. negative_allowed is the company's setting at the time of the last
-- movement, so the rule "stock cannot go below zero" is a constraint the API cannot forget.
CREATE TABLE stock_levels (
  entity_id         uuid NOT NULL REFERENCES entities(id),
  item_id           uuid NOT NULL REFERENCES items(id),
  warehouse_id      uuid NOT NULL REFERENCES warehouses(id),
  qty               qty NOT NULL DEFAULT 0,
  negative_allowed  boolean NOT NULL DEFAULT false,
  PRIMARY KEY (item_id, warehouse_id),
  CONSTRAINT stock_not_negative CHECK (qty >= 0 OR negative_allowed)
);
CREATE INDEX stock_levels_entity_ix ON stock_levels (entity_id);

CREATE FUNCTION app.apply_stock_movement() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE tracked boolean; allowed boolean;
BEGIN
  IF EXISTS (SELECT 1 FROM fiscal_periods p WHERE p.entity_id = NEW.entity_id AND p.status = 'locked'
                AND NEW.movement_date BETWEEN p.period_start AND p.period_end) THEN
    RAISE EXCEPTION 'Period for % is locked', NEW.movement_date;
  END IF;
  SELECT i.track_inventory AND i.kind = 'goods' INTO tracked FROM items i WHERE i.id = NEW.item_id AND i.entity_id = NEW.entity_id;
  IF NOT coalesce(tracked, false) THEN
    RAISE EXCEPTION 'Item % is not tracked in stock', NEW.item_id;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM warehouses w WHERE w.id = NEW.warehouse_id AND w.entity_id = NEW.entity_id) THEN
    RAISE EXCEPTION 'Warehouse % does not belong to this company', NEW.warehouse_id;
  END IF;
  SELECT e.allow_negative_stock INTO allowed FROM entities e WHERE e.id = NEW.entity_id;
  -- Update first: an upsert would check the constraint against the row it proposes to insert (the movement alone), not the new total.
  -- The API locks the item before it writes, so two first movements of an item do not race here; if they did, the primary key refuses one.
  UPDATE stock_levels SET qty = qty + NEW.qty_in - NEW.qty_out, negative_allowed = coalesce(allowed, false)
   WHERE item_id = NEW.item_id AND warehouse_id = NEW.warehouse_id;
  IF NOT FOUND THEN
    INSERT INTO stock_levels (entity_id, item_id, warehouse_id, qty, negative_allowed)
         VALUES (NEW.entity_id, NEW.item_id, NEW.warehouse_id, NEW.qty_in - NEW.qty_out, coalesce(allowed, false));
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER stock_movements_apply AFTER INSERT ON stock_movements FOR EACH ROW EXECUTE FUNCTION app.apply_stock_movement();

-- Stock on hand per item: quantity (all warehouses), value and the average cost it will leave at.
CREATE VIEW v_stock_on_hand WITH (security_invoker = true) AS
SELECT i.entity_id, i.id AS item_id, i.sku, i.name, i.uqc,
       coalesce((SELECT sum(s.qty) FROM stock_levels s WHERE s.item_id = i.id), 0)::numeric(18,3) AS qty,
       coalesce((SELECT sum(m.value) FROM stock_movements m WHERE m.item_id = i.id), 0)::numeric(18,2) AS value
  FROM items i WHERE i.track_inventory AND i.kind = 'goods';

DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['warehouses', 'stock_movements', 'stock_levels'] LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
    EXECUTE format($p$CREATE POLICY tenant_isolation ON %I USING (entity_id IN (SELECT app.visible_entity_ids())) WITH CHECK (entity_id IN (SELECT app.visible_entity_ids()))$p$, t);
  END LOOP;
END $$;
GRANT SELECT, INSERT, UPDATE ON warehouses, stock_levels TO biz_app, biz_worker;
GRANT SELECT, INSERT ON stock_movements TO biz_app, biz_worker;            -- no UPDATE or DELETE: a wrong movement is corrected by another one
GRANT SELECT ON v_stock_on_hand TO biz_app, biz_worker;
GRANT EXECUTE ON FUNCTION app.apply_stock_movement() TO biz_app, biz_worker;
