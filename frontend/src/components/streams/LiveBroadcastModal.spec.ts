import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { setActivePinia, createPinia } from "pinia";
import { i18n } from "@/i18n";
import { useLiveBroadcastStore } from "@/stores/liveBroadcast";
import type { StreamResponse } from "@/api/streams";
import LiveBroadcastModal from "./LiveBroadcastModal.vue";

class FakeTrack {
  kind = "audio";
  stop = vi.fn();
  addEventListener() {}
  removeEventListener() {}
}

class FakeMediaStream {
  tracks = [new FakeTrack()];
  getTracks() {
    return this.tracks;
  }
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

// AppModal teleports to <body> — query the document, not the wrapper.
function bodyText(): string {
  return document.body.textContent ?? "";
}

function findButton(text: string): HTMLButtonElement | null {
  for (const btn of Array.from(document.body.querySelectorAll("button"))) {
    if (btn.textContent?.trim() === text) return btn;
  }
  return null;
}

describe("LiveBroadcastModal", () => {
  let wrapper: ReturnType<typeof mount>;

  beforeEach(() => {
    setActivePinia(createPinia());
    localStorage.clear();
    vi.clearAllMocks();

    mediaDevices.getUserMedia.mockResolvedValue(new FakeMediaStream());
    mediaDevices.enumerateDevices.mockResolvedValue([
      { kind: "audioinput", deviceId: "mic-1", label: "Studio Mic" },
      { kind: "audioinput", deviceId: "mic-2", label: "USB Mic" },
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
    vi.stubGlobal("MediaRecorder", {
      isTypeSupported: () => true,
    });
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
    wrapper?.unmount();
    document.body.innerHTML = "";
    vi.unstubAllGlobals();
  });

  async function mountModal(open = true, stream = createStream()) {
    wrapper = mount(LiveBroadcastModal, {
      props: { open, stream },
      attachTo: document.body,
    });
    await flushPromises();
    return wrapper;
  }

  it("opens the pre-flight form once permission is granted", async () => {
    const store = useLiveBroadcastStore();
    await mountModal();

    expect(store.permission).toBe("granted");
    expect(mediaDevices.getUserMedia).toHaveBeenCalled();

    const select = document.body.querySelector("select");
    expect(select).not.toBeNull();
    const labels = Array.from(select!.querySelectorAll("option")).map(
      (o) => o.textContent,
    );
    expect(labels).toEqual([
      i18n.global.t("pages.streams.broadcast.defaultDevice"),
      "Studio Mic",
      "USB Mic",
    ]);
    expect(
      findButton(i18n.global.t("pages.streams.broadcast.goLive")),
    ).not.toBeNull();
    // The level meter is present for the preview.
    expect(document.body.querySelector('[role="meter"]')).not.toBeNull();
  });

  it("shows the unsupported panel when capture is unavailable", async () => {
    Object.defineProperty(navigator, "mediaDevices", {
      value: undefined,
      configurable: true,
    });
    await mountModal();

    expect(bodyText()).toContain(
      i18n.global.t("pages.streams.broadcast.unsupported"),
    );
  });

  it("shows the denied panel with a retry button", async () => {
    permissionStatus.state = "denied";
    await mountModal();

    expect(bodyText()).toContain(
      i18n.global.t("pages.streams.broadcast.permissionDenied"),
    );
    expect(findButton(i18n.global.t("common.retry"))).not.toBeNull();
  });

  it("selects a device through the store", async () => {
    const store = useLiveBroadcastStore();
    await mountModal();

    const select = document.body.querySelector("select") as HTMLSelectElement;
    select.value = "mic-2";
    select.dispatchEvent(new Event("change"));
    await flushPromises();

    expect(store.deviceId).toBe("mic-2");
    expect(localStorage.getItem("songhive.live.deviceId")).toBe("mic-2");
  });

  it("starts the broadcast from the Go live button", async () => {
    const store = useLiveBroadcastStore();
    vi.spyOn(store, "start").mockResolvedValue();
    await mountModal();

    const input = document.body.querySelector(
      'input[type="text"]',
    ) as HTMLInputElement;
    input.value = "My late-night set";
    input.dispatchEvent(new Event("input"));

    findButton(i18n.global.t("pages.streams.broadcast.goLive"))!.click();
    await flushPromises();

    expect(store.start).toHaveBeenCalledWith(
      expect.objectContaining({ id: "out-1" }),
      { title: "My late-night set" },
    );
  });

  it("shows the on-air panel while broadcasting on this stream", async () => {
    const store = useLiveBroadcastStore();
    await mountModal();
    store.outputId = "out-1";
    store.status = "on-air";
    store.startedAt = Date.now() - 65_000;
    store.listeners = 3;
    await flushPromises();

    expect(bodyText()).toContain(
      i18n.global.t("pages.streams.broadcast.onAir"),
    );
    expect(bodyText()).toContain("1:05");
    expect(bodyText()).toContain(i18n.global.t("pages.streams.listeners", 3));
    expect(
      findButton(i18n.global.t("pages.streams.broadcast.stop")),
    ).not.toBeNull();
  });

  it("stops the broadcast from the stop button", async () => {
    const store = useLiveBroadcastStore();
    await mountModal();
    store.outputId = "out-1";
    store.status = "on-air";
    store.startedAt = Date.now();
    await flushPromises();

    vi.spyOn(store, "stop");
    findButton(i18n.global.t("pages.streams.broadcast.stop"))!.click();
    await flushPromises();

    expect(store.stop).toHaveBeenCalled();
  });

  it("keeps broadcasting when the modal closes mid-broadcast", async () => {
    const store = useLiveBroadcastStore();
    await mountModal();
    store.outputId = "out-1";
    store.status = "on-air";
    store.startedAt = Date.now();
    await flushPromises();

    vi.spyOn(store, "stop");
    findButton(
      i18n.global.t("pages.streams.broadcast.keepBroadcasting"),
    )!.click();
    await flushPromises();

    expect(store.stop).not.toHaveBeenCalled();
    expect(store.status).toBe("on-air");
    expect(wrapper.emitted("close")).toBeTruthy();
  });

  it("releases the preview when the modal closes before going live", async () => {
    const store = useLiveBroadcastStore();
    await mountModal();
    expect(store.status).toBe("ready");

    const stream = (await mediaDevices.getUserMedia.mock.results.at(-1)!
      .value) as FakeMediaStream;
    vi.spyOn(store, "stop");
    findButton(i18n.global.t("common.cancel"))!.click();
    await flushPromises();

    expect(store.stop).toHaveBeenCalled();
    expect(store.status).toBe("idle");
    expect(stream.tracks[0].stop).toHaveBeenCalled();
    expect(wrapper.emitted("close")).toBeTruthy();
  });
});
