# Biz360X API — design guide

This guide covers four things: how the system is put together, the rules every endpoint follows, the main flows end to end, and which endpoints feed each screen. The full contract is `openapi.yaml` (OpenAPI 3.1, 270 operations); `api-reference.html` is the same contract rendered as readable docs.

---

## 1. Architecture

```
 Web app · Mobile app · CA-firm console · Customer pay page · Integrations (API keys)
                                │  HTTPS, JWT / API key
                    ┌───────────▼────────────┐
                    │  API service (stateless)│  auth · permissions · idempotency · validation
                    │  modular monolith:      │  /entities/{id}/… compliance, sales, purchases,
                    │  one module per domain  │  gst, tds, finance, payroll, legal, capital, docs
                    └──┬──────────────┬───────┘
     SET ROLE biz_app  │              │ INSERT jobs / event_outbox (same transaction)
     app.user_id = …   │              │
             ┌─────────▼──────┐   ┌───▼──────────────────────────────────────────────┐
             │ PostgreSQL 16  │◄──┤ Workers (biz_worker role)                         │
             │ RLS on every   │   │  · compliance engine (rules × facts → obligations)│
             │ tenant table   │   │  · portal adapters: GSP (GSTN), IRP, e-way, MCA,  │
             └───────┬────────┘   │    TRACES, EPFO, ESIC, Account Aggregator, banks  │
                     │            │  · OCR / extraction · copilot · notifications     │
       object store ◄┘            │  · outbox relay → SSE stream + signed webhooks    │
       (documents)                └──────────────────────────────────────────────────┘
       secret manager (portal credentials, webhook secrets, KMS keys)
```

- **Start as one deployable plus workers.** The `jobs` table, claimed with `FOR UPDATE SKIP LOCKED`, is the queue until volume justifies a broker. The interface does not change when you swap it.
- **Every external system sits behind an adapter interface.** For example, `IrpAdapter.generate_irn()` in `server/app/worker.py`. A sandbox implementation runs in development and tests.
- **Data stays in India.** Deploy to an Indian region and keep backups in the same region.

### Request lifecycle

1. Authenticate the bearer JWT or API key and get a `Principal`.
2. Open one database transaction. Run `SET LOCAL ROLE biz_app` and `set_config('app.user_id', …)`, so row-level security now scopes every query.
3. Check the permission (`module:action`) against the membership role, or against the advisor grant's scopes.
4. For money and filing writes, claim the `Idempotency-Key` inside the same transaction.
5. Run the domain logic. Database constraints are the last line of defence and map to stable error codes.
6. Write `audit_log` and `event_outbox` rows in the same transaction.
7. Commit. Long work continues on a `Job`, and clients learn the outcome through `/jobs/{id}`, `/events` (SSE) or a webhook.

---

## 2. Conventions

| Topic | Rule |
|---|---|
| Base URL | `https://api.[YOUR DOMAIN]/v1`. Breaking changes mean `/v2`; additive changes ship in place. A `Deprecation` header gives 6 months' notice. |
| Tenancy | Business resources live under `/entities/{entityId}/…`. An entity the caller cannot see returns **404**; a visible one without the permission returns **403**. |
| IDs | UUIDs. Human numbers (`NI/KA/213`, `Q-0418`) are fields, never path keys. |
| Money | A decimal string in INR with two places: `"283200.00"`. Never a float. |
| Dates | `YYYY-MM-DD` for legal dates (invoice date, due date); RFC 3339 UTC for moments. FY labels are `2026-27`. |
| Lists | `?limit=50&cursor=…` returns `{data, next_cursor}`. `sort=-due_date`. Filters are plain query parameters, and repeating one means OR. |
| Updates | `PATCH` is a JSON merge patch with `If-Match: <ETag>`; a stale version returns `412`. Read-only fields are ignored. |
| Actions | Verbs are sub-resources: `POST …/issue`, `…/approve`, `…/file`, `…/lock`. No `PUT status=…`. |
| Idempotency | `Idempotency-Key` is required where the spec says so (issue, file, pay, receipts, approvals). A replay within 24 h returns the first response. The same key with a different body returns `409 idempotency_conflict`. |
| Async | Portal calls return `202` with a `Job` and `Location: /jobs/{id}`. A job can pause in `waiting_for_otp`; send the code with `POST /jobs/{id}/otp`. |
| Errors | `application/problem+json` (RFC 9457) with a stable `code` and field-level `errors[]`. |
| Rate limits | 600 requests/min per user, 60/min per user for portal-calling actions, and 30/min per IP for public pay pages. `RateLimit` and `Retry-After` headers are sent. |
| Events | Types are `noun.verb` (`sales_document.paid`). Webhooks carry `Biz360X-Signature: t=…,v1=HMAC_SHA256(secret, t + "." + body)`. Reject if the timestamp is off by more than 300 s, and deduplicate on `Biz360X-Event-Id`. Failed deliveries retry with backoff for 72 h. |

