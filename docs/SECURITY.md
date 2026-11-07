# Security

What protects tenant data and credentials, how it is checked, and what is still open. Read it with `docs/API_GUIDE.md`
section 3 (permissions). The checks marked **(test)** run in `server/tests/test_m7.py` on every push.

## Production configuration

The API server and the worker call `security.check_environment()` on start and **refuse to run** outside development when any of
these is wrong. `BIZ_ENV` must be `dev`, `test`, `staging` or `production`; any other value is treated as production and reported.

| Setting | Required outside development |
|---|---|
| `JWT_SECRET` | Random, at least 32 characters. The development default is refused. |
| `BIZ_FIELD_KEY` | A Fernet key from the secret manager (encrypts `*_enc` columns, webhook secrets, TOTP secrets, OTPs held by paused jobs). |
| `BIZ_OTP_PROVIDER` | A real provider. `sandbox` is refused, which is what keeps the development code `000000` out of production. |
| `BIZ_ADAPTERS` | A real adapter for every portal family (see `docs/PROVIDERS.md`). Sandboxes are refused. |
| `BIZ_IDP_PROVIDER` | Optional. Unset means single sign-on is off; the sandbox identity provider is switched off in production. |
| `BIZ_ALERT_WEBHOOK` | Recommended: where the audit verification job posts an alert. |
| `BIZ_RATE_USER`, `BIZ_RATE_HEAVY` | Optional overrides of 600 and 60 requests a minute. |

**(test)** production refuses defaults, refuses sandbox adapters, starts when fully configured, and treats an unknown `BIZ_ENV` as production.

## Sign-in

- **Codes.** Six digits, five-minute life, five attempts, five requests an hour per identifier. Only an HMAC of the code is stored,
  in a table the application role cannot read. A request for an unknown address behaves exactly like one for a known address, and the
  limit counts both, so neither the answer nor the limit reveals who has an account. Failed attempts are committed, not rolled back with the error.
- **Access tokens** live 15 minutes and carry `typ=access`; a refresh token is not accepted as one.
- **Refresh tokens** are opaque, stored as SHA-256, rotate on every use, and live 30 days. Presenting a used token revokes the whole
  family, so a stolen copy ends the session for the thief and the owner alike. Logout revokes the family.
- **MFA.** A user with `mfa_enabled` must send a TOTP `mfa_code`. If MFA is on but no secret is enrolled, sign-in fails closed.
  *There is no enrolment endpoint yet*; set `users.totp_secret_enc` through support tooling until there is.
- **Single sign-on.** `authorization_code` goes through an `IdentityProvider`; only the sandbox exists. It signs in an existing user by verified
  email and never creates one. **Google, Microsoft and SAML providers are not written**; each is a class registered with `auth.register_idp`.
- **Passwords** are not supported.

**(test)** single use, attempt limit, hourly limit and no enumeration, mobile sign-in, refresh rotation and reuse detection, expiry, logout, MFA, SSO, development code off in production.

## API keys

A key is `bzx_live_<8 hex>_<secret>`. It is shown once; only its SHA-256 is stored. It acts as a **service user** (`auth_provider = 'service'`), so the
same row-level security applies, and it is limited to the permissions on the key (`module:read|write|use`). A key can never hold an approve, file or pay
permission, `settings:write` or anything on approvals, and it cannot create keys. A service user cannot sign in with a code or SSO. Revoking takes effect on
the next request. Keys are rate-limited as their own caller.

`app.create_api_key` and `app.revoke_api_key` are `SECURITY DEFINER` because a request may not write other users. They check that the caller can see the
entity but **not that the caller may manage keys**; the routes call `require(..., "settings:write")` first. Do not call these functions from anywhere else.

**(test)** shown once and stored hashed, permission list enforced, other entities invisible, forbidden permissions, expiry, tampering, revocation, audit entries.

## Webhooks

