// @vitest-environment jsdom
//
// React lifecycle wiring for useAuthedResource. The pure transition table is
// covered in resource.test.ts; what can only be proven here is that the hook
// dispatches those transitions at the right lifecycle boundaries — the exact
// place the old hook was wrong (loading:false before its effect ran).
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useAuthedResource } from "./useAuthedResource";
import type { AuthedResource } from "./resource";

let token: string | null = null;
let calendarKey: string | undefined = undefined;
vi.mock("@/auth/useAuth", () => ({
  useAuth: () => ({ token, calendarKey }),
}));

function deferred<T>() {
  let resolve!: (v: T) => void;
  let reject!: (e: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

/** Records the resource on every render pass, so first-commit state is observable. */
function renderResource<T>(fetcher: (t: string) => Promise<T>, deps: () => unknown[] = () => []) {
  const seen: AuthedResource<T>[] = [];
  const view = renderHook(() => {
    const resource = useAuthedResource(fetcher, deps());
    seen.push(resource);
    return resource;
  });
  return { ...view, seen };
}

beforeEach(() => {
  token = null;
  calendarKey = undefined;
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("guest", () => {
  it("is guest on the very first render when there is no token", () => {
    const fetcher = vi.fn();
    const { seen, result } = renderResource(fetcher);
    expect(seen[0]).toEqual({ status: "guest" });
    expect(result.current).toEqual({ status: "guest" });
  });

  it("never issues a request for a guest", () => {
    const fetcher = vi.fn();
    renderResource(fetcher);
    expect(fetcher).not.toHaveBeenCalled();
  });
});

describe("initial load", () => {
  it("is loading on the FIRST render when a token is present — never a false empty frame", () => {
    token = "t1";
    const { seen } = renderResource(() => deferred<string[]>().promise);
    expect(seen[0]).toEqual({ status: "loading" });
  });

  it("resolves to success", async () => {
    token = "t1";
    const { result } = renderResource(async () => ["a"]);
    await waitFor(() => expect(result.current.status).toBe("success"));
    expect(result.current).toEqual({ status: "success", data: ["a"], refresh: { status: "idle" } });
  });

  it("passes the token to the fetcher", async () => {
    token = "t1";
    const fetcher = vi.fn(async () => ["a"]);
    renderResource(fetcher);
    await waitFor(() => expect(fetcher).toHaveBeenCalledWith("t1"));
  });

  it("becomes a top-level error when there is no payload to keep", async () => {
    token = "t1";
    const { result } = renderResource(async () => {
      throw { message: "Server exploded" };
    });
    await waitFor(() => expect(result.current.status).toBe("error"));
    expect(result.current).toEqual({ status: "error", error: { message: "Server exploded" } });
  });
});

describe("refresh over an existing payload", () => {
  it("keeps the previous data on screen while a same-identity refetch runs", async () => {
    token = "t1";
    let key = 0;
    const second = deferred<string[]>();
    const fetcher = vi.fn().mockResolvedValueOnce(["first"]).mockReturnValueOnce(second.promise);

    const { result, rerender } = renderResource<string[]>(fetcher, () => [key]);
    await waitFor(() => expect(result.current.status).toBe("success"));

    key = 1;
    rerender();
    expect(result.current).toEqual({ status: "success", data: ["first"], refresh: { status: "loading" } });

    await act(async () => {
      second.resolve(["second"]);
    });
    expect(result.current).toEqual({ status: "success", data: ["second"], refresh: { status: "idle" } });
  });

  it("keeps the previous data when the refresh FAILS, and records the failure", async () => {
    token = "t1";
    let key = 0;
    const fetcher = vi
      .fn()
      .mockResolvedValueOnce(["first"])
      .mockRejectedValueOnce({ message: "Refresh failed" });

    const { result, rerender } = renderResource<string[]>(fetcher, () => [key]);
    await waitFor(() => expect(result.current.status).toBe("success"));

    key = 1;
    rerender();
    await waitFor(() =>
      expect(result.current).toEqual({
        status: "success",
        data: ["first"],
        refresh: { status: "error", error: { message: "Refresh failed" } },
      }),
    );
  });
});

describe("request authority", () => {
  it("an obsolete response cannot overwrite a newer one", async () => {
    token = "t1";
    let key = 0;
    const slowFirst = deferred<string[]>();
    const fastSecond = deferred<string[]>();
    const fetcher = vi
      .fn()
      .mockReturnValueOnce(slowFirst.promise)
      .mockReturnValueOnce(fastSecond.promise);

    const { result, rerender } = renderResource<string[]>(fetcher, () => [key]);
    key = 1;
    rerender();

    await act(async () => {
      fastSecond.resolve(["newer"]);
    });
    expect(result.current).toEqual({ status: "success", data: ["newer"], refresh: { status: "idle" } });

    // The first request finally lands — it must be ignored, not applied.
    await act(async () => {
      slowFirst.resolve(["older"]);
    });
    expect(result.current).toEqual({ status: "success", data: ["newer"], refresh: { status: "idle" } });
  });

  it("a replaced token discards the previous athlete's payload immediately", async () => {
    token = "t1";
    const fetcher = vi.fn().mockResolvedValueOnce(["athlete-one"]).mockReturnValueOnce(deferred<string[]>().promise);
    const { result, rerender } = renderResource<string[]>(fetcher);
    await waitFor(() => expect(result.current.status).toBe("success"));

    token = "t2";
    rerender();
    expect(result.current).toEqual({ status: "loading" });
    await waitFor(() => expect(fetcher).toHaveBeenLastCalledWith("t2"));
  });

  it("an in-flight response from a previous token cannot land on the new one", async () => {
    token = "t1";
    const firstAthlete = deferred<string[]>();
    const fetcher = vi
      .fn()
      .mockReturnValueOnce(firstAthlete.promise)
      .mockReturnValueOnce(deferred<string[]>().promise);

    const { result, rerender } = renderResource<string[]>(fetcher);
    token = "t2";
    rerender();

    await act(async () => {
      firstAthlete.resolve(["athlete-one"]);
    });
    expect(result.current).toEqual({ status: "loading" });
  });

  it("losing the token returns to guest", async () => {
    token = "t1";
    const { result, rerender } = renderResource(async () => ["a"]);
    await waitFor(() => expect(result.current.status).toBe("success"));

    token = null;
    rerender();
    expect(result.current).toEqual({ status: "guest" });
  });
});

describe("unmount", () => {
  it("does not update state after unmount", async () => {
    token = "t1";
    const pending = deferred<string[]>();
    const errorSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    const { unmount } = renderResource(() => pending.promise);

    unmount();
    await act(async () => {
      pending.resolve(["late"]);
    });

    expect(errorSpy).not.toHaveBeenCalled();
  });
});


describe("calendar identity (B1)", () => {
  function renderCalendarResource<T>(fetcher: (t: string) => Promise<T>) {
    const seen: AuthedResource<T>[] = [];
    const view = renderHook(() => {
      const resource = useAuthedResource(fetcher, [], { calendar: true });
      seen.push(resource);
      return resource;
    });
    return { ...view, seen };
  }

  it("does not fetch while the calendar is pending, then fetches once it settles", async () => {
    token = "t1";
    calendarKey = "pending";
    const fetcher = vi.fn(() => Promise.resolve("today"));
    const { result, rerender } = renderCalendarResource(fetcher);
    expect(result.current).toEqual({ status: "loading" });
    expect(fetcher).not.toHaveBeenCalled();
    calendarKey = "zone:America/New_York";
    rerender();
    await waitFor(() => expect(result.current).toEqual({ status: "success", data: "today", refresh: { status: "idle" } }));
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it("refetches on a confirmed calendar change and never shows the obsolete response", async () => {
    token = "t1";
    calendarKey = "unknown";
    const first = deferred<string>();
    const second = deferred<string>();
    const fetcher = vi.fn().mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);
    const { result, rerender, seen } = renderCalendarResource(fetcher);
    expect(fetcher).toHaveBeenCalledTimes(1); // fetched under the UTC calendar
    calendarKey = "zone:America/New_York"; // the capture is confirmed after that fetch began
    rerender();
    expect(fetcher).toHaveBeenCalledTimes(2);
    await act(async () => first.resolve("UTC day"));
    expect(result.current).toEqual({ status: "loading" });
    await act(async () => second.resolve("New York day"));
    expect(result.current).toEqual({ status: "success", data: "New York day", refresh: { status: "idle" } });
    expect(seen.some((r) => r.status === "success" && r.data === "UTC day")).toBe(false);
  });

  it("drops a payload fetched under the old calendar in the same commit as the change", async () => {
    token = "t1";
    calendarKey = "unknown";
    const fetcher = vi.fn().mockResolvedValueOnce("UTC day").mockReturnValueOnce(deferred<string>().promise);
    const { result, rerender } = renderCalendarResource(fetcher);
    await waitFor(() => expect(result.current.status).toBe("success"));
    calendarKey = "zone:Australia/Brisbane";
    rerender();
    expect(result.current).toEqual({ status: "loading" });
  });

  it("leaves a resource that is not day-dependent alone", async () => {
    token = "t1";
    calendarKey = "pending";
    const fetcher = vi.fn(() => Promise.resolve("history"));
    const { result } = renderResource(fetcher);
    await waitFor(() => expect(result.current.status).toBe("success"));
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
});
