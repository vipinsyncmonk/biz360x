"use client";
import { useState, type ReactNode } from "react";
import { api, unwrap } from "@/lib/api";
import { isoDate } from "@/lib/format";
import {
  DscForm, GrantForm, InviteForm, KIND_LABEL, LEVELS, MODULES, PORTALS_FOR_DSC, ROLES, blankDsc, blankGrant, blankInvite, dscBody, dscReady, grantBody, grantReady,
  inviteBody, inviteReady, keyPermissions, keyReady,
} from "@/lib/settings";
import { ErrorNote } from "./ui";

function Panel({ title, children, error, busy, ready, cta, onSubmit, onCancel }: { title: string; children: ReactNode; error: unknown; busy: boolean; ready: boolean; cta: string; onSubmit: () => void; onCancel: () => void }) {
  return (
    <form className="card-pad" style={{ border: "1px solid var(--line)", borderRadius: 12, background: "var(--surface-2)", display: "flex", flexDirection: "column", gap: 12 }}
          onSubmit={(e) => { e.preventDefault(); if (ready && !busy) onSubmit(); }}>
      <h3 style={{ margin: 0, fontSize: 15 }}>{title}</h3>
      {children}
      <ErrorNote error={error} />
      <span style={{ display: "flex", gap: 10 }}>
        <button type="submit" className="btn primary" disabled={!ready || busy}>{busy ? "Saving…" : cta}</button>
        <button type="button" className="btn" onClick={onCancel}>Cancel</button>
      </span>
    </form>
  );
}

function useSubmit(run: () => Promise<void>) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  return { busy, error, submit: async () => { setBusy(true); setError(null); try { await run(); } catch (e) { setError(e); } setBusy(false); } };
}

const toggle = (list: string[], v: string) => (list.includes(v) ? list.filter((x) => x !== v) : [...list, v]);

export function InvitePerson({ entityId, onDone, onCancel }: { entityId: string; onDone: () => void; onCancel: () => void }) {
  const [f, setF] = useState<InviteForm>(blankInvite());
  const s = useSubmit(async () => {
    unwrap(await api.POST("/entities/{entityId}/members", { params: { path: { entityId } }, body: inviteBody(f) as never }));
    onDone();
  });
  return (
    <Panel title="Invite a person" error={s.error} busy={s.busy} ready={inviteReady(f)} cta="Send invite" onSubmit={() => void s.submit()} onCancel={onCancel}>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: 12 }}>
        <div className="field"><label htmlFor="inv-email">Work email</label><input id="inv-email" type="email" className="input" value={f.email} onChange={(e) => setF({ ...f, email: e.target.value })} /></div>
        <div className="field"><label htmlFor="inv-name">Name (optional)</label><input id="inv-name" className="input" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></div>
      </div>
      <div role="radiogroup" aria-label="Role" style={{ display: "grid", gap: 6 }}>
        {ROLES.map(([k, l, h]) => (
          <label key={k} style={{ display: "flex", gap: 10, alignItems: "flex-start", fontSize: 14 }}>
            <input type="radio" name="inv-role" checked={f.role === k} onChange={() => setF({ ...f, role: k })} /><span>{l}<span className="t13" style={{ display: "block" }}>{h}</span></span>
          </label>
        ))}
      </div>
      <fieldset style={{ border: 0, padding: 0, margin: 0 }}>
        <legend className="t13">Modules (none ticked means all)</legend>
        <div style={{ display: "flex", flexWrap: "wrap", gap: "6px 16px" }}>
          {MODULES.map(([k, l]) => <label key={k} style={{ fontSize: 14, display: "flex", gap: 6 }}><input type="checkbox" checked={f.modules.includes(k)} onChange={() => setF({ ...f, modules: toggle(f.modules, k) })} />{l}</label>)}
        </div>
      </fieldset>
      <span className="t13">They sign in with a one-time code sent to this address. Nothing is emailed yet, so tell them to sign in.</span>
    </Panel>
  );
}

export function GrantAdvisor({ entityId, onDone, onCancel }: { entityId: string; onDone: () => void; onCancel: () => void }) {
  const [f, setF] = useState<GrantForm>(blankGrant());
  const today = isoDate(new Date());
  const s = useSubmit(async () => {
    unwrap(await api.POST("/entities/{entityId}/advisor-grants", { params: { path: { entityId } }, body: grantBody(f) as never }));
    onDone();
  });
  return (
    <Panel title="Give an advisor access" error={s.error} busy={s.busy} ready={grantReady(f, today)} cta="Send invite" onSubmit={() => void s.submit()} onCancel={onCancel}>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: 12 }}>
        <div className="field"><label htmlFor="ad-name">Firm or person</label><input id="ad-name" className="input" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></div>
        <div className="field"><label htmlFor="ad-kind">Kind</label><select id="ad-kind" className="input" value={f.kind} onChange={(e) => setF({ ...f, kind: e.target.value })}>{Object.entries(KIND_LABEL).map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></div>
        <div className="field"><label htmlFor="ad-email">Email (optional)</label><input id="ad-email" type="email" className="input" value={f.email} onChange={(e) => setF({ ...f, email: e.target.value })} /></div>
        <div className="field"><label htmlFor="ad-exp">Access ends (optional)</label><input id="ad-exp" type="date" className="input" min={today} value={f.expires} onChange={(e) => setF({ ...f, expires: e.target.value })} /></div>
      </div>
      <fieldset style={{ border: 0, padding: 0, margin: 0 }}>
        <legend className="t13">What they can do, by module</legend>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: 8 }}>
          {MODULES.map(([k, l]) => (
            <label key={k} style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8, fontSize: 14 }}>{l}
              <select aria-label={`${l} access`} className="input" style={{ height: 36, width: 120, fontSize: 13 }} value={f.levels[k] ?? ""} onChange={(e) => setF({ ...f, levels: { ...f.levels, [k]: e.target.value } })}>
                <option value="">No access</option>{LEVELS.map(([v, lv]) => <option key={v} value={v}>{lv}</option>)}
              </select>
            </label>
          ))}
        </div>
      </fieldset>
      <span className="t13">Access starts when their firm accepts. Certify lets them sign off filings; give it only to a qualified professional.</span>
    </Panel>
  );
}

