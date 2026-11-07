import type { ReactNode } from "react";
import { statusInfo, type Tone } from "@/lib/compliance";

export function Logo({ size = 24 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 28 28" aria-hidden="true">
      <circle cx="14" cy="14" r="10" fill="none" stroke="#FFFFFF" strokeWidth="3.2" strokeDasharray="47 16" strokeLinecap="round" />
      <circle cx="21.1" cy="6.9" r="2.8" fill="#8EA2FF" />
    </svg>
  );
}

export function Pill({ tone, children }: { tone: Tone; children: ReactNode }) {
  return <span className={`pill ${tone}`}>{children}</span>;
}

export function StatusPill({ status }: { status: string | undefined }) {
  const s = statusInfo(status);
  return <Pill tone={s.tone}>{s.label}</Pill>;
}

export function Icon({ d, size = 18 }: { d: string; size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d={d} />
    </svg>
  );
}

export function ErrorNote({ error }: { error: unknown }) {
  if (!error) return null;
  const msg = error instanceof Error ? error.message : String(error);
  return <div role="alert" className="error">{msg}</div>;
}

export function Loading({ what }: { what: string }) {
  return <div className="empty" role="status">Loading {what}…</div>;
}
