# Roles, practitioners and employee self-service — design

Status: phase 1 (presets, a level per module, the `hr` and `inventory` modules, the filtered sidebar and the home numbers) is built in migration 37; phases 2 to 7 are a proposal. Written against `main` after migration 36.

The goal: every person who touches a company in Biz360X signs in to a home that shows their own work. That covers the company's team (CEO, COO, CFO, HR Manager, Accountant), outside practitioners the company engages (CA, CS, statutory auditor, GST / PF-ESI / TDS / export-import consultants), and every employee (leave, calendar, tasks, onboarding, policies, payslips, year-end submissions). Later, practitioners can be found and engaged on the platform.

## 1. Decisions this design makes

1. **Titles are presets, not roles.** CEO, CFO and "GST consultant" are named starting points over one permission model (module × level). Companies differ in titles; they do not differ in what "may approve payroll" means.
2. **There are three kinds of people, and they are secured differently.** Team members and practitioners see a company's records within their scope. An employee sees only their own rows. An employee is therefore not a fifth membership level: it is a separate, much narrower door.
3. **A home is built from the person's work, not from their title.** Five homes (Leadership, Finance, People, Practitioner, Employee), each a "my work" queue plus the numbers that role watches. Work reaches a queue because a responsibility matrix says who prepares, reviews and files each kind of work.
4. **Confidential modules are enforced by the database before outsiders are let in.** Today the database isolates by company only; module limits live in the API.
5. **"Bring your own practitioner" first, marketplace last.** The directory is a business and regulatory question more than a software one (section 11).

## 2. What exists today

| Piece | Where | What it does |
|---|---|---|
| Membership | `memberships` (`db/01_foundation.sql`) | One row per person per company: `role` (owner, approver, maker, viewer) and `modules` (a list, or `*`). One level for all the person's modules. |
| Level → actions | `ROLE_ACTIONS`, `require()` in `server/app/core.py` | owner/approver: read, write, approve, file, pay. maker: read, write. viewer: read. Checked per request in the API. |
| Practitioner access | `advisor_grants` | A firm (`advisor_org_id`) gets a level **per module** (`scopes`: read, comment, prepare, certify), vault-folder limits and an expiry. Kinds: chartered accountant, company secretary, counsel, lender, investor, auditor. |
| Firm staff | `org_members` (admin, partner, staff) | Everyone in the firm sees every client the firm holds a grant for (`app.visible_entity_ids()`). |
| Row-level security | `db/09_security_views.sql` | Every table with `entity_id` is visible when the company is visible. Nothing narrower. |
| Work | `tasks.assignee_id`, `obligations.owner_user_id`, `approval_requests.assigned_to`, `document_requests` | The columns to give work to a person exist. Nothing fills them by rule. |
| Two-person rule | database constraint | The requester of an approval cannot decide it. |
| Firm console | `/group`, `routes_settings.py` | Client × authority grid, workload, chase clients. |
| Employee link | `employees.user_id` | Present, unused. Employees cannot sign in. |
| Screens | `web/src/components/nav.ts`, `/` (Today) | One sidebar and one dashboard for everyone. `/me` already returns each company's role and modules. |

## 3. What is wrong with it for this goal

1. **A team member has one level for every module.** An HR Manager who approves leave but only prepares payroll, or a CFO who approves finance but only reads HR, cannot be expressed. Practitioners already have per-module levels; members do not.
2. **The database does not know about modules.** `require()` is the only thing between a GST consultant and the salary register. That is one missed check away from a leak, and the project's own rule is that tenancy is enforced by the database.
3. **A practitioner can only be a firm, and a firm is all-or-nothing.** A sole practitioner has to be modelled as a one-person firm, every staff member of a firm sees every client, and there is no accept step, engagement period or record of what was agreed.
4. **An auditor is just a grant with read scopes.** Nothing stops an auditor grant from being given write access, and nothing marks the independence problem when the same firm keeps the books.
5. **Work is not routed.** Obligations and tasks have owner columns that stay empty, so nobody has a queue.
6. **Employees have no door at all.** Making them members would show them the whole company at the database level.

## 4. The permission model

### 4.1 One scale of levels

Members and practitioners use the same module list and one scale, so `require()` has a single rule.

| Level | May | Member today | Practitioner today |
|---|---|---|---|
| `read` | see | viewer | read |
| `comment` | see, raise queries and requests | – | comment |
| `write` | prepare and edit | maker | prepare |
| `approve` | decide approvals | approver | – |
| `file` | file with a portal, release payments | approver, owner | certify |