export function AddCertificate({ entityId, onDone, onCancel }: { entityId: string; onDone: () => void; onCancel: () => void }) {
  const [f, setF] = useState<DscForm>(blankDsc());
  const s = useSubmit(async () => {
    unwrap(await api.POST("/entities/{entityId}/dsc-certificates", { params: { path: { entityId } }, body: dscBody(f) as never }));
    onDone();
  });
  return (
    <Panel title="Record a certificate" error={s.error} busy={s.busy} ready={dscReady(f)} cta="Save certificate" onSubmit={() => void s.submit()} onCancel={onCancel}>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))", gap: 12 }}>
        <div className="field"><label htmlFor="ds-person">Holder</label><input id="ds-person" className="input" value={f.person} onChange={(e) => setF({ ...f, person: e.target.value })} /></div>
        <div className="field"><label htmlFor="ds-serial">Certificate serial</label><input id="ds-serial" className="input mono" value={f.serial} onChange={(e) => setF({ ...f, serial: e.target.value })} /></div>
        <div className="field"><label htmlFor="ds-class">Class</label><select id="ds-class" className="input" value={f.cls} onChange={(e) => setF({ ...f, cls: e.target.value })}><option value="class3">Class 3</option><option value="class2">Class 2</option></select></div>
        <div className="field"><label htmlFor="ds-from">Valid from</label><input id="ds-from" type="date" className="input" value={f.from} onChange={(e) => setF({ ...f, from: e.target.value })} /></div>
        <div className="field"><label htmlFor="ds-until">Valid until</label><input id="ds-until" type="date" className="input" value={f.until} onChange={(e) => setF({ ...f, until: e.target.value })} /></div>
      </div>
      <fieldset style={{ border: 0, padding: 0, margin: 0 }}>
        <legend className="t13">Registered on</legend>
        <div style={{ display: "flex", gap: 16, flexWrap: "wrap" }}>
          {PORTALS_FOR_DSC.map(([k, l]) => <label key={k} style={{ fontSize: 14, display: "flex", gap: 6 }}><input type="checkbox" checked={f.on.includes(k)} onChange={() => setF({ ...f, on: toggle(f.on, k) })} />{l}</label>)}
        </div>
      </fieldset>
      <span className="t13">Only a hash of the serial is kept, so we can tell two certificates apart. The token itself never leaves its holder.</span>
    </Panel>
  );
}

export function CreateApiKey({ entityId, onDone, onCancel }: { entityId: string; onDone: (key: string) => void; onCancel: () => void }) {
  const [name, setName] = useState("");
  const [access, setAccess] = useState<Record<string, string>>({});
  const s = useSubmit(async () => {
    const r = unwrap(await api.POST("/entities/{entityId}/api-keys", { params: { path: { entityId } }, body: { name: name.trim(), permissions: keyPermissions(access) } as never })) as unknown as { key: string };
    onDone(r.key);
  });
  return (
    <Panel title="Create an API key" error={s.error} busy={s.busy} ready={keyReady(name, access)} cta="Create key" onSubmit={() => void s.submit()} onCancel={onCancel}>
      <div className="field"><label htmlFor="key-name">Name</label><input id="key-name" className="input" placeholder="Zapier – invoices" value={name} onChange={(e) => setName(e.target.value)} /></div>
      <fieldset style={{ border: 0, padding: 0, margin: 0 }}>
        <legend className="t13">What the key can reach</legend>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: 8 }}>
          {MODULES.map(([k, l]) => (
            <label key={k} style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8, fontSize: 14 }}>{l}
              <select aria-label={`${l} access`} className="input" style={{ height: 36, width: 140, fontSize: 13 }} value={access[k] ?? ""} onChange={(e) => setAccess({ ...access, [k]: e.target.value })}>
                <option value="">None</option><option value="read">Read</option><option value="write">Read and write</option>
              </select>
            </label>
          ))}
        </div>
      </fieldset>
      <span className="t13">A key can never approve, file or pay. The full key is shown once, right after you create it.</span>
    </Panel>
  );
}
