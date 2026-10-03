import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { createRouter, createMemoryHistory } from "vue-router";
import { setActivePinia, createPinia } from "pinia";
import { i18n } from "@/i18n";
import * as streamsApi from "@/api/streams";
import { usePlayerStore } from "@/stores/player";
import StreamsView from "./StreamsView.vue";

vi.mock("@/api/streams", () => ({
  listStreams: vi.fn(),
}));

type BusHandler = (event: { type: string; data: unknown }) => void;

const { busHandlers, eventBus } = vi.hoisted(() => {
  const handlers = new Map<string, Set<BusHandler>>();
  return {
    busHandlers: handlers,
    eventBus: {
      on(type: string, handler: BusHandler) {
        if (!handlers.has(type)) handlers.set(type, new Set());
        handlers.get(type)!.add(handler);
      },
      off(type: string, handler: BusHandler) {
        handlers.get(type)?.delete(handler);
      },
      connect: vi.fn(),
      disconnect: vi.fn(),
      subscribe: vi.fn(),
      unsubscribe: vi.fn(),
      playbackControl: vi.fn(),
      status: { value: "closed" },
    },
  };
});

vi.mock("@/api/ws", () => ({ eventBus }));

function emitStreamUpdate(data: unknown) {
  busHandlers
    .get("stream_update")
    ?.forEach((h) => h({ type: "stream_update", data }));
}

function createTestRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: "/", component: { template: "<div/>" } },
      { path: "/@:username", component: { template: "<div/>" } },
      { path: "/tracks/:id", component: { template: "<div/>" } },
    ],
  });
}

