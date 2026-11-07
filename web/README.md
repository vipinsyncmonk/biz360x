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
| `src/app/(app)/more` | The phone's More tab: switch entity, every module, account. On screens up to 820px wide the sidebar gives way to a top bar and a bottom tab bar (Today, Compliance, Copilot, More) |
| `src/app/(app)/billing/receivables` | Collections: ageing by customer, record a receipt with allocation, allocate what is left of an advance, pause reminders, see the reminder sequence, pause or resume recurring invoices |
| `src/app/(app)/billing/quotes/new` | New quote; totals come from the invoice preview, so quote and invoice tax agree |
| `src/lib/billing.ts` | Tab queries, status labels, line validation, request bodies. Unit-tested |
| `src/app/(app)/finance` | Finance & Books: key numbers (`GET /finance/summary`), close checklist and month lock, bank reconciliation (accept or reject suggested matches), profit & loss in lakh |
| `src/app/(app)/gst` | GST & Tax: returns by GSTIN, purchase register vs GSTR-2B summary, electronic ledger balances |
| `src/lib/finance.ts` | Period choice, lakh display, GST ledger totals. Unit-tested |
| `src/app/(app)/payroll` | Payroll & HR: run status and progress, start a run, compute, send for approval, salary register with wage-code filter, statutory deposits, labour compliance |
| `src/lib/payroll.ts` | Run naming, next month, next step, status labels. Unit-tested |
| `src/app/(app)/legal` | Legal & Contracts: register ordered by urgency, key terms and alerts for the selected contract, out for signature, templates |
| `src/app/(app)/documents` | Document vault: folder and full-text search, expiring soon, advisor requests. Upload comes later |
| `src/lib/legal.ts` | Contract dates and urgency, file and expiry labels. Unit-tested |
| `src/app/(app)/settings` | Settings: people, advisors, signature certificates, connected portals, notification choices (saved per person), audit-chain check |
| `src/app/(app)/group` | Group view for firms: clients × authorities grid, filters, open items per client, team load |
| `src/lib/settings.ts` | Notification matrix, certificate status, grid ordering. Unit-tested |
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

Advisor invites at the end of onboarding, the incorporation checklist for companies that do not exist yet, editing reminder steps, creating recurring invoices, finance, GST, payroll, legal, documents, group view, settings, the mobile Copilot screen and the customer payment page. The design for each is in `design/screens/`.