### Error codes

`validation_failed` · `not_found` · `conflict` · `precondition_failed` · `idempotency_conflict` · `permission_denied` · `two_person_rule` · `approval_required` · `period_locked` · `journal_unbalanced` · `tax_split_mismatch` · `b2b_needs_gstin` · `note_needs_original` · `number_too_long` · `over_settlement` · `msme_terms_exceeded` · `rule_version_overlap` · `portal_session_expired` · `portal_rejected` · `rate_limited` · `unauthorized` · `mfa_required` · `internal`

Most domain errors are raised by the database itself. The API maps constraint names to codes in one table (`CONSTRAINT_CODES` in `server/app/core.py`), so the rule and its error message can never drift apart.

---

## 3. Permissions

Each operation carries `x-permission: module:action`.

The modules are `settings`, `compliance`, `approvals`, `tasks`, `parties`, `sales`, `purchases`, `gst`, `tds`, `finance`, `payroll`, `legal`, `capital`, `schemes`, `documents`, `copilot` and `audit`.

| Role (membership) | read | write | approve | file / pay |
|---|---|---|---|---|
| owner | ✓ | ✓ | ✓ | ✓ |
| approver | ✓ | ✓ | ✓ | ✓ |
| maker | ✓ | ✓ | – | – |
| viewer | ✓ | – | – | – |

- **Modules.** A membership can be limited to a list of modules (`{gst,finance}`); `*` means all.
- **Advisors.** An advisor grant maps each module to a level: `read`, `comment` (read), `prepare` (read + write) or `certify` (read + write + file).
- **Two-person rule.** It is enforced in the database: whoever requested an approval can never decide it, whatever their role.
- **API keys.** A key (`bzx_live_…`, shown once, stored as a hash) acts as a named service user, so the same row-level security applies, and it is limited to its listed `module:read|write|use` permissions. It can never approve, file or pay, and cannot create keys. See `docs/SECURITY.md`.

---

## 4. Key flows

### Onboarding

1. `POST /onboarding/lookups {identifier}` returns the registry data: name, directors, AGM and GSTINs.
2. `POST /onboarding/preview {answers}` returns the obligation count, triggers and first due items. Call it on every answer change.
3. `POST /onboarding/workspaces` (with Idempotency-Key) creates the entity, registrations and facts, then starts the first engine run.

### Quote → invoice → cash

1. `POST /quotes`, then `/quotes/{id}/send`, then `/quotes/{id}/accept`. Acceptance opens a sales order.
2. `POST /sales-orders/{id}/invoice {lines: lot 2 quantities}` creates a draft invoice.
3. While editing, `POST /sales-documents/preview` returns the IGST or CGST+SGST split, totals, amount in words, next number and checks: e-invoice, e-way bill (over ₹50,000), 194Q TDS, MSME terms, and the Sec 34 time limit for credit notes.
4. `POST /sales-documents/{id}/issue` (with Idempotency-Key) assigns the number atomically from `document_series` and returns `202` with an `einvoice.generate_irn` job. The worker calls the IRP; the document gets `irn`, `ack_no` and the signed QR, and `sales_document.irn_generated` is emitted.
5. The customer opens `/pay/{shortCode}` and pays by NEFT to the invoice's virtual account. The bank feed line is auto-matched; alternatively, `POST /receipts {allocations}` records the payment. Settlements update `payment_status`, and `sales_document.paid` fires.
6. Corrections go through `POST /sales-documents/{id}/credit-notes {cn_reason, lines}`, which drafts a credit note. Issuing it follows step 4, and the settlement reduces the invoice balance.

