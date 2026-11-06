"""Builds openapi.yaml for the Biz360X API (OpenAPI 3.1).

The spec is generated from compact definitions so every resource follows
the same conventions: entity-scoped paths, cursor pagination, ETags,
idempotency keys on money and filing operations, RFC 9457 errors,
202 + Job for anything that talks to a government portal.
"""
import copy, sys, yaml

SPEC = {
    "openapi": "3.1.0",
    "info": {
        "title": "Biz360X API",
        "version": "1.0.0",
        "summary": "Business OS for Indian companies, startups and MSMEs — compliance engine, books, GST, payroll, billing.",
        "description": (
            "All business data lives under an **entity** (a legal entity: company, LLP, firm). "
            "Every entity-scoped path starts with `/entities/{entityId}`. The server sets the caller on the "
            "database session and PostgreSQL row-level security decides visibility, so an entity the caller "
            "cannot see returns **404**, never 403.\n\n"
            "**Money** is a decimal string in INR with two places (`\"472000.00\"`). **Dates** are `YYYY-MM-DD`, "
            "moments are RFC 3339 UTC.\n\n"
            "**Writes that move money or file with a government portal** require `Idempotency-Key`. "
            "Operations that call GSTN, IRP, MCA, TRACES, EPFO or ESIC return `202 Accepted` with a `Job`; "
            "follow it at `/jobs/{jobId}`, on the `/events` stream, or by webhook.\n\n"
            "**Errors** are `application/problem+json` (RFC 9457) with a stable `code`.\n\n"
            "Each operation lists the permission it needs in `x-permission` (`module:action`)."
        ),
        "contact": {"name": "Biz360X", "email": "[API CONTACT EMAIL]"},
    },
    "servers": [
        {"url": "https://api.[YOUR DOMAIN]/v1", "description": "Production (data stays in India)"},
        {"url": "https://sandbox.api.[YOUR DOMAIN]/v1", "description": "Sandbox — portal calls hit GSP/IRP sandboxes"},
    ],
    "security": [{"bearerAuth": []}],
    "tags": [],
    "paths": {},
    "webhooks": {},
    "components": {"securitySchemes": {}, "parameters": {}, "headers": {}, "responses": {}, "schemas": {}},
}
C = SPEC["components"]
TAGS = {}

def tag(name, desc):
    TAGS[name] = desc

def ref(name, kind="schemas"):
    return {"$ref": f"#/components/{kind}/{name}"}

def schema(name, props, required=(), desc=None, extra=None):
    s = {"type": "object", "properties": props}
    if required:
        s["required"] = list(required)
    if desc:
        s["description"] = desc
    if extra:
        s.update(extra)
    C["schemas"][name] = s
    return ref(name)

# ---------------------------------------------------------------- primitives
S = lambda **kw: dict(type="string", **kw)
I = lambda **kw: dict(type="integer", **kw)
B = lambda **kw: dict(type="boolean", **kw)
N = lambda **kw: dict(type="number", **kw)
UUID = S(format="uuid")
RO_UUID = S(format="uuid", readOnly=True)
DATE = S(format="date")
TS = S(format="date-time")
RO_TS = S(format="date-time", readOnly=True)
def ENUM(*v, **kw): return S(enum=list(v), **kw)
def ARR(items, **kw): return dict(type="array", items=items, **kw)
def NULLABLE(s): return {"oneOf": [s, {"type": "null"}]}

