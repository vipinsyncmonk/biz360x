"use client";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { api, unwrap } from "@/lib/api";
import { CopilotMessage, CopilotThread, DEFAULT_TITLE, SUGGESTIONS, askReady, citationCta, citationHref, groupThreads } from "@/lib/copilot";
import { useLoad } from "@/lib/hooks";
import { useSession } from "@/lib/session";
import { ActionCard, AnswerText } from "@/components/CopilotParts";
import { ErrorNote, Loading } from "@/components/ui";

export default function Copilot() {
  const { entity, me } = useSession();
  const id = entity?.id ?? null;
  const [current, setCurrent] = useState<string | null>(null);
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const [pending, setPending] = useState<string | null>(null);     // the question while the answer is being written
  const [error, setError] = useState<unknown>(null);
  const [cite, setCite] = useState<{ message: string; n: number } | null>(null);
  const end = useRef<HTMLDivElement>(null);

  const threads = useLoad(async () => unwrap(await api.GET("/entities/{entityId}/copilot/threads", { params: { path: { entityId: id! }, query: { limit: 100 } } })).data as unknown as CopilotThread[], id);
  const thread = useLoad(async () => unwrap(await api.GET("/entities/{entityId}/copilot/threads/{id}", { params: { path: { entityId: id!, id: current! } } })) as unknown as CopilotThread, id && current ? `${id}|${current}` : null);

  const messages: CopilotMessage[] = current ? (thread.data?.messages ?? []) : [];
  const lastAnswer = [...messages].reverse().find((m) => m.role === "assistant");
  const shown = messages.find((m) => m.id === cite?.message) ?? lastAnswer;
  const selected = cite && shown && cite.message === shown.id ? cite.n : null;

  useEffect(() => { end.current?.scrollIntoView?.({ block: "end" }); }, [messages.length, pending]);

  async function send(question: string) {
    if (!id || !askReady(question) || sending) return;
    setSending(true); setError(null); setPending(question.trim());
    try {
      let tid = current;
      if (!tid) {
        const t = unwrap(await api.POST("/entities/{entityId}/copilot/threads", { params: { path: { entityId: id } }, body: {} as never })) as unknown as CopilotThread;
        tid = t.id; setCurrent(tid);
      }
      const reply = unwrap(await api.POST("/entities/{entityId}/copilot/threads/{id}/messages", { params: { path: { entityId: id, id: tid } }, body: { content: question.trim() } as never })) as unknown as CopilotMessage;
      setText(""); setCite({ message: reply.id, n: 1 });
      thread.reload(); threads.reload();
    } catch (e) { setError(e); }
    setPending(null); setSending(false);
  }

  const groups = groupThreads(threads.data ?? []);
  const title = current ? (thread.data?.title ?? DEFAULT_TITLE) : DEFAULT_TITLE;

  return (
    <main className="page" style={{ padding: 0 }}>
      <div style={{ display: "flex", flexWrap: "wrap", alignItems: "stretch", minHeight: "calc(100vh - 80px)" }}>
        <section aria-label="Conversations" style={{ flex: "1 1 240px", maxWidth: 300, borderRight: "1px solid var(--line)", padding: 16, display: "flex", flexDirection: "column", gap: 12 }}>
          <button type="button" className="btn primary" onClick={() => { setCurrent(null); setCite(null); setError(null); }}>+ New conversation</button>
          <ErrorNote error={threads.error} />
          {groups.length === 0 && !threads.loading && <span className="t13">No conversations yet.</span>}
          {groups.map((g) => (
            <div key={g.heading} style={{ display: "flex", flexDirection: "column", gap: 4 }}>
              <span className="mono t13">{g.heading}</span>
              {g.items.map((t) => (
                <button key={t.id} type="button" aria-pressed={current === t.id} onClick={() => { setCurrent(t.id); setCite(null); setError(null); }}
                        style={{ font: "inherit", textAlign: "left", border: 0, borderRadius: 10, padding: 10, minHeight: 44, cursor: "pointer", background: current === t.id ? "var(--line-2, #ECEBE5)" : "transparent", color: "var(--ink)" }}>
                  <span style={{ display: "block", fontSize: 14, fontWeight: 500 }}>{t.title}</span>
                  {t.module && <span className="t13">{t.module}</span>}
                </button>
              ))}
            </div>
          ))}
        </section>

        <section aria-label="Conversation" style={{ flex: "999 1 480px", minWidth: 0, display: "flex", flexDirection: "column" }}>
          <header style={{ padding: "16px 28px", borderBottom: "1px solid var(--line)", display: "flex", flexWrap: "wrap", gap: 10, alignItems: "center", justifyContent: "space-between" }}>
            <h1 style={{ margin: 0, fontSize: 17, fontWeight: 600 }}>{title}</h1>
            <span className="mono t13">Entity · {entity?.legal_name}</span>
          </header>
          <div style={{ flex: 1, padding: 28, display: "flex", flexDirection: "column", gap: 24, maxWidth: 820, width: "100%", boxSizing: "border-box", margin: "0 auto" }}>
            {current && thread.loading && !thread.data && <Loading what="the conversation" />}
            <ErrorNote error={thread.error} />
            {!current && !pending && (
              <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
                <h2 style={{ margin: 0 }}>Ask about your company</h2>
                <span className="t13">I answer from your own records and cite every one. I can suggest actions, but a person has to approve them before anything happens.</span>
                <span style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                  {SUGGESTIONS.map((s) => <button key={s} type="button" className="btn" disabled={sending} onClick={() => void send(s)}>{s}</button>)}
                </span>
              </div>
            )}
            {messages.map((m) => m.role === "user" ? (
              <div key={m.id} style={{ alignSelf: "flex-end", maxWidth: "80%", background: "var(--ink)", color: "#fff", padding: "14px 16px", borderRadius: "16px 16px 4px 16px", fontSize: 15, lineHeight: 1.5 }}>{m.content}</div>
            ) : (
              <article key={m.id} aria-label="Answer" style={{ display: "flex", flexDirection: "column", gap: 14 }}>
                <AnswerText message={m} selected={cite?.message === m.id ? cite.n : null} onCite={(n) => setCite({ message: m.id, n })} />
                {m.actions.map((a) => <ActionCard key={a.id} entityId={id!} meId={me?.id} action={a} onChanged={() => thread.reload()} />)}
              </article>
            ))}
            {pending && (
              <>
                {!messages.some((m) => m.content === pending) && <div style={{ alignSelf: "flex-end", maxWidth: "80%", background: "var(--ink)", color: "#fff", padding: "14px 16px", borderRadius: "16px 16px 4px 16px", fontSize: 15 }}>{pending}</div>}
                <span className="t13" role="status">Reading your records…</span>
              </>
            )}
            <ErrorNote error={error} />
            <div ref={end} />
          </div>

          <form style={{ padding: "16px 28px 24px", borderTop: "1px solid var(--line)" }} onSubmit={(e) => { e.preventDefault(); void send(text); }}>
            <div style={{ maxWidth: 820, margin: "0 auto", display: "flex", flexDirection: "column", gap: 8 }}>
              <label className="sr" htmlFor="ask">Ask a question</label>
              <div style={{ display: "flex", gap: 10, alignItems: "flex-end", border: "1px solid var(--line)", borderRadius: 16, padding: "10px 10px 10px 16px", background: "#fff" }}>
                <textarea id="ask" rows={2} value={text} placeholder="Ask about filings, GST credit or what customers owe" onChange={(e) => setText(e.target.value)}
                          onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); void send(text); } }}
                          style={{ flex: 1, border: 0, resize: "none", font: "inherit", fontSize: 15, lineHeight: 1.5, background: "transparent", outline: "none" }} />
                <button type="submit" className="btn primary" disabled={!askReady(text) || sending} style={{ height: 44 }}>{sending ? "Asking…" : "Ask"}</button>
              </div>
              <span className="t13">Copilot drafts and explains. People approve. Every answer cites the records it used.</span>
            </div>
          </form>
        </section>

        <aside aria-label="Sources" style={{ flex: "1 1 260px", maxWidth: 320, borderLeft: "1px solid var(--line)", background: "var(--paper, #FAFAF7)", padding: "20px 16px", display: "flex", flexDirection: "column", gap: 12 }}>
          <span className="mono t13">SOURCES IN THIS ANSWER</span>
          {(shown?.citations ?? []).length === 0 && <span className="t13">{shown ? "This answer did not use any record." : "Sources appear here with each answer."}</span>}
          {(shown?.citations ?? []).map((c) => {
            const href = citationHref(c);
            return (
              <div key={c.ordinal} style={{ borderRadius: 12, padding: 14, display: "flex", flexDirection: "column", gap: 6, background: "#fff", border: selected === c.ordinal ? "1px solid var(--cobalt, #2846E0)" : "1px solid var(--line)" }}>
                <div style={{ display: "flex", gap: 8, alignItems: "center" }}><span className="mono" style={{ fontSize: 11, fontWeight: 600, color: "#1A31A8", background: "#E8ECFF", borderRadius: 5, padding: "1px 6px" }}>{c.ordinal}</span><span style={{ fontSize: 14, fontWeight: 600 }}>{c.label}</span></div>
                {href && <Link href={href} style={{ fontSize: 13 }}>{citationCta(c.subject)}</Link>}
              </div>
            );
          })}
        </aside>
      </div>
    </main>
  );
}
