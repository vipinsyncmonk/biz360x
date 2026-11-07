"use client";
import { use, useEffect, useState, type ReactNode } from "react";
import { ApiProblem } from "@/lib/api";
import { formatInr, shortDate } from "@/lib/format";
import { METHOD_INFO, IntentResult, PublicInvoice, getInvoice, orderMethods, overdueLabel, pageState, queryReady, raiseQuery, startPayment } from "@/lib/pay";

const linkBtn = { textDecoration: "none", display: "inline-flex", justifyContent: "center", alignItems: "center" } as const;

/** Public payment page for one invoice. No sign-in; shows only what is printed on the invoice. */
export default function Pay({ params }: { params: Promise<{ code: string }> }) {
  const { code } = use(params);
  const [inv, setInv] = useState<PublicInvoice | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [method, setMethod] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<IntentResult | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [asking, setAsking] = useState(false);
  const [q, setQ] = useState({ name: "", email: "", message: "" });
  const [sent, setSent] = useState(false);

  useEffect(() => {
    let live = true;
    getInvoice(code).then((i) => { if (live) { setInv(i); setMethod(orderMethods(i.methods)[0] ?? ""); } }, (e: Error) => { if (live) setError(e); });
    return () => { live = false; };
  }, [code]);

  const methods = orderMethods(inv?.methods);

  async function pay() {
    setBusy(true); setActionError(null);
    try { setResult(await startPayment(code, method)); }
    catch (e) { setActionError((e as Error).message); }
    setBusy(false);
  }
  async function ask() {
    setBusy(true); setActionError(null);
    try { await raiseQuery(code, q); setSent(true); setAsking(false); }
    catch (e) { setActionError((e as Error).message); }
    setBusy(false);
  }

  const wrap = (children: ReactNode) => (
    <main style={{ minHeight: "100vh", display: "flex", justifyContent: "center", padding: "24px 16px" }}>
      <div style={{ width: "100%", maxWidth: 520, display: "flex", flexDirection: "column", gap: 16 }}>
        {children}
        <span className="t13" style={{ textAlign: "center" }}>Invoicing and payments by Biz360X</span>
      </div>
    </main>
  );

  if (error) {
    const notFound = error instanceof ApiProblem && error.status === 404;
    return wrap(
      <div className="card card-pad" role="alert">
        <h1 className="disp" style={{ margin: 0, fontSize: 26 }}>{notFound ? "This payment link was not found" : "We could not load this invoice"}</h1>
        <p className="muted" style={{ marginBottom: 0 }}>{notFound ? "Check the link in your invoice email, or ask the sender for a new one." : "Please try again in a minute."}</p>
      </div>,
    );
  }
  if (!inv) return wrap(<div className="card card-pad" role="status">Loading your invoice…</div>);

  const state = pageState(inv);
  const late = overdueLabel(inv.days_overdue ?? 0);
  const va = inv.virtual_account;

  return wrap(
    <>
      <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
        <b className="disp" style={{ fontSize: 20 }}>{inv.supplier_name}</b>
        <span className="t13 mono">GSTIN {inv.supplier_gstin}</span>
      </div>

      <section className="card card-pad" style={{ display: "flex", flexDirection: "column", gap: 10 }} aria-label="Invoice">
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
          <span className="mono" style={{ fontWeight: 600 }}>Invoice {inv.number}</span>
          {state === "due" && late && <span className="pill due">{late}</span>}
          {state === "paid" && <span className="pill ok">Paid</span>}
        </div>
        {state === "due" && <span className="t13">Amount due from {inv.buyer_name}</span>}
        <span className="disp" style={{ fontSize: 40, fontWeight: 700, lineHeight: 1.1 }}>{formatInr(inv.amount_due, { decimals: false })}</span>
        <dl style={{ margin: 0, display: "grid", gridTemplateColumns: "1fr auto", gap: "4px 12px", fontSize: 14 }}>
          <dt className="muted">Issued</dt><dd className="mono" style={{ margin: 0, textAlign: "right" }}>{shortDate(inv.doc_date)}</dd>
          <dt className="muted">{late ? "Was due" : "Due"}</dt><dd className="mono" style={{ margin: 0, textAlign: "right" }}>{shortDate(inv.due_date)}</dd>
          <dt className="muted">Taxable value</dt><dd className="mono" style={{ margin: 0, textAlign: "right" }}>{formatInr(inv.taxable_total)}</dd>
          <dt className="muted">GST</dt><dd className="mono" style={{ margin: 0, textAlign: "right" }}>{formatInr(inv.tax_total)}</dd>
        </dl>
        {inv.irn_verified && <span className="t13">E-invoice IRN verified</span>}
      </section>

      {state === "paid" && (
        <section className="card card-pad" role="status"><h2 style={{ margin: 0 }}>Thank you</h2><p className="muted" style={{ marginBottom: 0 }}>This invoice is paid in full. {inv.supplier_name} has been notified.</p></section>
      )}
      {state === "closed" && (
        <section className="card card-pad" role="status"><h2 style={{ margin: 0 }}>This payment link is no longer active</h2><p className="muted" style={{ marginBottom: 0 }}>Please contact {inv.supplier_name} for a new link.</p></section>
      )}

      {state === "due" && !result && (
        <section className="card card-pad" style={{ display: "flex", flexDirection: "column", gap: 12 }} aria-label="Payment">
          <h2 style={{ margin: 0, fontSize: 14 }} className="mono">HOW WOULD YOU LIKE TO PAY?</h2>
          {methods.length === 0 && <span className="t13">No online payment method is enabled for this invoice. Please pay as printed on the invoice.</span>}
          <div role="radiogroup" aria-label="Payment method" style={{ display: "grid", gap: 8 }}>
            {methods.map((m) => (
              <label key={m} style={{ display: "flex", gap: 10, alignItems: "flex-start", padding: 12, border: `${method === m ? 1.5 : 1}px solid ${method === m ? "var(--ink)" : "var(--line)"}`, borderRadius: 12, cursor: "pointer", background: "#fff" }}>
                <input type="radio" name="method" checked={method === m} onChange={() => setMethod(m)} />
                <span>{METHOD_INFO[m]?.title ?? m}<span className="t13" style={{ display: "block" }}>{METHOD_INFO[m]?.hint}</span></span>
              </label>
            ))}
          </div>
          {method === "bank_transfer" && va && (
            <div style={{ background: "var(--surface-2)", border: "1px solid var(--line-2)", borderRadius: 12, padding: 12, display: "grid", gridTemplateColumns: "auto 1fr", gap: "4px 12px", fontSize: 14 }}>
              <span className="muted">Account name</span><span>{inv.supplier_name}</span>
              <span className="muted">Account no.</span><span className="mono">{va.va_number}</span>
              <span className="muted">IFSC</span><span className="mono">{va.ifsc}</span>
              {va.bank_name && <><span className="muted">Bank</span><span>{va.bank_name}</span></>}
              <span className="t13" style={{ gridColumn: "1 / -1" }}>This account number is unique to this invoice, so your payment is matched automatically.</span>
            </div>
          )}
          {actionError && <span role="alert" className="t13" style={{ color: "var(--amber)" }}>{actionError}</span>}
          {method && <button type="button" className="btn primary" disabled={busy} onClick={() => void pay()}>{busy ? "One moment…" : METHOD_INFO[method]?.cta ?? "Continue"}</button>}
        </section>
      )}

      {result && (
        <section className="card card-pad" role="status" style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          <h2 style={{ margin: 0 }}>{result.redirect_url || result.upi_intent ? "Continue to pay" : "Next step"}</h2>
          {result.instructions && <p style={{ margin: 0 }}>{result.instructions}</p>}
          {result.redirect_url && <a className="btn primary" style={linkBtn} href={result.redirect_url}>Continue</a>}
          {result.upi_intent && <a className="btn primary" style={linkBtn} href={result.upi_intent}>Open your UPI app</a>}
          <span className="t13">Reference <span className="mono">{result.reference}</span>. The invoice shows as paid once the money reaches {inv.supplier_name}.</span>
          <button type="button" className="linkbtn" style={{ color: "var(--cobalt)" }} onClick={() => setResult(null)}>Choose another way to pay</button>
        </section>
      )}

      <section className="card card-pad" style={{ display: "flex", flexDirection: "column", gap: 10 }} aria-label="Query">
        {sent ? <span role="status">Thank you. {inv.supplier_name} has your query and will reply by email.</span> : !asking ? (
          <>
            <span className="t13">Something wrong with this invoice?</span>
            <button type="button" className="linkbtn" style={{ color: "var(--cobalt)", fontSize: 14 }} onClick={() => setAsking(true)}>Raise a query</button>
          </>
        ) : (
          <>
            <div className="field"><label htmlFor="q-name">Your name (optional)</label><input id="q-name" className="input" value={q.name} onChange={(e) => setQ({ ...q, name: e.target.value })} /></div>
            <div className="field"><label htmlFor="q-email">Your email</label><input id="q-email" type="email" className="input" value={q.email} onChange={(e) => setQ({ ...q, email: e.target.value })} /></div>
            <div className="field"><label htmlFor="q-msg">What is wrong?</label><textarea id="q-msg" className="input" rows={4} maxLength={2000} style={{ height: "auto", padding: 12 }} value={q.message} onChange={(e) => setQ({ ...q, message: e.target.value })} /></div>
            {actionError && <span role="alert" className="t13" style={{ color: "var(--amber)" }}>{actionError}</span>}
            <span style={{ display: "flex", gap: 10 }}>
              <button type="button" className="btn primary" disabled={busy || !queryReady(q)} onClick={() => void ask()}>{busy ? "Sending…" : "Send query"}</button>
              <button type="button" className="btn" onClick={() => setAsking(false)}>Cancel</button>
            </span>
          </>
        )}
      </section>
    </>,
  );
}
