import { describe, it, expect, vi, beforeEach } from "vitest";
import {
  apiRequest,
  setTokenProvider,
  setRefreshHandler,
  setLogoutHandler,
  ApiError,
} from "./client";

describe("apiRequest", () => {
  beforeEach(() => {
    setTokenProvider(() => "initial");
    setRefreshHandler(() => Promise.resolve(true));
    setLogoutHandler(() => {});
  });

  it("returns parsed JSON on 200", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        status: 200,
        ok: true,
        text: () => Promise.resolve('{"hello":"world"}'),
      }),
    );

    const result = await apiRequest<{ hello: string }>("/test");
    expect(result.hello).toBe("world");
  });

  it("refreshes once on 401 and retries", async () => {
    const refresh = vi.fn().mockResolvedValue(true);
    setRefreshHandler(refresh);

    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValueOnce({
          status: 401,
          ok: false,
          statusText: "Unauthorized",
          text: () => Promise.resolve(""),
        })
        .mockResolvedValueOnce({
          status: 200,
          ok: true,
          text: () => Promise.resolve('{"ok":true}'),
        }),
    );

    const result = await apiRequest<{ ok: boolean }>("/test");
    expect(refresh).toHaveBeenCalledTimes(1);
    expect(result.ok).toBe(true);
  });

  it("single-flights concurrent 401s", async () => {
    const refresh = vi.fn().mockResolvedValue(true);
    setRefreshHandler(refresh);

    let calls = 0;
    vi.stubGlobal("fetch", () => {
      calls += 1;
      if (calls === 1 || calls === 2) {
        return Promise.resolve({
          status: 401,
          ok: false,
          statusText: "Unauthorized",
          text: () => Promise.resolve(""),
        });
      }
      return Promise.resolve({
        status: 200,
        ok: true,
        text: () => Promise.resolve('{"ok":true}'),
      });
    });

    await Promise.all([apiRequest("/a"), apiRequest("/b")]);
    expect(refresh).toHaveBeenCalledTimes(1);
  });

  it("calls logout and rejects with ApiError when refresh fails", async () => {
    const logout = vi.fn();
    setRefreshHandler(() => Promise.resolve(false));
    setLogoutHandler(logout);

    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        status: 401,
        ok: false,
        statusText: "Unauthorized",
        text: () => Promise.resolve('{"detail":"nope"}'),
      }),
    );

    await expect(apiRequest("/test")).rejects.toBeInstanceOf(ApiError);
    expect(logout).toHaveBeenCalled();
  });

  it("parses RFC 7807 fields", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        status: 400,
        ok: false,
        statusText: "Bad Request",
        text: () =>
          Promise.resolve(
            '{"type":"/errors/validation","title":"Validation","status":400,"detail":"bad"}',
          ),
      }),
    );

    try {
      await apiRequest("/test");
    } catch (err) {
      expect(err).toBeInstanceOf(ApiError);
      const error = err as ApiError;
      expect(error.status).toBe(400);
      expect(error.title).toBe("Validation");
      expect(error.detail).toBe("bad");
    }
  });
});
