// src/perflab/screens/AppleHealthCard.tsx
//
// Apple Watch → Perf Lab. Apple keeps Watch data on the iPhone (HealthKit, no cloud API), so
// a morning iOS Shortcut pushes last night's HRV, resting HR and sleep to
// POST /v1/wellness/ingest with a personal, write-only token created here. The token is shown
// once. "Last sync" is the token's last successful push: a phone automation can fail
// silently, so a stale one is called out instead of left to rot. A missing Apple push never
// blocks the check-in; it just asks by hand.
import { useCallback, useEffect, useState } from "react";
import { useAuth } from "@/auth/useAuth";
import * as api from "@/api/perfLabClient";
import type { ApiError, IngestTokenCreated, IngestTokenOut } from "@/types";
import { Card, SectionLabel } from "../ui";
import { isPushStale, parseServerUtc } from "../wearableSync";

const SHORTCUT_BODY = `{
  "source": "apple_watch",
  "date": "<Current Date, yyyy-MM-dd>",
  "hrv_ms": <Heart Rate Variability>,
  "resting_hr": <Resting Heart Rate>,
  "sleep_hours": <hours asleep>
}`;

export function AppleHealthCard() {
  const auth = useAuth();
  const [tokens, setTokens] = useState<IngestTokenOut[] | null>(null);
  const [created, setCreated] = useState<IngestTokenCreated | null>(null);
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!auth.token) return;
    try {
      setTokens(await api.listIngestTokens(auth.token));
    } catch (e) {
      setError((e as ApiError).message ?? "Failed to load Apple Watch sync");
    }
  }, [auth.token]);

  useEffect(() => {
    void load();
  }, [load]);

  async function onCreate() {
    if (!auth.token) return;
    setBusy(true);
    setError(null);
    try {
      setCreated(await api.createIngestToken("Apple Watch", auth.token));
      await load();
    } catch (e) {
      setError((e as ApiError).message ?? "Could not create a token");
    } finally {
      setBusy(false);
    }
  }

  async function onRevoke(id: number) {
    if (!auth.token) return;
    setBusy(true);
    setError(null);
    try {
      await api.revokeIngestToken(id, auth.token);
      if (created?.id === id) setCreated(null);
      await load();
    } catch (e) {
      setError((e as ApiError).message ?? "Could not revoke the token");
    } finally {
      setBusy(false);
    }
  }

  async function copy(label: string, text: string) {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(label);
    } catch {
      setCopied(null);
    }
  }

  const now = new Date();
  const url = api.wellnessIngestUrl();

  return (
    <Card className="p-[22px]">
      <SectionLabel className="mb-1">Wearable — Apple Watch</SectionLabel>
      <div className="mb-4 text-[12px] font-medium leading-[1.5] text-mute">
        Apple keeps Watch data on your iPhone, so a morning Shortcut sends last night's HRV, resting HR and
        sleep here. When Oura also reported that day, Oura's numbers are used.
      </div>

      {!auth.isAuthenticated ? (
        <div className="text-[12px] font-medium text-mute">Sign in to set up Apple Watch sync.</div>
      ) : (
        <div className="flex flex-col gap-4">
          {(tokens ?? []).map((t) => {
            const stale = isPushStale(t.last_used_at, now);
            return (
              <div key={t.id} data-testid="ingest-token" className="flex flex-wrap items-center justify-between gap-2">
                <span className="text-[11.5px] font-medium text-mute">
                  <span className="font-semibold text-ink">{t.label}</span>
                  <span className="font-mono"> · {t.token_prefix}…</span>
                  {t.last_used_at ? (
                    <span className={stale ? "text-warn" : undefined}>
                      {` · last sync ${parseServerUtc(t.last_used_at).toLocaleString()}`}
                      {stale && " — no Apple data in over a day; run the Shortcut once"}
                    </span>
                  ) : (
                    " · not synced yet"
                  )}
                </span>
                <button
                  type="button"
                  onClick={() => void onRevoke(t.id)}
                  disabled={busy}
                  className="rounded-[9px] border border-hot/25 bg-hot/[0.08] px-3 py-[8px] text-[12px] font-semibold leading-none text-hot disabled:opacity-60"
                >
                  Revoke
                </button>
              </div>
            );
          })}

          {created ? (
            <div data-testid="new-ingest-token" className="flex flex-col gap-3 rounded-[11px] border border-ac/25 bg-ac/[0.05] p-3">
              <div className="text-[11.5px] font-semibold text-ac">Copy this token now — it won't be shown again.</div>
              <code className="break-all font-mono text-[11.5px] text-ink">{created.token}</code>
              <div className="flex gap-2">
                <button type="button" onClick={() => void copy("token", created.token)}
                  className="rounded-[9px] border border-white/10 bg-white/[0.03] px-3 py-[8px] text-[12px] font-semibold leading-none text-ink">
                  {copied === "token" ? "Copied" : "Copy token"}
                </button>
                <button type="button" onClick={() => void copy("url", url)}
                  className="rounded-[9px] border border-white/10 bg-white/[0.03] px-3 py-[8px] text-[12px] font-semibold leading-none text-ink">
                  {copied === "url" ? "Copied" : "Copy URL"}
                </button>
              </div>
            </div>
          ) : (
            <div>
              <button
                type="button"
                onClick={() => void onCreate()}
                disabled={busy}
                className="rounded-[11px] bg-gradient-to-r from-ac to-[#a7e36e] px-5 py-[12px] text-[13px] font-semibold leading-none text-[#0a0c10] disabled:opacity-60"
              >
                {busy ? "Creating…" : (tokens ?? []).length ? "Create another token" : "Set up Apple Watch sync"}
              </button>
            </div>
          )}

          <details className="text-[11.5px] font-medium leading-[1.55] text-mute">
            <summary className="cursor-pointer text-soft">How to build the Shortcut</summary>
            <ol className="mt-2 list-decimal space-y-1 pl-5">
              <li>Shortcuts → Automation → New → Time of Day, 7:00, Daily, Run Immediately.</li>
              <li>Find Health Samples: Heart Rate Variability, last 1 day, latest first, limit 1. Same for Resting Heart Rate.</li>
              <li>Find Health Samples: Sleep Analysis, last 1 day; add up the Duration of the asleep samples and divide by 3600.</li>
              <li>Get Contents of URL: <span className="font-mono">{url}</span>, Method POST, header <span className="font-mono">Authorization: Bearer &lt;token&gt;</span>, Request Body JSON:</li>
            </ol>
            <pre className="mt-2 overflow-x-auto rounded-[8px] bg-panel p-2 font-mono text-[10.5px] text-ink">{SHORTCUT_BODY}</pre>
            <p className="mt-2">Sending the same day again replaces it, so a retry never double-counts.</p>
          </details>
        </div>
      )}

      {error && (
        <div className="mt-4 flex items-start gap-[9px] rounded-[11px] border border-hot/[0.3] bg-hot/[0.05] px-3 py-[10px]">
          <span className="text-[13px] leading-none text-hot">!</span>
          <span className="text-[11.5px] font-medium leading-[1.45] text-[#cf9a93]">{error}</span>
        </div>
      )}
    </Card>
  );
}
