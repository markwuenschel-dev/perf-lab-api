// @vitest-environment jsdom
//
// src/perflab/screens/appleHealthCard.test.tsx
//
// Settings → Apple Watch. The write-only token is shown exactly once, at creation; the card
// shows each token's last successful push and calls out a stale one, because a phone
// automation fails silently; revoking removes it.
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { AppleHealthCard } from "./AppleHealthCard";

const listIngestTokens = vi.fn();
const createIngestToken = vi.fn();
const revokeIngestToken = vi.fn();

vi.mock("@/api/perfLabClient", () => ({
  listIngestTokens: (...a: unknown[]) => listIngestTokens(...a),
  createIngestToken: (...a: unknown[]) => createIngestToken(...a),
  revokeIngestToken: (...a: unknown[]) => revokeIngestToken(...a),
  wellnessIngestUrl: () => "https://perflab.example/v1/wellness/ingest",
}));

vi.mock("@/auth/useAuth", () => ({
  useAuth: () => ({ token: "athlete-token", isAuthenticated: true }),
}));

const iso = (hoursAgo: number) => new Date(Date.now() - hoursAgo * 3600e3).toISOString().replace("Z", "");

beforeEach(() => {
  listIngestTokens.mockResolvedValue([]);
  revokeIngestToken.mockResolvedValue(undefined);
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

it("shows a new token once, then only its prefix", async () => {
  const created = {
    id: 7, label: "Apple Watch", token_prefix: "plw_abcd1234", created_at: iso(0),
    last_used_at: null, token: "plw_abcd1234-the-whole-secret",
  };
  createIngestToken.mockResolvedValue(created);
  render(<AppleHealthCard />);

  listIngestTokens.mockResolvedValue([{ ...created, token: undefined }]);
  fireEvent.click(await screen.findByText("Set up Apple Watch sync"));

  const shown = await screen.findByTestId("new-ingest-token");
  expect(shown.textContent).toContain("plw_abcd1234-the-whole-secret");
  expect(shown.textContent).toContain("won't be shown again");
  await waitFor(() => expect(screen.getByTestId("ingest-token").textContent).toContain("not synced yet"));

  // Remount (a later visit): the secret is gone, only the prefix remains.
  cleanup();
  render(<AppleHealthCard />);
  await waitFor(() => expect(screen.getByTestId("ingest-token").textContent).toContain("plw_abcd1234…"));
  expect(document.body.textContent).not.toContain("the-whole-secret");
});

it("shows the last successful sync, and calls out a stale one", async () => {
  listIngestTokens.mockResolvedValue([
    { id: 1, label: "Fresh", token_prefix: "plw_1", created_at: iso(100), last_used_at: iso(3) },
    { id: 2, label: "Stale", token_prefix: "plw_2", created_at: iso(100), last_used_at: iso(50) },
  ]);
  render(<AppleHealthCard />);
  const [fresh, stale] = await screen.findAllByTestId("ingest-token");
  expect(fresh.textContent).toContain("last sync");
  expect(fresh.textContent).not.toContain("run the Shortcut");
  expect(stale.textContent).toContain("no Apple data in over a day");
});

it("revokes a token", async () => {
  listIngestTokens.mockResolvedValueOnce([
    { id: 3, label: "Apple Watch", token_prefix: "plw_3", created_at: iso(10), last_used_at: iso(2) },
  ]);
  render(<AppleHealthCard />);
  fireEvent.click(await screen.findByText("Revoke"));
  await waitFor(() => expect(revokeIngestToken).toHaveBeenCalledWith(3, "athlete-token"));
  await waitFor(() => expect(screen.queryByTestId("ingest-token")).toBeNull());
});
