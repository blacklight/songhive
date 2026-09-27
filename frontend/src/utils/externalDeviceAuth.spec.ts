import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import * as api from "@/api/externalLibraries";
import {
  beginDeviceAuth,
  pollDeviceAuth,
  completePkceDeviceAuth,
  type DeviceAuthPrompt,
  type DeviceAuthUpdate,
} from "./externalDeviceAuth";

vi.mock("@/api/externalLibraries", () => ({
  beginExternalDeviceAuth: vi.fn(),
  pollExternalDeviceAuth: vi.fn(),
  completeExternalDeviceAuth: vi.fn(),
}));

function makePrompt(
  overrides: Partial<DeviceAuthPrompt> = {},
): DeviceAuthPrompt {
  return {
    state: "state-1",
    mode: "device",
    userCode: "ABCD-EFGH",
    verificationUri: "https://link.tidal.com",
    verificationUriComplete: "https://link.tidal.com/ABCD-EFGH",
    authorizeUrl: null,
    expiresIn: 300,
    interval: 5,
    ...overrides,
  };
}

async function flushMicrotasks() {
  for (let i = 0; i < 5; i += 1) {
    await Promise.resolve();
  }
}

describe("beginDeviceAuth", () => {
  beforeEach(() => vi.clearAllMocks());

  it("normalizes the begin response into a prompt", async () => {
    vi.mocked(api.beginExternalDeviceAuth).mockResolvedValue({
      state: "state-1",
      mode: "device",
      user_code: "ABCD-EFGH",
      verification_uri: "https://link.tidal.com",
      verification_uri_complete: "https://link.tidal.com/ABCD-EFGH",
      expires_in: 120,
      interval: 2,
    } as never);

    const prompt = await beginDeviceAuth("tidal", { quality: "LOSSLESS" });

    expect(api.beginExternalDeviceAuth).toHaveBeenCalledWith({
      provider_type: "tidal",
      config: { quality: "LOSSLESS" },
      external_library_id: undefined,
      mode: undefined,
    });
    expect(prompt).toMatchObject({
      state: "state-1",
      userCode: "ABCD-EFGH",
      verificationUri: "https://link.tidal.com",
      expiresIn: 120,
      interval: 2,
    });
  });

  it("passes pkce mode and library id through", async () => {
    vi.mocked(api.beginExternalDeviceAuth).mockResolvedValue({
      state: "state-2",
      mode: "pkce",
      authorize_url: "https://login.tidal.com/authorize",
    } as never);

    const prompt = await beginDeviceAuth(
      "tidal",
      {},
      { externalLibraryId: "lib-1", mode: "pkce" },
    );

    expect(api.beginExternalDeviceAuth).toHaveBeenCalledWith(
      expect.objectContaining({
        external_library_id: "lib-1",
        mode: "pkce",
      }),
    );
    expect(prompt.mode).toBe("pkce");
    expect(prompt.authorizeUrl).toBe("https://login.tidal.com/authorize");
  });
});

describe("pollDeviceAuth", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("completes with the granted config", async () => {
    vi.mocked(api.pollExternalDeviceAuth).mockResolvedValue({
      status: "granted",
    } as never);
    vi.mocked(api.completeExternalDeviceAuth).mockResolvedValue({
      config: { access_token: "tok" },
    } as never);

    const updates: DeviceAuthUpdate[] = [];
    pollDeviceAuth(makePrompt(), (u) => updates.push(u));
    await flushMicrotasks();

    expect(api.completeExternalDeviceAuth).toHaveBeenCalledWith({
      state: "state-1",
    });
    expect(updates.at(-1)).toEqual({
      status: "granted",
      granted: { config: { access_token: "tok" } },
    });
  });

  it("keeps polling while pending and reports nextPollIn", async () => {
    vi.mocked(api.pollExternalDeviceAuth).mockResolvedValue({
      status: "pending",
    } as never);

    const updates: DeviceAuthUpdate[] = [];
    const flow = pollDeviceAuth(makePrompt({ interval: 7 }), (u) =>
      updates.push(u),
    );
    await flushMicrotasks();

    expect(updates.at(-1)).toEqual({ status: "pending", nextPollIn: 7 });
    await vi.advanceTimersByTimeAsync(7000);
    expect(api.pollExternalDeviceAuth).toHaveBeenCalledTimes(2);
    flow.cancel();
  });

  it("honours slow_down retry_after and keeps polling", async () => {
    vi.mocked(api.pollExternalDeviceAuth)
      .mockResolvedValueOnce({ status: "slow_down", retry_after: 20 } as never)
      .mockResolvedValueOnce({ status: "denied" } as never);

    const updates: DeviceAuthUpdate[] = [];
    pollDeviceAuth(makePrompt({ interval: 5 }), (u) => updates.push(u));
    await flushMicrotasks();

    expect(updates.at(-1)).toEqual({ status: "slow_down", nextPollIn: 20 });
    // Polling at the old interval must not fire.
    await vi.advanceTimersByTimeAsync(5000);
    expect(api.pollExternalDeviceAuth).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(15000);
    await flushMicrotasks();
    expect(api.pollExternalDeviceAuth).toHaveBeenCalledTimes(2);
    expect(updates.at(-1)?.status).toBe("denied");
  });

  it("emits expired when the deadline passes", async () => {
    vi.mocked(api.pollExternalDeviceAuth).mockResolvedValue({
      status: "pending",
    } as never);

    const updates: DeviceAuthUpdate[] = [];
    pollDeviceAuth(makePrompt({ expiresIn: 5, interval: 2 }), (u) =>
      updates.push(u),
    );
    await flushMicrotasks();
    await vi.advanceTimersByTimeAsync(6000);

    expect(updates.at(-1)?.status).toBe("expired");
    // No poll is scheduled after expiry.
    const calls = vi.mocked(api.pollExternalDeviceAuth).mock.calls.length;
    await vi.advanceTimersByTimeAsync(30000);
    expect(api.pollExternalDeviceAuth).toHaveBeenCalledTimes(calls);
  });

  it("emits error when polling fails", async () => {
    vi.mocked(api.pollExternalDeviceAuth).mockRejectedValue(
      new Error("network"),
    );

    const updates: DeviceAuthUpdate[] = [];
    pollDeviceAuth(makePrompt(), (u) => updates.push(u));
    await flushMicrotasks();

    expect(updates.at(-1)).toEqual({ status: "error", detail: "poll_failed" });
  });

  it("cancel stops further polling", async () => {
    vi.mocked(api.pollExternalDeviceAuth).mockResolvedValue({
      status: "pending",
    } as never);

    const flow = pollDeviceAuth(makePrompt({ interval: 5 }), () => {});
    await flushMicrotasks();
    flow.cancel();
    await vi.advanceTimersByTimeAsync(30000);

    expect(api.pollExternalDeviceAuth).toHaveBeenCalledTimes(1);
  });
});

describe("completePkceDeviceAuth", () => {
  it("forwards the pasted redirect URL", async () => {
    vi.mocked(api.completeExternalDeviceAuth).mockResolvedValue({
      config: {},
    } as never);

    await completePkceDeviceAuth(
      "state-9",
      "https://songhive.example/cb?code=1",
    );

    expect(api.completeExternalDeviceAuth).toHaveBeenCalledWith({
      state: "state-9",
      redirect_url: "https://songhive.example/cb?code=1",
    });
  });
});