function createStream(
  overrides: Partial<streamsApi.StreamResponse> = {},
): streamsApi.StreamResponse {
  return {
    id: "s1",
    name: "Test Radio",
    mount: "radio",
    stream_url: "/streams/radio",
    visibility: "public",
    is_owner: false,
    owner: {
      username: "alice",
      display_name: "DJ Alice",
      avatar_url: null,
    },
    enabled: true,
    online: true,
    description: "Chill beats",
    genre: "Ambient",
    format: "mp3",
    bitrate: "128k",
    content_type: "audio/mpeg",
    now_playing: null,
    listener_count: 0,
    created_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

describe("StreamsView", () => {
  let wrapper: ReturnType<typeof mount>;

  beforeEach(() => {
    setActivePinia(createPinia());
    vi.resetAllMocks();
    vi.mocked(streamsApi.listStreams).mockResolvedValue([]);
    busHandlers.clear();
    localStorage.clear();
  });

  afterEach(() => {
    vi.useRealTimers();
    wrapper?.unmount();
    document.body.innerHTML = "";
  });

  async function mountView() {
    const router = createTestRouter();
    await router.push("/");
    await router.isReady();
    wrapper = mount(StreamsView, {
      attachTo: document.body,
      global: { plugins: [router] },
    });
    await flushPromises();
  }

  it("renders streams with owner, metadata and status", async () => {
    vi.mocked(streamsApi.listStreams).mockResolvedValue([createStream()]);

    await mountView();

    const card = wrapper.find(".streams-view__card");
    expect(card.exists()).toBe(true);
    expect(wrapper.text()).toContain("Test Radio");
    expect(wrapper.text()).toContain("DJ Alice");
    expect(wrapper.text()).toContain("/streams/radio");
    expect(wrapper.text()).toContain("Ambient");
    expect(wrapper.text()).toContain("Chill beats");
    expect(wrapper.text()).toContain(i18n.global.t("pages.streams.live"));

    const ownerLink = wrapper.find(".streams-view__owner");
    expect(ownerLink.attributes("href")).toBe("/@alice");
  });

  it("renders now-playing info with a link to the track", async () => {
    vi.mocked(streamsApi.listStreams).mockResolvedValue([
      createStream({
        now_playing: {
          track_id: "t1",
          title: "Song",
          artist: "Artist",
          album: "LP",
        },
      }),
    ]);

    await mountView();

    const link = wrapper.find(".streams-view__now-playing-link");
    expect(link.exists()).toBe(true);
    expect(link.text()).toBe("Artist - Song");
    expect(link.attributes("href")).toBe("/tracks/t1");
  });

  it("shows offline and private badges", async () => {
    vi.mocked(streamsApi.listStreams).mockResolvedValue([
      createStream({
        online: false,
        visibility: "private",
        is_owner: true,
        stream_url: "/streams/radio?token=s3cret",
      }),
    ]);

    await mountView();

    expect(wrapper.text()).toContain(i18n.global.t("pages.streams.offline"));
    expect(wrapper.text()).toContain(i18n.global.t("pages.streams.private"));
    const audio = wrapper.find("audio");
    // Vue binds ``src`` as a DOM property, so read the resolved URL.
    expect((audio.element as HTMLAudioElement).src).toContain(
      "/streams/radio?token=s3cret",
    );
    // Live streams lazy-connect: no preload until the user hits play.
    expect(audio.attributes("preload")).toBe("none");
  });

  it("shows the listener count", async () => {
    vi.mocked(streamsApi.listStreams).mockResolvedValue([
      createStream({ listener_count: 3 }),
    ]);

    await mountView();

    expect(wrapper.text()).toContain(
      i18n.global.t("pages.streams.listeners", 3),
    );
  });

  it("embeds a live player that can hand off to the player bar", async () => {
    const player = usePlayerStore();
    vi.spyOn(player, "playTrack");
    vi.mocked(streamsApi.listStreams).mockResolvedValue([createStream()]);

    await mountView();

    const handoff = wrapper
      .findAll("button")
      .find(
        (b) =>
          b.attributes("aria-label") ===
          i18n.global.t("activities.audio.playInPlayer"),
      );
    expect(handoff).toBeDefined();
    await handoff?.trigger("click");
    await flushPromises();

    expect(player.playTrack).toHaveBeenCalledWith(
      expect.objectContaining({
        stream_url: "/streams/radio",
        title: "Test Radio",
        remote: true,
      }),
    );
  });

  it("shows the empty state", async () => {
    await mountView();

    expect(wrapper.text()).toContain(i18n.global.t("pages.streams.empty"));
  });

  it("shows an error banner with a retry button", async () => {
    vi.mocked(streamsApi.listStreams).mockRejectedValue(
      new Error("network failure"),
    );

    await mountView();

    expect(wrapper.text()).toContain("network failure");

    vi.mocked(streamsApi.listStreams).mockResolvedValue([createStream()]);
    const retry = wrapper
      .findAll("button")
      .find((b) => b.text() === i18n.global.t("common.retry"));
    await retry?.trigger("click");
    await flushPromises();

    expect(wrapper.text()).toContain("Test Radio");
    expect(wrapper.text()).not.toContain("network failure");
  });

  it("applies stream_update events live without refetching", async () => {
    localStorage.setItem(
      "songhive.auth.user",
      JSON.stringify({ id: "u1", username: "bob" }),
    );
    vi.mocked(streamsApi.listStreams).mockResolvedValue([
      createStream({
        now_playing: { track_id: "t1", title: "Song", artist: "Artist" },
      }),
    ]);

    await mountView();

    expect(eventBus.connect).toHaveBeenCalled();
    expect(busHandlers.get("stream_update")?.size).toBe(1);
    expect(wrapper.text()).toContain("Artist - Song");

    // A track change lands as a WS event and patches the card in place.
    emitStreamUpdate({
      mount: "radio",
      online: true,
      now_playing: { track_id: "t2", title: "Next", artist: "Artist" },
    });
    await flushPromises();

    const link = wrapper.find(".streams-view__now-playing-link");
    expect(link.exists()).toBe(true);
    expect(link.text()).toBe("Artist - Next");
    expect(link.attributes("href")).toBe("/tracks/t2");
    expect(streamsApi.listStreams).toHaveBeenCalledTimes(1);

    // Going offline clears the now-playing row and flips the badge.
    emitStreamUpdate({ mount: "radio", online: false, now_playing: null });
    await flushPromises();

    expect(wrapper.find(".streams-view__now-playing").exists()).toBe(false);
    expect(wrapper.text()).toContain(i18n.global.t("pages.streams.offline"));

    // Events for mounts not in the listing are ignored.
    emitStreamUpdate({
      mount: "other",
      online: true,
      now_playing: { title: "Elsewhere" },
    });
    await flushPromises();
    expect(streamsApi.listStreams).toHaveBeenCalledTimes(1);
  });

  it("does not open a WebSocket for anonymous visitors", async () => {
    await mountView();

    expect(eventBus.connect).not.toHaveBeenCalled();
    expect(busHandlers.size).toBe(0);
  });

  it("polls the directory periodically to reconcile state", async () => {
    vi.useFakeTimers();
    vi.mocked(streamsApi.listStreams).mockResolvedValue([createStream()]);

    await mountView();
    expect(streamsApi.listStreams).toHaveBeenCalledTimes(1);

    vi.mocked(streamsApi.listStreams).mockResolvedValue([
      createStream({ online: false }),
    ]);
    await vi.advanceTimersByTimeAsync(30_000);
    await flushPromises();

    expect(streamsApi.listStreams).toHaveBeenCalledTimes(2);
    expect(wrapper.text()).toContain(i18n.global.t("pages.streams.offline"));
  });
});
