"use client";
import { useEffect, useState } from "react";
import { api, unwrap } from "@/lib/api";
import { Job, jobStage } from "@/lib/integrations";
import { ErrorNote, Pill } from "./ui";

/** Follow a background job until it ends. Calls onFinished once with the final job. */
export function JobFollow({ jobId, doneLabel, onFinished }: { jobId: string; doneLabel: string; onFinished: (job: Job) => void }) {
  const [job, setJob] = useState<Job | null>(null);
  const [error, setError] = useState<unknown>(null);

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

  const st = job ? jobStage(job, doneLabel) : null;
  return (
    <span role="status" style={{ display: "inline-flex", gap: 8, alignItems: "center" }}>
      {st ? <Pill tone={st.tone}>{st.label}</Pill> : !error && <span className="t13">Starting…</span>}
      <ErrorNote error={error} />
    </span>
  );
}
