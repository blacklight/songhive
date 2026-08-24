import { describe, it, expect, vi, beforeEach } from "vitest";
import { setWsTokenProvider, EventBus } from "./ws";

class FakeWebSocket {
  url = "";
  private listeners: Record<string, ((event: unknown) => void)[]> = {};
  sent: string[] = [];

  constructor(url: string) {
    this.url = url;
    setTimeout(() => this.emit("open", {}), 0);
  }

  addEventListener(type: string, handler: (event: unknown) => void) {
    if (!this.listeners[type]) this.listeners[type] = [];
    this.listeners[type].push(handler);
  }

  removeEventListener() {
    // no-op for tests
  }

  send(data: string) {
    this.sent.push(data);
  }

  close() {
    this.emit("close", {});
  }

  emit(type: string, event: unknown) {
    this.listeners[type]?.forEach((h) => h(event));
  }

  push(data: unknown) {
    this.emit("message", { data: JSON.stringify(data) });
  }
}

describe("EventBus", () => {
  beforeEach(() => {
    vi.stubGlobal("WebSocket", FakeWebSocket);
    setWsTokenProvider(() => "token-123");
  });

  it("connects and subscribes", async () => {
    const bus = new EventBus();
    bus.subscribe(["tracks"]);
    await new Promise((resolve) => setTimeout(resolve, 10));
    expect(bus.status.value).toBe("open");
  });

  it("dispatches events to handlers", async () => {
    const bus = new EventBus();
    bus.connect();
    const handler = vi.fn();
    bus.on("track_start", handler);
    bus.subscribe(["tracks"]);
    await new Promise((resolve) => setTimeout(resolve, 10));
    const socket = (bus as unknown as { socket: FakeWebSocket }).socket;
    socket.push({ type: "track_start", data: { id: "t1" }, topic: "tracks" });
    expect(handler).toHaveBeenCalled();
  });

  it("reconnects after disconnect then an unexpected close", () => {
    vi.useFakeTimers();
    const bus = new EventBus();

    bus.subscribe(["tracks"]);
    vi.advanceTimersByTime(1);
    expect(bus.status.value).toBe("open");

    bus.disconnect();
    expect(bus.status.value).toBe("closed");

    bus.subscribe(["tracks"]);
    vi.advanceTimersByTime(1);
    expect(bus.status.value).toBe("open");

    const socket = (bus as unknown as { socket: FakeWebSocket }).socket;
    socket.emit("close", {});
    expect(bus.status.value).toBe("reconnecting");

    vi.advanceTimersByTime(1000);
    vi.advanceTimersByTime(1);
    expect(bus.status.value).toBe("open");

    bus.disconnect();
    vi.useRealTimers();
  });
});
