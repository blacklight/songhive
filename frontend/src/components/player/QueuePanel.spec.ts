import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { usePlayerStore } from "@/stores/player";
import { useAuthStore } from "@/stores/auth";
import { useToastStore } from "@/stores/toast";
import { i18n } from "@/i18n";
import * as playlistsApi from "@/api/playlists";
import { toQueueTrack } from "@/player/enrich";
import type { QueueTrack, TrackResponse } from "@/player/types";
import QueuePanel from "./QueuePanel.vue";

vi.mock("@/api/playlists", () => ({
  createPlaylist: vi.fn(),
  addTracksToPlaylist: vi.fn(),
}));

function makeTrack(overrides: Partial<TrackResponse> = {}): TrackResponse {
  return {
    id: "track-1",
    title: "Song One",
    artist_id: "artist-1",
    album_id: "album-1",
    track_number: 1,
    duration: 185,
    visibility: "public" as const,
    ...overrides,
  };
}

function makeQueueTrack(overrides: Partial<QueueTrack> = {}): QueueTrack {
  return {
    ...toQueueTrack(makeTrack(), { artist_name: "Artist" }),
    ...overrides,
  };
}

function setAuthenticated() {
  const authStore = useAuthStore();
  authStore.status = "authenticated";
  authStore.user = { id: "user-1", username: "alice" } as never;
}

function findBodyButton(text: string) {
  return Array.from(document.body.querySelectorAll("button")).find(
    (b) => b.textContent === text,
  );
}

function createMockEngine() {
  return {
    load: vi.fn(),
    play: vi.fn(),
    pause: vi.fn(),
    seek: vi.fn(),
    setVolume: vi.fn(),
    setNextTrack: vi.fn(),
    destroy: vi.fn(),
  };
}

