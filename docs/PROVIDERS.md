# Choosing the portal providers

Biz360X talks to government and bank systems only through the adapters in `server/app/adapters.py`. Today every adapter is a
sandbox. Going live means choosing providers and writing one class per interface. **This choice is open** (issue #39) and needs a
decision from the business; this page is the brief for it.

## What has to be decided

| Interface | What the code calls | What a provider must offer | Typical sources |
|---|---|---|---|
| `Gsp` | `fetch_inward`, `file_return` (with `signing` and `otp`) | GSTR-1 / 3B / 9 save, submit and file; GSTR-2A / 2B fetch; EVC or DSC signing; the OTP round trip | A GST Suvidha Provider (GSP) or an ASP that sits on one. GSTN licenses GSPs; an ASP is a customer of one |
| `Irp` | `generate_irn`, `cancel_irn` | IRN, signed QR and acknowledgement; cancellation within 24 hours | Usually the same vendor as the GSP |
| `Eway` | `generate`, `update_vehicle` | Part A and Part B, validity by distance | Same vendor, or the e-way portal API through an ASP |
| `Mca` | `lookup`, `file_form` | Master data by CIN, LLPIN, GSTIN or PAN; MCA V3 form filing with a DSC | A data vendor for lookups; filing is mostly browser automation with a human DSC |
| `Traces` | `fetch_26as`, `upload_return`, `pay_challan`, `fvu_validate` | Form 26AS / AIS, TDS return upload, challan payment | Income Tax / TRACES through a tax-API vendor |
| `Labour` | `upload_ecr`, `upload_esic` | EPFO ECR upload and TRRN; ESIC contribution | The portals directly (no public API), or an aggregator |
| `Sessions` | `start`, `complete` | The OTP login that keeps a portal session alive | Per portal |
| `AccountAggregator` | `create_consent`, `fetch` | RBI-regulated consent flow and statement pull | An AA (Finvu, OneMoney, CAMS Finserv and others are the usual shortlist; check current RBI registrations) |
| `Bank` | `send_payment`, `send_salaries` | Payout and salary-file upload | Your bank's API, or a payments aggregator |
| `Copilot` | `reply(question, tools)` | Answer only from what the tools return, put a `[n]` marker after each fact with its citation, and propose actions from a fixed list; never act | A language-model API. Decide first: which vendor, where the data is processed, what the contract says about retention and training, and who holds the key |

## Questions to put to each vendor

1. **Licence.** Is it a GSTN-licensed GSP, or an ASP on a named GSP? Ask for the licence reference and the GSP it relies on.
2. **Coverage.** Which of the rows above does one contract cover? Anything it does not cover needs a second vendor.
3. **Sandbox.** A full sandbox with GSTN-style test GSTINs and an OTP simulator, available before signing.
4. **Sessions.** How long a session lasts, whether the OTP can be delegated, and how renewal is automated (our `portal.renew_session`).
5. **Data location.** Servers and backups in India; DPDP Act processor terms; sub-processors.
6. **Credentials.** We keep portal credentials in the secret manager and pass them per call. Does the vendor need to hold them?
7. **Idempotency and errors.** Idempotency keys on filing calls; stable error codes (our `PortalRejected` carries the portal's code).
8. **Limits and SLA.** Rate limits, GST-deadline-week capacity, uptime commitment, support hours on the 11th and 20th.
9. **Price.** Per return, per GSTIN, per IRN, flat? Model it for 50, 500 and 5,000 entities.
10. **Exit.** Can we export our history and switch? The adapters make switching a code change, not a rewrite.

## Scorecard (fill in, then decide)

| | Vendor A | Vendor B | Vendor C |
|---|---|---|---|
| Licence verified | | | |
| GST + IRP + e-way in one contract | | | |
| TRACES / Income Tax | | | |
| Sandbox quality (1-5) | | | |
| India data residency | | | |
| Price at 500 entities | | | |
| Support on deadline days (1-5) | | | |
| **Decision** | | | |

## Wiring a chosen vendor

1. Write `class AcmeGsp` (and the other interfaces it covers) in a new module, implementing the methods in `adapters.py`.
   Raise `OtpRequired`, `PortalRejected` or `PortalUnavailable` exactly as described at the top of that file.
2. Register it: `adapters.register("gsp", "acme", AcmeGsp)` at import time.
3. Select it per environment: `BIZ_ADAPTERS=gsp=acme,irp=acme,eway=acme`. The sandbox refuses to start when `BIZ_ENV` is not `dev`.
4. Run `scripts/ci-local.sh`: the whole suite runs against whatever `BIZ_ADAPTERS` selects, and the sandbox tests stay as the specification of behaviour.
5. Move the secret store from the local encrypted files to the cloud secret manager behind `adapters.secrets` (`put`, `get`, `delete`).

Nothing in this repository has been tested against a real portal. Treat the first week on each real adapter as a pilot with a small
number of entities and a person watching every filing.
