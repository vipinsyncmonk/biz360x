"use client";
import { useCallback, useEffect, useRef, useState } from "react";

export interface Loaded<T> { data: T | null; error: unknown; loading: boolean; reload: () => void }

/** Run an async loader when `key` changes. A slow response from an old key never overwrites a newer one. */
export function useLoad<T>(loader: () => Promise<T>, key: string | null): Loaded<T> {
  const [state, setState] = useState<{ data: T | null; error: unknown; loading: boolean }>({ data: null, error: null, loading: key !== null });
  const seq = useRef(0);
  const fn = useRef(loader);
  fn.current = loader;
  const [tick, setTick] = useState(0);

  useEffect(() => {
    if (key === null) { setState({ data: null, error: null, loading: false }); return; }
    const mine = ++seq.current;
    setState((s) => ({ ...s, loading: true, error: null }));
    fn.current().then(
      (data) => { if (mine === seq.current) setState({ data, error: null, loading: false }); },
      (error) => { if (mine === seq.current) setState({ data: null, error, loading: false }); },
    );
  }, [key, tick]);

  const reload = useCallback(() => setTick((t) => t + 1), []);
  return { ...state, reload };
}
