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

Start the API first (see `CLAUDE.md`). In development sign in with any seeded email, for example `ananya@example.in`, and the code `000000`.

## What is here

| Path | Purpose |
|---|---|
| `src/api/schema.d.ts` | Generated from `api/openapi.yaml`. Do not edit. |
| `src/lib/api.ts` | Typed client (`openapi-fetch`), sign-in calls, token refresh, `unwrap` for problem+json errors |
| `src/lib/session.tsx` | Who is signed in, the entity switcher (remembered in localStorage) |
| `src/lib/format.ts`, `compliance.ts` | Pure helpers (Indian money grouping on strings, never floats; statuses; KPIs; calendar grouping). Unit-tested |
| `src/components/Shell.tsx`, `nav.ts` | Sidebar, entity switcher, route guard. `nav.ts` marks which screens are built |
| `src/app/login` | One-time-code sign-in, with the authenticator step for users who have MFA |
| `src/app/(app)/page.tsx` | Today (Command Center) |
| `src/app/(app)/compliance` | Obligations with the reason each applies, calendar, thresholds, re-run engine |
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

Create workspace and onboarding (needs `POST /onboarding/workspaces` on the server), the invoice editor and billing screens, finance, GST, payroll, legal, documents, group view, settings, mobile layouts for Today/Compliance/Copilot/More, and the customer payment page. The design for each is in `design/screens/`.