describe("QueuePanel", () => {
  let originalScrollIntoView: typeof Element.prototype.scrollIntoView;

  beforeEach(() => {
    originalScrollIntoView = Element.prototype.scrollIntoView;
    Element.prototype.scrollIntoView =
      vi.fn() as typeof Element.prototype.scrollIntoView;
    vi.mocked(playlistsApi.createPlaylist).mockReset();
    vi.mocked(playlistsApi.addTracksToPlaylist).mockReset();
  });

  afterEach(() => {
    Element.prototype.scrollIntoView = originalScrollIntoView;
    document.body.style.overflow = "";
    document.body.innerHTML = "";
  });

  it("scrolls the current track into view when opened", async () => {
    const tracks = [
      makeTrack({ id: "track-1", title: "Song One" }),
      makeTrack({ id: "track-2", title: "Song Two" }),
    ].map((t) => toQueueTrack(t, { artist_name: "Artist" }));

    const player = usePlayerStore();
    player.queue = tracks;
    player.index = 1;

    const wrapper = mount(QueuePanel, {
      props: { open: false },
      attachTo: document.body,
    });
    await flushPromises();

    expect(Element.prototype.scrollIntoView).not.toHaveBeenCalled();

    await wrapper.setProps({ open: true });
    await flushPromises();

    expect(Element.prototype.scrollIntoView).toHaveBeenCalledWith({
      behavior: "auto",
      block: "nearest",
    });
  });

  it("plays the clicked track", async () => {
    const tracks = [
      makeTrack({ id: "track-1", title: "Song One" }),
      makeTrack({ id: "track-2", title: "Song Two" }),
      makeTrack({ id: "track-3", title: "Song Three" }),
    ].map((t) => toQueueTrack(t, { artist_name: "Artist" }));

    const player = usePlayerStore();
    player.queue = tracks;
    player.index = 0;

    const wrapper = mount(QueuePanel, {
      props: { open: true },
      attachTo: document.body,
    });
    await flushPromises();

    const items = wrapper.findAll(".queue-panel__item");
    await items[2].trigger("click");

    expect(player.index).toBe(2);
    expect(player.currentTrack?.id).toBe("track-3");
    expect(player.isPlaying).toBe(true);
  });

  it("does not play a track when its remove button is clicked", async () => {
    const tracks = [
      makeTrack({ id: "track-1", title: "Song One" }),
      makeTrack({ id: "track-2", title: "Song Two" }),
    ].map((t) => toQueueTrack(t, { artist_name: "Artist" }));

    const player = usePlayerStore();
    player.queue = tracks;
    player.index = 0;

    const wrapper = mount(QueuePanel, {
      props: { open: true },
      attachTo: document.body,
    });
    await flushPromises();

    const items = wrapper.findAll(".queue-panel__item");
    await items[1].find(".queue-panel__remove").trigger("click");

    expect(player.queue.map((t) => t.id)).toEqual(["track-1"]);
    expect(player.index).toBe(0);
  });

  it("does not start playback when Enter is pressed on a remove button", async () => {
    const tracks = [
      makeTrack({ id: "track-1", title: "Song One" }),
      makeTrack({ id: "track-2", title: "Song Two" }),
    ].map((t) => toQueueTrack(t, { artist_name: "Artist" }));

    const player = usePlayerStore();
    const engine = createMockEngine();
    player.registerEngine(engine);
    player.queue = tracks;
    player.index = 0;
    player.isPlaying = true;
    player.playbackState = "playing";

    const wrapper = mount(QueuePanel, {
      props: { open: true },
      attachTo: document.body,
    });
    await flushPromises();

    const removeButton = wrapper
      .findAll(".queue-panel__item")[1]
      .find(".queue-panel__remove");
    await removeButton.trigger("keydown", { key: "Enter" });

    // The row's Enter-to-play handler must not fire through the button —
    // removing a queue row never changes what is playing.
    expect(engine.load).not.toHaveBeenCalled();
    expect(player.index).toBe(0);
    expect(player.currentTrack?.id).toBe("track-1");
  });

  it("renders repeated queue entries without duplicate key warnings", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const track = toQueueTrack(makeTrack({ id: "dup", title: "Same" }), {
      artist_name: "Artist",
    });

    const player = usePlayerStore();
    player.queue = [track, track];
    player.index = 0;

    const wrapper = mount(QueuePanel, {
      props: { open: true },
      attachTo: document.body,
    });
    await flushPromises();

    expect(wrapper.findAll(".queue-panel__item")).toHaveLength(2);
    const warned = warn.mock.calls.some((args) =>
      args.some(
        (arg) => typeof arg === "string" && arg.includes("Duplicate keys"),
      ),
    );
    expect(warned).toBe(false);
    warn.mockRestore();
  });

  it("hides the save-as-playlist button when unauthenticated", async () => {
    const player = usePlayerStore();
    player.queue = [makeQueueTrack({ id: "track-1" })];
    player.index = 0;

    const wrapper = mount(QueuePanel, {
      props: { open: true },
      attachTo: document.body,
    });
    await flushPromises();

    expect(wrapper.find(".queue-panel__save").exists()).toBe(false);
  });

  it("disables the save button when the queue has no saveable items", async () => {
    setAuthenticated();
    const player = usePlayerStore();
    // Attachment-only remote audio has no cached remote object row to add.
    player.queue = [makeQueueTrack({ id: "remote-1", remote: true })];
    player.index = 0;

    const wrapper = mount(QueuePanel, {
      props: { open: true },
      attachTo: document.body,
    });
    await flushPromises();

    const save = wrapper.find(".queue-panel__save");
    expect(save.exists()).toBe(true);
    expect(save.attributes("disabled")).toBeDefined();
  });

  it("saves the queue as a new playlist", async () => {
    setAuthenticated();
    vi.mocked(playlistsApi.createPlaylist).mockResolvedValue({
      id: "playlist-1",
      name: "My Mix",
      owner_id: "user-1",
      visibility: "private",
    } as never);
    vi.mocked(playlistsApi.addTracksToPlaylist).mockResolvedValue({
      added: 2,
      track_ids: ["track-1", "track-2"],
    });

    const player = usePlayerStore();
    player.queue = [
      makeQueueTrack({ id: "track-1", title: "One" }),
      makeQueueTrack({ id: "track-2", title: "Two" }),
    ];
    player.index = 0;

    const wrapper = mount(QueuePanel, {
      props: { open: true },
      attachTo: document.body,
    });
    await flushPromises();

    await wrapper.find(".queue-panel__save").trigger("click");
    await flushPromises();

    const nameInput = document.body.querySelector(
      '#queue-save-form input[type="text"]',
    ) as HTMLInputElement;
    expect(nameInput).not.toBeNull();
    nameInput.value = "My Mix";
    nameInput.dispatchEvent(new Event("input"));
    await flushPromises();

    const saveButton = findBodyButton(i18n.global.t("common.save"));
    expect(saveButton).toBeDefined();
    saveButton?.click();
    await flushPromises();

    expect(playlistsApi.createPlaylist).toHaveBeenCalledWith(
      { name: "My Mix", description: null },
      { visibility: "private" },
    );
    expect(playlistsApi.addTracksToPlaylist).toHaveBeenCalledTimes(1);
    expect(playlistsApi.addTracksToPlaylist).toHaveBeenCalledWith(
      "playlist-1",
      { track_ids: ["track-1", "track-2"], allow_duplicates: true },
    );

    const toastStore = useToastStore();
    expect(toastStore.toasts[0]?.type).toBe("success");
    expect(toastStore.toasts[0]?.message).toBe(
      i18n.global.t("player.queueSavedAsPlaylist", {
        name: "My Mix",
        count: 2,
      }),
    );
  });

  it("sends one ordered batch per contiguous item kind", async () => {
    setAuthenticated();
    vi.mocked(playlistsApi.createPlaylist).mockResolvedValue({
      id: "playlist-1",
      name: "Mixed",
      owner_id: "user-1",
      visibility: "private",
    } as never);
    vi.mocked(playlistsApi.addTracksToPlaylist).mockResolvedValue({
      added: 1,
      track_ids: [],
    });

    const player = usePlayerStore();
    player.queue = [
      makeQueueTrack({ id: "track-1" }),
      makeQueueTrack({ id: "ep-1", podcast_episode_id: "ep-1" }),
      makeQueueTrack({ id: "rt-1", remote: true, remote_object_id: "ro-1" }),
      makeQueueTrack({ id: "track-2" }),
    ];
    player.index = 0;

    const wrapper = mount(QueuePanel, {
      props: { open: true },
      attachTo: document.body,
    });
    await flushPromises();

    await wrapper.find(".queue-panel__save").trigger("click");
    await flushPromises();

    const nameInput = document.body.querySelector(
      '#queue-save-form input[type="text"]',
    ) as HTMLInputElement;
    nameInput.value = "Mixed";
    nameInput.dispatchEvent(new Event("input"));
    await flushPromises();

    findBodyButton(i18n.global.t("common.save"))?.click();
    await flushPromises();

    const add = vi.mocked(playlistsApi.addTracksToPlaylist);
    expect(add.mock.calls).toEqual([
      ["playlist-1", { track_ids: ["track-1"], allow_duplicates: true }],
      ["playlist-1", { episode_ids: ["ep-1"], allow_duplicates: true }],
      ["playlist-1", { remote_object_ids: ["ro-1"], allow_duplicates: true }],
      ["playlist-1", { track_ids: ["track-2"], allow_duplicates: true }],
    ]);
  });

  it("keeps repeated queue entries by splitting the batch", async () => {
    setAuthenticated();
    vi.mocked(playlistsApi.createPlaylist).mockResolvedValue({
      id: "playlist-1",
      name: "Repeats",
      owner_id: "user-1",
      visibility: "private",
    } as never);
    vi.mocked(playlistsApi.addTracksToPlaylist).mockResolvedValue({
      added: 1,
      track_ids: [],
    });

    const player = usePlayerStore();
    player.queue = [
      makeQueueTrack({ id: "track-1" }),
      makeQueueTrack({ id: "track-2" }),
      makeQueueTrack({ id: "track-1" }),
    ];
    player.index = 0;

    const wrapper = mount(QueuePanel, {
      props: { open: true },
      attachTo: document.body,
    });
    await flushPromises();

    await wrapper.find(".queue-panel__save").trigger("click");
    await flushPromises();

    const nameInput = document.body.querySelector(
      '#queue-save-form input[type="text"]',
    ) as HTMLInputElement;
    nameInput.value = "Repeats";
    nameInput.dispatchEvent(new Event("input"));
    await flushPromises();

    findBodyButton(i18n.global.t("common.save"))?.click();
    await flushPromises();

    const add = vi.mocked(playlistsApi.addTracksToPlaylist);
    expect(add.mock.calls).toEqual([
      [
        "playlist-1",
        { track_ids: ["track-1", "track-2"], allow_duplicates: true },
      ],
      ["playlist-1", { track_ids: ["track-1"], allow_duplicates: true }],
    ]);
  });

  it("shows the API error when playlist creation fails", async () => {
    setAuthenticated();
    vi.mocked(playlistsApi.createPlaylist).mockRejectedValue(
      new Error("network down"),
    );

    const player = usePlayerStore();
    player.queue = [makeQueueTrack({ id: "track-1" })];
    player.index = 0;

    const wrapper = mount(QueuePanel, {
      props: { open: true },
      attachTo: document.body,
    });
    await flushPromises();

    await wrapper.find(".queue-panel__save").trigger("click");
    await flushPromises();

    const nameInput = document.body.querySelector(
      '#queue-save-form input[type="text"]',
    ) as HTMLInputElement;
    nameInput.value = "Failing";
    nameInput.dispatchEvent(new Event("input"));
    await flushPromises();

    findBodyButton(i18n.global.t("common.save"))?.click();
    await flushPromises();

    expect(
      document.body.querySelector(".queue-panel__save-error")?.textContent,
    ).toBe("network down");
    expect(playlistsApi.addTracksToPlaylist).not.toHaveBeenCalled();
  });
});