### Filing a return (e.g. GSTR-1)

1. The engine has already created the obligation (`GET /obligations?status=upcoming`).
2. `POST /obligations/{id}/prepare` returns `202`; the job builds the draft from the issued sales documents.
3. A maker calls `POST /obligations/{id}/submit` to create an approval request, and a different person approves it with `POST /approvals/{id}/approve`.
4. `POST /obligations/{id}/file {signing: evc_otp}` returns `202`. The job pauses at `waiting_for_otp`; the owner calls `POST /jobs/{id}/otp`. The job ends with the ARN; the obligation becomes `filed` and the acknowledgement is stored in the vault.

### Paying an MSME supplier

`GET /msme-payables?breached=false` sorted by `msme_pay_by`, then `POST /vendor-payments` (creates an approval request), approve, then `POST /vendor-payments/{id}/release` returns `202`. The bank call runs on the job, and the bill's `amount_paid` updates.

### Payroll

`POST /payroll-runs {period_month}`, then `PUT …/inputs`, then `POST …/compute` (payslips plus PF, ESI, PT and TDS). Then `POST …/submit` and approve, then `POST …/bank-file` returns `202`. The statutory obligations (TDS on the 7th, PF/ESI on the 15th, PT on the 20th) are linked to the run.

### Copilot

1. `POST /copilot/threads/{id}/messages` with `Accept: text/event-stream` streams the answer tokens, citations and proposed actions.
2. The model reads only through the caller's permissions, under the same row-level security.
3. Actions are proposals. `POST /copilot/actions/{id}/request-approval` creates an approval request, and nothing executes until it is approved. The database enforces this.

### CA firm

