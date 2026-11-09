# Biz360X web

Next.js (App Router, TypeScript) front end for the API in `server/`. No UI library: the design tokens from `design/README.md` are CSS variables in `src/app/globals.css`.

```bash
cd web
npm ci
npm run dev            # http://localhost:3000, forwards /api/v1/* to the API on :8000
API_URL=https://api.example.com npm run build && npm start
npm run gen:api        # regenerate src/api/schema.d.ts after the contract changes (CI fails if it is stale)
npm run typecheck && npm test
```

## Browser tests

`web/e2e` holds Playwright tests that drive the real app in Chromium against the real stack (PostgreSQL with the demo data, API, worker). `smoke.e2e.ts` signs in and opens every screen, failing on an error banner, an uncaught page error or a 5xx from the server. `flows.e2e.ts` runs the key flows end to end: issue an invoice, file a GSTR-1 with a wrong then the right one-time code, create and delete a data room, add a client to a firm. The tests change the demo data, so they always run on a freshly reset database.

```bash
powershell -ExecutionPolicy Bypass -File scripts/e2e-local.ps1          # Windows with WSL: does everything and cleans up
powershell -ExecutionPolicy Bypass -File scripts/e2e-local.ps1 -g invoice   # only tests whose name matches
```

On Linux, run `bash scripts/e2e-stack.sh start`, build and start the web app (`npx next build && npx next start -p 3000`), then `npx playwright install chromium && npm run e2e`, and `bash scripts/e2e-stack.sh stop` afterwards. CI does this in the `e2e` job and keeps the report when it fails.

Start the API first (see `CLAUDE.md`). Registry lookups are jobs, so for a company that is not already cached also run the worker (`python -m app.worker`); the development registry knows `U72900MH2022PTC999999` (Orchid Labs), which has no demo workspace. In development sign in with any seeded email, for example `ananya@example.in`, and the code `000000`.

## What is here