Deliveries are signed `t=<unix>,v1=HMAC_SHA256(secret, t + "." + body)`; receivers reject a timestamp more than 300 s old and deduplicate on `Biz360X-Event-Id`.
Secrets are encrypted at rest and shown once. Endpoint URLs must be `https`, carry no credentials and must not resolve to a private, loopback, link-local or
reserved address; the check runs again at send time, redirects are not followed, and there is a 10-second timeout. Failed deliveries retry after 1 min, 5 min,
30 min, 2 h, 6 h and then every 6 h until 72 hours after the event.

**(test)** signature and replay window, SSRF refusals, entity isolation, retry schedule, give-up, disabled endpoints.

**Known gap:** the SSE stream `GET /events` in the contract is not implemented.

## Rate limits

600 requests a minute per user (or API key), 60 for portal-calling actions (every `POST` that answers `202`), 30 a minute per IP on public pay pages, with
`RateLimit` and `Retry-After`. Unauthenticated calls, including bad tokens, count against the client IP. The window store is **in process memory**: with more than one API
process the effective limit is multiplied, and the store must move to Redis (`ratelimit.LIMITER`). Behind a proxy, make sure the client IP the server sees is the real
one (`--proxy-headers` with a trusted proxy list).

**(test)** window reset, per caller, per key, the heavy limit, headers.

## Audit trail

`audit_log` is append-only (triggers; `biz_app` and `biz_worker` hold no UPDATE or DELETE) and hash-chained globally. `app.audit_verify()` is `SECURITY DEFINER`
so that it checks every tenant's rows rather than the caller's. The worker runs it daily (`audit.verify`). A break writes a `system_alerts` row (one per break, resolved
when the chain verifies again), logs at critical level and posts to `BIZ_ALERT_WEBHOOK`. `GET /entities/{id}/audit-log/verify` reports a break in another tenant's rows
without saying where.

**(test)** listing and filters, verification, tamper detection, alert once, resolution, scheduling.

## Tenancy and secrets, checked every push

- Every table with an `entity_id` column has row-level security enabled **and forced** **(test)**.
- `auth_otps`, `refresh_tokens` and `system_alerts` are not readable by the application role **(test)**.
- Every `*_enc` column is `bytea` and is written through `secretbox` **(test)**.
- Dependencies are scanned with `pip-audit` in CI and in `scripts/ci-local.sh`.
- Responses carry `nosniff`, `no-store`, `no-referrer`, HSTS and a deny-all content security policy.

## Review findings

| # | Finding | Status |
|---|---|---|
| 1 | The development OTP could be accepted anywhere the server ran | Fixed: allowed only in `dev`/`test` with the sandbox sender, and the server will not start otherwise |
| 2 | Attempt counters rolled back with the 401, so a code could be guessed without limit | Fixed and tested |
| 3 | `audit_verify()` saw only the caller's tenant, so a global chain looked broken | Fixed (`SECURITY DEFINER`) |
| 4 | Sandbox identity provider accepts any email | Switched off in production |
| 5 | Webhook URLs could target internal addresses | Fixed (resolve, refuse, re-check at send time, no redirects) |
| 6 | Local secret store is an encrypted file | Acceptable for development only. **Move to a cloud secret manager and KMS** before real credentials are stored |
| 7 | `BIZ_FIELD_KEY` has no rotation | Open: switch `secretbox` to `MultiFernet` and re-encrypt before launch |
| 8 | Rate limit store is per process | Open (see above) |
| 9 | No MFA enrolment, no Google/Microsoft/SAML providers, no real OTP provider | Open: interfaces and sandboxes only; **needs a provider decision** |
| 10 | The two definer functions for API keys trust their caller to have checked `settings:write` | Documented above; keep them route-only |
| 11 | Provider adapters were never run against a real portal | Open (M6 note); treat the first weeks as a pilot |
| 12 | Indian-law figures are a starter library | Open: CA/CS review, issue #63 |

This review was written by the team that built the code. It is a checklist, not a penetration test: commission an independent one before launch.
