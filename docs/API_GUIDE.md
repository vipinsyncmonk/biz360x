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

`validation_failed` · `not_found` · `conflict` · `precondition_failed` · `idempotency_conflict` · `permission_denied` · `two_person_rule` · `approval_required` · `period_locked` · `journal_unbalanced` · `tax_split_mismatch` · `b2b_needs_gstin` · `note_needs_original` · `number_too_long` · `over_settlement` · `msme_terms_exceeded` · `insufficient_stock` · `item_not_tracked` · `leave_balance_short` · `onboarding_incomplete` · `audit_read_only` · `independence_conflict` · `rule_version_overlap` · `portal_session_expired` · `portal_rejected` · `rate_limited` · `unauthorized` · `mfa_required` · `internal`

Most domain errors are raised by the database itself. The API maps constraint names to codes in one table (`CONSTRAINT_CODES` in `server/app/core.py`), so the rule and its error message can never drift apart.

---

## 3. Permissions

Each operation carries `x-permission: module:action`.

The modules are `settings`, `compliance`, `approvals`, `tasks`, `trade`, `parties`, `sales`, `purchases`, `inventory`, `gst`, `tds`, `finance`, `payroll`, `hr`, `legal`, `capital`, `schemes`, `documents`, `copilot` and `audit`. `hr` (onboarding, leave, holidays, policy and handbook) is separate from `payroll` (pay, salary structures, payslips), so a person can run leave without seeing salaries; `inventory` is separate from `finance`.

A team member holds a **level for each module** (`memberships.scopes`, migration 37), on one scale. `*` stands for every module not named.

| Level | read | write | approve | file / pay |
|---|---|---|---|---|
| `file` | ✓ | ✓ | ✓ | ✓ |
| `approve` | ✓ | ✓ | ✓ | – |
| `write` | ✓ | ✓ | – | – |
| `comment`, `read` | ✓ | – | – | – |

