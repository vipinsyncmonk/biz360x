"use client";
import { useRouter } from "next/navigation";
import { useEffect, useState, type FormEvent } from "react";
import { ApiProblem, requestCode, signUp } from "@/lib/api";
import { TRACK_KEY, type Track } from "@/lib/onboarding";
import { useSession } from "@/lib/session";
import { ErrorNote, Logo } from "@/components/ui";

const WEEK = [
  { d: "30 Oct", t: "AOC-4 · financial statements", a: "MCA" },
  { d: "31 Oct", t: "TDS return · Q2", a: "Income Tax" },
  { d: "07 Nov", t: "TDS deposit · October", a: "Income Tax" },
  { d: "11 Nov", t: "GSTR-1 · October", a: "GSTN" },
  { d: "15 Nov", t: "PF and ESI deposit", a: "EPFO · ESIC" },
];

export default function Login() {
  const { status, login } = useSession();
  const router = useRouter();
  const [step, setStep] = useState<"email" | "code">("email");
  const [mode, setMode] = useState<"signin" | "create">("signin");
  const [name, setName] = useState("");
  const [track, setTrack] = useState<Track>("startup");
  const [email, setEmail] = useState("");
  const [otp, setOtp] = useState("");
  const [mfa, setMfa] = useState("");
  const [needMfa, setNeedMfa] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => { if (status === "authed") router.replace("/"); }, [status, router]);

  async function sendCode(e: FormEvent) {
    e.preventDefault();
    setBusy(true); setError(null);
    try {
      if (mode === "create") {
        await signUp(email.trim(), name.trim());
        try { window.sessionStorage.setItem(TRACK_KEY, track); } catch { /* the track is only a default */ }
      } else {
        await requestCode(email.trim());
      }
      setStep("code");
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  async function submitCode(e: FormEvent) {
    e.preventDefault();
    setBusy(true); setError(null);
    try {
      await login(email.trim(), otp.trim(), needMfa ? mfa.trim() : undefined);
    } catch (err) {
      if (err instanceof ApiProblem && err.code === "mfa_required") {
        setNeedMfa(true);
        setError(new Error(mfa ? "That authenticator code was not accepted." : "Enter the code from your authenticator app."));
      } else {
        setError(err instanceof ApiProblem && err.status === 401 ? new Error("That code is not valid. Ask for a new one.") : err);
      }
      setBusy(false);
    }
  }

  return (
    <div className="login">
      <aside className="brandpanel">
        <span className="brand" style={{ padding: 0 }}><Logo size={28} /><b className="disp" style={{ fontSize: 21 }}>Biz360X</b></span>
        <div style={{ display: "flex", flexDirection: "column", gap: 20, marginTop: "auto" }}>
          <h1 className="disp">Your company’s week, already planned.</h1>
          <p style={{ margin: 0, fontSize: 17, lineHeight: 1.55, color: "#B7BCC5", maxWidth: 460 }}>
            Sign in to see what is due, what is ready to file and what is waiting on you.
          </p>
        </div>
        <div style={{ border: "1px solid #2A2E38", borderRadius: 16, overflow: "hidden", maxWidth: 520 }}>
          <div className="mono" style={{ padding: "14px 18px", fontSize: 12, color: "#9CA3AF" }}>DUE ACROSS INDIA · TYPICAL MONTH</div>
          {WEEK.map((w) => (
            <div key={w.t} style={{ display: "flex", gap: 16, alignItems: "center", padding: "12px 18px", borderTop: "1px solid #1F232B" }}>
              <span className="mono" style={{ width: 56, flex: "none", fontSize: 13, color: "#8EA2FF" }}>{w.d}</span>
              <span style={{ flex: 1, fontSize: 15, color: "#fff" }}>{w.t}</span>
              <span style={{ fontSize: 13, color: "#9CA3AF" }}>{w.a}</span>
            </div>
          ))}
        </div>
      </aside>

      <main className="formpanel">
        <div className="formbox">
          {step === "email" && (
            <div className="seg" role="tablist" aria-label="Account" style={{ display: "grid", gridTemplateColumns: "1fr 1fr" }}>
              <button type="button" role="tab" aria-selected={mode === "signin"} onClick={() => { setMode("signin"); setError(null); }}>Sign in</button>
              <button type="button" role="tab" aria-selected={mode === "create"} onClick={() => { setMode("create"); setError(null); }}>Create workspace</button>
            </div>
          )}
          {step === "email" ? (
            <form onSubmit={sendCode} style={{ display: "flex", flexDirection: "column", gap: 18 }}>
              <div>
                <h2 className="disp" style={{ margin: 0, fontSize: 32 }}>{mode === "create" ? "Create your workspace" : "Welcome back"}</h2>
                <p className="muted" style={{ margin: "6px 0 0", fontSize: 15 }}>{mode === "create" ? "Takes about two minutes. No card needed." : "We will send a one-time code to your work email."}</p>
              </div>
              {mode === "create" && (
                <>
                  <fieldset style={{ border: 0, margin: 0, padding: 0 }}>
                    <legend style={{ fontSize: 14, fontWeight: 500, marginBottom: 8, padding: 0 }}>Which best describes you?</legend>
                    <div style={{ display: "grid", gridTemplateColumns: "repeat(3, minmax(0, 1fr))", gap: 8 }}>
                      {([["business", "Business", "Operating company or firm"], ["startup", "Startup", "Founding or funded"], ["msme", "MSME", "Udyam or eligible"]] as const).map(([k, t, b]) => (
                        <button key={k} type="button" aria-pressed={track === k} onClick={() => setTrack(k)} className="trackbtn">
                          <span style={{ fontSize: 15, fontWeight: 600 }}>{t}</span><span style={{ fontSize: 12, opacity: 0.75 }}>{b}</span>
                        </button>
                      ))}
                    </div>
                  </fieldset>
                  <div className="field">
                    <label htmlFor="nm">Your name</label>
                    <input id="nm" className="input" autoComplete="name" required value={name} onChange={(e) => setName(e.target.value)} />
                  </div>
                </>
              )}
              <div className="field">
                <label htmlFor="em">Work email</label>
                <input id="em" className="input" type="email" autoComplete="email" required value={email} onChange={(e) => setEmail(e.target.value)} placeholder="you@company.in" />
              </div>
              <ErrorNote error={error} />
              <button className="go" type="submit" disabled={busy || !email || (mode === "create" && !name.trim())}>{busy ? "Sending…" : mode === "create" ? "Send code to continue" : "Send code"}</button>
              {mode === "create" && <p className="muted" style={{ margin: 0, fontSize: 13, textAlign: "center", lineHeight: 1.5 }}>We confirm your email with a one-time code, then ask for your company’s CIN, LLPIN, GSTIN or PAN.</p>}
            </form>
          ) : (
            <form onSubmit={submitCode} style={{ display: "flex", flexDirection: "column", gap: 18 }}>
              <div>
                <h2 className="disp" style={{ margin: 0, fontSize: 32 }}>Enter your code</h2>
                <p className="muted" style={{ margin: "6px 0 0", fontSize: 15 }}>A six-digit code is on its way to {email}. It is valid for five minutes.</p>
              </div>
              <div className="field">
                <label htmlFor="otp">One-time code</label>
                <input id="otp" className="input mono" inputMode="numeric" autoComplete="one-time-code" pattern="[0-9]{6}" maxLength={6} required value={otp}
                       onChange={(e) => setOtp(e.target.value.replace(/\D/g, ""))} autoFocus />
              </div>
              {needMfa && (
                <div className="field">
                  <label htmlFor="mfa">Authenticator code</label>
                  <input id="mfa" className="input mono" inputMode="numeric" autoComplete="off" pattern="[0-9]{6}" maxLength={6} required value={mfa}
                         onChange={(e) => setMfa(e.target.value.replace(/\D/g, ""))} />
                </div>
              )}
              <ErrorNote error={error} />
              <button className="go" type="submit" disabled={busy || otp.length !== 6}>{busy ? "Signing in…" : "Sign in"}</button>
              <button type="button" className="btn" onClick={() => { setStep("email"); setOtp(""); setError(null); setNeedMfa(false); }}>Use a different email</button>
              {process.env.NODE_ENV !== "production" && (
                <p className="muted" style={{ margin: 0, fontSize: 13, textAlign: "center" }}>Development server: the code <span className="mono">000000</span> works.</p>
              )}
            </form>
          )}
        </div>
      </main>
    </div>
  );
}
