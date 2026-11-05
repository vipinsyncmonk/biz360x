# Biz360X database (PostgreSQL 16)

119 tables, 9 views, row-level security on every tenant table. It was built against the 25 Biz360X screens, and every number those screens show maps to a column or a view here.

## Run it

```bash
createdb biz360x
for f in 0*.sql 10_seed_reference.sql 11_api_support.sql; do psql -d biz360x -v ON_ERROR_STOP=1 -f "$f"; done
psql -d biz360x -f tests/demo_and_checks.sql      # optional: demo data + 23 behaviour checks
```

Run the migration as a superuser, because it creates two roles. The API then connects as `biz_app` and runs `SET app.user_id = '<uuid>'` on every request.

## Files

| File | What it holds |
|---|---|
| `01_foundation.sql` | Domains (PAN, GSTIN, TAN, CIN, HSN, `inr`), states, authorities, organizations, entities, registrations, users, memberships, advisor grants, DSCs |
| `02_compliance.sql` | Versioned rules, entity facts, engine runs, obligations, thresholds, notices, approvals, signatures, tasks |
| `03_ledger_parties.sql` | Parties and their GSTINs, items, chart of accounts, periods, journals (balance and lock guards), bank feeds and matching, close checklist |
| `04_sales.sql` | Number series (16-character GST rule), quotes, sales orders, invoices, credit and debit notes in one table, IRN fields, e-way bills, virtual accounts, payment links, receipts, settlements, TDS receivable, dunning, recurring invoices |
| `05_purchases_tax.sql` | POs, GRNs, purchase bills with the MSMED 45-day clock, vendor payments, GST returns, 2A/2B docs, ITC matching, credit ledger, challans, TDS returns and deductions, advance tax, ITR |
| `06_payroll.sql` | Employees, effective-dated salary structures (wage flag for the labour codes), payroll runs, payslips, leave, gratuity, bonus, committees |
| `07_legal_capital_schemes.sql` | Contracts and extracted terms, meetings and resolutions, cap table, rounds, allotments, ESOPs, schemes and benefits, TReDS, delayed-payment claims |
| `08_platform.sql` | Document vault (full-text search), links, requests, data rooms, portal connections, AA consents, sync jobs, copilot, notifications, hash-chained audit log, subscriptions |
| `09_security_views.sql` | Roles, RLS policies, read models (compliance calendar, authority standing, receivables ageing, MSME payables, wage-code check, cap table, ITC at risk, contract notice windows) |
| `11_api_support.sql` | Jobs queue (`claim_job` with SKIP LOCKED), idempotency keys, API keys, webhook endpoints and deliveries, event outbox, worker role |
| `10_seed_reference.sql` | GST state codes, authorities, sample HSN/SAC codes, 12 starter compliance rules, schemes |

## Rules the database itself enforces

- **Tenant isolation.** A user sees an entity only as a member, as staff of its organisation, or through an active advisor grant. Child rows (invoice lines, payslips and so on) inherit visibility from their parent.
- **Two-person rule.** The person who requested an approval can never decide it. A copilot action can't be approved or executed without an approval request.
- **Ledger integrity.** A journal posts only if it balances and its period is open. Lines of a posted journal are frozen, and a correction is a reversal.
- **GST shape.**
  - An inter-state document carries only IGST; an intra-state one only CGST and SGST, in equal amounts.
  - A B2B invoice needs the buyer's GSTIN.
  - Credit and debit notes must point at their original invoice, and a credit note needs a reason.
  - Document numbers are at most 16 characters and unique per GSTIN per financial year.
- **Settlements.** Receipts, credit notes, TDS and write-offs can't exceed an invoice's total, and the payment status follows automatically.
- **MSMED Act.** For micro and small suppliers, the pay-by date is the agreed term capped at 45 days from acceptance, or 15 days if nothing was agreed.
- **Allotments.** Shares must be allotted within 60 days of the money being received.
- **Rule versions.** Each compliance rule has exactly one live version on any given day.
- **Audit log.** It is append-only and SHA-256 chained, and `SELECT app.audit_verify()` returns the first broken row, or NULL if the chain is intact.

## Before production

- Write the rule evaluator that turns `compliance_rules` and `entity_facts` into obligations. The JSON languages it reads are documented at the top of `10_seed_reference.sql`.
- Have a CA/CS review the rule library (fees, caps, dates). Ship every change as a new rule version, never as an edit.
- Move these files into your migration tool (Flyway, sqitch or Prisma migrate).
- At scale, partition `audit_log`, `bank_transactions` and `entity_facts` by month.
- Encrypt the `*_enc` columns with KMS in the app. Portal credentials live only in a secret manager; `portal_connections.credential_ref` holds the path to them, never the secret.