`GET /organizations/{id}/entities?filter=waiting_on_client` returns the client × authority matrix. `GET /organizations/{id}/workload` returns staff load. `POST /organizations/{id}/client-reminders` queues an email reminder to the owners of each blocked client (never to the firm's own staff), skips a client reminded in the last 24 hours, and returns who was reminded and who was skipped and why. A firm adds a client through the same onboarding flow, with `organization_id` set.

---

## 5. Screen → endpoint map

| Screen | Endpoints |
|---|---|
| Sign in / Create workspace | `POST /auth/otp`, `POST /auth/signup`, `POST /auth/token`, `POST /onboarding/lookups` |
| Onboarding | `GET /onboarding/lookups/{id}`, `POST /onboarding/preview`, `POST /onboarding/workspaces` |
| Command Center | `GET /compliance/summary`, `GET /approvals?assigned_to=me`, `GET /compliance/calendar`, `GET /compliance/standing`, `GET /finance/summary`, `GET /audit-log?limit=5`, `/events` |
| Compliance Engine | `GET /obligations`, `GET /obligations/{id}`, `GET /compliance/calendar`, `GET /compliance/thresholds`, `GET /notices`, `POST /compliance/engine-runs` |
| Startup workspace | `GET /cap-table?scenario=…`, `GET /funding-rounds/{id}` (steps), `GET /benefits`, `GET /meetings`, `GET /esop-grants` |
| MSME workspace | `GET /receivables/ageing`, `GET /benefits`, `GET /entities/{id}` (Udyam), `POST /delayed-payment-claims`, `POST /treds-invoices` |
| Copilot (web + mobile) | `/copilot/threads`, `…/messages` (SSE), `…/actions/{id}/request-approval` |
| Finance & Books | `GET /finance/summary`, `GET /periods`, `GET /periods/{id}/close-tasks`, `PATCH …/close-tasks/{taskId}`, `GET /bank-transactions?match_status=suggested`, `POST /bank-accounts/{id}/transactions`, `POST /bank-matches/{id}/accept`, `POST /bank-matches/{id}/reject`, `GET /reports/profit-and-loss`, `GET /reports/cash-bridge`, `POST /periods/{id}/lock` |
| Payroll & HR | `GET /payroll-runs`, `POST /payroll-runs`, `POST /payroll-runs/{id}/compute`, `POST /payroll-runs/{id}/submit`, `GET /payroll-runs/{id}`, `GET /employees`, `POST /employees`, `POST /employees/{id}/salary-structures`, `GET /payroll-runs/{id}/inputs`, `PUT /payroll-runs/{id}/inputs`, `POST /payroll-runs/{id}/bank-file`, `GET /payroll-runs/{id}/payslips`, `GET /payroll/wage-code-check`, `GET /payroll/labour-compliance`, `GET /obligations?authority=EPFO` |
| GST & Tax | `GET /gst-returns?period=…`, `GET /itc-matches`, `GET /itc-matches/summary`, `POST /itc-matches/{id}/resolve`, `POST /itc-matches/remind-suppliers`, `POST /gst-returns/{id}/compute`, `POST /gst-returns/{id}/file`, `POST /gst/inward-sync`, `GET /gst/ledger-balances`, `GET /tds-deductions`, `GET /advance-tax` |
| Legal & Contracts | `GET /contracts?notice_within_days=30`, `GET /contracts/{id}`, `POST …/extract-terms`, `POST …/notices`, `GET /signature-requests`, `GET /contract-templates`, `GET /documents?q=…`, `GET /documents?expiring_within_days=90`, `GET /document-requests` |
| Document Vault | `GET /documents?q=…&folder=…`, `POST /documents` (signed upload), `GET /documents?expiring_within_days=90`, `GET /document-requests`, `POST /document-requests/{id}/share`, `GET /data-rooms`, `POST /data-rooms`, `PATCH /data-rooms/{id}`, `DELETE /data-rooms/{id}` |
| Group view | `GET /organizations/{id}/entities`, `GET /organizations/{id}/workload`, `POST /organizations/{id}/client-reminders` |
| Settings | `/members`, `/advisor-grants`, `/dsc-certificates`, `/portal-connections` (+ `/portal-connections/{id}/renew`), `/me/notification-preferences`, `/webhooks` (+ `/webhooks/{id}/test`, `/webhooks/{id}/deliveries`), `/api-keys`, `/audit-log`, `/audit-log/verify` |
| Sales & Billing | `GET /sales/summary`, `GET /sales-documents?payment_status=…`, `GET /dunning-sequences`, `GET /recurring-invoices` |
| New GST invoice | `POST /sales-documents/preview` (every change), `POST /sales-documents`, `POST /sales-documents/{id}/issue` |
| Collections | `GET /receivables/ageing`, `GET /receipts`, `POST /receipts`, `POST /receipts/{id}/allocate`, `GET /dunning-sequences`, `POST /parties/{id}/pause-dunning`, `GET /recurring-invoices`, `PATCH /recurring-invoices/{id}` |
| Quotes | `GET /quotes`, `POST /quotes`, `POST /quotes/{id}/send`, `POST /quotes/{id}/mark-lost`, `POST /quotes/{id}/revise`, `POST /quotes/{id}/accept`, `POST /sales-orders/{id}/invoice` |
| Credit note | `POST /sales-documents/preview {doc_type: credit_note}`, `POST /sales-documents/{id}/credit-notes`, `POST …/issue` |
| Customer payment page | `GET /pay/{shortCode}`, `POST /pay/{shortCode}/intents`, `POST /pay/{shortCode}/queries` |

---

## 6. Reference server

`server/` is a FastAPI implementation of the core slice, wired to the real schema with row-level security:

- sign-in
- `/me`
- obligations (keyset pagination)
- submit for approval
- approve and reject (with the two-person rule)
- invoice preview, create, get (with ETag), and issue (atomic numbering plus an IRN job)
- receipts with allocations
- `/jobs/{id}`
- a worker with a sandbox IRP adapter
- the compliance engine: engine runs, calendar, thresholds and the onboarding preview (dry run)
- ledger posting (invoices, credit notes and receipts post journals in the same transaction), quotes -> sales orders -> invoices by lot, credit notes, payment links with per-invoice virtual accounts and the public pay page, bank-feed matching, month-end close and period lock, reports, dunning and recurring invoices
- purchases (orders, bills booked to the ledger with TDS withheld), the MSME 45-day clock, vendor payments that need an independent approval before the bank job runs, GSTR-1 and GSTR-3B built from the books (ITC claimed only when it is in 2B, set off in the legal order), 2B matching, TDS deposit challans, TDS returns and advance tax, all driven from `POST /obligations/{id}/prepare`
- payroll: employees (bank account encrypted at rest), effective-dated salary structures, payroll runs (inputs, compute, approval, bank file), payslips with PF, ESI, professional tax and TDS on salary, the payroll journal, PF/ESI/PT/TDS challans prepared from the approved run, the wage-code check, labour compliance (gratuity, statutory bonus, POSH committee, shops registration)
- document vault (register, signed upload and download with size and SHA-256 checks, full-text search over the text layer, expiry, links to any record, advisor requests, data rooms), contracts with notice-window alerts and terms (pattern-based extraction until the copilot, verified terms never overwritten), template library, signature requests with ordered signers that activate a contract, board meetings with the 120-day gap, resolutions that create the MGT-14 obligation when a special resolution is passed, and the notice desk with draft replies
- portal adapters: one interface per outside system with sandboxes selected by `BIZ_ADAPTERS`; connections whose credentials live in a secret store; jobs that pause in `waiting_for_otp` and resume with `POST /jobs/{id}/otp` (GST filing, EPFO, session renewal); permanent portal refusals fail at once while transient ones retry with backoff; e-way bills, e-invoice cancellation, filing of MCA/EPFO/ESIC/TDS-return obligations, Form 26AS matching, the Account Aggregator (signed callback, incremental sync), registry lookups with a 24-hour cache
- billing reads: `GET /registrations`, `GET /parties` (with GSTINs and receivable balance), `GET /sales-documents` (filters: type, status, payment status, party, overdue, dates, text search with wildcards treated as text) and `GET /sales/summary` (invoiced and collected in a period, outstanding and overdue as of today)
- onboarding: `POST /auth/signup` (creates the person, sends a code, same answer for an existing address) and `POST /onboarding/workspaces` (organization, entity, GSTINs, owner membership and first facts from the person's own registry lookup, then the first engine run, all in one transaction with an `Idempotency-Key`; a company that already has a workspace answers 409)
- auth and hardening: one-time codes (hashed, five attempts, five an hour, no account enumeration), refresh tokens that rotate with reuse detection, TOTP for users with MFA, an identity-provider interface for single sign-on, API keys acting as service users, signed webhooks with retry for 72 h and a block on internal addresses, rate limits with `RateLimit` headers, a daily audit-chain check that raises an alert, and a startup guard that refuses to run in production with a default secret or any sandbox provider (`docs/SECURITY.md`)

Its 14 end-to-end tests run against PostgreSQL 16 and cover the IGST and CGST+SGST split, the 194Q and e-way bill checks, mapping database constraints to error codes, advisor scope limits, idempotency replay and conflict, invisible-entity 404s, over-settlement, the two-person rule, and the audit chain staying intact.

```bash
pip install -r server/requirements.txt
createdb biz360x && for f in db/[0-9][0-9]_*.sql; do psql -d biz360x -v ON_ERROR_STOP=1 -f $f; done
psql -d biz360x -f db/tests/demo_and_checks.sql
cd server && DATABASE_URL=postgresql://postgres@localhost/biz360x pytest -q
DATABASE_URL=… uvicorn app.main:app --reload        # API on :8000, spec at /v1/openapi.yaml
DATABASE_URL=… python -m app.worker                 # job worker
```

In production the API connects as a login role that is a member of `biz_app`, and the workers as one that is a member of `biz_worker`. Development sign-in accepts OTP `000000`, only when `BIZ_ENV` is `dev` or `test` and the OTP provider is the sandbox; the server refuses to start in production with the sandbox provider. Choose and register a real OTP provider (`auth.register_otp_sender`) and an identity provider (`auth.register_idp`) before launch.

## 7. What comes next

1. ~~Compliance engine evaluator~~ - done: `server/app/engine.py` evaluates `applicability` and `due_rule` against facts, upserts obligations with `reason_snapshot`, accrues late fees, and raises threshold alerts. Triggers: schedule (worker, daily), fact change, rule change (migration 12), manual (`POST /compliance/engine-runs`).
2. **Portal adapters through a licensed GSP/ASP.** GSTN returns, IRP, e-way bill, plus MCA, TRACES and EPFO/ESIC where APIs exist; browser automation with human OTP where they don't.
3. **Ledger posting rules.** One journal template per source document, posted in the same transaction as issue and book actions.
4. **The remaining operations in the spec,** following the same patterns. `build_openapi.py` keeps the contract consistent; regenerate it and lint with `npx @redocly/cli lint openapi.yaml`.