| Path | Purpose |
|---|---|
| `src/api/schema.d.ts` | Generated from `api/openapi.yaml`. Do not edit. |
| `src/lib/api.ts` | Typed client (`openapi-fetch`), sign-in calls, token refresh, `unwrap` for problem+json errors |
| `src/lib/session.tsx` | Who is signed in, the entity switcher (remembered in localStorage) |
| `src/lib/format.ts`, `compliance.ts` | Pure helpers (Indian money grouping on strings, never floats; statuses; KPIs; calendar grouping). Unit-tested |
| `src/components/Shell.tsx`, `nav.ts` | Sidebar, entity switcher, route guard. `nav.ts` marks which screens are built |
| `src/app/login` | One-time-code sign-in (authenticator step for MFA users) and **Create workspace**, which signs a new person up |
| `src/app/onboarding` | Four steps: look up a CIN/LLPIN/GSTIN/PAN, confirm the company and its GSTINs, answer four questions with a live obligation preview, then create the workspace |
| `src/lib/onboarding.ts` | Identifier formats, answer bands, hint text. Unit-tested |
| `src/app/(app)/page.tsx` | Today (Command Center) |
| `src/app/(app)/compliance` | Obligations with the reason each applies, calendar, thresholds, re-run engine |
| `src/app/(app)/billing` | Sales & Billing: KPIs, invoice list with tabs and search, issue a draft, how customers paid |
| `src/app/(app)/billing/new` | Invoice editor. Every change calls `POST /sales-documents/preview`, so the tax shown is the server's, never recomputed in the browser |
| `src/app/(app)/billing/[id]` | One document: lines, totals, e-invoice state, issue a draft, start a credit note |
| `src/app/(app)/billing/[id]/credit-note` | Credit note: reason, quantity per item, live preview from the server, save as draft or issue |
| `src/app/(app)/billing/quotes` | Quotes list with send, new version, accept, mark lost and create invoice from an accepted quote |
| `src/app/pay/[code]` | The customer payment page: no sign-in, no app shell. Invoice summary, bank-transfer details, payment methods, raise a query. Talks to the public `/pay` endpoints without a token (`src/lib/pay.ts`) |
| `src/app/(app)/startup` | Startup track: cash, burn, runway, team and ESOP tiles; the funding round as six steps you can advance; the fully diluted cap table with a Today / After the round switch; ESOP pools and vesting; Startup India benefits checked against your facts, with a place to record an application; a founder calendar; an investor update drafted from the books; and forms to set up classes, holders, allotments, rounds, pools and grants |
| `src/lib/startup.ts` | Share-count grouping, bar widths, round headline and next status, ESOP summary, benefit wording, setup form rules and the investor update text. Unit-tested in `startup.test.ts` |
| `src/app/(app)/copilot` | Copilot: your conversations, the answer with `[n]` buttons that select the record behind each fact, a Sources panel that links to it, and proposed actions that you send for approval (a different person approves them on Today) |
| `src/lib/copilot.ts` | Answer parsing into lines and citation markers, source links, action wording and state, conversation grouping. Unit-tested |
| `src/app/(app)/msme` | MSME track: buyers past the due day with the interest owed under the MSMED Act, claims for delayed payment (draft, then filed on MSME Samadhaan as a background job), TReDS uploads you move forward, receivables ageing, what you owe small suppliers, schemes you qualify for, the Udyam classification bars with a form for your figures and your Udyam registration |
| `src/lib/msme.ts` | Lakh and crore wording, headline, ageing bars, buyer actions, claim and TReDS states and form rules, Udyam bars and the facts form. Unit-tested in `msme.test.ts` |
| `src/app/(app)/finance/import` | Import a bank statement saved as CSV: the file is read in the browser, shown with what was recognised, the money in and out and whether the running balance agrees, checked against the account for lines already there, then imported in batches of 1000. Lines already imported are skipped |
| `src/lib/statement.ts` | The CSV reader: header detection, day-first Indian dates, debit/credit or signed amounts, a stable key per line, UTR extraction, balance check. Unit-tested in `statement.test.ts` |
| `src/app/(app)/finance/history` | Opening position from earlier books: load an opening trial balance, the invoices still open and the bills still open from CSV on a cut-off date, check each file (every problem by spreadsheet row), import, and undo a whole import |
| `src/lib/history.ts` | The CSV readers and templates for those three files, with loose header matching. Unit-tested in `history.test.ts` |
| `src/app/(app)/more` | The phone's More tab: switch entity, every module, account. On screens up to 820px wide the sidebar gives way to a top bar and a bottom tab bar (Today, Compliance, Copilot, More) |
| `src/app/(app)/board` | Board and resolutions: schedule meetings, mark them held with quorum, draft resolutions and move them to circulated, passed, failed or withdrawn (a passed special resolution raises MGT-14) |
| `src/app/(app)/notices` | Notice desk: record a notice from an authority, see reply dates and demand, draft a reply from your own records (background job, saved to the vault), move it through replied, hearing, appealed and closed |
| `src/app/(app)/billing/receivables` | Collections: ageing by customer, record a receipt with allocation, allocate what is left of an advance, pause reminders, see the reminder sequence, pause or resume recurring invoices |
| `src/app/(app)/billing/quotes/new` | New quote; totals come from the invoice preview, so quote and invoice tax agree |
| `src/lib/billing.ts` | Tab queries, status labels, line validation, request bodies. Unit-tested |
| `src/app/(app)/finance` | Finance & Books: key numbers (`GET /finance/summary`), close checklist and month lock, bank reconciliation (accept or reject suggested matches), profit & loss in lakh |
| `src/app/(app)/tds` | TDS & Advance tax: deducted vs on a challan, record and pay challans (background job), validate quarterly returns (shows what blocks them), advance tax instalments, Form 26AS match |
| `src/lib/tds.ts` | Financial year, challan form rules and body, totals, instalment state, validation wording. Unit-tested |
| `src/app/(app)/gst` | GST & Tax: returns by GSTIN with Recompute and File (background jobs; filing stops for the one-time code), purchase register vs GSTR-2B with fetch-and-match, settle each difference, remind suppliers, electronic ledger balances |
| `src/lib/gst.ts` | Which actions each kind of difference offers, month and request bodies, wording. Unit-tested |
| `src/lib/finance.ts` | Period choice, lakh display, GST ledger totals. Unit-tested |
| `src/app/(app)/payroll/employees` | Employees: list and search, add an employee (bank account stored encrypted), set or revise a salary structure |
| `src/app/(app)/payroll/inputs/[runId]` | Days without pay and one-off amounts for the month, then compute |
| `src/app/(app)/payroll` | Payroll & HR: run status and progress, start a run, compute, send for approval, prepare the bank file once approved, salary register with wage-code filter, statutory deposits, labour compliance |
| `src/lib/payroll.ts` | Run naming, next month, next step, status labels, employee, salary and input form rules. Unit-tested |
| `src/app/(app)/legal` | Legal & Contracts: register ordered by urgency, key terms and alerts for the selected contract, out for signature, templates |
| `src/app/(app)/documents/rooms` | Data rooms: create a room from vault documents, name the outside viewers, set expiry, watermark and download rules, change or delete it |
| `src/app/(app)/documents` | Document vault: folder and full-text search, expiring soon, advisor requests. Upload comes later, add a document (the browser hashes the file and sends the bytes through the app's own origin), edit a document, share documents against an advisor's request |
| `src/lib/legal.ts` | Contract dates and urgency, file and expiry labels. Unit-tested |
| `src/app/(app)/settings` | Settings: people (invite, remove), advisors (grant, revoke), signature certificates (record), API keys (create, revoke; the key is shown once), portals (connect, OTP renewal), webhooks, notification choices (saved per person), audit-chain check |
| `src/app/(app)/group` | Group view for firms: clients × authorities grid, filters, open items per client, team load, Add client (the onboarding flow filed under the firm) and Chase clients (queues reminders to client owners) |
| `src/lib/group.ts` | Chase targets and wording, the add-client link. Unit-tested |
| `src/components/IntegrationsPanels.tsx` | Portal connections (connect, renew with the OTP flow, disconnect) and webhooks (add, pause, test, deliveries, delete) |
| `src/components/SettingsForms.tsx` | The invite, advisor, certificate and API key forms |
| `src/lib/board.ts` | Contract, meeting and resolution form rules, IST display of meeting times. Unit-tested |
| `src/lib/vault.ts` | File hashing, upload body, same-origin upload address, edit rules. Unit-tested |
| `src/lib/notices.ts` | Notice wording, reply urgency, desk totals, form rules. Unit-tested |
| `src/lib/rooms.ts` | Data room form rules, viewer list parsing, expiry and viewer wording. Unit-tested |
| `src/lib/settings.ts` | Form rules, notification matrix, certificate status, grid ordering. Unit-tested |
| `src/app/(app)/m/[slug]` | Placeholder for screens that are not built yet |

## Rules for new screens

- Call the API only through `api` and `unwrap`. Never build URLs by hand and never write a response type by hand when the contract has one.
- Money is a string. Use `formatInr` and `sumMoney`; never `Number()` an amount.
- Writes that need an `Idempotency-Key` send `newIdempotencyKey()`. Show a problem's `title`; map codes such as `two_person_rule` to plain words.
- A screen must work at 375 px wide and be usable by keyboard. Amber is only for "needs action".
- The API is the authority on permissions. The UI may hide what a role cannot do, but must handle a 403 or 404 anyway.

## Tokens

The access token lives in memory; the refresh token is in `sessionStorage` (it survives a reload, not a closed tab). Refreshing is single-flight, because the server ends the whole session if a refresh token is used twice.

## Not built yet

Advisor invites at the end of onboarding, the incorporation checklist for companies that do not exist yet, editing reminder steps, creating recurring invoices, finance, GST, payroll, legal, documents, group view, settings, the mobile Copilot screen. The design for each is in `design/screens/`.