- **Titles are presets.** CEO, COO, CFO, Accountant, HR Manager and the rest are named starting points (`GET /role-presets`, `server/app/roles.py`) that fill the scopes, a title and the home screen; the owner can change every line (`POST /members` with `preset` and/or `scopes`, `PATCH /members/{id}`). A preset is not a guarantee of what a title should see in a given company.
- **Owner.** `role = owner` is the one flag that is not a level: an owner has every module and is the only one who can invite a person, change their access or remove them (403 for anyone else, an API key included). A person cannot change their own access, and a company keeps at least one owner.
- **`role` and `modules`** are still returned and still accepted on `POST /members` as the earlier way to invite. A database trigger keeps them in step with the scopes: the role is `owner`, or the highest level held (`approver` for approve or file, `maker` for write, `viewer` below).
- **Approvals and tasks** are never granted on their own: a person works with them at their highest level.
- **Home.** `/me` returns each company's `scopes`, `title` and `home` (`leadership`, `finance`, `people`, `general`, or `practitioner` for someone who comes in through a firm). The web app filters the sidebar by the modules a person can open and puts the home's numbers above the shared sections; a section a person has no scope for is not asked for. The server stays the authority: hiding a link is not a permission.
- **Practitioners.** An engagement maps each module to a level: `read`, `comment` (read), `prepare` (read + write) or `certify` (read + write + file). It never includes settings or an approving level, and it gives access only once the practitioner has accepted ("Engaging a practitioner" below).
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
5c. An export invoice can be priced in the customer's currency (migration 31): send `priced_in_foreign: true` and each line's `rate` and `discount` are in `currency` (the rate may have four decimals). The foreign amount of a line is `qty × rate − discount` to the cent, `foreign_total` is the sum of those (plus the IGST converted, when tax is charged), and the rupee columns are each line's foreign amount converted at `exchange_rate`: they are what GST, the ledger and the IRN use, and GST still rounds the rupee total to a whole rupee. Without the flag the rates are rupees and the foreign total is worked back from the rupee total, which can be a cent out. The rate is the one for the books and GST on the invoice date; what the bank credits when the customer pays will differ, and `POST /shipping-bills/{id}/realisations` posts the difference as exchange gain or loss. A credit or debit note keeps the invoice's currency and rate but is entered in rupees (`priced_in_foreign` is accepted on a note, but the credit-note screen does not offer it yet). Not built: a live market rate; the rate is typed.
5a. `GET /sales-documents/{id}/pdf` returns the document as a PDF (`application/pdf`; `?download=true` sets `attachment` so the browser saves it) for sending to the customer. `server/app/invoice_pdf.py` draws it from the same document the API returns: supplier and buyer, lines with their tax, totals in words, the LUT or IGST statement and the foreign amount on an export, and the IRN with its QR once generated. A draft carries a DRAFT mark. The web app fetches it with the session token and opens or saves the blob, because a plain link cannot carry the token. Not built yet: sending it by email or WhatsApp (`POST /sales-documents/{id}/send` is in the contract only), the bank and UPI payment details, and the company logo.
5b. Whether an IRN is needed is decided by `server/app/turnover.py`, from the turnover of the years before the document's own: e-invoicing applies from the financial year after turnover first went above ₹5 Cr (counting from 2017-18) and stays on, so a company that crosses the limit in February issues its first IRN on the next 1 April. A year's figure is the one declared as an `aato` fact dated in it (`POST /facts`; onboarding's "turnover last financial year" is dated 31 March of last year, migration 30) or, when none is declared, the taxable value of the sales documents issued in it: invoices, export invoices, bills of supply and debit notes less credit notes. `GET /einvoice-status` returns the level (`clear`, `approaching` from 80% of the limit this year, `crossed`, `applies`), the year it starts and the figure for each year; the engine opens one task a year when turnover reaches 80%. The figure is for one company, not the whole PAN across GSTINs and group companies, so a CA must confirm it. The compliance rule `GST.EINVOICE` still reads the latest `aato` fact and has not been moved to this test.
6. Corrections go through `POST /sales-documents/{id}/credit-notes {cn_reason, lines}`, which drafts a credit note. Issuing it follows step 4, and the settlement reduces the invoice balance.

### Filing a return (e.g. GSTR-1)

1. The engine has already created the obligation (`GET /obligations?status=upcoming`).
2. `POST /obligations/{id}/prepare` returns `202`; the job builds the draft from the issued sales documents.
3. A maker calls `POST /obligations/{id}/submit` to create an approval request, and a different person approves it with `POST /approvals/{id}/approve`.
4. `POST /obligations/{id}/file {signing: evc_otp}` returns `202`. The job pauses at `waiting_for_otp`; the owner calls `POST /jobs/{id}/otp`. The job ends with the ARN; the obligation becomes `filed` and the acknowledgement is stored in the vault.

### Booking the bank statement

A bank line is half of a journal entry; the job is to find the other half. `bank_parse.py` reads each narration into how the money moved (upi, neft, rtgs, imps, auto_debit, transfer, cheque, card, cash, charges, interest), the counterparty (a UPI handle, a beneficiary, a card or deposit number), the remark the sender typed and the reference. Banks write several shapes each, so an unrecognised one falls back to `other` with its words kept. `bank_rules.suggest_classifications` then proposes the other side for every open line, in this order, and only ever as a suggestion:

1. **A document the app already holds** (debits): a vendor payment (same UTR, or same amount within three days), a challan (reference in the narration, or same amount), a payroll run (the amount is an employee's net pay). These were booked when they were paid, so accepting only links the line; posting it again would count the money twice.
2. **A rule the company taught**: `POST /bank-rules`, or `remember` on a classification. A rule states a counterparty, a keyword (whole words, so "rent" does not match "current") and/or a mode, and every stated condition must hold. The most specific rule wins (counterparty 0.95, keyword 0.90, mode 0.70).
3. **The starter list** (`bank_rules.STARTER`): a transfer to a fixed deposit, a card bill, salary, rent, electricity, telephone, bank charges, insurance, PF, ESI, professional tax, interest. A transfer to a deposit or a card payment is not an expense: the card statement carries the spending, and the deposit is an asset. Salary goes to *Salary payable* when the company runs payroll here (the run already booked the expense) and to *Salaries and wages* when it does not. A payment to a director is suggested at 0.55 because a loan, remuneration and a reimbursement are treated differently. The list is a starting point and needs a CA's review.

`POST /bank-transactions/{id}/classify` (with an `Idempotency-Key`) books a line: money out debits the account and credits the bank, money in the reverse, as a `bank` journal in the same transaction as the audit entry. With `remember` it also saves a rule, and with `apply_to_similar` it books every other open line the rule matches. `POST /bank-matches/{id}/accept` does the same for a suggested account, or links a suggested payment, challan or payroll run. A line cannot be booked to its own bank account, a booked line cannot be booked again (409), and a locked period refuses the posting. A refused suggestion is not offered again for that line. Standard accounts the statement needs (fixed deposits, credit card payable, directors' loan, interest income, rent, utilities, insurance) are created on first use. Not built: pairing a transfer between two of the company's own accounts, TDS and GST on the payment, asking for the bill when a payment has none, and a model-assisted suggestion for the lines nothing explains.

### Fixed assets and depreciation

`POST /fixed-assets` (with an `Idempotency-Key`) registers an asset. The category fills in the usual life (Schedule II) and a 5% residual value (none for software); both can be set. `booking` says where the cost already is: `reclassify` moves it from Purchases and expenses to Fixed assets in the same transaction, `already_booked` only records it (the bill went straight to an asset account), and `opening` is an asset from earlier books with the depreciation charged so far and the month-end it is as of. The cost is what is capitalised, without GST that is claimed as credit. Depreciation is straight line (`life_months`) or written down value (`rate_pct`, a year's book value at its start × the rate ÷ 12); the first month is charged for the days from the put-to-use date, and the month of a sale for the days up to the sale. The schedule is in `assets.py` and is pure.

`GET /depreciation/preview?period=YYYY-MM` shows what a run would post, and `POST /depreciation/run` posts one `asset` journal per month not yet posted up to the period, covering all assets: Dr Depreciation, Cr Accumulated depreciation. A second run posts nothing, a month that has not started is refused, and a locked period refuses the journal. `POST /fixed-assets/{id}/dispose` sells or writes off an asset: depreciation up to the month before must be posted (409 `depreciation_pending`), the part month is charged inside the sale journal, and the gain or loss against the book value goes to Profit or loss on sale of assets. The money goes to the bank account, or to `proceeds_account_id`. Standard accounts (fixed assets 1500, accumulated depreciation 1550, depreciation 5300, profit or loss on sale 4960) are created on first use. All of it runs under the `finance` permission. Not built: GST on the sale, income-tax depreciation (blocks of assets), asset-level revaluation or impairment, capitalising a bill line from the bill screen, depreciation on a bill of entry's duty.

### Inventory

Only items marked `track_inventory` (goods) move stock; `POST /items` and `PATCH /items/{id}` set it, and an item with stock on hand cannot stop being tracked. Stock is kept by warehouse (`GET` and `POST /warehouses`; the first movement creates the default one, `MAIN`), and every change is a row in `stock_movements` (migration 34): quantity in or out, the unit cost, the signed value, and the bill, document, bill of entry or adjustment behind it. Movements are append-only; a wrong one is corrected by another. A database trigger keeps the quantity on hand per item and warehouse (`stock_levels`), refuses a date in a locked period whether or not a journal goes with the movement, and a constraint refuses a quantity below zero.

**Valuation is weighted average cost, per item across all warehouses** (`inventory.py`, pure and unit-tested). An item's position is a quantity and a value; a receipt adds both and an issue leaves at value over quantity as it stands then. Taking the last units takes the whole value, so rounding leaves nothing behind. The average is worked out in the order movements are recorded, not re-run by date: a back-dated bill changes the cost of later sales only. It is enough for now because a trading company has one cost per item, it needs no cost layers, and returns and write-offs fall out of the same sum; FIFO needs a layer per receipt and can be added on the same movements table. The value of stock is always the sum of the movements' values, and each of those is posted to the Inventory account in the same transaction, so `GET /stock-valuation?as_of=` (quantity and value from movements dated up to the day) agrees with the ledger; it returns the Inventory account's balance and the `difference` so a mismatch is visible.

What moves stock by itself:

- **Booking a purchase bill** (`POST /purchase-bills/{id}/book`): a line for a tracked item debits Inventory instead of Purchases and comes in at its taxable value, plus its own GST when the bill's credit cannot be claimed. Other lines are booked as before. Stock is received into the default warehouse.
- **A bill of entry linked to that bill** (`POST /bills-of-entry` or `PATCH` with `purchase_bill_id`, or booking a bill that already has one): basic customs duty, the surcharge and other duty (not IGST, which is a credit) are added to the value of the stock the bill brought in, in proportion to the stock lines' share of the bill, as a movement with no quantity: Dr Inventory, Cr Customs duty payable. The share of an item that is already sold out goes to Cost of goods sold. It is posted once per bill of entry; changing the duty afterwards is not re-posted, and the duty payment itself is booked from the bank statement against Customs duty payable.
- **Issuing an invoice** (tax invoice, export invoice, bill of supply): each tracked line leaves the default warehouse at the average cost, and Dr Cost of goods sold, Cr Inventory is posted as an `inventory` journal in the same transaction as the invoice journal. A sale for more than is on hand is refused with 409 `insufficient_stock` and nothing is issued, unless the company allows negative stock (`PUT /inventory/settings`, off by default; only an owner or approver can change it, `finance:approve`). With negative stock the cost is the last known one and is an estimate until the purchase is booked.
- **Issuing a credit note with `cn_reason` `sales_return`**: the goods come back at the cost they left at (the movement of the original invoice line, by `original_line_id` or by item), not at today's average. Other credit notes move no stock. A debit note moves none.

What a person records: `POST /stock-adjustments` (with an `Idempotency-Key`) takes `opening`, `count` or `write_off`. A count gives the quantity physically there and books the difference at the average cost; a write-off takes a quantity out; both need a reason and post against Stock adjustments. Opening stock brings in a quantity at a unit cost, once per item and warehouse, for a company that starts here mid-year: `booking` `post` books Dr Inventory, Cr Opening balance equity, and `in_opening_balances` only records the stock because the opening trial balance (`POST /history/imports`) already carries its value. Left out, it is `in_opening_balances` when an opening trial balance has been imported and `post` when not. `POST /stock-transfers` moves a quantity between two warehouses at the same cost and posts nothing. `GET /stock-items` lists the tracked items with quantity, average cost and value, and `GET /stock-movements` the history. Standard accounts (inventory 1400, cost of goods sold 5050, stock adjustments 5060, customs duty payable 2260, opening balance equity 3900) are created on first use. The catalogue runs under `parties`, the rest under `inventory`.

Not built: batches, serial numbers and expiry; bills of material and manufacturing; multi-warehouse transfers beyond a simple move (no transit, no per-warehouse cost, and the screen does not offer the move yet); linking a dispatch to an e-way bill; FIFO or LIFO; reorder alerts; choosing the warehouse on a bill or an invoice; stock on imported opening invoices and bills (they carry no movement); un-booking a bill. Which costs belong in the value of stock (duty, freight, tax that cannot be claimed) is a starter treatment for a CA to review.

### Uploading a bill of entry

The assessed copy of a bill of entry (the PDF from ICEGATE, with a text layer) can be uploaded instead of typed. `POST /bills-of-entry/read` takes the file as base64, `boe_parse.py` reads it (the figures are matched to the column label they sit under, because customs leaves empty cells out), the file is stored in the vault under Purchases (the same bytes are one document), and the figures come back for a person to check. **Nothing is recorded by reading.** The person confirms with `POST /bills-of-entry` passing `document_id`, which links the PDF to the bill of entry as evidence; `new_supplier_name` adds the overseas supplier (a supplier with the same name, ignoring Co., Ltd and punctuation, is reused).

Read: port, number, date, importer GSTIN and IEC, supplier and country, currency, exchange rate, value (assessable value over the rate, so freight and insurance customs added are included), BCD, SWS, IGST, other duty (total duty less those three, which is how customs' rupee rounding shows up), challan, and the status from the events (assessed; duty paid when the challan has a payment date; cleared once out of charge). `warnings` lists what to check: a GSTIN or IEC that is not this company's, a value that differs from the invoice, duty that does not add up. A scan or a photo has no text layer and is refused with 422 `not_a_bill_of_entry`. Reading a bill that is already recorded links the file to it and returns `existing_boe_id`. Not read: the items (HSN, quantity, duty by item), which stay in the stored PDF; nothing is posted to the ledger.

### Imports that are free of charge (samples)

A sample arrives with nothing to pay the supplier, but the duty and IGST are still paid at the port. Record the bill of entry with `free_of_charge: true`: it then has no purchase bill (400 if one is given) and no supplier payable. The IGST decision is kept per import in `igst_credit_status`: `eligible` (claimed as input credit), `blocked` (not claimed, a cost of the goods) or `undecided`. A bill left without a status is `eligible`, except a free-of-charge one, which starts `undecided` so a person has to look at it. Only the IGST of an eligible bill whose duty is paid counts as credit (`igst_credit`, and `igst_credit_inr` on `/trade/summary`); `igst_undecided_inr` and `igst_not_claimed_inr` report the rest. Whether credit is allowed depends on how the goods are used: section 17(5)(h) of the CGST Act blocks it on goods given away as free samples, while samples kept for the company's own use can carry it. That is a CA's call, so recording a free-of-charge import, and each later change of its decision, opens a task for the advisor ("CA to review: …", source `advisor`, due in a week) and raises a `bill_of_entry.ca_review_needed` event; the audit log keeps the before and after. `PATCH /bills-of-entry/{id}` makes the decision. The screen shows what each choice means before it is saved. Not built: posting the duty (and the IGST when blocked) to the books, and the purpose of the samples as a field of its own.

### Paying an MSME supplier

`GET /msme-payables?breached=false` sorted by `msme_pay_by`, then `POST /vendor-payments` (creates an approval request), approve, then `POST /vendor-payments/{id}/release` returns `202`. The bank call runs on the job, and the bill's `amount_paid` updates.

### Payroll

`POST /payroll-runs {period_month}`, then `PUT …/inputs`, then `POST …/compute` (payslips plus PF, ESI, PT and TDS). Then `POST …/submit` and approve, then `POST …/bank-file` returns `202`. The statutory obligations (TDS on the 7th, PF/ESI on the 15th, PT on the 20th) are linked to the run.

### HR: policy, onboarding, leave and the payroll cycle

The company states its policy once and the rest follows from it, so a month's payroll is: start the run, read the readiness list, compute, send for approval. Migration 35; the maths is in `hr.py` (pure), the database helpers in `hrdb.py`, the routes in `routes_hr.py`. HR runs under the `hr` module (policy, leave types, the handbook, leave decisions and balance adjustments need `hr:approve`); pay stays under `payroll` (the CTC preview, payslips, the run's readiness). Entering a new hire sets a salary, so it needs `hr:write` and `payroll:write`; `GET /hr/people` lists the company's people without anything about pay, and someone without `payroll:read` sees a checklist without the salary.

- **Policy** (`GET` and `PUT /hr/policy`): how a CTC is split (basic as a share of the CTC, HRA as a share of basic, whether the CTC includes the employer's PF, ESI and gratuity), the leave year, the weekly off, whether leave beyond the balance is unpaid or refused, the payroll calendar, and the onboarding checklist. A policy is versioned by `effective_from`, which must be today or later: it is never changed for the past. A company that has saved none is on the starter policy (`is_default`). Leave types (`GET` and `POST /leave-types`) carry the days a year, whether they are granted at the start of the year or earned month by month, and whether they are paid; the first use creates EL, CL, SL and unpaid leave.
- **CTC to salary** (`GET /payroll/ctc-breakup?ctc_annual=`): basic and HRA follow the policy, the employer's PF (12% of basic capped at the wage ceiling, when the company is registered), ESI (while the pay is under the limit) and gratuity (4.81% of basic) come out of the CTC when the policy says it includes them, and the special allowance is whatever is left, so the components add up to CTC / 12 to the paisa. `POST /employees/{id}/salary-structures` without `components` builds them the same way, so a raise needs only the new CTC and a date. A salary entered for an earlier date uses today's policy.
- **Onboarding** (`POST /employees/onboard`): the person, the joining date and the CTC, entered once. It creates the employee as `onboarding` (outside payroll), the salary structure from the policy, and the checklist copied from the policy. Items marked `auto` are read from the record (PAN on file, bank account on file, salary set, handbook acknowledged); the rest are ticked with `PUT /employees/{id}/onboarding/{key}`. `POST /employees/{id}/activate` is refused with 409 `onboarding_incomplete` until every required item is done; after it the person is paid from the joining date.
- **Leave** (`POST /leave-requests`, then `/approve`, `/reject` or `/cancel`): the days are the working days in the range, leaving out the weekly off and `holidays`. A paid type is paid up to the balance; beyond it the rest is loss of pay when the policy allows it and the request is refused with 409 `leave_balance_short` when it does not. The unpaid days are the last days of the request. Two requests of one person cannot overlap (a database constraint). `GET /leave-balances` shows what each person has earned in the leave year, what was added by hand (`POST /leave-adjustments`: an opening balance, a carry-forward, a correction), taken, waiting and left.
- **Payroll**: starting a run (`POST /payroll-runs`) fills the unpaid days of approved leave into its inputs, and approving or withdrawing leave afterwards updates every open run it touches (a computed run goes back to open, because its payslips are out of date). `POST /payroll-runs/{id}/inputs/from-leave` does it again on request; figures typed for the month are kept. A leave with unpaid days cannot be withdrawn once that month's payroll is approved. `GET /payroll-runs/{id}/readiness` lists what stops the run (`blockers`: no salary set) and what to look at first (`warnings`: onboarding not finished, no bank account, no PAN, leave awaiting a decision, unpaid leave changed since the inputs were filled), with the month's joiners and leavers.
- **Payslips**: `GET /employees/{id}/payslips` (approved runs), `GET /payslips/{id}` (lines and the year so far) and `GET /payslips/{id}/pdf` (`payslip_pdf.py`; a payslip from a run that is not approved carries a DRAFT mark; the PAN is masked).
- **Handbook** (`/hr/policy-documents`): policy documents as text, by category. Publishing with an existing slug adds the next version and the earlier one is kept; `POST /hr/policy-documents/{id}/acknowledgements` records that a person has accepted the current version, and a new version is acknowledged afresh.

Not built: an employee's own sign-in (people get their payslips, leave and policies through HR; an employee role needs its own row-level-security design), sending payslips by email, offer and appointment letters, attendance and shift capture, year-end carry-forward and leave encashment, comp-off and half-day sessions, full and final settlement, tax declarations and Form 16, employer PF administration and EDLI charges in the CTC, reimbursement claims. The CTC split, the leave quotas, the gratuity share and the checklist are a starter policy for the company and a CA to review.

### Who does what, and a person's work

A company says once who prepares and who approves each kind of work (`GET` and `PUT /responsibilities`, migration 38; `routing.py`, `routes_work.py`). The kinds are GST, TDS, PF and ESI, professional tax, company filings (MCA), income tax, other filings, the payroll run, vendor payments and leave. Filings have two steps, `prepare` and `approve`; the payroll run, vendor payments and leave have only `approve`, because whoever starts them is the preparer. Anyone in the company can read the matrix; only an owner can change it, and a person can be named approver only where they can approve (leave needs an approving level on `hr`, the payroll run on `payroll`, vendor payments on `purchases`).

The routing is in database triggers, so every path that creates work follows one rule:

- A new obligation takes its owner (`prepare`) and approver (`approve`) from the matrix, unless whoever created it named them. `app.work_kind_of_rule` maps a rule to its kind from its code and authority.
- A request for approval is assigned to the named approver of that kind of work, unless that person raised it: the two-person rule would stop them, so it stays open to the other approvers.
- Saving the matrix moves open filings that nobody owns, or that the matrix's earlier choice still holds, to the person now named. An owner a person chose by hand stays.
- A person invited with a preset takes the work that preset usually does, where nobody holds it yet (an Accountant prepares GST, TDS and income tax; a CFO approves them and payments; an HR Manager prepares PF, ESI and professional tax and decides leave). Removing a person, or taking away the level they need, frees their slots.

With no row for a kind, nothing changes: the work is unowned and any approver may decide it.

`GET /me/work` is the caller's queue across every company they can see, overdue first and then by due date: tasks given to them, filings they own, approvals for them to decide (assigned to them, or unassigned in a company where they can approve, never their own request), leave for them to decide (the named approver, or anyone with an approving level on `hr` when nobody is named) and open document requests in companies where they can write to the vault. `GET /me/calendar` gives the dated items with the companies' holidays, day by day. Tasks (`GET` and `POST /tasks`, `PATCH /tasks/{id}`) are given to a member; the person a task is assigned to can always finish it, and anything else needs `tasks:write`.

Not built: naming a practitioner or a firm's staff in the matrix (it comes with engagements), a `review` or `file` step, reminders and escalation when work sits, reassigning in bulk when someone is away, comments on a task, and the queue on the phone layout's own tab.

### Engaging a practitioner

A CA, CS, auditor or consultant works for a company through an **engagement** (`advisor_grants`, migration 39; `routes_settings.py`, `routes_engagements.py`). Only an owner can engage one or end an engagement.

1. `POST /advisor-grants` with the name, `invite_email`, a `service` (bookkeeping, GST, TDS, PF and ESI, payroll, secretarial, trade, statutory audit, tax audit, internal audit, virtual CFO, legal) and either a practitioner `preset` or `scopes` in the practitioner's words (`read`, `comment`, `prepare`, `certify`). Settings can never be given, and neither can an approving level: approving and paying stay with the company. The engagement starts as `invited`. Without an email it is only a record.
2. The person invited cannot see the company yet. `GET /me/engagements` lists their invitations (read through a database function that reaches exactly those rows) and the firms they are a partner or admin of.
3. `POST /me/engagements/{id}/accept` starts access. Accepting as oneself makes it a personal engagement; passing `organization_id` makes it the firm's. `POST /me/engagements/{id}/decline` refuses it. Both are written to the audit log by the database, because someone who declines never gets to see the company.
4. `DELETE /advisor-grants/{id}` withdraws an invitation or ends the engagement at once. An engagement past its `expires_on` gives no access.

What a practitioner may do is checked in `require()` from `app.my_grant_scopes`: the engagements that reach them, as a person or through their firm. `/me` shows such a company with role `advisor`, home `practitioner` and the service as the title.

- **Audit mode.** A statutory, tax or internal audit engagement can hold only `read` and `comment`, and must have an end date. It is a table constraint (400 `audit_read_only`), not a convention, and an auditor is never offered in "who does what".
- **Independence.** The same firm or person cannot hold the statutory audit and the bookkeeping, internal audit or virtual CFO engagement of one company for the same year (a trigger; 409 `independence_conflict`, at invitation or at acceptance, whichever first knows who the practitioner is). Which services an auditor may not provide is set by the Companies Act and the ICAI rules: this list is a starter rule for a CA to confirm, and it does not catch a partner taking one engagement as a person and the other through their firm.
- **A firm's staff see only the clients they are assigned to** (`firm_client_staff`; `GET /organizations/{id}/client-staff`, `PUT /organizations/{id}/client-staff/{entityId}`). Partners and admins see every client, whether it engaged the firm or the firm set it up. Only a partner or admin assigns, and someone taken off a client stops seeing it at once. Staff who existed when migration 39 ran were assigned to every client their firm had, so nobody lost access; staff added later see nothing until assigned. A business group's own people are not affected.
- **Work.** A practitioner engaged as a person can be named to prepare a kind of work and be given tasks, and takes on what their preset usually prepares when they accept (a GST consultant prepares GST). A firm's engagement names nobody: the firm routes its own staff.

Not built: sending the invitation by email, the engagement letter and UDIN on certificates, verifying the membership number, fees and billing, a practitioner asking a company for an engagement, and the directory (phase 7 of `docs/ROLES_DESIGN.md`). Module limits are still checked in the API, not in row-level security: that is the next phase and should land before practitioners outside a company's trusted circle are let in.

### Copilot

1. `POST /copilot/threads` starts a conversation (only its owner can see it). `POST /copilot/threads/{id}/messages` returns the answer as JSON, or with `Accept: text/event-stream` streams `token`, `citation`, `action` and `done` events.
2. The model reads only through the caller's permissions, under the same row-level security.
3. Actions are proposals. `POST /copilot/actions/{id}/request-approval` creates an approval request (subject `copilot_action`), and nothing executes until a different person approves it from the approvals inbox. The database enforces this: an action cannot be `approved` or `executed` without an approval request, and the requester can never decide it. On approval the action runs in the same transaction (`send_supplier_reminders`, `create_task`); if it cannot run it is marked `failed` and the approval stands.
4. The model is the `copilot` adapter. It is handed read-only tools (open filings, supplier invoices missing from GSTR-2A, unpaid customer invoices) that run on the caller's own session, and it returns text with `[n]` markers, the citations behind them and proposed actions. The sandbox adapter is rule-based and is refused outside development; a real model is a provider decision (`docs/PROVIDERS.md`).

### CA firm

`GET /organizations/{id}/entities?filter=waiting_on_client` returns the client × authority matrix. `GET /organizations/{id}/workload` returns staff load. `POST /organizations/{id}/client-reminders` queues an email reminder to the owners of each blocked client (never to the firm's own staff), skips a client reminded in the last 24 hours, and returns who was reminded and who was skipped and why. A firm adds a client through the same onboarding flow, with `organization_id` set.

---

## 5. Screen → endpoint map

| Screen | Endpoints |
|---|---|
| Sign in / Create workspace | `POST /auth/otp`, `POST /auth/signup`, `POST /auth/token`, `POST /onboarding/lookups` |
| Onboarding | `GET /onboarding/lookups/{id}`, `POST /onboarding/preview`, `POST /onboarding/workspaces` |
| Command Center | `GET /me/engagements`, `POST /me/engagements/{id}/accept`, `POST /me/engagements/{id}/decline`, `GET /me/work`, `POST /tasks`, `PATCH /tasks/{id}`, `GET /compliance/summary`, `GET /approvals?assigned_to=me`, `GET /compliance/calendar`, `GET /compliance/standing`, `GET /finance/summary`, `GET /audit-log?limit=5`, `/events` |
| Compliance Engine | `GET /obligations`, `GET /obligations/{id}`, `GET /compliance/calendar`, `GET /compliance/thresholds`, `GET /notices`, `POST /compliance/engine-runs` |
| Exports & imports | `GET /trade/summary`, `GET /shipping-bills`, `POST /shipping-bills`, `GET /shipping-bills/{id}`, `PATCH /shipping-bills/{id}`, `POST /shipping-bills/{id}/realisations`, `GET /bills-of-entry`, `POST /bills-of-entry`, `POST /bills-of-entry/read`, `PATCH /bills-of-entry/{id}`, `GET /sales-documents?doc_type=export_invoice`, `POST /registrations` (the LUT and IEC) |
| Startup workspace | `GET /cap-table` (`scenario=current` or `post_round&round_id=…`), `POST /share-classes`, `POST /shareholders`, `GET /funding-rounds`, `POST /funding-rounds`, `PATCH /funding-rounds/{id}` (steps come with the round), `POST /allotments`, `GET /esop-pools`, `POST /esop-pools`, `GET /esop-grants`, `POST /esop-grants`, `GET /benefits`, `POST /benefits/check`, `PUT /benefits/{scheme_code}`, `GET /finance/summary`, `GET /obligations` |
| MSME workspace | `GET /msme/late-buyers`, `GET /msme/classification`, `GET /receivables/ageing`, `GET /msme-payables`, `GET /delayed-payment-claims`, `POST /delayed-payment-claims`, `POST /delayed-payment-claims/{id}/file`, `GET /treds-invoices`, `POST /treds-invoices`, `PATCH /treds-invoices/{id}`, `GET /benefits`, `POST /benefits/check`, `PUT /benefits/{scheme_code}`, `POST /facts`, `PATCH /entities/{id}` (Udyam registration), `GET /obligations` |
| Copilot (web + mobile) | `GET /copilot/threads`, `POST /copilot/threads`, `GET /copilot/threads/{id}`, `POST /copilot/threads/{id}/messages` (JSON or SSE), `POST /copilot/actions/{id}/request-approval`, then `POST /approvals/{id}/approve` |
| Finance & Books | `GET /finance/summary`, `GET /periods`, `GET /periods/{id}/close-tasks`, `PATCH …/close-tasks/{taskId}`, `POST /history/imports`, `GET /history/batches`, `POST /history/batches/{id}/undo`, `POST /bank-accounts/{id}/transactions/preview`, `POST /bank-accounts/{id}/transactions`, `GET /bank-transactions?match_status=suggested`, `POST /bank-transactions/{id}/export-proceeds`, `POST /bank-accounts/{id}/transactions`, `POST /bank-matches/{id}/accept`, `POST /bank-matches/{id}/reject`, `POST /bank-transactions/{id}/classify`, `POST /bank-transactions/suggest`, `GET /bank-rules`, `POST /bank-rules`, `DELETE /bank-rules/{id}`, `GET /reports/profit-and-loss`, `GET /reports/cash-bridge`, `POST /periods/{id}/lock` |
| Payroll & HR | `GET /payroll-runs`, `POST /payroll-runs`, `POST /payroll-runs/{id}/compute`, `POST /payroll-runs/{id}/submit`, `GET /payroll-runs/{id}`, `GET /employees`, `POST /employees`, `POST /employees/{id}/salary-structures`, `GET /payroll-runs/{id}/inputs`, `PUT /payroll-runs/{id}/inputs`, `POST /payroll-runs/{id}/bank-file`, `GET /payroll-runs/{id}/payslips`, `GET /payroll-runs/{id}/readiness`, `GET /payslips/{id}/pdf`, `GET /employees/{id}/payslips`, `GET /payroll/wage-code-check`, `GET /payroll/labour-compliance`, `GET /obligations?authority=EPFO` |
| New hires | `GET /employees?status=onboarding`, `POST /employees/onboard`, `GET /payroll/ctc-breakup`, `GET /employees/{id}/onboarding`, `PUT /employees/{id}/onboarding/{key}`, `PATCH /employees/{id}`, `POST /employees/{id}/activate` |
| Leave | `GET /hr/people`, `GET /leave-requests`, `POST /leave-requests`, `POST /leave-requests/{id}/approve`, `POST /leave-requests/{id}/reject`, `POST /leave-requests/{id}/cancel`, `GET /leave-balances`, `GET /leave-types`, `GET /holidays`, `POST /holidays`, `DELETE /holidays/{id}` |
| HR policy | `GET /hr/policy`, `PUT /hr/policy`, `GET /hr/policy-documents`, `POST /hr/policy-documents`, `GET /hr/policy-documents/{id}`, `POST /hr/policy-documents/{id}/acknowledgements` |
| TDS & Advance tax | `GET /tds-deductions`, `GET /challans`, `POST /challans`, `POST /challans/{id}/pay`, `GET /tds-returns`, `POST /tds-returns/{id}/validate`, `GET /advance-tax`, `GET /tds-receivables`, `POST /tds-receivables/sync` |
| GST & Tax | `GET /gst-returns?period=…`, `GET /itc-matches`, `GET /itc-matches/summary`, `POST /itc-matches/{id}/resolve`, `POST /itc-matches/remind-suppliers`, `POST /gst-returns/{id}/compute`, `POST /gst-returns/{id}/file`, `POST /gst/inward-sync`, `GET /gst/ledger-balances`, `GET /tds-deductions`, `GET /advance-tax` |
| Legal & Contracts | `GET /contracts?notice_within_days=30`, `GET /contracts/{id}`, `POST …/extract-terms`, `POST …/notices`, `GET /signature-requests`, `GET /contract-templates`, `GET /documents?q=…`, `GET /documents?expiring_within_days=90`, `GET /document-requests` |
| Document Vault | `GET /documents?q=…&folder=…`, `POST /documents` (signed upload), `GET /documents?expiring_within_days=90`, `GET /document-requests`, `POST /document-requests/{id}/share`, `GET /data-rooms`, `POST /data-rooms`, `PATCH /data-rooms/{id}`, `DELETE /data-rooms/{id}` |
| Group view | `GET /organizations/{id}/entities`, `GET /organizations/{id}/workload`, `GET /organizations/{id}/client-staff`, `PUT /organizations/{id}/client-staff/{entityId}`, `POST /organizations/{id}/client-reminders` |
| Settings | `/members` (+ `PATCH /members/{id}`), `GET /role-presets`, `GET /responsibilities`, `PUT /responsibilities`, `/advisor-grants`, `/dsc-certificates`, `/portal-connections` (+ `/portal-connections/{id}/renew`), `/me/notification-preferences`, `/webhooks` (+ `/webhooks/{id}/test`, `/webhooks/{id}/deliveries`), `/api-keys`, `/audit-log`, `/audit-log/verify` |
| Sales & Billing | `GET /sales/summary`, `GET /einvoice-status` (E-invoicing card), `POST /facts` (declare a year's turnover), `GET /sales-documents?payment_status=…`, `GET /dunning-sequences`, `GET /recurring-invoices` |
| New GST invoice | `POST /sales-documents/preview` (every change), `POST /sales-documents`, `POST /sales-documents/{id}/issue`, `GET /sales-documents/{id}/pdf` (View PDF, Download PDF) |
| Collections | `GET /receivables/ageing`, `GET /receipts`, `POST /receipts`, `POST /receipts/{id}/allocate`, `GET /dunning-sequences`, `POST /parties/{id}/pause-dunning`, `GET /recurring-invoices`, `PATCH /recurring-invoices/{id}` |
| Quotes | `GET /quotes`, `POST /quotes`, `POST /quotes/{id}/send`, `POST /quotes/{id}/mark-lost`, `POST /quotes/{id}/revise`, `POST /quotes/{id}/accept`, `POST /sales-orders/{id}/invoice` |
| Credit note | `POST /sales-documents/preview {doc_type: credit_note}`, `POST /sales-documents/{id}/credit-notes`, `POST …/issue` |
| Customer payment page | `GET /pay/{shortCode}`, `POST /pay/{shortCode}/intents`, `POST /pay/{shortCode}/queries` |
| Inventory | `GET /stock-items`, `GET /stock-movements`, `POST /stock-adjustments`, `GET /stock-valuation`, `POST /items`, `PUT /inventory/settings` |
| Fixed assets | `GET /fixed-assets`, `POST /fixed-assets`, `GET /fixed-assets/{id}`, `POST /fixed-assets/{id}/dispose`, `GET /fixed-asset-categories`, `GET /depreciation/preview`, `POST /depreciation/run` |

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
- startup track: share classes and holders, allotments (60-day rule enforced by the database; a PAS-3 task due in 15 days and, for a non-resident holder, an FC-GPR task due in 30), the fully diluted cap table from the allotted shares plus ESOP pools, a post-round scenario (price = pre-money over today's fully diluted shares; new shares rounded down), funding rounds with their six steps worked out from the recorded status and dates, ESOP grants with cliff and vesting, and Startup India scheme eligibility checked against company facts (`routes_capital.py`, `captable.py`). The timelines and eligibility tests are a starter library for a CA or CS to review
- copilot: conversations per person, answers from the company's own records with a citation behind every fact, and proposed actions that run only after a different person approves them (`routes_copilot.py`; sandbox model in `adapters.py`)
- export flow: a customer outside India (`POST /parties` with a `country` and a `currency`, no GSTIN) turns a tax invoice into an `export_invoice` when it is previewed or saved (`routes_sales.py: export_terms`): place of supply `96`, the foreign currency and the exchange rate on the document, the foreign total worked out by the database (`sales_documents.foreign_total`, migration 26), and the supply under LUT when a valid LUT (`POST /registrations`, kind `lut`) covers the date, with payment of IGST when not. The invoice itself stays in rupees; GSTR-1 reads `supply_type` into its exports table. Issuing an export invoice with goods opens a draft shipping bill (foreign value of the goods at the invoice rate) and a task; `PATCH /shipping-bills/{id}` with the number, port and date customs issued files it, and recording a shipping bill against the invoice fills its draft instead of adding a second. Services open no shipping bill. A credit or debit note to a customer abroad must correct an export invoice (`export_note_needs_invoice` otherwise) and follows it: its currency and exchange rate, no GST when the invoice was supplied under LUT and IGST when it was exported with payment of tax, whatever the LUT says today; it opens no shipping bill and does not change the one already filed. Proceeds from abroad (`POST /shipping-bills/{id}/realisations` with `amount_inr`, the rupees the bank credited, and optionally `bank_charges` and `bank_account_id`; migration 27) create a receipt, settle the export invoice at its own rate and post the difference as foreign exchange gain or loss (`ledger.export_proceeds_lines`; it needs an `Idempotency-Key` and an issued export invoice behind the shipping bill, `proceeds_need_invoice` otherwise). A partial shipment files its share: when a draft is filed for less than the goods still to ship, a draft for the rest opens (`routes_sales.reconcile_shipments`), and together the shipping bills of an invoice cannot exceed its goods (`shipments_exceed_invoice`). Goods credited back by a credit note shrink or remove the draft; when only a filed shipping bill is left a task says to have it amended with customs. The IRN job sends the portal the export supply type (`EXPWOP` or `EXPWP`), buyer `URP`, place of supply 96, the currency, the buyer's country and the shipping bill once customs has issued it (`trade.irp_export_details`; the sandbox ignores them). A bank statement line is matched to the proceeds loosely, because the bank shows the rupees after its charges and its own rate, usually as an inward remittance with no invoice number. Importing lines runs `routes_finance.suggest_export_proceeds`: a credit that no invoice claimed, and that reads like money from abroad (inward, remittance, FIRC, SWIFT, a currency code), is ranked against shipping bills still owed by `trade.rank_proceeds` on the narration, the remitter's name, a quoted invoice or shipping bill number, a foreign amount in the narration, and a credit within 10% of the rest of the invoice at its own rate. The suggestion (`target_kind` `shipping_bill`, confidence never above 0.95) carries an `export` hint with the guessed foreign amount and how far the credit is from it; nothing is posted. `POST /bank-transactions/{id}/export-proceeds` takes the foreign amount, the charges and the FIRC, and records the money exactly as the shipping bill screen does, with the receipt tied to the bank line (migration 28 allows the new match kind). A refused suggestion is not offered again for that line. The LUT is also a yearly compliance duty (rule `GST.LUT.ANNUAL`, migration 29): an entity is an exporter when it holds an IEC, has a customer outside India or has an invoice in a foreign currency, and then the duty is due on 1 April of each financial year, appears ahead of it, and stays open (overdue) until a LUT valid for the whole year is recorded under `POST /registrations`, which files it against that LUT's number. A LUT that has expired leaves the new year uncovered, so the duty is overdue. Not built yet: unrealised forex revaluation at period end, refund of IGST or ITC on exports, bulk shipments, and the bank's separate charges line. The LUT rule and the zero-rating of exports are a starter library for a CA to review.
- exports and imports: shipping bills (rupee value worked out in the database from the foreign amount and the customs rate), the date of export that starts the nine-month clock for bringing proceeds home (with a task due on the last day), money received against each bill (never more than it is worth), RoDTEP and drawback claims and the e-BRC reference, bills of entry with the duty assessed and the IGST credit once the duty is paid, and the LUT read from the company's LUT registration (`routes_trade.py`, `trade.py`, migration 24). A person records what customs and the bank issued: nothing calls ICEGATE, DGFT or a bank, and nothing posts to the ledger yet. The nine-month period and how it starts are a starter rule for a CA or CS to review
- module map (`/modules`): the product's 27 modules by layer and track, linking only to screens that exist, with the rest marked planned, and the outside systems marked sandbox or planned
- msme track: buyers past the day the MSMED Act required payment (invoice date plus the agreed terms, at most 45 days, 15 with no agreement) with the interest owed (compound, monthly rests, three times the RBI bank rate from `ref_rbi_bank_rate`, migration 23; micro and small suppliers only), claims for delayed payment drafted from the invoices and filed on MSME Samadhaan as a job (`samadhaan` adapter, sandbox here), TReDS uploads recorded through to settlement, the Udyam classification tests, recording company facts (`GET` and `POST /facts`, which re-runs the rules) and the Udyam registration (`routes_msme.py`, `msme.py`). The 45 and 15 day limits, the rate multiple, the Udyam limits and the bank-rate history are a starter library: a CA or CS must check them, and a row must be added to `ref_rbi_bank_rate` whenever RBI changes the rate
- opening position from earlier books: an opening trial balance (one `opening` journal), and the invoices and bills still open on the cut-off date, checked row by row in a dry run and saved all or nothing as a batch that can be undone while nothing has been received or paid against it (`routes_history.py`, `history.py`, migration 25). Imported invoices carry no e-invoice and no ledger posting of their own (the opening journal holds the balance); imported bills are marked as input credit already claimed in the month of the bill, so they cannot be claimed again. Past GST returns and a full invoice-by-invoice history are not rebuilt
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
