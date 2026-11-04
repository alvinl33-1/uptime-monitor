import { afterEach, describe, expect, it, vi } from "vitest";
import { fetchStatus, overallLabel } from "./api";

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status });
}
// Swap in a fake fetch with:   vi.stubGlobal("fetch", vi.fn().mockResolvedValue(...))
afterEach(() => vi.unstubAllGlobals())

const GOOD = {
  overall: "operational",
  generated_at: "2026-01-01T12:00:00+00:00",
  monitors: [],
  open_incidents: [],
};

describe("fetchStatus", () => {
  it("returns the parsed status document", async () => {
    // Fake fetch returns jsonResponse(GOOD). Assert the result equals GOOD.
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(GOOD));
    vi.stubGlobal("fetch", fetchMock);
    await expect(fetchStatus()).resolves.toEqual(GOOD);
  });

  it("requests /api/status", async () => {
    // Assert the fake fetch was called with "/api/status" as its first argument.
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(GOOD));
    vi.stubGlobal("fetch", fetchMock);
    await expect(fetchStatus()).toHaveBeenCalledWith("/api/status", expect.anything());
  });

  it("passes the abort signal through", async () => {
    // Pass new AbortController().signal. Assert the second argument given to
    // fetch has that same signal.
    const controller = new AbortController();
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(GOOD));
    vi.stubGlobal("fetch", fetchMock);
    await expect(fetchStatus(controller.signal)).toHaveBeenCalledWith(
      expect.anything(), expect.objectContaining({ signal: controller.signal })
    );
  });

  it("throws on an error status", async () => {
    // jsonResponse({}, 502) -> await expect(fetchStatus()).rejects.toThrow("502")
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({}, 502));
    vi.stubGlobal("fetch", fetchMock);
    await expect(fetchStatus()).rejects.toThrow("502");
  });

  it("throws on a response with the wrong shape", async () => {
    // jsonResponse({ hello: "world" }) -> rejects.toThrow("Unexpected response")
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ hello: "world" }));
    vi.stubGlobal("fetch", fetchMock);
    await expect(fetchStatus()).rejects.toThrow("Unexpected response");
  });

  it("throws if the body is not JSON", async () => {
    // new Response("<html>oops</html>") -> rejects.toThrow()
    const fetchMock = vi.fn().mockResolvedValue(new Response("<html>oops</html>"));
    vi.stubGlobal("fetch", fetchMock);
    await expect(fetchStatus()).rejects.toThrow();
  });
});

describe("overallLabel", () => {
  it("labels every known state", async () => {
    // Check all five states against the spec's headline text.
    expect(overallLabel("operational")).toBe("All systems operational");
    expect(overallLabel("degraded")).toBe("Degraded performance");
    expect(overallLabel("partial_outage")).toBe("Partial outage");
    expect(overallLabel("major_outage")).toBe("Major outage");
    expect(overallLabel("no_data")).toBe("No data yet");
  });

  it("does not crash on an unknown state", async () => {
    // overallLabel("brand_new_state") === "Unknown"
    expect(overallLabel("brand_new_state")).toBe("Unknown");
  });
});
