import { describe, it, expect, vi, beforeEach } from "vitest";
import * as authApi from "./auth";
import {
  setTokenProvider,
  setRefreshHandler,
  setLogoutHandler,
  ApiError,
} from "./client";

function mockFetchWithStatus(status: number, body: string) {
  return vi.fn().mockResolvedValue({
    status,
    ok: status >= 200 && status < 300,
    statusText: status === 401 ? "Unauthorized" : "OK",
    text: () => Promise.resolve(body),
  });
}

describe("auth endpoints", () => {
  beforeEach(() => {
    setTokenProvider(() => "access-token");
    setRefreshHandler(() => Promise.resolve(false));
    setLogoutHandler(() => {});
  });

  it("login does not send Authorization or trigger refresh on 401", async () => {
    const refresh = vi.fn().mockResolvedValue(true);
    const fetchMock = mockFetchWithStatus(
      401,
      '{"detail":"Invalid credentials"}',
    );
    setRefreshHandler(refresh);
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      authApi.login({ username: "u", password: "p" }),
    ).rejects.toBeInstanceOf(ApiError);
    expect(refresh).not.toHaveBeenCalled();

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    const headers = new Headers(init.headers);
    expect(headers.has("Authorization")).toBe(false);
  });

  it("logout does not send Authorization or trigger refresh on 401", async () => {
    const refresh = vi.fn().mockResolvedValue(true);
    const fetchMock = mockFetchWithStatus(
      401,
      '{"detail":"Invalid token"}',
    );
    setRefreshHandler(refresh);
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      authApi.logout({ refresh_token: "stale-refresh" }),
    ).rejects.toBeInstanceOf(ApiError);
    expect(refresh).not.toHaveBeenCalled();

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    const headers = new Headers(init.headers);
    expect(headers.has("Authorization")).toBe(false);
  });

  it("refresh does not re-enter the refresh loop on 401", async () => {
    const refresh = vi.fn().mockResolvedValue(true);
    const fetchMock = mockFetchWithStatus(
      401,
      '{"detail":"Invalid refresh token"}',
    );
    setRefreshHandler(refresh);
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      authApi.refresh({ refresh_token: "stale-refresh" }),
    ).rejects.toBeInstanceOf(ApiError);
    expect(refresh).not.toHaveBeenCalled();
  });

  it("refresh endpoint does not deadlock the refresh handler on 401", async () => {
    let fetchCalls = 0;
    vi.stubGlobal("fetch", () => {
      fetchCalls += 1;
      return Promise.resolve({
        status: 401,
        ok: false,
        statusText: "Unauthorized",
        text: () => Promise.resolve('{"detail":"Invalid refresh token"}'),
      });
    });

    setRefreshHandler(async () => {
      try {
        await authApi.refresh({ refresh_token: "stale-refresh" });
        return true;
      } catch {
        return false;
      }
    });

    const logout = vi.fn();
    setLogoutHandler(logout);

    await expect(authApi.listApiTokens()).rejects.toBeInstanceOf(ApiError);
    expect(logout).toHaveBeenCalled();
    expect(fetchCalls).toBe(2);
  });
});