A practitioner is never given `approve` (approvals are the company's decision) and an API key never gets approve, file or pay, as today.

### 4.2 Members get a level per module

`memberships` gains `scopes jsonb` (`{"finance": "approve", "payroll": "read"}`), `title text` and `preset text`. `role` and `modules` stay for one release as the source for a backfill (`owner` → every module at `file`, and so on), then `require()` reads `scopes` only. `owner` remains a distinct flag: only an owner manages people, engagements and settings.

### 4.3 Modules

Existing: `settings`, `compliance`, `approvals`, `tasks`, `trade`, `parties`, `sales`, `purchases`, `gst`, `tds`, `finance`, `payroll`, `legal`, `capital`, `schemes`, `documents`, `copilot`, `audit`.

Proposed additions: `hr` (onboarding, leave, holidays, policy and handbook; `payroll` keeps pay, salary structures and payslips) and `inventory` (it runs under `finance` and `parties` today). Splitting `hr` from `payroll` is what lets an HR executive run leave without seeing salaries.

### 4.4 Presets

Presets are data in the server (`roles.py`), offered when inviting a person. They fill `scopes` and a default home; the owner can change any line. They are a starting point, not a guarantee of what a title should see in a given company.

| Preset | Kind | Scopes it starts with | Home |
|---|---|---|---|
| Founder / CEO | member, owner | every module at `file` | Leadership |
| COO | member | compliance, sales, purchases, inventory, trade, hr, legal, documents at `approve`; finance, payroll at `read` | Leadership |
| CFO | member | finance, gst, tds, purchases, sales, trade, inventory, capital at `file`; payroll at `approve`; compliance, documents, legal at `read` | Finance |
| Accountant | member | finance, sales, purchases, parties, inventory, gst, tds, trade, documents at `write` | Finance |
| HR Manager | member | hr at `approve`; payroll at `write`; documents at `write`; compliance at `read` | People |
| Chartered accountant (retained) | practitioner | finance, gst, tds at `file`; sales, purchases, payroll, documents at `read` | Practitioner |
| Company secretary | practitioner | legal, capital, compliance at `file`; documents at `write` | Practitioner |
| GST consultant | practitioner | gst at `file`; sales, purchases, parties at `read`; documents (GST folder) | Practitioner |
| PF / ESI consultant | practitioner | payroll at `read`; compliance (EPFO, ESIC) at `file` | Practitioner |
| TDS consultant | practitioner | tds at `file`; purchases, payroll at `read` | Practitioner |
| Export–import consultant | practitioner | trade at `write`; sales, purchases at `read`; documents (trade folder) | Practitioner |
| Statutory auditor | practitioner, audit mode | read on finance, sales, purchases, inventory, gst, tds, payroll, legal, capital, documents, audit; `comment` to raise queries | Practitioner |
| Employee | employee | none: own records only (section 8) | Employee |

A PF/ESI or TDS consultant needs wage data to do the work; the scope makes that explicit to the owner at the time of granting, and section 9 is what keeps it to that module.

## 5. Routing work: the responsibility matrix

A home is only useful if work arrives in it. One table says, per company, who does each step of each kind of work.

`responsibilities (entity_id, work_kind, step, membership_id | engagement_id)`

- `work_kind`: `gst_return`, `tds_return`, `tds_deposit`, `pf_esi`, `professional_tax`, `mca_filing`, `income_tax`, `payroll_run`, `month_close`, `vendor_payment`, `leave`, `onboarding`, `notice`, `export_docs`. Obligations map to a kind through their rule's authority and code.
- `step`: `prepare`, `review`, `approve`, `file`.
- The assignee is a member or an engagement. A firm's engagement resolves to the staff assigned to that client (section 7).

Effects:

- The compliance engine sets `obligations.owner_user_id` from the matrix when it creates an obligation, and opens a task for the `prepare` step when the obligation comes into its window.
- Submitting something for approval sets `approval_requests.assigned_to` from the `approve` step. The two-person rule is unchanged: if the matrix names the requester, the request falls to the owner.
- A preset fills the matrix with sensible defaults (GST consultant: prepare and file `gst_return`; CFO: approve it). With no row, work falls to the owner, which is today's behaviour.

`tasks` gains `description`, `created_by`, `engagement_id` and `work_kind`, so a task can be given to an engagement before a named person picks it up.

**`GET /me/work`** returns, across every company the person can see, one list sorted by due date: tasks assigned to them, obligations they own, approvals waiting on them, document requests addressed to them, and leave requests they must decide. **`GET /me/calendar`** returns the same dated items plus holidays, their own leave and board meetings. Both read through existing row-level security.

## 6. Homes

`/` renders the home named on the membership or engagement. Every home starts with the person's work queue; the rest is assembled from endpoints that already exist, so a section a person has no scope for simply is not there. The sidebar is filtered the same way from `/me`.

| Home | Sections beyond the queue | Built from |
|---|---|---|
| Leadership | Compliance standing and exposure; cash and runway; approvals waiting; overdue items by owner; headcount and payroll cost | `/compliance/summary`, `/compliance/standing`, `/finance/summary`, `/approvals`, `/payroll-runs` |
| Finance | Close checklist; bank lines to book; receivables and payables ageing; filings due with their preparer; stock value | `/periods/{id}/close-tasks`, `/bank-transactions`, `/receivables/ageing`, `/msme-payables`, `/obligations`, `/stock-items` |
| People | People being onboarded; leave waiting; this month's run and its readiness; policies awaiting acknowledgement; labour compliance | `/employees?status=onboarding`, `/leave-requests`, `/payroll-runs/{id}/readiness`, `/hr/policy-documents`, `/payroll/labour-compliance` |
| Practitioner | All clients in one grid: what is due, what waits on the client, what waits on me; requests I have open; per-client drill-down | `/organizations/{id}/entities`, `/organizations/{id}/workload`, `/document-requests`, `/me/work` |
| Employee | Leave balance and requests; payslips; my tasks and onboarding checklist; policies to acknowledge; holidays; tax declarations | the self-service endpoints in section 8 |

A person with more than one hat (an HR Manager is also an employee; a CA serves six clients) gets their main home plus a "My space" entry for the employee view, and the company switcher that exists today.

## 7. Practitioners and engagements

`advisor_grants` becomes the engagement. It keeps its scopes, folders and expiry and gains:

| Column | Why |
|---|---|
| `practitioner_user_id` | A sole practitioner is engaged as a person. Exactly one of this and `advisor_org_id` is set. |
| `service` | `bookkeeping`, `gst`, `tds`, `pf_esi`, `payroll`, `secretarial`, `trade`, `statutory_audit`, `tax_audit`, `internal_audit`, `virtual_cfo`, `legal`. Drives the preset and the matrix defaults. |
| `period_fy` | An audit or a year's filing work belongs to a financial year. |
| `invited_email`, `accepted_at`, `accepted_by` | The company invites; the practitioner accepts. Access starts on acceptance, not on invitation. |
| `engagement_letter_document_id` | The signed letter, in the vault. |
| `membership_no`, `verified_at`, `verified_by` | The professional membership number and who checked it (section 11). |

**Which firm staff see which client.** `engagement_staff (engagement_id, user_id)`. A firm's partners and admins see all its clients; staff see only the clients they are assigned to. `app.visible_entity_ids()` changes accordingly. This is a behaviour change for existing firms: a migration assigns every current staff member to every current client so nobody loses access on the day it ships.

**Audit mode.** An engagement whose `service` is `statutory_audit`, `tax_audit` or `internal_audit`:

- can hold only `read` and `comment` scopes — a database constraint, not a convention;
- must have an `expires_on`;
- can raise queries through `document_requests`, and every record it opens is written to the audit log as a read by an auditor;
- can export what it is entitled to see (ledgers, registers, the audit chain verification) as files.

**Independence.** When a company adds a statutory audit engagement, the system refuses it if the same firm or person holds a `bookkeeping`, `internal_audit` or `virtual_cfo` engagement for an overlapping year, and the reverse. Which services an auditor may not provide is set by the Companies Act and the ICAI rules; the exact list here is a starter rule for a CA to confirm before it is enforced.

**Certificates.** Where a practitioner certifies something, the record carries the UDIN they generated on the ICAI portal. Biz360X stores it; it does not generate it.

## 8. Employee self-service

### 8.1 Security

Two ways to do it:

- **A. A separate door (recommended).** Requests under `/v1/self/…` run as a new database role, `biz_self`, which has grants on a short list of tables only, each with a policy tied to the caller's own employee record. `biz_self` has no privilege on the ledger, sales, purchases or any other table, so a bug in a self-service handler cannot reach them.
- **B. An `employee` membership level.** The person becomes visible to the company's rows and every one of roughly 150 table policies must then exclude them. One missed table is a leak.

A is more code in one place and far less risk. The employee is found through `employees.user_id`; they are not in `memberships` and `app.visible_entity_ids()` does not change.

| Table | What `biz_self` may do |
|---|---|
| `employees` | read own row (not the bank account number: last four digits only, through a view) |
| `payslips`, `payslip_lines` | read own, from approved runs only |
| `leave_requests` | read own; insert a pending request for themselves; cancel their own pending request |
| `leave_types`, `holidays`, `hr_policies` (leave and calendar parts) | read, for their company |
| `hr_policy_documents` | read current versions for their company |
| `hr_policy_acks` | insert their own acknowledgement |
| `onboarding_tasks` | read own; tick own non-automatic items |
| `tasks` | read and complete tasks assigned to them |
| `tax_declarations` (new) | read and write own, while the window is open |
| `documents` | upload to their own folder (proofs); read their own |

### 8.2 Signing in

Onboarding (or HR, for existing staff) sends an invitation to the employee's email. Accepting it creates or finds the `users` row and sets `employees.user_id`. Sign-in is the existing one-time code. `/me` gains `employments: [{entity_id, employee_id, legal_name}]`. Leaving the company (`status = exited`) keeps read access to payslips and Form 16 for a period the company sets, and nothing else.

### 8.3 What the employee does

- **Leave.** Applies for themselves; it goes to their manager (`employees.manager_id`, new) or to HR when there is none. The balance check and working-day count are the ones already in `hr.py`. A manager approves through `/me/work`; a manager is a team member with `hr` at `approve` for their reports only, which needs the rule "decides leave for people who report to them".
- **Payslips.** List and PDF, from `payslip_pdf.py`.
- **Policies.** Read and acknowledge. Today HR records the acknowledgement; this makes it the employee's own act, with the time and the person recorded.
- **Onboarding.** Fill in their own details (PAN, bank account, address, emergency contact), upload proofs, see the checklist. HR still activates.
- **Calendar and tasks.** `/self/calendar` (holidays, own leave, team leave if the company allows) and tasks assigned to them.
- **Year-end submissions** (new module, `tax_declarations`): the tax regime for the year, planned investments and rent at the start of the year, actual proofs before the cut-off, which HR verifies. Payroll then deducts TDS on the verified figures instead of assuming only PF under the old regime, as `payroll.py` does today. Part B of Form 16 can be produced from the year's payslips; Part A comes from TRACES and is attached. Every limit and section in this module is an Indian law figure and needs a CA's review before it drives a deduction.

## 9. Enforcing modules in the database

Scope: **confidentiality of sensitive modules**, not every write rule.

- `app.can(entity_id, module, level)`: a `SECURITY DEFINER` function owned by `biz_security` that reads the caller's membership scopes and engagements, like `app.visible_entity_ids()`.
- A restrictive read policy on the tables of the sensitive modules: `payroll` (employees' pay data, salary structures, payslips), `hr` where it is personal (leave, acknowledgements), `capital` (cap table, ESOP), bank account details, and vault documents by folder.
- Writes stay checked by `require()` plus the existing constraints. Enforcing module writes in the database would break legitimate cross-module postings (approving payroll posts a `finance` journal) unless every posting helper became a privileged function; that is a larger change with little confidentiality gain.

Cost: one more predicate per query on those tables. It has to be measured on the payroll run and the salary register before it ships. Test: for each sensitive table, a member without the scope and an engagement without the scope read zero rows through a raw connection as `biz_app`, not through the API.

## 10. API

| New or changed | Purpose |
|---|---|
| `GET /role-presets` | The presets with their scopes, for the invite screen |
| `POST /members`, `PATCH /members/{id}` | Accept `scopes`, `title`, `preset`; `role`/`modules` deprecated |
| `GET`, `PUT /responsibilities` | The matrix |
| `GET /me/work`, `GET /me/calendar` | The queue and calendar, across companies |
| `POST /advisor-grants` → engagements | `service`, individual practitioner, invitation; `POST /engagements/{id}/accept`; `PUT /engagements/{id}/staff` |
| `/v1/self/…` | Employment, payslips, leave, policies, onboarding, tasks, calendar, declarations |
| `GET /me` | Adds `home`, `scopes`, `employments` |

Everything follows the existing rules: the contract is generated from `api/build_openapi.py`, invisible is 404, audit and events in the same transaction, a new numbered migration (37 onward) per phase.

## 11. The marketplace

Three stages, each useful without the next.

1. **Bring your own practitioner.** The company invites the CA, CS or consultant it already uses; they accept and get a console across their clients. Sections 4–7 deliver this.
2. **Verified profiles.** A practitioner or firm has a profile: services, membership number, certificate of practice, firm registration, states served. Biz360X verifies the membership number before the badge shows.
3. **Directory and engagement requests.** A company searches by service and state and sends a request; the practitioner accepts with an engagement letter.

Questions to settle with a CA, a CS and counsel **before** stage 2 is designed. I have not checked the current text of these rules; they are the areas I would expect to constrain the product.

- **Solicitation and advertising.** The ICAI and ICSI codes of ethics restrict how members may advertise and solicit work. A directory that ranks, promotes or recommends practitioners may count.
- **Fee sharing.** Members are generally barred from sharing fees with, or paying commission to, non-members. A platform commission on a practitioner's fee may be unworkable; a subscription paid by the practitioner or the company is the safer shape.
- **Auditor independence and eligibility** under the Companies Act (who may be appointed, and which other services they may not provide).
- **Personal data.** Giving a consultant access to salary and employee data is a disclosure to a processor under the DPDP Act; the engagement needs the right terms, and employees the right notice.
- **Liability.** What Biz360X promises when it shows a "verified" badge, and what it does not.

## 12. Phases

Each phase ships alone and is a pull request or two. Sizes are relative, not estimates.

| # | Phase | Contains | Size | Done when |
|---|---|---|---|---|
| 1 | Presets and homes | `scopes` on members with backfill, presets, `title`; sidebar filtered by scope; the five homes from existing endpoints (Employee home waits for phase 4) | M | An invited "Accountant" lands on the Finance home, sees no payroll entry in the sidebar, and gets 403 from payroll endpoints |
| 2 | Work routing | `responsibilities`, engine and approvals fill owners, `/me/work`, `/me/calendar`, queue on every home | M | A GST return obligation appears in the GST consultant's queue to prepare and the CFO's to approve, with no manual assignment |
| 3 | Engagements | Individual practitioners, invite and accept, `service`, `engagement_staff`, audit mode and its constraint, independence check, UDIN field | L | A sole GST consultant accepts an invitation, sees only GST and its inputs for that client; an auditor engagement cannot be saved with a write scope |
| 4 | Employee self-service | `biz_self`, `/v1/self`, invitation, own leave, payslips, policies, onboarding details, manager approval | L | An employee signs in, applies for leave, downloads a payslip, and a raw `biz_self` connection can read no ledger row |
| 5 | Year-end submissions | `tax_declarations`, proofs, HR verification, TDS on verified figures, Form 16 Part B | L | A declared and verified 80C investment changes the month's TDS; CA has reviewed the figures |
| 6 | Module confidentiality in the database | `app.can`, restrictive read policies on sensitive tables, performance check | M | The raw-connection test in section 9 passes for every sensitive table |
| 7 | Verified profiles, then directory | Section 11 stages 2 and 3 | L | After the questions in section 11 are answered |

Phase 6 must land before any practitioner outside the company's own trusted circle is given access, so if stage 2 of the marketplace is near, pull it forward to follow phase 3.

## 13. Decisions needed from you

1. **Split `hr` from `payroll`?** Recommended: yes. Without it an HR executive who handles leave can also see every salary.
2. **Employee access: separate door (A) or membership level (B)?** Recommended: A.
3. **Do managers approve leave, or only HR?** It decides whether `employees.manager_id` and a "my reports" rule are built in phase 4.
4. **Firm staff see only assigned clients?** Recommended: yes, with existing staff assigned to all current clients on migration.
5. **How is the marketplace paid for?** It decides nothing before phase 7, but the answer to the fee-sharing question may rule out commission.
6. **May a practitioner be given `approve`?** Recommended: no. Approvals stay with the company.

## 14. Not in this design

Custom roles beyond editing a preset's scopes; field-level permissions (hiding one column from a role); attendance and shift rosters; performance reviews and appraisals; an org chart beyond `manager_id`; practitioner billing and invoicing through the platform; chat between company and practitioner (document requests and task comments only); single sign-on for employees (the existing identity-provider interface applies when a provider is chosen).
