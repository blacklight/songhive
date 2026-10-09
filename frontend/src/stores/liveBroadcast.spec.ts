import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { setActivePinia, createPinia } from "pinia";
import { useLiveBroadcastStore } from "./liveBroadcast";
import type { StreamResponse } from "@/api/streams";

class FakeTrack {
  kind = "audio";
  readyState: "live" | "ended" = "live";
  stop = vi.fn(() => {
    this.readyState = "ended";
  });
  private listeners = new Map<string, Set<() => void>>();

  addEventListener(type: string, fn: () => void) {
    if (!this.listeners.has(type)) this.listeners.set(type, new Set());
    this.listeners.get(type)!.add(fn);
  }

  removeEventListener(type: string, fn: () => void) {
    this.listeners.get(type)?.delete(fn);
  }

  emit(type: string) {
    this.listeners.get(type)?.forEach((fn) => fn());
  }
}

class FakeMediaStream {
  tracks: FakeTrack[];
  constructor(tracks = [new FakeTrack()]) {
    this.tracks = tracks;
  }
  getTracks() {
    return this.tracks;
  }
  getAudioTracks() {
    return this.tracks;
  }
}

class FakeMediaRecorder {
  static CONNECTING = 0;
  static instances: FakeMediaRecorder[] = [];
  static isTypeSupported = vi.fn((mime: string) => mime.includes("webm"));

  state: "inactive" | "recording" = "inactive";
  mimeType: string;
  options: unknown;
  private listeners = new Map<string, Set<(e: unknown) => void>>();

  constructor(_stream: unknown, options?: { mimeType?: string }) {
    this.mimeType = options?.mimeType ?? "";
    this.options = options;
    FakeMediaRecorder.instances.push(this);
  }

  addEventListener(type: string, fn: (e: unknown) => void) {
    if (!this.listeners.has(type)) this.listeners.set(type, new Set());
    this.listeners.get(type)!.add(fn);
  }

  start() {
    this.state = "recording";
  }

  stop() {
    this.state = "inactive";
  }

  emitData(data: Blob) {
    this.listeners.get("dataavailable")?.forEach((fn) => fn({ data }));
  }
}

class FakeWebSocket {
  static CONNECTING = 0;
  static OPEN = 1;
  static CLOSING = 2;
  static CLOSED = 3;
  static instances: FakeWebSocket[] = [];

  url: string;
  readyState = FakeWebSocket.CONNECTING;
  bufferedAmount = 0;
  binaryType = "blob";
  sent: unknown[] = [];
  closeCalls: Array<{ code?: number; reason?: string }> = [];
  private listeners = new Map<string, Set<(e: unknown) => void>>();

  constructor(url: string) {
    this.url = url;
    FakeWebSocket.instances.push(this);
  }

  addEventListener(type: string, fn: (e: unknown) => void) {
    if (!this.listeners.has(type)) this.listeners.set(type, new Set());
    this.listeners.get(type)!.add(fn);
  }

  send(data: unknown) {
    this.sent.push(data);
  }

  close(code?: number, reason?: string) {
    this.closeCalls.push({ code, reason });
    this.serverClose(code ?? 1000);
  }

  private emit(type: string, event: unknown = {}) {
    this.listeners.get(type)?.forEach((fn) => fn(event));
  }

  serverOpen() {
    this.readyState = FakeWebSocket.OPEN;
    this.emit("open");
  }

  serverMessage(payload: unknown) {
    this.emit("message", {
      data: typeof payload === "string" ? payload : JSON.stringify(payload),
    });
  }

  serverClose(code = 1000, reason = "") {
    this.readyState = FakeWebSocket.CLOSED;
    this.emit("close", { code, reason });
  }
}

