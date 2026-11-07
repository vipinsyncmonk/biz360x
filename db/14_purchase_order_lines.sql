-- =====================================================================
-- Part 14 · Purchase order lines
-- purchase_orders (part 05) had a total but nowhere to keep the lines the API accepts.
-- =====================================================================
CREATE TABLE purchase_order_lines (
  id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  po_id        uuid NOT NULL REFERENCES purchase_orders(id) ON DELETE CASCADE,
  line_no      smallint NOT NULL,
  item_id      uuid REFERENCES items(id),
  description  text NOT NULL,
  hsn_sac      hsn_sac,
  qty          qty NOT NULL CHECK (qty > 0),
  uqc          text NOT NULL DEFAULT 'NOS',
  rate         inr NOT NULL CHECK (rate >= 0),
  gst_rate     numeric(5,2) NOT NULL,
  received_qty qty NOT NULL DEFAULT 0,
  UNIQUE (po_id, line_no),
  CHECK (received_qty <= qty)
);

ALTER TABLE purchase_order_lines ENABLE ROW LEVEL SECURITY;
ALTER TABLE purchase_order_lines FORCE ROW LEVEL SECURITY;
CREATE POLICY via_parent ON purchase_order_lines
  USING (EXISTS (SELECT 1 FROM purchase_orders p WHERE p.id = purchase_order_lines.po_id))
  WITH CHECK (EXISTS (SELECT 1 FROM purchase_orders p WHERE p.id = purchase_order_lines.po_id));

GRANT SELECT, INSERT, UPDATE, DELETE ON purchase_order_lines TO biz_app;
GRANT SELECT, INSERT, UPDATE ON purchase_order_lines TO biz_worker;