C["schemas"]["Money"] = S(pattern=r"^-?[0-9]{1,16}\.[0-9]{2}$", examples=["472000.00"], description="INR, two decimals, as a string")
C["schemas"]["Gstin"] = S(pattern=r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$", examples=["29AAHCN1234Q1Z8"])
C["schemas"]["Pan"] = S(pattern=r"^[A-Z]{5}[0-9]{4}[A-Z]$", examples=["AAHCN1234Q"])
C["schemas"]["StateCode"] = S(pattern=r"^[0-9]{2}$", examples=["29"], description="GST state code")
C["schemas"]["HsnSac"] = S(pattern=r"^[0-9]{4,8}$", examples=["902620"])
C["schemas"]["FyLabel"] = S(pattern=r"^[0-9]{4}-[0-9]{2}$", examples=["2026-27"])
MONEY, GSTIN, PAN, STATE, HSN, FY = ref("Money"), ref("Gstin"), ref("Pan"), ref("StateCode"), ref("HsnSac"), ref("FyLabel")

# ---------------------------------------------------------------- security
C["securitySchemes"] = {
    "bearerAuth": {"type": "http", "scheme": "bearer", "bearerFormat": "JWT",
                   "description": "Access token from `/auth/token` or the OIDC/SAML login. 15-minute lifetime; refresh with the refresh token."},
    "apiKey": {"type": "apiKey", "in": "header", "name": "X-Api-Key",
               "description": "Server-to-server keys, scoped to one entity and a list of permissions (Settings › API keys)."},
}

# ---------------------------------------------------------------- shared parameters, headers, responses
C["parameters"] = {
    "EntityId": {"name": "entityId", "in": "path", "required": True, "schema": UUID, "description": "Legal entity the call acts on"},
    "Id": {"name": "id", "in": "path", "required": True, "schema": UUID},
    "Limit": {"name": "limit", "in": "query", "schema": I(minimum=1, maximum=200, default=50)},
    "Cursor": {"name": "cursor", "in": "query", "schema": S(), "description": "Opaque cursor from `next_cursor`"},
    "Sort": {"name": "sort", "in": "query", "schema": S(examples=["-due_date"]), "description": "Field name; prefix `-` for descending"},
    "IdempotencyKey": {"name": "Idempotency-Key", "in": "header", "required": True, "schema": S(minLength=8, maxLength=128),
                       "description": "Unique per logical operation. Replays within 24 h return the first response."},
    "IfMatch": {"name": "If-Match", "in": "header", "required": True, "schema": S(), "description": "ETag from the last GET; stale writes get 412"},
    "From": {"name": "from", "in": "query", "schema": DATE},
    "To": {"name": "to", "in": "query", "schema": DATE},
    "Q": {"name": "q", "in": "query", "schema": S(), "description": "Free-text search"},
}
P = lambda n: ref(n, "parameters")
C["headers"] = {
    "ETag": {"schema": S(), "description": "Version of the resource for If-Match"},
    "RateLimit": {"schema": S(), "description": "IETF RateLimit header: `limit=600, remaining=598, reset=42`"},
}

schema("Problem", {
    "type": S(format="uri", examples=["https://docs.[YOUR DOMAIN]/errors/tax_split_mismatch"]),
    "title": S(), "status": I(), "detail": S(), "instance": S(),
    "code": ENUM("validation_failed", "not_found", "conflict", "precondition_failed", "idempotency_conflict",
                 "two_person_rule", "permission_denied", "period_locked", "journal_unbalanced",
                 "tax_split_mismatch", "b2b_needs_gstin", "note_needs_original", "number_too_long",
                 "over_settlement", "msme_terms_exceeded", "rule_version_overlap", "portal_session_expired",
                 "portal_rejected", "approval_required", "rate_limited", "internal"),
    "errors": ARR(schema("FieldError", {"field": S(examples=["lines[2].hsn_sac"]), "message": S()})),
    "request_id": S(),
}, required=("type", "title", "status", "code"), desc="RFC 9457 problem details. `code` is stable; `title`/`detail` are for humans.")

def problem_resp(desc):
    return {"description": desc, "content": {"application/problem+json": {"schema": ref("Problem")}}}
C["responses"] = {
    "BadRequest": problem_resp("Validation failed (`validation_failed`, `b2b_needs_gstin`, `tax_split_mismatch` …)"),
    "Unauthorized": problem_resp("Missing or expired token"),
    "Forbidden": problem_resp("Caller can see the entity but lacks the permission (`permission_denied`, `two_person_rule`)"),
    "NotFound": problem_resp("Resource does not exist or is not visible to the caller"),
    "Conflict": problem_resp("State conflict (`conflict`, `period_locked`, `over_settlement`, `idempotency_conflict`)"),
    "PreconditionFailed": problem_resp("If-Match did not match the current ETag"),
    "TooMany": problem_resp("Rate limit exceeded; see Retry-After"),
}
STD_ERRORS = {"400": ref("BadRequest", "responses"), "401": ref("Unauthorized", "responses"),
              "403": ref("Forbidden", "responses"), "404": ref("NotFound", "responses"),
              "429": ref("TooMany", "responses")}

schema("Job", {
    "id": RO_UUID, "kind": S(examples=["gst.file_gstr1"]),
    "status": ENUM("queued", "running", "waiting_for_otp", "succeeded", "failed", "cancelled"),
    "progress": N(minimum=0, maximum=1), "subject": S(examples=["gst_returns"]), "subject_id": UUID,
    "result": {"type": "object", "description": "e.g. {\"arn\":\"AA2910260012345\"}"},
    "error": ref("Problem"), "created_at": RO_TS, "finished_at": NULLABLE(TS),
}, required=("id", "kind", "status"), desc="Long-running operation, usually a government-portal call")

def page_of(name):
    pname = f"{name}Page"
    if pname not in C["schemas"]:
        schema(pname, {"data": ARR(ref(name)), "next_cursor": NULLABLE(S())}, required=("data", "next_cursor"))
    return ref(pname)

# ---------------------------------------------------------------- operation builders
OPIDS = set()
def _op(method, path, tag_, opid, summary, perm=None, params=(), body=None, resp=None, status="200",
        idem=False, ifmatch=False, desc=None, security=None, extra_responses=None):
    assert opid not in OPIDS, opid
    OPIDS.add(opid)
    op = {"tags": [tag_], "operationId": opid, "summary": summary}
    if desc:
        op["description"] = desc
    prm = []
    if path.startswith("/entities/{entityId}"):
        prm.append(P("EntityId"))
    if "{id}" in path:
        prm.append(P("Id"))
    prm += list(params)
    if idem:
        prm.append(P("IdempotencyKey"))
    if ifmatch:
        prm.append(P("IfMatch"))
    if prm:
        op["parameters"] = prm
    if body is not None:
        op["requestBody"] = {"required": True, "content": {"application/json": {"schema": body}}}
    responses = {}
    if status == "204":
        responses["204"] = {"description": "Done"}
    elif status == "202":
        responses["202"] = {"description": "Accepted — follow the job", "content": {"application/json": {"schema": ref("Job")}},
                            "headers": {"Location": {"schema": S(), "description": "/jobs/{jobId}"}}}
    else:
        r = {"description": "OK" if status == "200" else "Created"}
        if resp is not None:
            r["content"] = {"application/json": {"schema": resp}}
        if method in ("get", "patch", "post") and resp is not None and "$ref" in resp and not resp["$ref"].endswith("Page"):
            r["headers"] = {"ETag": ref("ETag", "headers")}
        responses[status] = r
    responses.update(copy.deepcopy(STD_ERRORS))
    if method in ("post", "patch", "delete"):
        responses["409"] = ref("Conflict", "responses")
    if ifmatch:
        responses["412"] = ref("PreconditionFailed", "responses")
    if extra_responses:
        responses.update(extra_responses)
    op["responses"] = responses
    if perm:
        op["x-permission"] = perm
    if security is not None:
        op["security"] = security
    SPEC["paths"].setdefault(path, {})[method] = op

def collection(base, tag_, name, perm, filters=(), create=True, create_schema=None, idem=False, summary_plural=None):
    plural = summary_plural or name + "s"
    _op("get", base, tag_, f"list{plural}", f"List {split(plural)}", f"{perm}:read",
        params=[P("Limit"), P("Cursor"), P("Sort")] + list(filters), resp=page_of(name))
    if create:
        _op("post", base, tag_, f"create{name}", f"Create {split(name)}", f"{perm}:write",
            body=create_schema or ref(name), resp=ref(name), status="201", idem=idem)

def item(base, tag_, name, perm, patch=True, delete=False, delete_note=None):
    _op("get", base + "/{id}", tag_, f"get{name}", f"Get {split(name)}", f"{perm}:read", resp=ref(name))
    if patch:
        _op("patch", base + "/{id}", tag_, f"update{name}", f"Update {split(name)}", f"{perm}:write",
            body=ref(name), resp=ref(name), ifmatch=True, desc="JSON merge patch (RFC 7396). Read-only fields are ignored.")
    if delete:
        _op("delete", base + "/{id}", tag_, f"delete{name}", f"Delete {split(name)}", f"{perm}:write", status="204", desc=delete_note)

def action(path, tag_, opid, summary, perm, body=None, resp=None, status="200", idem=False, desc=None, params=()):
    _op("post", path, tag_, opid, summary, perm, body=body, resp=resp, status=status, idem=idem, desc=desc, params=params)

def split(camel):
    out = ""
    for ch in camel:
        out += (" " + ch.lower()) if ch.isupper() and out else ch.lower()
    return out

def q(name, sch, desc=None):
    d = {"name": name, "in": "query", "schema": sch}
    if desc:
        d["description"] = desc
    return d

E = "/entities/{entityId}"

# =================================================================== AUTH & ME
tag("Auth", "Sign-in. Email + password, mobile OTP, Google/Microsoft OIDC, or SAML SSO. Tokens are JWTs.")
schema("TokenResponse", {"access_token": S(), "refresh_token": S(), "token_type": ENUM("Bearer"), "expires_in": I(examples=[900])},
       required=("access_token", "token_type", "expires_in"))
_op("post", "/auth/otp", "Auth", "requestOtp", "Send a one-time code", body=schema("OtpRequest", {"mobile_e164": S(), "email": S(format="email")}),
    status="204", security=[], desc="Rate-limited to 5 per hour per identifier.")
_op("post", "/auth/token", "Auth", "issueToken", "Exchange credentials, an OTP or a refresh token for tokens",
    body=schema("TokenRequest", {
        "grant_type": ENUM("password", "otp", "refresh_token", "authorization_code"),
        "email": S(format="email"), "password": S(format="password"), "mobile_e164": S(), "otp": S(pattern=r"^[0-9]{6}$"),
        "refresh_token": S(), "code": S(), "redirect_uri": S(format="uri"), "mfa_code": S()}, required=("grant_type",)),
    resp=ref("TokenResponse"), security=[])
_op("post", "/auth/logout", "Auth", "logout", "Revoke the refresh token", status="204")

tag("Me", "The signed-in user, their entities and preferences")
schema("EntitySummary", {"id": UUID, "legal_name": S(), "constitution": S(), "track": ENUM("business", "startup", "msme"),
                         "role": ENUM("owner", "approver", "maker", "viewer", "advisor", "org_staff"), "modules": ARR(S())})
schema("Me", {"id": RO_UUID, "email": S(format="email"), "full_name": S(), "mobile_e164": S(), "mfa_enabled": B(),
              "entities": ARR(ref("EntitySummary")), "organizations": ARR(schema("OrgSummary", {"id": UUID, "name": S(), "kind": S(), "role": S()}))})
_op("get", "/me", "Me", "getMe", "Who am I, and which entities can I act on", resp=ref("Me"))
schema("NotificationPreference", {"entity_id": UUID, "event_type": ENUM("due_in_7_days", "due_tomorrow", "approval_requested", "notice_received", "threshold_crossed", "copilot_suggestion"),
                                  "channel": ENUM("email", "whatsapp", "push", "sms"), "enabled": B()}, required=("entity_id", "event_type", "channel", "enabled"))
_op("get", "/me/notification-preferences", "Me", "listNotificationPreferences", "My notification matrix", resp=ARR(ref("NotificationPreference")))
_op("put", "/me/notification-preferences", "Me", "replaceNotificationPreferences", "Replace my notification matrix",
    body=ARR(ref("NotificationPreference")), resp=ARR(ref("NotificationPreference")))

# =================================================================== ONBOARDING & ENTITIES
tag("Onboarding", "Look an identifier up in public registries and turn it into a workspace and an obligation graph")
schema("RegistryLookup", {
    "id": RO_UUID, "identifier": S(examples=["U26510KA2019PTC123456"]),
    "identifier_kind": ENUM("cin", "llpin", "gstin", "pan", readOnly=True),
    "status": ENUM("running", "found", "not_found", "failed", readOnly=True),
    "entity": {"type": "object", "readOnly": True, "properties": {
        "legal_name": S(), "constitution": S(), "incorporated_on": DATE, "registered_state": STATE,
        "registered_address": S(), "paid_up_capital": MONEY, "nic_code": S(), "last_agm_on": DATE,
        "directors": ARR(schema("RegistryDirector", {"name": S(), "din": S()}))}},
    "gstins": ARR(schema("RegistryGstin", {"gstin": GSTIN, "state_code": STATE, "status": S(), "filing_frequency": S(), "label": S()}), readOnly=True),
    "udyam": {"type": ["object", "null"], "readOnly": True, "properties": {"number": S(), "class": ENUM("micro", "small", "medium")}},
}, required=("identifier",))
action("/onboarding/lookups", "Onboarding", "createRegistryLookup", "Look up a CIN, LLPIN, GSTIN or PAN",
       None, body=ref("RegistryLookup"), resp=ref("RegistryLookup"), status="201",
       desc="Synchronous when cached (≈ 4 s); otherwise poll `GET /onboarding/lookups/{id}`.")
_op("get", "/onboarding/lookups/{id}", "Onboarding", "getRegistryLookup", "Get a lookup", resp=ref("RegistryLookup"))
schema("OnboardingAnswers", {
    "lookup_id": UUID, "track": ENUM("business", "startup", "msme"), "organization_id": NULLABLE(UUID),
    "gstins": ARR(GSTIN, description="Registrations to manage"),
    "headcount_band": ENUM("1-9", "10-19", "20-49", "50+"),
    "turnover_band": ENUM("<1.5cr", "1.5-5cr", "5-10cr", ">10cr"),
    "has_foreign_shareholder": B(), "buys_from_mse": ENUM("yes", "no", "unsure")}, required=("lookup_id", "track"))
schema("ObligationPreview", {"total": I(), "by_authority": ARR(schema("AuthorityCount", {"authority": S(), "count": I()})),
                             "triggers": ARR(schema("Trigger", {"why": S(), "what": S()})),
                             "first_due": ARR(schema("DueItem", {"title": S(), "authority": S(), "due_date": DATE}))})
action("/onboarding/preview", "Onboarding", "previewObligations", "Preview the obligation graph for a set of answers (no writes)",
       None, body=ref("OnboardingAnswers"), resp=ref("ObligationPreview"),
       desc="Powers the live count while the user answers questions.")
action("/onboarding/workspaces", "Onboarding", "createWorkspace", "Create the entity, registrations, facts and first engine run",
       None, body=ref("OnboardingAnswers"), resp=schema("WorkspaceCreated", {"entity_id": UUID, "engine_run_id": UUID, "obligations_created": I()}),
       status="201", idem=True)

tag("Entities", "Legal entities, their registrations, members, advisors and facts")
schema("Entity", {
    "id": RO_UUID, "organization_id": UUID, "legal_name": S(), "trade_name": S(),
    "constitution": ENUM("private_limited", "public_limited", "opc", "section8", "llp", "partnership", "proprietorship", "trust", "society", "huf"),
    "track": ENUM("business", "startup", "msme"), "pan": PAN, "cin": S(), "llpin": S(), "incorporated_on": DATE,
    "registered_state": STATE, "registered_address": S(), "nic_code": S(),
    "udyam_number": S(), "udyam_class": NULLABLE(ENUM("micro", "small", "medium")), "dpiit_number": S(),
    "created_at": RO_TS}, required=("legal_name", "constitution"))
_op("get", E, "Entities", "getEntity", "Get the entity", "settings:read", resp=ref("Entity"))
_op("patch", E, "Entities", "updateEntity", "Update the entity", "settings:write", body=ref("Entity"), resp=ref("Entity"), ifmatch=True)
schema("Registration", {"id": RO_UUID, "kind": ENUM("gstin", "tan", "epf", "esic", "professional_tax", "iec", "shops_establishment", "factory_licence", "fssai", "trade_licence", "lut", "other"),
                        "number": S(), "state_code": STATE, "label": S(), "gst_filing_frequency": NULLABLE(ENUM("monthly", "qrmp", "composition")),
                        "valid_from": DATE, "valid_until": NULLABLE(DATE), "status": ENUM("active", "suspended", "cancelled", "applied")},
       required=("kind", "number"))
collection(E + "/registrations", "Entities", "Registration", "settings", filters=[q("kind", S())])
item(E + "/registrations", "Entities", "Registration", "settings")
schema("Member", {"id": RO_UUID, "user_id": UUID, "email": S(format="email"), "full_name": S(readOnly=True),
                  "role": ENUM("owner", "approver", "maker", "viewer"), "modules": ARR(S(), examples=[["gst", "finance"]]),
                  "accepted_at": NULLABLE(RO_TS), "auth": S(readOnly=True, examples=["SSO · 2FA"])}, required=("role",))
collection(E + "/members", "Entities", "Member", "settings", create_schema=ref("Member"))
item(E + "/members", "Entities", "Member", "settings", delete=True, delete_note="Revokes access; history keeps the person's name.")
schema("AdvisorGrant", {"id": RO_UUID, "advisor_org_id": NULLABLE(UUID), "advisor_name": S(),
                        "advisor_kind": ENUM("chartered_accountant", "company_secretary", "counsel", "lender", "investor", "auditor"),
                        "invite_email": S(format="email", writeOnly=True),
                        "scopes": {"type": "object", "additionalProperties": ENUM("read", "comment", "prepare", "certify"),
                                   "examples": [{"finance": "read", "gst": "prepare", "mca_financial": "certify"}]},
                        "document_folders": ARR(S()), "expires_on": NULLABLE(DATE),
                        "status": ENUM("invited", "active", "revoked", "expired", readOnly=True)}, required=("advisor_name", "advisor_kind", "scopes"))
collection(E + "/advisor-grants", "Entities", "AdvisorGrant", "settings")
item(E + "/advisor-grants", "Entities", "AdvisorGrant", "settings", delete=True, delete_note="Revokes the grant immediately.")
schema("Fact", {"fact_key": S(examples=["aato", "headcount", "paid_up_capital", "cash_receipts_pct"]),
                "value": {"type": ["number", "string", "boolean"]}, "as_of": DATE,
                "source": ENUM("books", "payroll", "registry", "user", "engine", "advisor", readOnly=True)}, required=("fact_key", "value", "as_of"))
_op("get", E + "/facts", "Entities", "listFacts", "Current facts the engine uses", "compliance:read", resp=ARR(ref("Fact")),
    params=[q("history", B(), "Include every recorded value")])
action(E + "/facts", "Entities", "recordFacts", "Record facts (each is a new, dated value)", "compliance:write",
       body=ARR(ref("Fact")), resp=ARR(ref("Fact")), status="201",
       desc="A changed fact triggers an engine run when it can switch an obligation on or off.")
schema("DscCertificate", {"id": RO_UUID, "person_name": S(), "dsc_class": S(), "valid_from": DATE, "valid_until": DATE,
                          "registered_on": ARR(ENUM("MCA", "GSTN", "ITD"))}, required=("person_name", "valid_from", "valid_until"))
collection(E + "/dsc-certificates", "Entities", "DscCertificate", "settings")

# =================================================================== COMPLIANCE ENGINE
tag("Compliance", "Rules, obligations, calendar, thresholds and notices")
schema("Rule", {"id": RO_UUID, "code": S(examples=["GST.GSTR1.MONTHLY"]), "version": I(), "title": S(), "form_code": S(),
                "authority_code": S(), "statute_ref": S(), "frequency": S(), "applicability": {"type": "object"},
                "due_rule": {"type": "object"}, "penalty_rule": {"type": "object"},
                "effective_from": DATE, "effective_to": NULLABLE(DATE), "change_note": S()})
_op("get", "/rules", "Compliance", "listRules", "Rule library (global, versioned)", params=[q("authority", S()), q("as_of", DATE)], resp=page_of("Rule"))
_op("get", "/rules/{id}", "Compliance", "getRule", "Get a rule version", resp=ref("Rule"))
schema("Obligation", {
    "id": RO_UUID, "rule": ref("ObligationRule"),
    "registration": NULLABLE(schema("RegistrationRef", {"id": UUID, "kind": S(), "number": S(), "label": S()})),
    "fy": FY, "period_label": S(examples=["Oct 2026"]), "period_start": DATE, "period_end": DATE,
    "due_date": DATE, "extended_due_date": NULLABLE(DATE), "days_left": I(readOnly=True),
    "status": ENUM("upcoming", "in_preparation", "awaiting_client", "awaiting_approval", "ready_to_file", "filed", "overdue", "not_applicable", "waived"),
    "owner_user_id": NULLABLE(UUID), "approver_user_id": NULLABLE(UUID),
    "late_fee_accrued": MONEY, "interest_accrued": MONEY,
    "reason": {"type": "object", "readOnly": True, "description": "Facts and rule clause that made it apply"},
    "filed_at": NULLABLE(RO_TS), "acknowledgement": NULLABLE(S(readOnly=True, examples=["AA2910260012345"])),
    "draft_document_id": NULLABLE(UUID), "approval_request_id": NULLABLE(RO_UUID)})
schema("ObligationRule", {"id": UUID, "code": S(), "title": S(), "form_code": S(), "authority_code": S(), "statute_ref": S(), "penalty_rule": {"type": "object"}}, extra={"readOnly": True})
_op("get", E + "/obligations", "Compliance", "listObligations", "List obligations", "compliance:read",
    params=[P("Limit"), P("Cursor"), P("Sort"), q("status", ARR(S()), "Repeatable"), q("authority", S()),
            q("registration_id", UUID), q("due_before", DATE), q("due_after", DATE), q("owner", S(), "`me` or a user id"), q("fy", FY)],
    resp=page_of("Obligation"))
item(E + "/obligations", "Compliance", "Obligation", "compliance")
action(E + "/obligations/{id}/prepare", "Compliance", "prepareObligation", "Build the draft return / form from the books", "compliance:write",
       status="202", desc="e.g. GSTR-1 from issued sales documents, ECR from the payroll run. The job attaches `draft_document_id`.")
action(E + "/obligations/{id}/submit", "Compliance", "submitObligationForApproval", "Send the draft for approval", "compliance:write",
       body=schema("SubmitForApproval", {"assigned_to": NULLABLE(UUID), "note": S()}), resp=ref("ApprovalRequest"), status="201")
action(E + "/obligations/{id}/file", "Compliance", "fileObligation", "File with the portal (after approval)", "compliance:file",
       body=schema("FileRequest", {"signing": ENUM("dsc", "evc_otp", "none"), "dsc_certificate_id": NULLABLE(UUID)}),
       status="202", idem=True, desc="Fails with `approval_required` unless the approval request is approved. The job ends with the ARN/SRN.")
action(E + "/obligations/{id}/mark-filed", "Compliance", "markObligationFiled", "Record a filing made outside Biz360X", "compliance:file",
       body=schema("MarkFiled", {"filed_at": TS, "acknowledgement": S(), "document_id": NULLABLE(UUID)}, required=("filed_at", "acknowledgement")),
       resp=ref("Obligation"), idem=True)
schema("CalendarDay", {"date": DATE, "items": ARR(schema("CalendarItem", {"obligation_id": UUID, "title": S(), "authority": S(), "status": S(), "registration": S()}))})
_op("get", E + "/compliance/calendar", "Compliance", "getComplianceCalendar", "Obligations by day", "compliance:read",
    params=[P("From"), P("To")], resp=ARR(ref("CalendarDay")))
schema("AuthorityStanding", {"authority": S(), "overdue": I(), "waiting_on_client": I(), "due_7_days": I(), "last_filed_at": NULLABLE(TS),
                             "status": ENUM("clear", "on_track", "action_due", "overdue")})
_op("get", E + "/compliance/standing", "Compliance", "getAuthorityStanding", "Standing with each authority", "compliance:read", resp=ARR(ref("AuthorityStanding")))
schema("ComplianceSummary", {"health_score": I(minimum=0, maximum=100), "filed_this_fy": I(), "due_30_days": I(),
                             "needs_action": I(), "overdue": I(), "late_fee_exposure": MONEY})
_op("get", E + "/compliance/summary", "Compliance", "getComplianceSummary", "Headline numbers", "compliance:read", resp=ref("ComplianceSummary"))
schema("ThresholdWatch", {"rule_code": S(), "title": S(), "fact_key": S(), "threshold": N(), "current_value": N(), "pct": N(), "note": S()})
_op("get", E + "/compliance/thresholds", "Compliance", "listThresholds", "Duties that switch on as you grow", "compliance:read", resp=ARR(ref("ThresholdWatch")))
schema("EngineRun", {"id": RO_UUID, "trigger": ENUM("schedule", "fact_change", "rule_change", "manual", "onboarding", readOnly=True),
                     "started_at": RO_TS, "finished_at": NULLABLE(RO_TS), "rules_evaluated": I(readOnly=True),
                     "obligations_created": I(readOnly=True), "obligations_changed": I(readOnly=True)})
action(E + "/compliance/engine-runs", "Compliance", "runEngine", "Re-run the engine now", "compliance:write", resp=ref("EngineRun"), status="201")
_op("get", E + "/compliance/engine-runs/{id}", "Compliance", "getEngineRun", "Get an engine run", "compliance:read", resp=ref("EngineRun"))
schema("Notice", {"id": RO_UUID, "authority_code": S(), "registration_id": NULLABLE(UUID), "form_code": S(examples=["ASMT-10"]),
                  "reference_no": S(), "period_label": S(), "received_on": DATE, "reply_due_on": NULLABLE(DATE), "hearing_on": NULLABLE(DATE),
                  "demand_amount": NULLABLE(MONEY), "status": ENUM("open", "draft_reply", "replied", "hearing", "closed", "appealed"),
                  "summary": S(), "linked_obligation_id": NULLABLE(UUID), "document_ids": ARR(UUID)}, required=("authority_code", "form_code", "received_on"))
collection(E + "/notices", "Compliance", "Notice", "compliance", filters=[q("status", S())])
item(E + "/notices", "Compliance", "Notice", "compliance")
action(E + "/notices/{id}/draft-reply", "Compliance", "draftNoticeReply", "Ask the copilot to draft a reply from the matched records", "copilot:use",
       status="202")

# =================================================================== APPROVALS, SIGNATURES, TASKS
tag("Approvals", "Maker–checker approvals, e-sign / DSC signature requests, and tasks")
schema("ApprovalRequest", {
    "id": RO_UUID, "subject": ENUM("obligation", "sales_document", "purchase_bill", "vendor_payment", "payroll_run", "journal_entry",
                                    "contract", "board_resolution", "copilot_action", "credit_note", "quote", "notice_reply"),
    "subject_id": UUID, "summary": S(), "amount": NULLABLE(MONEY), "requested_by": RO_UUID, "assigned_to": NULLABLE(UUID),
    "due_on": NULLABLE(DATE), "status": ENUM("pending", "approved", "rejected", "withdrawn", "expired", readOnly=True),
    "decided_by": NULLABLE(RO_UUID), "decided_at": NULLABLE(RO_TS), "decision_note": S(readOnly=True), "created_at": RO_TS})
_op("get", E + "/approvals", "Approvals", "listApprovals", "Approvals inbox", "approvals:read",
    params=[P("Limit"), P("Cursor"), q("status", S(default="pending")), q("assigned_to", S(), "`me` or a user id"), q("subject", S())],
    resp=page_of("ApprovalRequest"))
_op("get", E + "/approvals/{id}", "Approvals", "getApproval", "Get an approval request", "approvals:read", resp=ref("ApprovalRequest"))
schema("Decision", {"note": S(), "dsc_certificate_id": NULLABLE(UUID)})
action(E + "/approvals/{id}/approve", "Approvals", "approveRequest", "Approve", "approvals:approve", body=ref("Decision"), resp=ref("ApprovalRequest"),
       idem=True, desc="Rejected with `two_person_rule` when the caller requested it. Approving a filing does not file it — call the file action.")
action(E + "/approvals/{id}/reject", "Approvals", "rejectRequest", "Reject", "approvals:approve", body=ref("Decision"), resp=ref("ApprovalRequest"), idem=True)
action(E + "/approvals/{id}/withdraw", "Approvals", "withdrawRequest", "Withdraw my request", "approvals:read", resp=ref("ApprovalRequest"))
schema("Signer", {"person_id": NULLABLE(UUID), "external_name": S(), "external_email": S(format="email"), "sign_order": I(),
                  "status": ENUM("queued", "waiting", "signed", "declined", readOnly=True), "signed_at": NULLABLE(RO_TS)})
schema("SignatureRequest", {"id": RO_UUID, "subject": S(), "subject_id": UUID, "document_id": UUID,
                            "method": ENUM("dsc", "aadhaar_esign", "click_to_sign"), "signers": ARR(ref("Signer")),
                            "status": ENUM("draft", "sent", "partially_signed", "completed", "declined", "expired", readOnly=True),
                            "sent_at": NULLABLE(RO_TS), "completed_at": NULLABLE(RO_TS)}, required=("subject", "subject_id", "document_id", "method", "signers"))
collection(E + "/signature-requests", "Approvals", "SignatureRequest", "approvals", filters=[q("status", S())], idem=True)
_op("get", E + "/signature-requests/{id}", "Approvals", "getSignatureRequest", "Get a signature request", "approvals:read", resp=ref("SignatureRequest"))
action(E + "/signature-requests/{id}/remind", "Approvals", "remindSigners", "Remind pending signers", "approvals:read", status="204")
schema("Task", {"id": RO_UUID, "title": S(), "subject": NULLABLE(S()), "subject_id": NULLABLE(UUID), "assignee_id": NULLABLE(UUID),
                "due_on": NULLABLE(DATE), "status": ENUM("open", "done", "cancelled"), "source": ENUM("user", "engine", "dunning", "copilot", "advisor", readOnly=True)},
       required=("title",))
collection(E + "/tasks", "Approvals", "Task", "tasks", filters=[q("assignee", S()), q("status", S())])
item(E + "/tasks", "Approvals", "Task", "tasks")

# =================================================================== PARTIES & ITEMS
tag("Parties & items", "Customers, vendors and the item catalogue")
schema("PartyGstin", {"gstin": NULLABLE(GSTIN), "state_code": STATE, "taxpayer_type": ENUM("regular", "composition", "sez_unit", "sez_developer", "uin", "non_resident", "unregistered", "overseas"),
                      "legal_name_on_gstn": S(readOnly=True), "gstn_status": S(readOnly=True), "verified_at": NULLABLE(RO_TS), "is_default": B()})
schema("Address", {"kind": ENUM("billing", "shipping", "registered"), "line1": S(), "line2": S(), "city": S(), "state_code": STATE,
                   "pincode": S(pattern=r"^[1-9][0-9]{5}$"), "country": S(default="IN")}, required=("kind", "line1", "city"))
schema("Contact", {"name": S(), "email": S(format="email"), "mobile_e164": S(), "whatsapp_opt_in": B(), "is_billing_contact": B()}, required=("name",))
schema("Party", {"id": RO_UUID, "legal_name": S(), "display_name": S(), "is_customer": B(), "is_vendor": B(), "pan": PAN,
                 "msme_class": NULLABLE(ENUM("micro", "small", "medium")), "udyam_number": S(), "payment_terms_days": I(),
                 "credit_limit": NULLABLE(MONEY), "deducts_tds_194q": B(), "default_tds_section": S(),
                 "status": ENUM("active", "on_hold", "blocked", "archived"), "gstins": ARR(ref("PartyGstin")),
                 "addresses": ARR(ref("Address")), "contacts": ARR(ref("Contact")),
                 "balance_receivable": {**MONEY, "readOnly": True}, "balance_payable": {**MONEY, "readOnly": True}},
       required=("legal_name",))
collection(E + "/parties", "Parties & items", "Party", "parties", filters=[q("role", ENUM("customer", "vendor")), P("Q"), q("msme", B())],
           summary_plural="Parties")
item(E + "/parties", "Parties & items", "Party", "parties")
action(E + "/parties/{id}/verify-gstin", "Parties & items", "verifyPartyGstin", "Check the party's GSTIN status on GSTN", "parties:write", resp=ref("Party"))
schema("StatementLine", {"date": DATE, "kind": S(), "reference": S(), "debit": MONEY, "credit": MONEY, "balance": MONEY})
_op("get", E + "/parties/{id}/statement", "Parties & items", "getPartyStatement", "Statement of account", "parties:read",
    params=[P("From"), P("To"), q("format", ENUM("json", "pdf"))], resp=ARR(ref("StatementLine")))
schema("Item", {"id": RO_UUID, "sku": S(), "name": S(), "kind": ENUM("goods", "service"), "hsn_sac": HSN, "uqc": S(default="NOS"),
                "gst_rate": N(enum=[0, 0.1, 0.25, 1.5, 3, 5, 12, 18, 28, 40]), "cess_rate": N(), "sale_rate": NULLABLE(MONEY),
                "purchase_rate": NULLABLE(MONEY), "track_inventory": B(), "is_active": B()}, required=("sku", "name", "kind", "hsn_sac", "gst_rate"))
collection(E + "/items", "Parties & items", "Item", "parties", filters=[P("Q")])
item(E + "/items", "Parties & items", "Item", "parties")

# =================================================================== SALES & BILLING
tag("Sales & billing", "Quotes, orders, invoices, credit/debit notes, e-invoice, e-way bills, payment links, receipts, dunning")
LINE_IN = schema("DocumentLineInput", {"item_id": NULLABLE(UUID), "description": S(), "hsn_sac": HSN, "qty": S(pattern=r"^[0-9]+(\.[0-9]{1,3})?$"),
                                        "uqc": S(), "rate": MONEY, "discount": MONEY, "gst_rate": N(), "so_line_id": NULLABLE(I()),
                                        "original_line_id": NULLABLE(I())}, required=("description", "hsn_sac", "qty", "rate", "gst_rate"))
schema("DocumentLine", {"line_no": I(readOnly=True), "taxable_value": MONEY, "cgst_amount": MONEY, "sgst_amount": MONEY,
                        "igst_amount": MONEY, "cess_amount": MONEY}, extra={"allOf": [LINE_IN]})
schema("Quote", {"id": RO_UUID, "quote_no": S(readOnly=True), "version": I(readOnly=True), "party_id": UUID, "registration_id": UUID,
                 "title": S(), "status": ENUM("draft", "sent", "viewed", "negotiating", "accepted", "rejected", "expired", "lost", readOnly=True),
                 "place_of_supply": STATE, "valid_until": DATE, "advance_pct": N(), "payment_terms_days": I(),
                 "lines": ARR(LINE_IN), "taxable_total": {**MONEY, "readOnly": True}, "tax_total": {**MONEY, "readOnly": True},
                 "grand_total": {**MONEY, "readOnly": True}, "view_count": I(readOnly=True), "customer_po_ref": S(),
                 "invoiced_pct": N(readOnly=True), "sales_order_id": NULLABLE(RO_UUID)}, required=("party_id", "registration_id", "lines"))
collection(E + "/quotes", "Sales & billing", "Quote", "sales", filters=[q("status", S()), q("party_id", UUID)])
item(E + "/quotes", "Sales & billing", "Quote", "sales")
action(E + "/quotes/{id}/send", "Sales & billing", "sendQuote", "Email / WhatsApp the quote", "sales:write",
       body=schema("SendRequest", {"channels": ARR(ENUM("email", "whatsapp")), "to": ARR(S()), "message": S()}), resp=ref("Quote"))
action(E + "/quotes/{id}/revise", "Sales & billing", "reviseQuote", "Create the next version", "sales:write", resp=ref("Quote"), status="201")
action(E + "/quotes/{id}/accept", "Sales & billing", "acceptQuote", "Record acceptance and open a sales order", "sales:write",
       body=schema("AcceptQuote", {"accepted_at": TS, "customer_po_ref": S()}), resp=ref("SalesOrder"), status="201", idem=True)
action(E + "/quotes/{id}/mark-lost", "Sales & billing", "markQuoteLost", "Mark lost", "sales:write", body=schema("LostReason", {"reason": S()}), resp=ref("Quote"))
schema("SalesOrderLine", {"id": I(readOnly=True), "description": S(), "hsn_sac": HSN, "ordered_qty": S(), "invoiced_qty": S(readOnly=True), "rate": MONEY, "gst_rate": N()})
schema("SalesOrder", {"id": RO_UUID, "order_no": S(readOnly=True), "quote_id": NULLABLE(UUID), "party_id": UUID, "order_date": DATE,
                      "customer_po_ref": S(), "status": ENUM("open", "partially_invoiced", "invoiced", "closed", "cancelled", readOnly=True),
                      "lines": ARR(ref("SalesOrderLine"))})
collection(E + "/sales-orders", "Sales & billing", "SalesOrder", "sales", filters=[q("status", S())])
item(E + "/sales-orders", "Sales & billing", "SalesOrder", "sales", patch=False)
action(E + "/sales-orders/{id}/invoice", "Sales & billing", "invoiceSalesOrder", "Draft an invoice for the remaining (or given) quantities", "sales:write",
       body=schema("InvoiceFromOrder", {"lines": ARR(schema("OrderLineQty", {"so_line_id": I(), "qty": S()})), "doc_date": DATE}),
       resp=ref("SalesDocument"), status="201", desc="Powers \"Invoice lot 2\".")

schema("TaxCheck", {"code": S(examples=["eway_bill_required"]), "level": ENUM("ok", "info", "warn", "block"), "title": S(), "detail": S()})
schema("TaxPreviewRequest", {"doc_type": ENUM("tax_invoice", "credit_note", "debit_note", "export_invoice", "bill_of_supply"),
                             "registration_id": UUID, "party_id": UUID, "party_gst_id": NULLABLE(UUID), "place_of_supply": NULLABLE(STATE),
                             "doc_date": DATE, "payment_terms_days": I(), "lines": ARR(LINE_IN), "original_document_id": NULLABLE(UUID),
                             "cn_reason": NULLABLE(S()), "gst_adjusted": B(default=True)}, required=("doc_type", "registration_id", "party_id", "lines"))
schema("TaxPreview", {"supply_type": S(), "is_inter_state": B(), "place_of_supply": STATE, "lines": ARR(ref("DocumentLine")),
                      "taxable_total": MONEY, "cgst_total": MONEY, "sgst_total": MONEY, "igst_total": MONEY, "cess_total": MONEY,
                      "round_off": MONEY, "grand_total": MONEY, "amount_in_words": S(), "due_date": DATE,
                      "tds_expected": MONEY, "expected_receipt": MONEY, "next_number": S(examples=["NI/KA/213"]),
                      "checks": ARR(ref("TaxCheck"))})
action(E + "/sales-documents/preview", "Sales & billing", "previewSalesDocument", "Compute tax split, totals and pre-send checks without saving",
       "sales:read", body=ref("TaxPreviewRequest"), resp=ref("TaxPreview"),
       desc="Stateless. The invoice and credit-note editors call this on every change: IGST vs CGST+SGST, e-invoice, e-way bill (> ₹50,000), 194Q TDS, MSME terms, Sec 34 time limit for credit notes.")
schema("SalesDocument", {
    "id": RO_UUID, "doc_type": ENUM("tax_invoice", "bill_of_supply", "export_invoice", "credit_note", "debit_note", "proforma"),
    "number": NULLABLE(S(readOnly=True, maxLength=16)), "doc_date": DATE, "fy": {**FY, "readOnly": True},
    "registration_id": UUID, "party_id": UUID, "party_gst_id": NULLABLE(UUID), "buyer_gstin": NULLABLE(S(readOnly=True)),
    "place_of_supply": STATE, "is_inter_state": B(readOnly=True),
    "supply_type": ENUM("b2b", "b2c_small", "b2c_large", "export_with_tax", "export_under_lut", "sez_with_tax", "sez_under_lut", "deemed_export"),
    "reverse_charge": B(), "quote_id": NULLABLE(UUID), "sales_order_id": NULLABLE(UUID), "customer_po_ref": S(),
    "original_document_id": NULLABLE(UUID), "cn_reason": NULLABLE(ENUM("sales_return", "rate_revision", "post_sale_discount", "deficiency_in_service", "correction", "other")),
    "gst_adjusted": B(default=True, description="false = commercial credit note"),
    "payment_terms_days": I(), "due_date": DATE, "lines": ARR(ref("DocumentLine")),
    "taxable_total": {**MONEY, "readOnly": True}, "cgst_total": {**MONEY, "readOnly": True}, "sgst_total": {**MONEY, "readOnly": True},
    "igst_total": {**MONEY, "readOnly": True}, "grand_total": {**MONEY, "readOnly": True}, "tds_expected": {**MONEY, "readOnly": True},
    "amount_settled": {**MONEY, "readOnly": True}, "balance_due": {**MONEY, "readOnly": True},
    "status": ENUM("draft", "issued", "cancelled", readOnly=True),
    "payment_status": ENUM("unpaid", "partially_paid", "paid", "written_off", "not_applicable", readOnly=True),
    "einvoice": schema("EInvoice", {"status": ENUM("not_required", "pending", "generated", "cancelled", "failed"), "irn": NULLABLE(S()),
                                     "ack_no": NULLABLE(S()), "ack_at": NULLABLE(TS), "signed_qr": NULLABLE(S()), "cancel_deadline": NULLABLE(TS)}, extra={"readOnly": True}),
    "eway_bill_ids": ARR(UUID, readOnly=True), "payment_link": NULLABLE(ref("PaymentLink")),
    "pdf_url": NULLABLE(S(format="uri", readOnly=True)), "issued_at": NULLABLE(RO_TS)}, required=("doc_type", "doc_date", "registration_id", "party_id", "lines"))
_op("get", E + "/sales-documents", "Sales & billing", "listSalesDocuments", "List invoices and notes", "sales:read",
    params=[P("Limit"), P("Cursor"), P("Sort"), q("doc_type", S()), q("status", S()), q("payment_status", ARR(S())), q("party_id", UUID),
            q("overdue", B()), P("From"), P("To"), P("Q")], resp=page_of("SalesDocument"))
_op("post", E + "/sales-documents", "Sales & billing", "createSalesDocument", "Create a draft invoice, credit note or debit note", "sales:write",
    body=ref("SalesDocument"), resp=ref("SalesDocument"), status="201",
    desc="Drafts have no number. Totals and tax split are computed server-side exactly as in the preview.")
item(E + "/sales-documents", "Sales & billing", "SalesDocument", "sales", delete=True, delete_note="Drafts only. Issued documents are cancelled, never deleted.")
action(E + "/sales-documents/{id}/issue", "Sales & billing", "issueSalesDocument", "Issue: take the next number, generate IRN, post to the ledger", "sales:write",
       body=schema("IssueRequest", {"send": NULLABLE(ref("SendRequest")), "create_payment_link": B(default=True), "generate_eway_bill": B(default=True)}),
       status="202", idem=True,
       desc="Synchronously assigns the number and posts the journal; the IRN (and e-way bill Part A) come back on the job. "
            "The document is `issued` with `einvoice.status = pending` until the IRP responds.")
action(E + "/sales-documents/{id}/cancel", "Sales & billing", "cancelSalesDocument", "Cancel (IRN cancellation within 24 h of generation)", "sales:write",
       body=schema("CancelRequest", {"reason": ENUM("duplicate", "data_entry_mistake", "order_cancelled", "other"), "remark": S()}, required=("reason",)),
       status="202", idem=True)
action(E + "/sales-documents/{id}/send", "Sales & billing", "sendSalesDocument", "Email / WhatsApp the PDF and payment link", "sales:write",
       body=ref("SendRequest"), status="204")
action(E + "/sales-documents/{id}/credit-notes", "Sales & billing", "createCreditNoteFor", "Draft a credit note against this invoice", "sales:write",
       body=schema("CreditNoteRequest", {"cn_reason": ENUM("sales_return", "rate_revision", "post_sale_discount", "deficiency_in_service", "correction", "other"),
                                         "gst_adjusted": B(default=True), "lines": ARR(LINE_IN), "agreed_before_supply": B()}, required=("cn_reason", "lines")),
       resp=ref("SalesDocument"), status="201",
       desc="Rejected after the Sec 34 time limit (30 Nov after FY end, or annual return date if earlier) unless `gst_adjusted=false`.")
schema("EwayBill", {"id": RO_UUID, "sales_document_id": NULLABLE(UUID), "purpose": ENUM("supply", "sales_return", "branch_transfer", "job_work", "export", "other"),
                    "ewb_no": NULLABLE(S(readOnly=True)), "goods_value": MONEY, "from_pincode": S(), "to_pincode": S(), "distance_km": I(),
                    "transport_mode": ENUM("road", "rail", "air", "ship"), "transporter_gstin": NULLABLE(S()), "vehicle_no": NULLABLE(S()),
                    "valid_until": NULLABLE(RO_TS), "status": ENUM("draft", "generated", "extended", "cancelled", "expired", readOnly=True)})
collection(E + "/eway-bills", "Sales & billing", "EwayBill", "sales", filters=[q("status", S())])
action(E + "/eway-bills/{id}/vehicle", "Sales & billing", "updateEwayVehicle", "Add or change Part B (vehicle)", "sales:write",
       body=schema("VehicleUpdate", {"vehicle_no": S(), "reason": S()}, required=("vehicle_no",)), status="202", idem=True)
schema("PaymentLink", {"id": RO_UUID, "sales_document_id": UUID, "short_code": S(readOnly=True), "url": S(format="uri", readOnly=True),
                       "amount": MONEY, "methods": ARR(ENUM("bank_transfer", "upi", "netbanking", "card")),
                       "virtual_account": NULLABLE(schema("VirtualAccount", {"va_number": S(), "ifsc": S(), "bank_name": S()}, extra={"readOnly": True})),
                       "status": ENUM("active", "paid", "expired", "cancelled", readOnly=True), "first_viewed_at": NULLABLE(RO_TS), "expires_at": NULLABLE(TS)})
action(E + "/sales-documents/{id}/payment-links", "Sales & billing", "createPaymentLink", "Create a payment link and virtual account", "sales:write",
       body=ref("PaymentLink"), resp=ref("PaymentLink"), status="201", idem=True)
schema("Allocation", {"invoice_id": UUID, "amount": MONEY}, required=("invoice_id", "amount"))
schema("Receipt", {"id": RO_UUID, "receipt_no": S(readOnly=True), "party_id": UUID, "received_on": DATE, "amount": MONEY,
                   "method": ENUM("neft", "rtgs", "imps", "upi", "cheque", "card", "netbanking", "treds", "cash", "adjustment"),
                   "utr": S(), "bank_account_id": NULLABLE(UUID), "bank_transaction_id": NULLABLE(UUID), "is_advance": B(),
                   "tds_deducted": NULLABLE(schema("TdsOnReceipt", {"section": S(examples=["194Q"]), "amount": MONEY})),
                   "allocations": ARR(ref("Allocation")), "unallocated": {**MONEY, "readOnly": True},
                   "status": ENUM("pending", "confirmed", "bounced", "reversed", readOnly=True)}, required=("party_id", "received_on", "amount", "method"))
collection(E + "/receipts", "Sales & billing", "Receipt", "sales", filters=[q("party_id", UUID), P("From"), P("To")], idem=True)
item(E + "/receipts", "Sales & billing", "Receipt", "sales", patch=False)
action(E + "/receipts/{id}/allocate", "Sales & billing", "allocateReceipt", "Allocate (more of) a receipt to invoices", "sales:write",
       body=ARR(ref("Allocation")), resp=ref("Receipt"), idem=True, desc="Fails with `over_settlement` if an invoice would be over-settled.")
schema("AgeingRow", {"party_id": UUID, "legal_name": S(), "msme_class": NULLABLE(S()), "b0_30": MONEY, "b31_45": MONEY, "b46_60": MONEY,
                     "b61_90": MONEY, "b90_plus": MONEY, "total": MONEY, "interest_accrued_msmed": NULLABLE(MONEY)})
_op("get", E + "/receivables/ageing", "Sales & billing", "getReceivablesAgeing", "Receivables ageing by customer", "sales:read",
    params=[q("as_of", DATE)], resp=ARR(ref("AgeingRow")))
schema("SalesSummary", {"invoiced": MONEY, "collected": MONEY, "outstanding": MONEY, "overdue": MONEY, "overdue_count": I(),
                        "tds_to_claim": MONEY, "collected_by_method": {"type": "object", "additionalProperties": MONEY}})
_op("get", E + "/sales/summary", "Sales & billing", "getSalesSummary", "Headline numbers for a period", "sales:read",
    params=[P("From"), P("To")], resp=ref("SalesSummary"))
schema("DunningStep", {"offset_days": I(examples=[-3, 0, 7, 15, 30]), "action": ENUM("message", "task", "escalate", "hold_orders"),
                       "channels": ARR(ENUM("email", "whatsapp", "sms")), "template_key": S(), "attach_statement": B()}, required=("offset_days", "action"))
schema("DunningSequence", {"id": RO_UUID, "name": S(), "is_default": B(), "steps": ARR(ref("DunningStep"))}, required=("name", "steps"))
collection(E + "/dunning-sequences", "Sales & billing", "DunningSequence", "sales")
item(E + "/dunning-sequences", "Sales & billing", "DunningSequence", "sales")
action(E + "/parties/{id}/pause-dunning", "Sales & billing", "pauseDunning", "Pause reminders for a customer", "sales:write",
       body=schema("PauseDunning", {"until": DATE, "reason": S()}, required=("until",)), status="204")
schema("RecurringInvoice", {"id": RO_UUID, "name": S(), "party_id": UUID, "registration_id": UUID, "template_document_id": UUID,
                            "frequency": ENUM("monthly", "quarterly", "half_yearly", "annual"), "bill_in_advance": B(), "next_run_on": DATE,
                            "ends_on": NULLABLE(DATE), "auto_issue": B(), "is_active": B()}, required=("name", "party_id", "registration_id", "template_document_id", "frequency", "next_run_on"))
collection(E + "/recurring-invoices", "Sales & billing", "RecurringInvoice", "sales")
item(E + "/recurring-invoices", "Sales & billing", "RecurringInvoice", "sales", delete=True)
schema("DocumentSeries", {"id": RO_UUID, "registration_id": UUID, "doc_type": S(), "fy": FY, "prefix": S(maxLength=13), "pad_to": I(), "next_number": I()},
       required=("registration_id", "doc_type", "fy", "prefix"))
collection(E + "/document-series", "Sales & billing", "DocumentSeries", "settings", summary_plural="DocumentSeriesList")

# Public payment page (no auth)
tag("Public pay", "The customer-facing payment page. No authentication; rate-limited; exposes only what is printed on the invoice.")
schema("PublicInvoice", {"supplier_name": S(), "supplier_gstin": GSTIN, "buyer_name": S(), "number": S(), "doc_date": DATE, "due_date": DATE,
                         "taxable_total": MONEY, "tax_total": MONEY, "amount_due": MONEY, "days_overdue": I(), "irn_verified": B(),
                         "pdf_url": S(format="uri"), "methods": ARR(S()), "virtual_account": NULLABLE(ref("VirtualAccount")),
                         "status": ENUM("due", "paid", "cancelled")})
_op("get", "/pay/{shortCode}", "Public pay", "getPublicInvoice", "Invoice summary for the payment page",
    params=[{"name": "shortCode", "in": "path", "required": True, "schema": S()}], resp=ref("PublicInvoice"), security=[])
schema("PaymentIntent", {"method": ENUM("upi", "netbanking", "card", "bank_transfer"), "return_url": S(format="uri")}, required=("method",))
schema("PaymentIntentResult", {"redirect_url": NULLABLE(S(format="uri")), "upi_intent": NULLABLE(S()), "instructions": NULLABLE(S()), "reference": S()})
_op("post", "/pay/{shortCode}/intents", "Public pay", "createPaymentIntent", "Start a payment",
    params=[{"name": "shortCode", "in": "path", "required": True, "schema": S()}], body=ref("PaymentIntent"), resp=ref("PaymentIntentResult"),
    status="201", idem=True, security=[])
_op("post", "/pay/{shortCode}/queries", "Public pay", "raiseInvoiceQuery", "Customer raises a query about the invoice",
    params=[{"name": "shortCode", "in": "path", "required": True, "schema": S()}],
    body=schema("InvoiceQuery", {"name": S(), "email": S(format="email"), "message": S(maxLength=2000)}, required=("email", "message")),
    status="204", security=[])

# =================================================================== PURCHASES
tag("Purchases", "Purchase orders, bills (with the MSMED 45-day clock) and vendor payments")
schema("PurchaseBill", {
    "id": RO_UUID, "party_id": UUID, "registration_id": UUID, "bill_no": S(), "bill_date": DATE, "accepted_on": DATE,
    "place_of_supply": STATE, "reverse_charge": B(), "lines": ARR(LINE_IN), "taxable_total": MONEY, "cgst_total": MONEY, "sgst_total": MONEY,
    "igst_total": MONEY, "grand_total": MONEY, "tds_section": NULLABLE(S()), "tds_amount": MONEY,
    "itc_eligibility": ENUM("eligible", "blocked_17_5", "ineligible", "partial", "reverse_charge"), "agreed_terms_days": NULLABLE(I()),
    "due_date": DATE, "msme_applicable": B(readOnly=True), "msme_pay_by": NULLABLE(S(format="date", readOnly=True)),
    "amount_paid": {**MONEY, "readOnly": True}, "balance_due": {**MONEY, "readOnly": True},
    "itc_match_status": NULLABLE(S(readOnly=True)), "document_id": NULLABLE(UUID),
    "status": ENUM("draft", "booked", "approved", "partially_paid", "paid", "disputed", "cancelled", readOnly=True)},
    required=("party_id", "registration_id", "bill_no", "bill_date", "accepted_on", "lines"))
collection(E + "/purchase-bills", "Purchases", "PurchaseBill", "purchases",
           filters=[q("party_id", UUID), q("status", S()), q("msme", B()), q("due_before", DATE)])
item(E + "/purchase-bills", "Purchases", "PurchaseBill", "purchases")
action(E + "/purchase-bills/from-document", "Purchases", "extractPurchaseBill", "Create a draft bill from an uploaded PDF / image (OCR)", "purchases:write",
       body=schema("FromDocument", {"document_id": UUID}, required=("document_id",)), status="202")
action(E + "/purchase-bills/{id}/book", "Purchases", "bookPurchaseBill", "Book the bill (posts to the ledger)", "purchases:write", resp=ref("PurchaseBill"), idem=True)
schema("MsmePayable", {"bill_id": UUID, "party_id": UUID, "legal_name": S(), "msme_class": S(), "bill_no": S(), "accepted_on": DATE,
                       "msme_pay_by": DATE, "days_left": I(), "breached": B(), "balance_due": MONEY,
                       "deduction_at_risk_43bh": MONEY})
_op("get", E + "/msme-payables", "Purchases", "listMsmePayables", "Bills to micro/small suppliers and the 45-day clock", "purchases:read",
    params=[q("breached", B())], resp=ARR(ref("MsmePayable")))
schema("VendorPayment", {"id": RO_UUID, "payment_no": S(readOnly=True), "party_id": UUID, "scheduled_on": NULLABLE(DATE), "paid_on": NULLABLE(DATE),
                         "amount": MONEY, "method": S(), "bank_account_id": UUID, "utr": NULLABLE(S()),
                         "allocations": ARR(schema("BillAllocation", {"bill_id": UUID, "amount": MONEY}, required=("bill_id", "amount"))),
                         "approval_request_id": NULLABLE(RO_UUID),
                         "status": ENUM("draft", "scheduled", "approved", "sent", "paid", "failed", "cancelled", readOnly=True)},
       required=("party_id", "amount", "method", "bank_account_id", "allocations"))
collection(E + "/vendor-payments", "Purchases", "VendorPayment", "purchases", filters=[q("status", S())], idem=True)
item(E + "/vendor-payments", "Purchases", "VendorPayment", "purchases", patch=False)
action(E + "/vendor-payments/{id}/release", "Purchases", "releaseVendorPayment", "Send an approved payment to the bank", "purchases:pay",
       status="202", idem=True, desc="Requires the payment's approval request to be approved (`approval_required`).")
schema("PurchaseOrder", {"id": RO_UUID, "po_no": S(), "party_id": UUID, "po_date": DATE, "status": S(readOnly=True), "total": MONEY, "lines": ARR(LINE_IN)},
       required=("party_id", "po_date", "lines"))
collection(E + "/purchase-orders", "Purchases", "PurchaseOrder", "purchases")
item(E + "/purchase-orders", "Purchases", "PurchaseOrder", "purchases")

# =================================================================== GST & TDS
tag("GST & tax", "GST returns, 2A/2B matching, credit ledger, TDS, challans, advance tax, ITR")
schema("GstReturn", {"id": RO_UUID, "registration_id": UUID, "return_type": ENUM("GSTR1", "IFF", "GSTR3B", "GSTR9", "GSTR9C", "CMP08", "GSTR4", "ITC04"),
                     "period": S(examples=["2026-10"]), "status": ENUM("drafting", "ready", "submitted", "filed"), "summary": {"type": "object"},
                     "output_tax": MONEY, "itc_claimed": MONEY, "cash_paid": MONEY, "arn": NULLABLE(S()), "filed_at": NULLABLE(TS),
                     "obligation_id": NULLABLE(UUID)}, extra={"readOnly": True})
_op("get", E + "/gst-returns", "GST & tax", "listGstReturns", "List GST returns", "gst:read",
    params=[P("Limit"), P("Cursor"), q("registration_id", UUID), q("return_type", S()), q("period", S())], resp=page_of("GstReturn"))
_op("get", E + "/gst-returns/{id}", "GST & tax", "getGstReturn", "Get a GST return with table-wise figures", "gst:read", resp=ref("GstReturn"))
action(E + "/gst-returns/{id}/compute", "GST & tax", "computeGstReturn", "Recompute from books and portal data", "gst:write", status="202")
action(E + "/gst-returns/{id}/file", "GST & tax", "fileGstReturn", "Save, submit and file with EVC or DSC", "gst:file",
       body=ref("FileRequest"), status="202", idem=True, desc="Job may pause in `waiting_for_otp`; send it to `/jobs/{jobId}/otp`.")
action(E + "/gst/inward-sync", "GST & tax", "syncInwardDocs", "Fetch GSTR-2A / 2B for a period and re-run matching", "gst:write",
       body=schema("InwardSync", {"registration_ids": ARR(UUID), "period": S(), "source": ENUM("2A", "2B", "IMS")}, required=("period", "source")), status="202")
schema("ItcMatch", {"id": RO_UUID, "status": ENUM("matched", "amount_differs", "not_in_portal", "not_in_books"), "party_id": NULLABLE(UUID),
                    "supplier_gstin": S(), "supplier_name": S(), "purchase_bill_id": NULLABLE(UUID), "inward_doc_id": NULLABLE(UUID),
                    "books_tax": NULLABLE(MONEY), "portal_tax": NULLABLE(MONEY), "tax_difference": MONEY, "finding": S(),
                    "suggested_action": S(), "action": NULLABLE(S()), "action_taken_at": NULLABLE(TS)}, extra={"readOnly": True})
_op("get", E + "/itc-matches", "GST & tax", "listItcMatches", "Purchase register vs GSTR-2A/2B", "gst:read",
    params=[P("Limit"), P("Cursor"), q("status", S()), q("registration_id", UUID), q("period", S()), q("open", B())], resp=page_of("ItcMatch"))
schema("ItcMatchSummary", {"status": S(), "count": I(), "itc": MONEY})
_op("get", E + "/itc-matches/summary", "GST & tax", "getItcMatchSummary", "Counts and ITC by match status", "gst:read",
    params=[q("period", S())], resp=ARR(ref("ItcMatchSummary")))
action(E + "/itc-matches/{id}/resolve", "GST & tax", "resolveItcMatch", "Take an action on a mismatch", "gst:write",
       body=schema("ResolveMatch", {"action": ENUM("claim", "claim_lower", "defer", "remind_supplier", "ask_to_amend", "book_bill", "investigate", "reject"), "note": S()}, required=("action",)),
       resp=ref("ItcMatch"))
action(E + "/itc-matches/remind-suppliers", "GST & tax", "remindSuppliers", "Remind every supplier whose invoices are missing from 2A", "gst:write",
       body=schema("RemindSuppliers", {"party_ids": ARR(UUID), "channels": ARR(S())}), status="202", idem=True)
schema("GstLedgerBalance", {"registration_id": UUID, "ledger": ENUM("cash", "credit", "liability"), "head": ENUM("igst", "cgst", "sgst", "cess"), "as_of": DATE, "balance": MONEY})
_op("get", E + "/gst/ledger-balances", "GST & tax", "getGstLedgerBalances", "Electronic cash / credit / liability ledgers", "gst:read",
    params=[q("registration_id", UUID)], resp=ARR(ref("GstLedgerBalance")))
schema("TdsDeduction", {"id": RO_UUID, "deductee_party_id": NULLABLE(UUID), "deductee_employee_id": NULLABLE(UUID), "deductee_pan": PAN, "section": S(),
                        "deducted_on": DATE, "base_amount": MONEY, "rate_pct": N(), "tds_amount": MONEY, "challan_id": NULLABLE(UUID),
                        "tds_return_id": NULLABLE(UUID), "certificate_issued_at": NULLABLE(TS)}, extra={"readOnly": True})
_op("get", E + "/tds-deductions", "GST & tax", "listTdsDeductions", "TDS we deducted", "tds:read",
    params=[P("Limit"), P("Cursor"), q("section", S()), q("unpaid", B()), q("fy", FY), q("quarter", I())], resp=page_of("TdsDeduction"))
schema("Challan", {"id": RO_UUID, "kind": ENUM("itns281_tds", "itns280_income_tax", "gst_pmt06", "epf_ecr", "esic", "professional_tax", "other"),
                   "registration_id": NULLABLE(UUID), "period_label": S(), "amount": MONEY, "interest": MONEY, "penalty": MONEY,
                   "paid_on": NULLABLE(DATE), "challan_ref": NULLABLE(S()), "status": ENUM("draft", "scheduled", "paid", "failed", readOnly=True),
                   "obligation_id": NULLABLE(UUID)}, required=("kind", "period_label", "amount"))
collection(E + "/challans", "GST & tax", "Challan", "tds", filters=[q("kind", S()), q("status", S())], idem=True)
action(E + "/challans/{id}/pay", "GST & tax", "payChallan", "Pay a challan through the bank / portal", "tds:pay", status="202", idem=True)
schema("TdsReturn", {"id": RO_UUID, "registration_id": UUID, "form": ENUM("24Q", "26Q", "27Q", "27EQ"), "fy": FY, "quarter": I(),
                     "status": S(), "fvu_validated_at": NULLABLE(TS), "token_no": NULLABLE(S()), "filed_at": NULLABLE(TS)}, extra={"readOnly": True})
_op("get", E + "/tds-returns", "GST & tax", "listTdsReturns", "TDS returns", "tds:read", params=[q("fy", FY)], resp=page_of("TdsReturn"))
action(E + "/tds-returns/{id}/validate", "GST & tax", "validateTdsReturn", "Generate and FVU-validate the return file", "tds:write", status="202")
schema("AdvanceTaxInstalment", {"instalment_no": I(), "due_date": DATE, "cumulative_pct": N(), "estimated_tax": MONEY, "paid": MONEY, "challan_id": NULLABLE(UUID)})
_op("get", E + "/advance-tax", "GST & tax", "getAdvanceTax", "Advance tax plan for a year", "tds:read", params=[q("fy", FY)], resp=ARR(ref("AdvanceTaxInstalment")))
schema("TdsReceivable", {"id": RO_UUID, "party_id": UUID, "sales_document_id": NULLABLE(UUID), "section": S(), "amount": MONEY, "fy": FY,
                         "quarter": I(), "seen_in_26as_at": NULLABLE(TS), "form16a_document_id": NULLABLE(UUID)}, extra={"readOnly": True})
_op("get", E + "/tds-receivables", "GST & tax", "listTdsReceivables", "TDS customers deducted from us (26AS matching)", "tds:read",
    params=[P("Limit"), P("Cursor"), q("fy", FY), q("unmatched", B())], resp=page_of("TdsReceivable"))

# =================================================================== FINANCE
tag("Finance & books", "Chart of accounts, journals, periods, banking, reconciliation, close and reports")
schema("LedgerAccount", {"id": RO_UUID, "code": S(), "name": S(), "type": ENUM("asset", "liability", "equity", "income", "expense"), "subtype": NULLABLE(S()),
                         "schedule_iii_head": NULLABLE(S()), "parent_id": NULLABLE(UUID), "is_system": B(readOnly=True), "is_active": B()}, required=("code", "name", "type"))
collection(E + "/accounts", "Finance & books", "LedgerAccount", "finance", filters=[q("type", S()), P("Q")])
item(E + "/accounts", "Finance & books", "LedgerAccount", "finance")
schema("JournalLine", {"line_no": I(readOnly=True), "account_id": UUID, "party_id": NULLABLE(UUID), "registration_id": NULLABLE(UUID),
                       "cost_center": NULLABLE(S()), "debit": MONEY, "credit": MONEY, "memo": S()}, required=("account_id",))
schema("Journal", {"id": RO_UUID, "entry_no": S(readOnly=True), "entry_date": DATE, "source": S(readOnly=True), "source_id": NULLABLE(RO_UUID),
                   "narration": S(), "status": ENUM("draft", "posted", "reversed", readOnly=True), "lines": ARR(ref("JournalLine"), minItems=2),
                   "posted_at": NULLABLE(RO_TS)}, required=("entry_date", "lines"))
collection(E + "/journals", "Finance & books", "Journal", "finance", filters=[P("From"), P("To"), q("source", S()), q("account_id", UUID)])
item(E + "/journals", "Finance & books", "Journal", "finance", delete=True, delete_note="Drafts only.")
action(E + "/journals/{id}/post", "Finance & books", "postJournal", "Post (must balance; period must be open)", "finance:write", resp=ref("Journal"), idem=True)
action(E + "/journals/{id}/reverse", "Finance & books", "reverseJournal", "Reverse a posted journal", "finance:write",
       body=schema("ReverseRequest", {"reversal_date": DATE, "narration": S()}, required=("reversal_date",)), resp=ref("Journal"), status="201", idem=True)
schema("FiscalPeriod", {"id": RO_UUID, "fy": FY, "period_start": DATE, "period_end": DATE, "status": ENUM("open", "closing", "locked"),
                        "locked_at": NULLABLE(TS), "close_progress": schema("CloseProgress", {"done": I(), "total": I()})}, extra={"readOnly": True})
_op("get", E + "/periods", "Finance & books", "listPeriods", "Fiscal periods", "finance:read", params=[q("fy", FY)], resp=ARR(ref("FiscalPeriod")))
action(E + "/periods/{id}/lock", "Finance & books", "lockPeriod", "Lock a period (all close tasks done)", "finance:approve", resp=ref("FiscalPeriod"), idem=True)
action(E + "/periods/{id}/reopen", "Finance & books", "reopenPeriod", "Reopen a locked period (needs approval)", "finance:approve",
       body=schema("ReopenReason", {"reason": S()}, required=("reason",)), resp=ref("ApprovalRequest"), status="201")
schema("CloseTask", {"id": RO_UUID, "task_key": S(), "title": S(), "owner_user_id": NULLABLE(UUID), "is_automatic": B(), "done_at": NULLABLE(TS), "done_by": NULLABLE(UUID)})
_op("get", E + "/periods/{id}/close-tasks", "Finance & books", "listCloseTasks", "Month-end checklist", "finance:read", resp=ARR(ref("CloseTask")))
_op("patch", E + "/periods/{id}/close-tasks/{taskId}", "Finance & books", "updateCloseTask", "Tick or untick a close task", "finance:write",
    params=[{"name": "taskId", "in": "path", "required": True, "schema": UUID}], body=schema("CloseTaskPatch", {"done": B()}, required=("done",)), resp=ref("CloseTask"))
schema("BankAccount", {"id": RO_UUID, "ledger_account_id": UUID, "bank_name": S(), "account_last4": S(), "ifsc": S(),
                       "kind": ENUM("current", "savings", "cash_credit", "overdraft", "fixed_deposit", "escrow"), "sanctioned_limit": NULLABLE(MONEY),
                       "feed": ENUM("account_aggregator", "bank_api", "statement_upload", "manual"), "balance": {**MONEY, "readOnly": True},
                       "balance_as_of": NULLABLE(RO_TS)}, required=("ledger_account_id", "bank_name", "account_last4", "kind"))
collection(E + "/bank-accounts", "Finance & books", "BankAccount", "finance")
item(E + "/bank-accounts", "Finance & books", "BankAccount", "finance")
action(E + "/bank-accounts/{id}/statements", "Finance & books", "uploadStatement", "Import a statement file (CSV / MT940 / PDF)", "finance:write",
       body=schema("StatementImport", {"document_id": UUID}, required=("document_id",)), status="202")
schema("BankFeedLine", {"external_id": S(description="Dedupe key from the feed"), "txn_date": DATE, "amount": {**MONEY, "description": "+ credit to us, - debit"}, "description": S(), "utr": NULLABLE(S()), "counterparty_name": NULLABLE(S()), "counterparty_va": NULLABLE(S(description="Virtual account the money was paid into")), "balance_after": NULLABLE(MONEY)}, required=("external_id", "txn_date", "amount", "description"))
action(E + "/bank-accounts/{id}/transactions", "Finance & books", "importBankTransactions", "Import bank lines from a feed (Account Aggregator, bank API or parsed statement)", "finance:write",
       body=schema("BankFeedImport", {"transactions": ARR(ref("BankFeedLine"), minItems=1, maxItems=1000)}, required=("transactions",)),
       resp=schema("BankFeedResult", {"imported": I(), "duplicates": I(), "suggested": I()}), status="201",
       desc="Duplicates (same `external_id`) are ignored. Credits paid into an invoice virtual account, or naming an invoice number, get a suggested receipt to accept or reject.")
schema("BankMatchSuggestion", {"id": RO_UUID, "target_kind": S(), "target_id": UUID, "label": S(examples=["Invoice NI/KA/184"]), "amount": MONEY,
                               "confidence": N(), "rule": S(), "status": ENUM("suggested", "accepted", "rejected")})
schema("BankTransaction", {"id": RO_UUID, "bank_account_id": UUID, "txn_date": DATE, "amount": MONEY, "description": S(), "utr": NULLABLE(S()),
                           "counterparty_name": NULLABLE(S()), "match_status": ENUM("unmatched", "suggested", "matched", "ignored"),
                           "suggestions": ARR(ref("BankMatchSuggestion"))}, extra={"readOnly": True})
_op("get", E + "/bank-transactions", "Finance & books", "listBankTransactions", "Bank lines", "finance:read",
    params=[P("Limit"), P("Cursor"), q("bank_account_id", UUID), q("match_status", S()), P("From"), P("To")], resp=page_of("BankTransaction"))
action(E + "/bank-matches/{id}/accept", "Finance & books", "acceptBankMatch", "Accept a suggested match", "finance:write", resp=ref("BankTransaction"), idem=True)
action(E + "/bank-matches/{id}/reject", "Finance & books", "rejectBankMatch", "Reject a suggested match", "finance:write", resp=ref("BankTransaction"))
action(E + "/bank-transactions/{id}/match", "Finance & books", "matchBankTransaction", "Match a line manually", "finance:write",
       body=schema("ManualMatch", {"targets": ARR(schema("MatchTarget", {"target_kind": S(), "target_id": UUID, "amount": MONEY}, required=("target_kind", "target_id", "amount")))}, required=("targets",)),
       resp=ref("BankTransaction"), idem=True)
schema("ReportRow", {"line": S(), "values": ARR(MONEY)})
schema("Report", {"columns": ARR(S(), examples=[["Aug", "Sep", "Oct"]]), "rows": ARR(ref("ReportRow")), "provisional": B()})
_op("get", E + "/reports/{report}", "Finance & books", "getReport", "Financial reports", "finance:read",
    params=[{"name": "report", "in": "path", "required": True, "schema": ENUM("profit-and-loss", "balance-sheet", "cash-bridge", "trial-balance", "gst-liability")},
            P("From"), P("To"), q("compare", ENUM("none", "month", "year")), q("format", ENUM("json", "xlsx", "pdf"))], resp=ref("Report"))
schema("FinanceSummary", {"revenue": MONEY, "gross_margin_pct": N(), "ebitda": MONEY, "cash_in_bank": MONEY, "cash_change": MONEY,
                          "receivables": MONEY, "receivables_overdue": MONEY, "runway_months": NULLABLE(N()), "net_burn": NULLABLE(MONEY)})
_op("get", E + "/finance/summary", "Finance & books", "getFinanceSummary", "Headline numbers (dashboard tiles)", "finance:read",
    params=[P("From"), P("To")], resp=ref("FinanceSummary"))

# =================================================================== PAYROLL
tag("Payroll & HR", "Employees, salary structures, payroll runs, payslips, labour-law checks")
schema("SalaryComponent", {"code": S(), "monthly_amount": MONEY, "kind": ENUM("earning", "employer_contribution", "reimbursement"), "in_wages": B(), "taxable": B()},
       required=("code", "monthly_amount", "kind", "in_wages"))
schema("SalaryStructure", {"id": RO_UUID, "effective_from": DATE, "effective_to": NULLABLE(DATE), "ctc_annual": MONEY, "components": ARR(ref("SalaryComponent"))},
       required=("effective_from", "ctc_annual", "components"))
schema("Employee", {"id": RO_UUID, "employee_code": S(), "full_name": S(), "pan": PAN, "uan": NULLABLE(S()), "esic_ip_no": NULLABLE(S()),
                    "email": S(format="email"), "date_of_joining": DATE, "date_of_leaving": NULLABLE(DATE), "designation": S(), "department": S(),
                    "work_state": STATE, "employment_type": S(), "tax_regime": ENUM("new", "old"),
                    "bank_account": schema("BankDetails", {"account_no": S(writeOnly=True), "account_last4": S(readOnly=True), "ifsc": S()}),
                    "status": ENUM("onboarding", "active", "notice", "exited"), "current_structure": NULLABLE(ref("SalaryStructure"))},
       required=("employee_code", "full_name", "date_of_joining", "work_state"))
collection(E + "/employees", "Payroll & HR", "Employee", "payroll", filters=[q("status", S()), P("Q"), q("esi_covered", B()), q("wage_code_flag", B())])
item(E + "/employees", "Payroll & HR", "Employee", "payroll")
action(E + "/employees/{id}/salary-structures", "Payroll & HR", "createSalaryStructure", "Add a salary revision (effective-dated)", "payroll:write",
       body=ref("SalaryStructure"), resp=ref("SalaryStructure"), status="201", desc="Overlapping ranges are rejected; the previous structure is closed the day before.")
schema("Payslip", {"id": RO_UUID, "employee_id": UUID, "employee_name": S(), "paid_days": N(), "lop_days": N(), "gross": MONEY, "wages": MONEY,
                   "pf_wage": MONEY, "pf_employee": MONEY, "pf_employer_epf": MONEY, "pf_employer_eps": MONEY, "esi_applicable": B(),
                   "esi_employee": MONEY, "esi_employer": MONEY, "professional_tax": MONEY, "tds": MONEY, "other_deductions": MONEY,
                   "net_pay": MONEY, "flags": ARR(S(), examples=[["wage_below_50pct"]]), "pdf_url": NULLABLE(S(format="uri"))}, extra={"readOnly": True})
schema("PayrollRun", {"id": RO_UUID, "period_month": DATE, "status": ENUM("inputs_open", "inputs_locked", "computed", "approved", "bank_file_sent", "paid", "statutory_done", readOnly=True),
                      "headcount": I(readOnly=True), "gross_total": {**MONEY, "readOnly": True}, "net_total": {**MONEY, "readOnly": True},
                      "employer_cost_total": {**MONEY, "readOnly": True}, "statutory": ARR(schema("StatutoryDue", {"kind": S(), "amount": MONEY, "due_date": DATE, "obligation_id": UUID}), readOnly=True),
                      "approval_request_id": NULLABLE(RO_UUID), "paid_on": NULLABLE(DATE)}, required=("period_month",))
collection(E + "/payroll-runs", "Payroll & HR", "PayrollRun", "payroll", filters=[q("fy", FY)])
_op("get", E + "/payroll-runs/{id}", "Payroll & HR", "getPayrollRun", "Get a payroll run", "payroll:read", resp=ref("PayrollRun"))
schema("PayrollInput", {"employee_id": UUID, "lop_days": N(), "overtime": MONEY, "bonus": MONEY, "reimbursements": MONEY, "other_deductions": MONEY}, required=("employee_id",))
_op("put", E + "/payroll-runs/{id}/inputs", "Payroll & HR", "putPayrollInputs", "Set attendance and one-off inputs", "payroll:write",
    body=ARR(ref("PayrollInput")), resp=ref("PayrollRun"))
action(E + "/payroll-runs/{id}/compute", "Payroll & HR", "computePayrollRun", "Compute payslips, PF, ESI, PT and TDS", "payroll:write", resp=ref("PayrollRun"))
action(E + "/payroll-runs/{id}/submit", "Payroll & HR", "submitPayrollRun", "Send for approval", "payroll:write", resp=ref("ApprovalRequest"), status="201")
action(E + "/payroll-runs/{id}/bank-file", "Payroll & HR", "createPayrollBankFile", "Generate the bank upload file (after approval)", "payroll:pay",
       body=schema("BankFileRequest", {"bank_account_id": UUID, "format": ENUM("h2h_api", "csv", "bank_specific")}, required=("bank_account_id",)),
       status="202", idem=True)
_op("get", E + "/payroll-runs/{id}/payslips", "Payroll & HR", "listPayslips", "Payslips in a run", "payroll:read",
    params=[P("Limit"), P("Cursor"), q("flag", S())], resp=page_of("Payslip"))
schema("WageCodeCheck", {"employee_id": UUID, "full_name": S(), "gross_monthly": MONEY, "wages_monthly": MONEY, "wage_pct": N(), "statutory_wage": MONEY, "pf_wage_should_be": MONEY})
_op("get", E + "/payroll/wage-code-check", "Payroll & HR", "getWageCodeCheck", "Employees whose wages are under 50% of pay", "payroll:read", resp=ARR(ref("WageCodeCheck")))
schema("LeaveRequest", {"id": RO_UUID, "employee_id": UUID, "leave_type": S(), "from": DATE, "to": DATE, "days": N(), "status": ENUM("pending", "approved", "rejected", "cancelled", readOnly=True)},
       required=("employee_id", "leave_type", "from", "to"))
collection(E + "/leave-requests", "Payroll & HR", "LeaveRequest", "payroll", filters=[q("status", S()), q("employee_id", UUID)])

# =================================================================== LEGAL, BOARD, CAPITAL, SCHEMES
tag("Legal & board", "Contracts, extracted terms, templates, meetings and resolutions")
schema("ContractTerm", {"term_key": S(examples=["renewal", "payment", "liability_cap"]), "value_text": S(), "value_json": {"type": "object"},
                        "source_page": NULLABLE(I()), "extracted_by": ENUM("user", "copilot"), "confidence": NULLABLE(N()), "verified": B()})
schema("Contract", {"id": RO_UUID, "party_id": NULLABLE(UUID), "counterparty_name": S(), "title": S(),
                    "kind": ENUM("customer", "vendor", "property", "employment", "nda", "consultant", "licence", "loan", "shareholder", "other"),
                    "template_id": NULLABLE(UUID), "start_date": NULLABLE(DATE), "end_date": NULLABLE(DATE), "auto_renews": B(),
                    "renewal_months": NULLABLE(I()), "notice_days": NULLABLE(I()), "notice_deadline": NULLABLE(S(format="date", readOnly=True)),
                    "value_amount": NULLABLE(MONEY), "value_period": NULLABLE(S()), "payment_terms_days": NULLABLE(I()), "governing_law": S(),
                    "dispute_forum": S(), "stamp_state": NULLABLE(STATE), "stamp_duty_paid": NULLABLE(MONEY), "registered_on": NULLABLE(DATE),
                    "status": ENUM("draft", "negotiating", "out_for_signature", "active", "expired", "terminated", "renewed"),
                    "signed_document_id": NULLABLE(UUID), "terms": ARR(ref("ContractTerm"), readOnly=True),
                    "alerts": ARR(ref("TaxCheck"), readOnly=True)}, required=("counterparty_name", "title", "kind"))
collection(E + "/contracts", "Legal & board", "Contract", "legal", filters=[q("kind", S()), q("status", S()), q("notice_within_days", I())])
item(E + "/contracts", "Legal & board", "Contract", "legal")
action(E + "/contracts/{id}/extract-terms", "Legal & board", "extractContractTerms", "Extract key terms from the signed copy (copilot)", "legal:write", status="202")
_op("put", E + "/contracts/{id}/terms/{termKey}", "Legal & board", "putContractTerm", "Correct or verify a term", "legal:write",
    params=[{"name": "termKey", "in": "path", "required": True, "schema": S()}], body=ref("ContractTerm"), resp=ref("ContractTerm"))
action(E + "/contracts/{id}/notices", "Legal & board", "draftContractNotice", "Draft a renewal / termination notice", "legal:write",
       body=schema("ContractNoticeRequest", {"kind": ENUM("non_renewal", "renegotiation", "termination")}, required=("kind",)), status="202")
schema("ContractTemplate", {"id": RO_UUID, "name": S(), "kind": S(), "version": S(), "reviewed_by": S(), "reviewed_on": NULLABLE(DATE), "global": B(readOnly=True)})
_op("get", E + "/contract-templates", "Legal & board", "listContractTemplates", "Templates (yours and the library)", "legal:read", resp=ARR(ref("ContractTemplate")))
action(E + "/contract-templates/{id}/generate", "Legal & board", "generateFromTemplate", "Fill a template into a draft contract", "legal:write",
       body=schema("TemplateFill", {"party_id": UUID, "fields": {"type": "object"}}, required=("party_id",)), resp=ref("Contract"), status="201")
schema("Resolution", {"id": RO_UUID, "meeting_id": NULLABLE(UUID), "number": S(), "title": S(), "kind": ENUM("board", "ordinary", "special", "circular"),
                      "passed_on": NULLABLE(DATE), "needs_mgt14": B(readOnly=True), "status": ENUM("draft", "circulated", "passed", "failed", "withdrawn"),
                      "signature_request_id": NULLABLE(UUID)}, required=("number", "title", "kind"))
schema("Meeting", {"id": RO_UUID, "kind": ENUM("board", "agm", "egm", "committee", "partners"), "scheduled_for": TS, "held_on": NULLABLE(DATE),
                   "notice_sent_on": NULLABLE(DATE), "quorum_met": NULLABLE(B()), "minutes_document_id": NULLABLE(UUID),
                   "resolutions": ARR(ref("Resolution"), readOnly=True), "days_since_last_board": NULLABLE(I(readOnly=True))}, required=("kind", "scheduled_for"))
collection(E + "/meetings", "Legal & board", "Meeting", "legal", filters=[q("kind", S()), P("From"), P("To")])
item(E + "/meetings", "Legal & board", "Meeting", "legal")
collection(E + "/resolutions", "Legal & board", "Resolution", "legal", filters=[q("status", S())])
item(E + "/resolutions", "Legal & board", "Resolution", "legal")

tag("Capital", "Cap table, funding rounds, allotments and ESOPs")
schema("CapTableRow", {"holder": S(), "class_name": S(), "fd_shares": N(), "fd_pct": N()})
schema("CapTable", {"as_of": DATE, "scenario": ENUM("current", "post_round"), "rows": ARR(ref("CapTableRow")), "total_fd_shares": N()})
_op("get", E + "/cap-table", "Capital", "getCapTable", "Fully diluted cap table", "capital:read",
    params=[q("scenario", ENUM("current", "post_round")), q("round_id", UUID)], resp=ref("CapTable"))
schema("FundingRound", {"id": RO_UUID, "name": S(), "share_class_id": NULLABLE(UUID), "route": S(), "target_amount": NULLABLE(MONEY),
                        "pre_money_valuation": NULLABLE(MONEY), "term_sheet_signed_on": NULLABLE(DATE), "status": S(),
                        "steps": ARR(schema("RoundStep", {"key": S(), "title": S(), "status": ENUM("done", "in_progress", "next"), "detail": S(), "obligation_id": NULLABLE(UUID)}), readOnly=True)},
       required=("name",))
collection(E + "/funding-rounds", "Capital", "FundingRound", "capital")
item(E + "/funding-rounds", "Capital", "FundingRound", "capital")
schema("Allotment", {"id": RO_UUID, "round_id": NULLABLE(UUID), "share_class_id": UUID, "shareholder_id": UUID, "quantity": I(), "issue_price": S(),
                     "money_received_on": NULLABLE(DATE), "allotted_on": NULLABLE(DATE), "pas3_obligation_id": NULLABLE(RO_UUID), "fcgpr_obligation_id": NULLABLE(RO_UUID)},
       required=("share_class_id", "shareholder_id", "quantity", "issue_price"))
collection(E + "/allotments", "Capital", "Allotment", "capital", idem=True)
schema("EsopGrant", {"id": RO_UUID, "pool_id": UUID, "employee_id": UUID, "granted_on": DATE, "options": I(), "strike_price": S(), "cliff_months": I(),
                     "vesting_months": I(), "vested_to_date": I(readOnly=True), "next_vest": NULLABLE(schema("NextVest", {"date": DATE, "options": I()}, extra={"readOnly": True}))},
       required=("pool_id", "employee_id", "granted_on", "options", "strike_price"))
collection(E + "/esop-grants", "Capital", "EsopGrant", "capital")

tag("Schemes & credit", "Government schemes and benefits, TReDS, delayed-payment claims")
schema("Benefit", {"scheme_code": S(), "name": S(), "kind": S(), "status": ENUM("not_eligible", "eligible", "applied", "in_progress", "granted", "rejected", "expired"),
                   "reason": S(), "reference_no": NULLABLE(S()), "valid_until": NULLABLE(DATE), "next_action": NULLABLE(S())}, extra={"readOnly": True})
_op("get", E + "/benefits", "Schemes & credit", "listBenefits", "Schemes and their status for this entity", "schemes:read", resp=ARR(ref("Benefit")))
action(E + "/benefits/check", "Schemes & credit", "checkBenefits", "Re-check eligibility against current facts", "schemes:read", resp=ARR(ref("Benefit")))
schema("TredsInvoice", {"id": RO_UUID, "sales_document_id": UUID, "platform": S(), "uploaded_on": DATE, "status": S(readOnly=True),
                        "discount_rate_pct": NULLABLE(N()), "amount_received": NULLABLE(MONEY)}, required=("sales_document_id", "platform"))
collection(E + "/treds-invoices", "Schemes & credit", "TredsInvoice", "schemes", idem=True)
schema("DelayedPaymentClaim", {"id": RO_UUID, "buyer_party_id": UUID, "invoice_ids": ARR(UUID), "principal": {**MONEY, "readOnly": True},
                               "interest_claimed": {**MONEY, "readOnly": True}, "forum": ENUM("msme_samadhaan", "msefc", "court"),
                               "status": S(readOnly=True), "case_no": NULLABLE(S())}, required=("buyer_party_id", "invoice_ids"))
collection(E + "/delayed-payment-claims", "Schemes & credit", "DelayedPaymentClaim", "schemes")
action(E + "/delayed-payment-claims/{id}/file", "Schemes & credit", "fileDelayedPaymentClaim", "File on MSME Samadhaan", "schemes:file", status="202", idem=True)

# =================================================================== DOCUMENTS
tag("Documents", "Vault uploads, search, links, advisor requests and data rooms")
schema("Document", {"id": RO_UUID, "folder": ENUM("incorporation", "tax", "mca", "payroll", "contracts", "board", "bank_finance", "sales", "purchases", "licences", "other"),
                    "title": S(), "file_name": S(), "mime_type": S(), "size_bytes": I(), "sha256": S(), "source": S(readOnly=True),
                    "period_label": NULLABLE(S()), "valid_until": NULLABLE(DATE), "links": ARR(schema("DocumentLink", {"subject": S(), "subject_id": UUID, "role": S()})),
                    "download_url": NULLABLE(S(format="uri", readOnly=True, description="Short-lived signed URL")), "created_at": RO_TS},
       required=("folder", "title", "file_name", "mime_type", "size_bytes", "sha256"))
schema("UploadTicket", {"document": ref("Document"), "upload_url": S(format="uri"), "upload_method": ENUM("PUT"), "upload_headers": {"type": "object"}, "expires_at": TS})
action(E + "/documents", "Documents", "createDocument", "Register a document and get a signed upload URL", "documents:write",
       body=ref("Document"), resp=ref("UploadTicket"), status="201",
       desc="1) POST metadata incl. sha256 → 2) PUT bytes to `upload_url` → 3) the object store event marks it ready and starts OCR. Duplicate sha256 returns the existing document.")
_op("get", E + "/documents", "Documents", "searchDocuments", "Search and list", "documents:read",
    params=[P("Limit"), P("Cursor"), P("Q"), q("folder", S()), q("source", S()), q("expiring_within_days", I()), q("subject", S()), q("subject_id", UUID)],
    resp=page_of("Document"))
item(E + "/documents", "Documents", "Document", "documents")
schema("DocumentRequest", {"id": RO_UUID, "advisor_grant_id": NULLABLE(UUID), "requested_by_name": S(), "title": S(), "due_on": NULLABLE(DATE),
                           "status": ENUM("open", "partially_fulfilled", "fulfilled", "cancelled", readOnly=True), "document_ids": ARR(UUID, readOnly=True)},
       required=("title",))
collection(E + "/document-requests", "Documents", "DocumentRequest", "documents", filters=[q("status", S())])
action(E + "/document-requests/{id}/share", "Documents", "fulfilDocumentRequest", "Share documents against a request", "documents:write",
       body=schema("ShareDocuments", {"document_ids": ARR(UUID, minItems=1)}, required=("document_ids",)), resp=ref("DocumentRequest"))
schema("DataRoom", {"id": RO_UUID, "name": S(), "purpose": S(), "expires_at": NULLABLE(TS), "watermark": B(), "allow_download": B(),
                    "document_ids": ARR(UUID), "access": ARR(schema("RoomAccess", {"email": S(format="email"), "last_viewed_at": NULLABLE(RO_TS)}))}, required=("name",))
collection(E + "/data-rooms", "Documents", "DataRoom", "documents")
item(E + "/data-rooms", "Documents", "DataRoom", "documents", delete=True)

# =================================================================== PORTALS & JOBS
tag("Portals & jobs", "Government / bank connections, OTP sessions, sync jobs and the job resource")
schema("PortalConnection", {"id": RO_UUID, "portal": ENUM("gstn", "irp", "eway", "mca", "income_tax", "traces", "epfo", "esic", "professional_tax", "udyam", "account_aggregator", "bank_api", "treds", "digilocker"),
                            "registration_id": NULLABLE(UUID), "via": NULLABLE(S()), "owner_user_id": NULLABLE(UUID),
                            "status": ENUM("not_connected", "connected", "expiring", "expired", "error", "revoked", readOnly=True),
                            "session_expires_at": NULLABLE(RO_TS), "last_sync_at": NULLABLE(RO_TS), "last_error": NULLABLE(S(readOnly=True)),
                            "credentials": {"type": "object", "writeOnly": True, "description": "Sent once, stored only in the secret manager"}}, required=("portal",))
collection(E + "/portal-connections", "Portals & jobs", "PortalConnection", "settings")
item(E + "/portal-connections", "Portals & jobs", "PortalConnection", "settings", delete=True, delete_note="Revokes and deletes stored credentials.")
action(E + "/portal-connections/{id}/renew", "Portals & jobs", "renewPortalSession", "Start an OTP session renewal", "settings:write", status="202",
       desc="The job pauses in `waiting_for_otp`; the owner submits the OTP to `/jobs/{jobId}/otp`.")
schema("AaConsent", {"id": RO_UUID, "bank_account_ids": ARR(UUID), "status": S(readOnly=True), "valid_until": TS, "redirect_url": S(format="uri", readOnly=True)},
       required=("bank_account_ids", "valid_until"))
collection(E + "/aa-consents", "Portals & jobs", "AaConsent", "settings")
_op("get", "/jobs/{id}", "Portals & jobs", "getJob", "Get a job", resp=ref("Job"))
_op("post", "/jobs/{id}/otp", "Portals & jobs", "submitJobOtp", "Provide the OTP a paused job is waiting for",
    body=schema("OtpSubmission", {"otp": S(pattern=r"^[0-9]{6}$")}, required=("otp",)), resp=ref("Job"))
_op("post", "/jobs/{id}/cancel", "Portals & jobs", "cancelJob", "Cancel a queued or waiting job", resp=ref("Job"))

# =================================================================== COPILOT
tag("Copilot", "Questions answered from the entity's own records, with citations; actions always go through approval")
schema("Citation", {"ordinal": I(), "subject": S(examples=["gst_inward_docs"]), "subject_id": NULLABLE(UUID), "label": S()})
schema("CopilotAction", {"id": RO_UUID, "kind": S(examples=["send_supplier_reminders"]), "payload": {"type": "object"},
                         "status": ENUM("proposed", "approved", "executed", "rejected", "failed", readOnly=True), "approval_request_id": NULLABLE(RO_UUID)})
schema("CopilotMessage", {"id": RO_UUID, "role": ENUM("user", "assistant"), "content": S(), "citations": ARR(ref("Citation"), readOnly=True),
                          "actions": ARR(ref("CopilotAction"), readOnly=True), "created_at": RO_TS}, required=("content",))
schema("CopilotThread", {"id": RO_UUID, "title": S(), "module": NULLABLE(S()), "created_at": RO_TS, "messages": ARR(ref("CopilotMessage"), readOnly=True)})
collection(E + "/copilot/threads", "Copilot", "CopilotThread", "copilot")
_op("get", E + "/copilot/threads/{id}", "Copilot", "getCopilotThread", "Get a thread with messages", "copilot:use", resp=ref("CopilotThread"))
_op("post", E + "/copilot/threads/{id}/messages", "Copilot", "askCopilot", "Ask a question (streams with SSE)", "copilot:use",
    body=ref("CopilotMessage"), resp=ref("CopilotMessage"), status="201",
    desc="With `Accept: text/event-stream` the answer streams as `token`, `citation`, `action` and `done` events. "
         "The model only reads through the caller's own permissions (same RLS), and can only *propose* actions.",
    extra_responses={"200": {"description": "Event stream", "content": {"text/event-stream": {"schema": S()}}}})
action(E + "/copilot/actions/{id}/request-approval", "Copilot", "requestCopilotActionApproval", "Turn a proposed action into an approval request", "copilot:use",
       body=schema("ActionApproval", {"assigned_to": NULLABLE(UUID), "payload_overrides": {"type": "object"}}), resp=ref("ApprovalRequest"), status="201")

# =================================================================== GROUP / ORGANIZATIONS
tag("Organizations", "Organisation-level views for business groups and CA / CS firms")
schema("EntityStanding", {"entity_id": UUID, "legal_name": S(), "constitution": S(), "owner_staff": NULLABLE(S()),
                          "cells": {"type": "object", "additionalProperties": ENUM("clear", "due", "client", "overdue", "na"),
                                    "examples": [{"MCA": "client", "GST": "due", "TDS": "client", "EPFO_ESIC": "due", "INCOME_TAX": "clear"}]},
                          "open_items": ARR(schema("OpenItem", {"title": S(), "detail": S(), "due_on": DATE}))})
_op("get", "/organizations/{id}", "Organizations", "getOrganization", "Get an organisation", resp=schema("Organization", {"id": UUID, "name": S(), "kind": S(), "plan": S(), "entity_count": I()}))
_op("get", "/organizations/{id}/entities", "Organizations", "listOrganizationEntities", "Entities the organisation owns or advises, with standing",
    params=[P("Limit"), P("Cursor"), q("filter", ENUM("all", "waiting_on_client", "overdue")), q("staff", UUID)], resp=page_of("EntityStanding"))
schema("StaffLoad", {"user_id": UUID, "name": S(), "filings_due": I()})
_op("get", "/organizations/{id}/workload", "Organizations", "getStaffWorkload", "Filings assigned per staff member this week", resp=ARR(ref("StaffLoad")))
action("/organizations/{id}/client-reminders", "Organizations", "chaseClients", "Send document / approval reminders to clients that are blocking filings",
       None, body=schema("ChaseClients", {"entity_ids": ARR(UUID)}), status="202", idem=True)

# =================================================================== EVENTS, WEBHOOKS, AUDIT
tag("Events & webhooks", "Server-sent events for the UI and signed webhooks for integrations")
schema("Event", {"id": S(), "type": S(examples=["sales_document.paid"]), "entity_id": UUID, "occurred_at": TS,
                 "subject": S(), "subject_id": UUID, "data": {"type": "object"}}, required=("id", "type", "entity_id", "occurred_at"))
_op("get", "/events", "Events & webhooks", "streamEvents", "Live event stream for the signed-in user (SSE)",
    params=[q("entity_id", UUID), {"name": "Last-Event-ID", "in": "header", "schema": S()}],
    resp=None, extra_responses={"200": {"description": "text/event-stream of Event", "content": {"text/event-stream": {"schema": S()}}}})
schema("WebhookEndpoint", {"id": RO_UUID, "url": S(format="uri"), "events": ARR(S(), examples=[["obligation.filed", "sales_document.paid"]]),
                           "secret": S(readOnly=True, description="Shown once; used for the HMAC signature"), "is_active": B()}, required=("url", "events"))
collection(E + "/webhooks", "Events & webhooks", "WebhookEndpoint", "settings")
item(E + "/webhooks", "Events & webhooks", "WebhookEndpoint", "settings", delete=True)
action(E + "/webhooks/{id}/test", "Events & webhooks", "testWebhook", "Send a test event", "settings:write", status="204")

EVENTS = {
    "obligation.created": "Engine switched on a new obligation",
    "obligation.due_soon": "An obligation is 7 days / 1 day from due",
    "obligation.filed": "Filed; acknowledgement attached",
    "obligation.overdue": "Passed its due date unfiled",
    "approval.requested": "Something waits on an approver",
    "approval.decided": "Approved or rejected",
    "notice.received": "A notice arrived from an authority",
    "threshold.crossed": "A fact crossed a rule threshold",
    "sales_document.issued": "Invoice / note issued (IRN may follow)",
    "sales_document.irn_generated": "IRP returned the IRN and signed QR",
    "sales_document.paid": "Fully settled",
    "receipt.created": "Money received and allocated",
    "msme_payable.due_soon": "A micro/small supplier bill nears day 45",
    "job.succeeded": "A long-running job finished",
    "job.failed": "A long-running job failed",
    "portal.session_expiring": "A portal session needs an OTP renewal",
}
for ev, desc in EVENTS.items():
    SPEC["webhooks"][ev] = {"post": {
        "summary": desc, "operationId": "webhook_" + ev.replace(".", "_"),
        "parameters": [
            {"name": "Biz360X-Signature", "in": "header", "required": True, "schema": S(examples=["t=1793840000,v1=5257a869e7…"]),
             "description": "HMAC-SHA256 of `t.body` with the endpoint secret. Reject if |now − t| > 300 s."},
            {"name": "Biz360X-Event-Id", "in": "header", "required": True, "schema": S(), "description": "Deduplicate on this"}],
        "requestBody": {"content": {"application/json": {"schema": ref("Event")}}},
        "responses": {"2XX": {"description": "Acknowledged. Non-2xx is retried with backoff for 72 h."}}}}

tag("Audit", "Append-only, hash-chained audit trail")
schema("AuditEntry", {"id": I(), "at": TS, "actor_kind": S(), "actor_user_id": NULLABLE(UUID), "actor_name": NULLABLE(S()), "action": S(),
                      "subject": S(), "subject_id": NULLABLE(UUID), "before": NULLABLE({"type": "object"}), "after": NULLABLE({"type": "object"}),
                      "hash": S(), "prev_hash": NULLABLE(S())}, extra={"readOnly": True})
_op("get", E + "/audit-log", "Audit", "listAuditEntries", "Audit trail", "audit:read",
    params=[P("Limit"), P("Cursor"), q("subject", S()), q("subject_id", UUID), q("actor", UUID), P("From"), P("To")], resp=page_of("AuditEntry"))
_op("get", E + "/audit-log/verify", "Audit", "verifyAuditChain", "Verify the hash chain", "audit:read",
    resp=schema("AuditVerification", {"intact": B(), "first_broken_id": NULLABLE(I()), "checked": I()}))

# ---------------------------------------------------------------- finish
SPEC["tags"] = [{"name": k, "description": v} for k, v in TAGS.items()]
SPEC["x-tagGroups"] = [
    {"name": "Platform", "tags": ["Auth", "Me", "Onboarding", "Entities", "Organizations", "Portals & jobs", "Events & webhooks", "Audit"]},
    {"name": "Govern", "tags": ["Compliance", "Approvals", "Legal & board", "Documents"]},
    {"name": "Run", "tags": ["Parties & items", "Sales & billing", "Public pay", "Purchases", "GST & tax", "Finance & books", "Payroll & HR"]},
    {"name": "Grow", "tags": ["Capital", "Schemes & credit", "Copilot"]},
]

class Dumper(yaml.SafeDumper):
    def ignore_aliases(self, data):
        return True

if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "openapi.yaml"
    with open(out, "w") as f:
        yaml.dump(SPEC, f, Dumper=Dumper, sort_keys=False, allow_unicode=True, width=120)
    n_ops = sum(1 for p in SPEC["paths"].values() for m in p)
    print(f"wrote {out}: {len(SPEC['paths'])} paths, {n_ops} operations, {len(C['schemas'])} schemas, {len(SPEC['webhooks'])} webhooks")