function createStream(overrides: Partial<StreamResponse> = {}): StreamResponse {
  return {
    id: "out-1",
    name: "Test Radio",
    mount: "radio",
    stream_url: "/streams/radio",
    visibility: "public",
    is_owner: true,
    can_manage: true,
    can_broadcast: true,
    owner: { username: "alice" },
    enabled: true,
    online: true,
    listener_count: 0,
    created_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

const mediaDevices = {
  getUserMedia: vi.fn(),
  enumerateDevices: vi.fn(),
  addEventListener: vi.fn(),
};

const permissionStatus = {
  state: "granted" as PermissionState,
  addEventListener: vi.fn(),
};

async function settle() {
  await vi.waitFor(() => {});
  await Promise.resolve();
  await Promise.resolve();
}

describe("liveBroadcast store", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    localStorage.clear();
    FakeWebSocket.instances = [];
    FakeMediaRecorder.instances = [];
    vi.clearAllMocks();

    mediaDevices.getUserMedia.mockResolvedValue(new FakeMediaStream());
    mediaDevices.enumerateDevices.mockResolvedValue([
      { kind: "audioinput", deviceId: "mic-1", label: "Studio Mic" },
      { kind: "audioinput", deviceId: "mic-2", label: "USB Mic" },
      { kind: "videoinput", deviceId: "cam-1", label: "Webcam" },
    ]);
    permissionStatus.state = "granted";

    Object.defineProperty(window, "isSecureContext", {
      value: true,
      configurable: true,
    });
    Object.defineProperty(navigator, "mediaDevices", {
      value: mediaDevices,
      configurable: true,
    });
    Object.defineProperty(navigator, "permissions", {
      value: { query: vi.fn(() => Promise.resolve(permissionStatus)) },
      configurable: true,
    });
    vi.stubGlobal("WebSocket", FakeWebSocket);
    vi.stubGlobal("MediaRecorder", FakeMediaRecorder);
    vi.stubGlobal(
      "AudioContext",
      class {
        createMediaStreamSource() {
          return { connect: vi.fn() };
        }
        createAnalyser() {
          return {
            fftSize: 0,
            getByteTimeDomainData(arr: Uint8Array) {
              arr.fill(128);
            },
          };
        }
        close() {
          return Promise.resolve();
        }
      },
    );
    vi.stubGlobal(
      "requestAnimationFrame",
      vi.fn(() => 1),
    );
    vi.stubGlobal("cancelAnimationFrame", vi.fn());
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("reports unsupported when capture is unavailable", async () => {
    Object.defineProperty(navigator, "mediaDevices", {
      value: undefined,
      configurable: true,
    });
    const store = useLiveBroadcastStore();

    expect(await store.ensurePermission()).toBe("unsupported");
    expect(store.status).toBe("error");
    expect(store.error?.key).toBe("unsupported");
  });

  it("resolves granted permission and enumerates audio inputs", async () => {
    const store = useLiveBroadcastStore();

    expect(await store.ensurePermission()).toBe("granted");
    expect(store.status).toBe("ready");
    // getUserMedia is not called: the permission query already knew.
    expect(mediaDevices.getUserMedia).not.toHaveBeenCalled();
    expect(store.devices.map((d) => d.deviceId)).toEqual(["mic-1", "mic-2"]);
  });

  it("prompts once when permission state is prompt", async () => {
    permissionStatus.state = "prompt";
    const store = useLiveBroadcastStore();

    expect(await store.ensurePermission()).toBe("granted");
    expect(mediaDevices.getUserMedia).toHaveBeenCalledWith({ audio: true });
    expect(store.status).toBe("ready");
  });

  it("surfaces a denied permission as an error", async () => {
    permissionStatus.state = "prompt";
    mediaDevices.getUserMedia.mockRejectedValue(
      new DOMException("nope", "NotAllowedError"),
    );
    const store = useLiveBroadcastStore();

    expect(await store.ensurePermission()).toBe("denied");
    expect(store.status).toBe("error");
    expect(store.error?.key).toBe("permissionDenied");
  });

  it("opens a preview capture with the chosen device", async () => {
    const store = useLiveBroadcastStore();
    await store.ensurePermission();
    await store.selectDevice("mic-2");

    expect(store.deviceId).toBe("mic-2");
    expect(localStorage.getItem("songhive.live.deviceId")).toBe("mic-2");
    expect(mediaDevices.getUserMedia).toHaveBeenCalledWith({
      audio: expect.objectContaining({
        deviceId: { exact: "mic-2" },
        echoCancellation: false,
      }),
    });
  });

  it("re-captures the preview when voice mode toggles", async () => {
    const store = useLiveBroadcastStore();
    await store.ensurePermission();
    await store.openPreview();
    expect(mediaDevices.getUserMedia).toHaveBeenCalledTimes(1);

    await store.setVoiceMode(true);
    expect(mediaDevices.getUserMedia).toHaveBeenCalledTimes(2);
    expect(mediaDevices.getUserMedia).toHaveBeenLastCalledWith({
      audio: expect.objectContaining({ echoCancellation: true }),
    });
  });

  async function startBroadcast() {
    const store = useLiveBroadcastStore();
    await store.ensurePermission();
    await store.start(createStream(), { title: "Late night set" });
    await settle();
    const ws = FakeWebSocket.instances.at(-1)!;
    ws.serverOpen();
    return { store, ws };
  }

  it("sends the start handshake and records after accepted", async () => {
    const { store, ws } = await startBroadcast();

    expect(ws.url).toBe("/ws/live/out-1");
    const startMsg = JSON.parse(String(ws.sent[0]));
    expect(startMsg.action).toBe("start");
    expect(startMsg.ingest_id).toMatch(/^[A-Za-z0-9._-]{8,128}$/);
    expect(startMsg.mime).toBe("audio/webm;codecs=opus");
    expect(startMsg.title).toBe("Late night set");

    expect(FakeMediaRecorder.instances).toHaveLength(0);
    ws.serverMessage({ type: "accepted", ingest_id: startMsg.ingest_id });

    const recorder = FakeMediaRecorder.instances.at(-1)!;
    expect(recorder.state).toBe("recording");
    expect(recorder.mimeType).toBe("audio/webm;codecs=opus");

    recorder.emitData(new Blob(["abc"], { type: "audio/webm" }));
    expect(ws.sent.at(-1)).toBeInstanceOf(Blob);

    ws.serverMessage({ type: "on_air", ingest_id: startMsg.ingest_id });
    expect(store.status).toBe("on-air");
    expect(store.startedAt).not.toBeNull();

    ws.serverMessage({ type: "listeners", count: 4 });
    expect(store.listeners).toBe(4);
  });

  it("tries fallback mime types when webm is unsupported", async () => {
    FakeMediaRecorder.isTypeSupported.mockImplementation(
      (mime: string) => mime === "audio/mp4",
    );
    const { ws } = await startBroadcast();

    const startMsg = JSON.parse(String(ws.sent[0]));
    expect(startMsg.mime).toBe("audio/mp4");
  });

  it("maps terminal close codes to errors", async () => {
    const { store, ws } = await startBroadcast();
    ws.serverMessage({ type: "error", code: "conflict" });
    ws.serverClose(4409);
    await settle();

    expect(store.status).toBe("error");
    expect(store.error?.key).toBe("conflict");
  });

  it("maps worker unavailability to a dedicated error", async () => {
    const { store, ws } = await startBroadcast();
    ws.serverClose(4503);
    await settle();

    expect(store.status).toBe("error");
    expect(store.error?.key).toBe("workerUnavailable");
  });

  it("reconnects with a fresh recorder on an abnormal close", async () => {
    vi.useFakeTimers();
    const { store, ws } = await startBroadcast();
    ws.serverMessage({ type: "accepted", ingest_id: "i-1" });
    ws.serverMessage({ type: "on_air", ingest_id: "i-1" });
    const firstIngest = JSON.parse(String(ws.sent[0])).ingest_id;

    ws.serverClose(1006);
    await vi.advanceTimersByTimeAsync(1000);
    await settle();

    const ws2 = FakeWebSocket.instances.at(-1)!;
    expect(ws2).not.toBe(ws);
    ws2.serverOpen();
    const secondMsg = JSON.parse(String(ws2.sent[0]));
    expect(secondMsg.ingest_id).not.toBe(firstIngest);
    expect(store.status).toBe("connecting");

    ws2.serverMessage({ type: "accepted", ingest_id: secondMsg.ingest_id });
    expect(FakeMediaRecorder.instances).toHaveLength(2);
    ws2.serverMessage({ type: "on_air", ingest_id: secondMsg.ingest_id });
    expect(store.status).toBe("on-air");
  });

  it("gives up reconnecting after repeated failures", async () => {
    vi.useFakeTimers();
    const { store, ws } = await startBroadcast();
    ws.serverMessage({ type: "accepted", ingest_id: "i-1" });
    ws.serverMessage({ type: "on_air", ingest_id: "i-1" });

    // MAX_RECONNECTS retries, then the next drop ends the broadcast.
    for (let i = 0; i < 6; i += 1) {
      const current = FakeWebSocket.instances.at(-1)!;
      current.serverClose(1006);
      await vi.advanceTimersByTimeAsync(10_000);
      await settle();
    }

    expect(store.status).toBe("error");
    expect(store.error?.key).toBe("connectionLost");
  });

  it("stops cleanly: stop message, socket close, released tracks", async () => {
    const { store, ws } = await startBroadcast();
    ws.serverMessage({ type: "accepted", ingest_id: "i-1" });
    ws.serverMessage({ type: "on_air", ingest_id: "i-1" });

    store.stop();
    await settle();

    expect(JSON.parse(String(ws.sent.at(-1)))).toEqual({ action: "stop" });
    expect(ws.closeCalls.length).toBeGreaterThan(0);
    expect(store.status).toBe("idle");
    expect(store.outputId).toBeNull();
    const stream = mediaDevices.getUserMedia.mock.results.at(-1)!
      .value as Promise<FakeMediaStream>;
    const tracks = (await stream).getTracks();
    expect(tracks.every((t) => t.stop.mock.calls.length > 0)).toBe(true);
  });

  it("returns to idle when the server ends the broadcast", async () => {
    const { store, ws } = await startBroadcast();
    ws.serverMessage({ type: "accepted", ingest_id: "i-1" });
    ws.serverMessage({ type: "on_air", ingest_id: "i-1" });

    ws.serverMessage({ type: "stopped", reason: "max_duration" });
    ws.serverClose(1000);
    await settle();

    expect(store.status).toBe("idle");
    expect(store.error).toBeNull();
  });

  it("fails when the input device ends mid-broadcast", async () => {
    const { store, ws } = await startBroadcast();
    ws.serverMessage({ type: "accepted", ingest_id: "i-1" });
    ws.serverMessage({ type: "on_air", ingest_id: "i-1" });

    const stream = (await mediaDevices.getUserMedia.mock.results.at(-1)!
      .value) as FakeMediaStream;
    stream.getTracks()[0].emit("ended");

    expect(store.status).toBe("error");
    expect(store.error?.key).toBe("deviceLost");
  });

  it("refuses to start when no recording format is available", async () => {
    FakeMediaRecorder.isTypeSupported.mockReturnValue(false);
    const store = useLiveBroadcastStore();
    await store.ensurePermission();

    await store.start(createStream());

    expect(store.status).toBe("error");
    expect(store.error?.key).toBe("noRecorder");
    expect(FakeWebSocket.instances).toHaveLength(0);
  });
});
