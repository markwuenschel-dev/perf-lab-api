/**
 * perfLabClient calls this on 401 for Bearer requests so AuthProvider can clear state.
 *
 * The 401 carries the token the request was sent with: a late 401 answering a request made with
 * an earlier session's token is about that session, and the handler must not let it sign out the
 * current one.
 */

type ClearFn = (requestToken: string) => void;

let onUnauthorized: ClearFn | null = null;

export function setUnauthorizedHandler(fn: ClearFn | null): void {
  onUnauthorized = fn;
}

export function notifyUnauthorized(requestToken: string): void {
  onUnauthorized?.(requestToken);
}
