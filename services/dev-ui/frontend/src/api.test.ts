import { afterEach, describe, expect, it, vi } from "vitest";
import { adminFetch, apiFetch, authFetch, jsonOptions } from "./api";

describe("apiFetch", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("sends same-origin no-store requests with cache busting headers", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ status: "ok" }), {
        status: 200,
        headers: { "Content-Type": "application/json" }
      })
    );

    await expect(apiFetch<{ status: string }>("/v1/health")).resolves.toEqual({ status: "ok" });

    expect(fetchMock).toHaveBeenCalledWith(
      "/v1/health",
      expect.objectContaining({
        cache: "no-store",
        credentials: "same-origin",
        headers: expect.any(Headers)
      })
    );
    const headers = fetchMock.mock.calls[0]?.[1]?.headers as Headers;
    expect(headers.get("Cache-Control")).toBe("no-cache");
    expect(headers.get("Pragma")).toBe("no-cache");
  });

  it("preserves caller-provided request headers", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(null, { status: 204 }));

    await apiFetch<void>("/v1/items", { headers: { "Cache-Control": "max-age=60", "X-Trace": "trace-1" } });

    const headers = fetchMock.mock.calls[0]?.[1]?.headers as Headers;
    expect(headers.get("Cache-Control")).toBe("max-age=60");
    expect(headers.get("Pragma")).toBe("no-cache");
    expect(headers.get("X-Trace")).toBe("trace-1");
  });

  it("formats FastAPI validation errors into readable messages", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          detail: [
            { loc: ["body", "username"], msg: "Field required" },
            { loc: ["body", "password"], msg: "Too short" }
          ]
        }),
        { status: 422, headers: { "Content-Type": "application/json" } }
      )
    );

    await expect(apiFetch("/v1/auth/login")).rejects.toThrow(
      "body.username: Field required; body.password: Too short"
    );
  });

  it("falls back to response text for non-json errors", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("service unavailable", { status: 503 }));

    await expect(apiFetch("/v1/providers")).rejects.toThrow("service unavailable");
  });

  it("prefixes auth and admin helper paths", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(null, { status: 204 }));

    await authFetch("me");
    await adminFetch("providers");

    expect(fetchMock.mock.calls[0]?.[0]).toBe("/v1/auth/me");
    expect(fetchMock.mock.calls[1]?.[0]).toBe("/v1/management/providers");
  });

  it("builds JSON request options", () => {
    expect(jsonOptions({ enabled: true })).toEqual({
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled: true })
    });
  });
});
