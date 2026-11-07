"use client";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { use, useState } from "react";
import { api, unwrap } from "@/lib/api";
import { useLoad } from "@/lib/hooks";
import { Employee, INPUT_FIELDS, InputDraft, InputRow, Run, draftFromRows, inputProblem, inputsBody, inputsProblem, runName } from "@/lib/payroll";
import { useSession } from "@/lib/session";
import { ErrorNote, Loading } from "@/components/ui";

/** Attendance and one-off figures for one month. Saving replaces the month's inputs; pay is worked out by the server when the run is computed. */
export default function Inputs({ params }: { params: Promise<{ runId: string }> }) {
  const { runId } = use(params);
  const { entity } = useSession();
  const id = entity?.id ?? null;
  const router = useRouter();
  const [edits, setEdits] = useState<InputDraft | null>(null);
  const [busy, setBusy] = useState<"save" | "compute" | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [saved, setSaved] = useState(false);

  const run = useLoad(async () => unwrap(await api.GET("/entities/{entityId}/payroll-runs/{id}", { params: { path: { entityId: id!, id: runId } } })) as unknown as Run, id ? `${id}|${runId}` : null);
  const people = useLoad(async () => unwrap(await api.GET("/entities/{entityId}/employees", { params: { path: { entityId: id! }, query: { limit: 200 } } })).data as unknown as Employee[], id);
  const stored = useLoad(async () => unwrap(await api.GET("/entities/{entityId}/payroll-runs/{id}/inputs", { params: { path: { entityId: id!, id: runId } } })) as unknown as InputRow[], id ? `${id}|${runId}` : null);

  const draft: InputDraft = edits ?? draftFromRows(stored.data ?? []);
  const staff = (people.data ?? []).filter((p) => p.status !== "exited" && p.status !== "onboarding");
  const editable = run.data ? ["inputs_open", "computed"].includes(run.data.status) : false;
  const bad = inputsProblem(draft);

  const set = (emp: string, key: string, v: string) => setEdits({ ...draft, [emp]: { ...draft[emp], [key]: v } });

  async function save(andCompute: boolean) {
    setBusy(andCompute ? "compute" : "save"); setError(null); setSaved(false);
    try {
      unwrap(await api.PUT("/entities/{entityId}/payroll-runs/{id}/inputs", { params: { path: { entityId: id!, id: runId } }, body: inputsBody(draft) as never }));
      if (andCompute) {
        unwrap(await api.POST("/entities/{entityId}/payroll-runs/{id}/compute", { params: { path: { entityId: id!, id: runId } } }));
        router.push("/payroll");
        return;
      }
      setSaved(true); setEdits(null); stored.reload(); run.reload();
    } catch (e) { setError(e); }
    setBusy(null);
  }

  if (!entity) return <main className="page"><div className="card card-pad">Select a workspace first.</div></main>;
  return (
    <main className="page">
      <div>
        <Link href="/payroll" style={{ fontSize: 14 }}>← Payroll &amp; HR</Link>
        <h1 className="disp" style={{ margin: "8px 0 0", fontSize: 36 }}>{run.data ? `${runName(run.data.period_month)} inputs` : "Payroll inputs"}</h1>
        <span className="muted" style={{ fontSize: 14 }}>Days without pay and one-off amounts for this month. Leave a box empty for none.</span>
      </div>
      <ErrorNote error={run.error ?? people.error ?? stored.error ?? error} />
      {run.data && !editable && <div className="card card-pad" role="status">This run is {run.data.status.replace(/_/g, " ")}, so its inputs can no longer change.</div>}

      <section className="card" style={{ overflow: "hidden" }}>
        {people.loading || stored.loading ? <Loading what="employees" /> : (
          <div className="tablewrap">
            <table style={{ minWidth: 760 }}>
              <thead><tr><th scope="col">Employee</th>{INPUT_FIELDS.map(([k, l]) => <th key={k} scope="col">{l}</th>)}</tr></thead>
              <tbody>
                {staff.map((p) => (
                  <tr key={p.id}>
                    <td><span style={{ fontWeight: 500 }}>{p.full_name}</span><span className="t13" style={{ display: "block" }}>{p.employee_code}</span></td>
                    {INPUT_FIELDS.map(([k, l]) => {
                      const v = draft[p.id]?.[k] ?? "";
                      const problem = inputProblem(k, v);
                      return (
                        <td key={k}>
                          <input aria-label={`${l}, ${p.full_name}`} className="input mono" style={{ height: 38, fontSize: 14, width: 110 }} inputMode="decimal" placeholder="0" disabled={!editable}
                                 value={v} onChange={(e) => set(p.id, k, e.target.value)} />
                          {problem && <span className="t13" style={{ display: "block", color: "var(--amber)" }}>{problem}</span>}
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
            {staff.length === 0 && <div className="empty">No one is on payroll yet. <Link href="/payroll/employees">Add employees</Link> first.</div>}
          </div>
        )}
      </section>

      {editable && (
        <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "center" }}>
          <button type="button" className="btn" disabled={!!busy || bad} onClick={() => void save(false)}>{busy === "save" ? "Saving…" : "Save inputs"}</button>
          <button type="button" className="btn primary" disabled={!!busy || bad} onClick={() => void save(true)}>{busy === "compute" ? "Computing…" : "Save and compute pay"}</button>
          <span className="t13" role="status">{saved ? "Saved" : ""}</span>
        </div>
      )}
    </main>
  );
}
