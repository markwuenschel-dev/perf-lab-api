/**
 * perfLabClient calls this on 401 for Bearer requests so AuthProvider can clear state.
 */

type ClearFn = () => void;

let onUnauthorized: ClearFn | null = null;

export function setUnauthorizedHandler(fn: ClearFn | null): void {
  onUnauthorized = fn;
}

export function notifyUnauthorized(): void {
  onUnauthorized?.();
}
