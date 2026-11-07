"use client";
import { useEffect, useState } from "react";
import { api, unwrap } from "@/lib/api";
import { Job, jobStage, otpReady } from "@/lib/integrations";
import { ErrorNote, Pill } from "./ui";

/** Follow a background job until it ends. Calls onFinished once with the final job. Asks for the one-time code when the portal does. */
export function JobFollow({ jobId, doneLabel, onFinished }: { jobId: string; doneLabel: string; onFinished: (job: Job) => void }) {
  const [job, setJob] = useState<Job | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [otp, setOtp] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let live = true;
    let timer: ReturnType<typeof setTimeout>;
    async function tick() {
      try {
        const j = unwrap(await api.GET("/jobs/{id}", { params: { path: { id: jobId } } })) as unknown as Job;
        if (!live) return;
        setJob(j);
        if (jobStage(j, doneLabel).finished) { onFinished(j); return; }
        timer = setTimeout(() => void tick(), 1500);
      } catch (e) { if (live) setError(e); }
    }
    void tick();
    return () => { live = false; clearTimeout(timer); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobId]);

  async function sendOtp() {
    setBusy(true); setError(null);
    try { setJob(unwrap(await api.POST("/jobs/{id}/otp", { params: { path: { id: jobId } }, body: { otp: otp.trim() } })) as unknown as Job); setOtp(""); }
    catch (e) { setError(e); }
    setBusy(false);
  }

  const st = job ? jobStage(job, doneLabel) : null;
  return (
    <span role="status" style={{ display: "inline-flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
      {st ? <Pill tone={st.tone}>{st.label}</Pill> : !error && <span className="t13">Starting…</span>}
      {st?.needsOtp && (
        <form style={{ display: "inline-flex", gap: 8, alignItems: "center" }} onSubmit={(e) => { e.preventDefault(); if (otpReady(otp) && !busy) void sendOtp(); }}>
          <label className="sr" htmlFor={`jo-${jobId}`}>6-digit code</label>
          <input id={`jo-${jobId}`} className="input mono" inputMode="numeric" maxLength={6} placeholder="6-digit code" style={{ width: 130, height: 36 }} value={otp} onChange={(e) => setOtp(e.target.value.replace(/\D/g, ""))} />
          <button type="submit" className="btn primary" disabled={!otpReady(otp) || busy}>{busy ? "Sending…" : "Submit code"}</button>
        </form>
      )}
      <ErrorNote error={error} />
    </span>
  );
}
