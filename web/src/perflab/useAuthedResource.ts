// src/perflab/useAuthedResource.ts
//
// Small data-fetching hook for screens that pull live data from the token-gated
// backend. It fetches once when a real session exists and re-fetches when `deps`
// change; for a guest (no token) it stays idle so the screen falls back to its
// local/prototype content. A 401 inside the fetcher already clears the session
// via perfLabClient's sessionOn401 path, so callers only deal with data/error.
import { useEffect, useRef, useState } from "react";
import { useAuth } from "@/auth/useAuth";
import type { ApiError } from "@/types";

export interface Resource<T> {
  data: T | null;
  loading: boolean;
  error: string | null;
}

export function useAuthedResource<T>(
  fetcher: (token: string) => Promise<T>,
  deps: unknown[] = [],
): Resource<T> {
  const { token } = useAuth();
  const [state, setState] = useState<Resource<T>>({ data: null, loading: false, error: null });

  // Keep the latest fetcher without making it a dep — `deps` drives re-fetch.
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;

  useEffect(() => {
    if (!token) {
      setState({ data: null, loading: false, error: null });
      return;
    }
    let cancelled = false;
    setState((s) => ({ ...s, loading: true, error: null }));
    fetcherRef.current(token).then(
      (data) => {
        if (!cancelled) setState({ data, loading: false, error: null });
      },
      (e: unknown) => {
        if (cancelled) return;
        const msg = (e as ApiError)?.message;
        setState({ data: null, loading: false, error: typeof msg === "string" ? msg : "Failed to load" });
      },
    );
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token, ...deps]);

  return state;
}
