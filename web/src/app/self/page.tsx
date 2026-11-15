"use client";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { api, unwrap } from "@/lib/api";
import { useLoad } from "@/lib/hooks";
import { employmentLine, pickEmployment, type Employment, type MyRecord } from "@/lib/self";
import { useSession } from "@/lib/session";
import { EmploymentInvitations, MyDetailsPanel, MyLeavePanel, MyPayslipsPanel, MyPoliciesPanel, MyRecordCard } from "@/components/SelfPanels";
import { MyTaxPanel } from "@/components/TaxPanels";
import { ErrorNote, Loading, Logo } from "@/components/ui";

const PICK_KEY = "bzx.employment";

/** The employee's own page: their record, leave, payslips and the company's policies. It sits outside the company workspace because an
 *  employee is not a member of the company and sees nothing else of it. */
export default function Self() {
  const { status, me, reload, logout } = useSession();
  const router = useRouter();
  const [wanted, setWanted] = useState<string | null>(null);

  useEffect(() => { if (status === "anon") router.replace("/login"); }, [status, router]);
  useEffect(() => { try { setWanted(window.sessionStorage.getItem(PICK_KEY)); } catch { /* the first one is shown */ } }, []);

  const employments = ((me as { employments?: Employment[] } | null)?.employments ?? []);
  const current = pickEmployment(employments, wanted);
  const id = current?.employee_id ?? null;
  const record = useLoad(async () => unwrap(await api.GET("/self/{employeeId}", { params: { path: { employeeId: id! } } })) as unknown as MyRecord, id);

  function choose(employeeId: string) {
    setWanted(employeeId);
    try { window.sessionStorage.setItem(PICK_KEY, employeeId); } catch { /* not remembered */ }
  }

  if (status !== "authed" || !me) return <div className="empty" role="status">Loading…</div>;
  const hasWorkspace = (me.entities ?? []).length > 0;

  return (
    <div style={{ minHeight: "100vh" }}>
      <header style={{ borderBottom: "1px solid var(--line)", background: "#fff" }}>
        <div style={{ maxWidth: 960, margin: "0 auto", padding: "0 24px", minHeight: 64, display: "flex", flexWrap: "wrap", alignItems: "center", gap: 16, justifyContent: "space-between" }}>
          <span style={{ display: "flex", alignItems: "center", gap: 10 }}><Logo /><b className="disp" style={{ fontSize: 20 }}>Biz360X</b></span>
          <span style={{ display: "flex", alignItems: "center", gap: 16, flexWrap: "wrap" }}>
            {hasWorkspace && <Link href="/" style={{ fontSize: 14 }}>Company workspace</Link>}
            <span style={{ fontSize: 14 }}>{me.full_name ?? me.email}</span>
            <button type="button" className="linkbtn" onClick={() => void logout().then(() => router.replace("/login"))}>Sign out</button>
          </span>
        </div>
      </header>

      <main className="page" style={{ maxWidth: 960, margin: "0 auto" }}>
        <EmploymentInvitations onAccepted={(eid) => { choose(eid); void reload(); }} />
        {!current ? (
          <section className="card card-pad" aria-label="No employee page yet">
            <h1 className="disp" style={{ margin: 0, fontSize: 28 }}>No employee page yet</h1>
            <p style={{ margin: "8px 0 0" }}>Your employer opens this page for you from their HR screen, using the email on your employee record. If they have, check that you signed in with that same email.</p>
            <p style={{ margin: "8px 0 0" }}>{hasWorkspace ? <Link href="/">Go to your company workspace</Link> : <Link href="/onboarding">Set up a company of your own instead</Link>}</p>
          </section>
        ) : (
          <>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 16, justifyContent: "space-between", alignItems: "flex-end" }}>
              <div>
                <span className="mono t13">MY EMPLOYEE PAGE</span>
                <h1 className="disp" style={{ margin: "6px 0 0", fontSize: 34 }}>{current.legal_name}</h1>
                <span className="t13">{employmentLine(current)}</span>
              </div>
              {employments.length > 1 && (
                <div className="field" style={{ minWidth: 220 }}><label htmlFor="self-co">Company</label>
                  <select id="self-co" className="input" value={current.employee_id} onChange={(e) => choose(e.target.value)}>
                    {employments.map((e) => <option key={e.employee_id} value={e.employee_id}>{e.legal_name}</option>)}
                  </select></div>
              )}
            </div>
            <ErrorNote error={record.error} />
            {record.data ? <MyRecordCard rec={record.data} onChanged={() => record.reload()} /> : !record.error && <Loading what="your record" />}
            <MyDetailsPanel employeeId={current.employee_id} onChanged={() => record.reload()} />
            <MyLeavePanel employeeId={current.employee_id} />
            <MyPayslipsPanel employeeId={current.employee_id} />
            <MyTaxPanel employeeId={current.employee_id} />
            <MyPoliciesPanel employeeId={current.employee_id} onChanged={() => record.reload()} />
            <span className="t13">You see only your own record here. Changes to your PAN, bank account or mobile are checked by HR before they apply.</span>
          </>
        )}
      </main>
    </div>
  );
}
