-- =====================================================================
-- Part 16 · Payroll run inputs
-- One-off values for a month (loss-of-pay days, overtime, bonus, reimbursements, recoveries) belong to the run,
-- not to the employee, so they are kept as one JSON document per run.
-- =====================================================================
CREATE TABLE payroll_run_inputs (
  run_id      uuid PRIMARY KEY REFERENCES payroll_runs(id) ON DELETE CASCADE,
  inputs      jsonb NOT NULL DEFAULT '{}',        -- { "<employee_id>": {"lop_days": "2", "bonus": "5000", ...} }
  updated_at  timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE payroll_run_inputs ENABLE ROW LEVEL SECURITY;
ALTER TABLE payroll_run_inputs FORCE ROW LEVEL SECURITY;
CREATE POLICY via_parent ON payroll_run_inputs
  USING (EXISTS (SELECT 1 FROM payroll_runs r WHERE r.id = payroll_run_inputs.run_id))
  WITH CHECK (EXISTS (SELECT 1 FROM payroll_runs r WHERE r.id = payroll_run_inputs.run_id));

GRANT SELECT, INSERT, UPDATE, DELETE ON payroll_run_inputs TO biz_app;
GRANT SELECT, INSERT, UPDATE ON payroll_run_inputs TO biz_worker;
