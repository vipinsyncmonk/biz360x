"use client";
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { api, clearTokens, hasRefreshToken, refresh, signInWithCode, signOut, unwrap, type Me } from "./api";

type Status = "loading" | "anon" | "authed";
type EntityRow = NonNullable<Me["entities"]>[number];

interface Session {
  status: Status;
  me: Me | null;
  entity: EntityRow | null;
  setEntityId: (id: string) => void;
  login: (email: string, otp: string, mfa?: string) => Promise<void>;
  logout: () => Promise<void>;
}

const Ctx = createContext<Session | null>(null);
const ENTITY_KEY = "bzx.entity";

function remembered(): string | null {
  try { return window.localStorage.getItem(ENTITY_KEY); } catch { return null; }       // blocked storage must not break the app
}

export function SessionProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<Status>("loading");
  const [me, setMe] = useState<Me | null>(null);
  const [entityId, setId] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const m = unwrap(await api.GET("/me"));
      setMe(m);
      const ids = (m.entities ?? []).map((e) => e.id);
      const pick = remembered();
      setId(pick && ids.includes(pick) ? pick : (ids[0] ?? null));
      setStatus("authed");
    } catch {
      clearTokens();
      setMe(null);
      setStatus("anon");
    }
  }, []);

  useEffect(() => {
    if (!hasRefreshToken()) { setStatus("anon"); return; }
    void refresh().then((t) => (t ? load() : setStatus("anon")));
  }, [load]);

  const value = useMemo<Session>(() => ({
    status, me,
    entity: (me?.entities ?? []).find((e) => e.id === entityId) ?? null,
    setEntityId: (id) => { setId(id); try { window.localStorage.setItem(ENTITY_KEY, id); } catch { /* ignore */ } },
    login: async (email, otp, mfa) => { await signInWithCode(email, otp, mfa); await load(); },
    logout: async () => { await signOut(); setMe(null); setId(null); setStatus("anon"); },
  }), [status, me, entityId, load]);

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useSession(): Session {
  const v = useContext(Ctx);
  if (!v) throw new Error("useSession outside SessionProvider");
  return v;
}
