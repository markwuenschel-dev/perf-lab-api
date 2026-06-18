// src/perflab/PerfLabProvider.tsx
// React provider for Perf Lab state: wires the reducer, localStorage
// persistence, the live-session timer, responsive nav collapse and the runtime
// accent (`--ac`). Kept in its own file so it is the only export (Fast Refresh).
import { useEffect, useMemo, useReducer, useRef } from "react";
import type { ReactNode } from "react";
import { buildActions, initialState, PerfLabContext, reducer, STORAGE_KEY } from "./store";

export function PerfLabProvider({ children }: { children: ReactNode }) {
  const [state, dispatch] = useReducer(reducer, undefined, initialState);
  const wasNarrow = useRef(false);

  // Persist {ftDone, fresh, fieldTest, twinState}
  useEffect(() => {
    try {
      localStorage.setItem(
        STORAGE_KEY,
        JSON.stringify({
          ftDone: state.ftDone,
          fresh: state.fresh,
          fieldTest: state.fieldTest,
          twinState: state.twinState,
        }),
      );
    } catch {
      /* ignore */
    }
  }, [state.ftDone, state.fresh, state.fieldTest, state.twinState]);

  // Runtime accent
  useEffect(() => {
    document.documentElement.style.setProperty("--ac", state.settings.accent);
  }, [state.settings.accent]);

  // Responsive auto-collapse of the sidebar below 900px
  useEffect(() => {
    wasNarrow.current = window.innerWidth < 900;
    if (wasNarrow.current) dispatch({ type: "merge", patch: { navCollapsed: true } });
    const onResize = () => {
      const narrow = window.innerWidth < 900;
      if (narrow !== wasNarrow.current) {
        wasNarrow.current = narrow;
        dispatch({ type: "merge", patch: { navCollapsed: narrow } });
      }
    };
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  // Live-session timer (driven by sessRunning)
  useEffect(() => {
    if (!state.sessRunning) return;
    const id = window.setInterval(() => dispatch({ type: "tick" }), 1000);
    return () => window.clearInterval(id);
  }, [state.sessRunning]);

  const actions = useMemo(() => buildActions(dispatch), []);

  return <PerfLabContext.Provider value={{ state, actions }}>{children}</PerfLabContext.Provider>;
}
