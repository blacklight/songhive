import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { createRouter, createMemoryHistory } from "vue-router";
import { setActivePinia, createPinia } from "pinia";
import { i18n } from "@/i18n";
import { usePlayerStore } from "@/stores/player";
import type { TrackResponse } from "@/player/types";
import { toQueueTrack } from "@/player/enrich";
import TrackList from "./TrackList.vue";

const actionsLabel = i18n.global.t("browse.detail.actions");

function createTestRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: "/", component: { template: "<div/>" } },
      { path: "/artists/:id", component: { template: "<div/>" } },
      { path: "/albums/:id", component: { template: "<div/>" } },
    ],
  });
}

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

function mountTrackList(
  props: Record<string, unknown> = {},
  providedRouter = createTestRouter(),
) {
  return {
    wrapper: mount(TrackList, {
      props: { tracks: [], ...props },
      attachTo: document.body,
      global: { plugins: [providedRouter] },
    }),
    router: providedRouter,
  };
}

function findMenuLabel(text: string) {
  const menu = document.body.querySelector(".context-menu");
  const labels = Array.from(
    menu?.querySelectorAll(".context-menu__label") ?? [],
  );
  return labels.find((el) => el.textContent === text);
}

async function clickMenuItem(text: string) {
  const label = findMenuLabel(text);
  const item = label?.parentElement as HTMLElement | null;
  if (item) {
    item.click();
    await flushPromises();
  }
}

describe("TrackList", () => {
  let wrapper: ReturnType<typeof mountTrackList>["wrapper"];

  beforeEach(() => {
    setActivePinia(createPinia());
  });

  afterEach(() => {
    wrapper?.unmount();
    document.body.innerHTML = "";
  });

  it("renders track metadata and uses context as the artist fallback", async () => {
    ({ wrapper } = mountTrackList({
      tracks: [makeTrack()],
      context: "Fallback Artist",
    }));
    await flushPromises();

    expect(wrapper.text()).toContain("Song One");
    expect(wrapper.text()).toContain("Fallback Artist");
    expect(wrapper.text()).toContain("3:05");
  });

  it("enriches tracks from the lookup map", async () => {
    const enrich = new Map([
      [
        "track-1",
        {
          artist_name: "Resolved Artist",
          album_title: "Resolved Album",
          artwork_url: "https://example.com/art.jpg",
        },
      ],
    ]);

    ({ wrapper } = mountTrackList({
      tracks: [makeTrack()],
      showArtwork: true,
      enrich,
    }));
    await flushPromises();

    expect(wrapper.text()).toContain("Resolved Artist");
    expect(wrapper.text()).toContain("Resolved Album");
    expect(wrapper.find(".track-list__artwork").attributes("src")).toBe(
      "https://example.com/art.jpg",
    );
  });

  it("plays a single track and updates the player store", async () => {
    const tracks = [
      makeTrack(),
      makeTrack({ id: "track-2", title: "Song Two" }),
    ];
    ({ wrapper } = mountTrackList({ tracks }));
    await flushPromises();

    await wrapper.find(".track-list__title-btn").trigger("click");
    await flushPromises();

    const player = usePlayerStore();
    expect(player.queue.map((t) => t.id)).toEqual(["track-1", "track-2"]);
    expect(player.currentTrack?.id).toBe("track-1");
    expect(wrapper.emitted("play")?.[0]).toEqual([0]);
  });

  it("plays all tracks when the header button is clicked", async () => {
    const tracks = [
      makeTrack(),
      makeTrack({ id: "track-2", title: "Song Two" }),
    ];
    ({ wrapper } = mountTrackList({ tracks, context: "Artist" }));
    await flushPromises();

    await wrapper.findAll("button").at(0)?.trigger("click");
    await flushPromises();

    const player = usePlayerStore();
    expect(player.queue.map((t) => t.id)).toEqual(["track-1", "track-2"]);
    expect(wrapper.emitted("play-all")?.length).toBe(1);
  });

  it("emits toggle-favorite from the context menu", async () => {
    const tracks = [makeTrack()];
    ({ wrapper } = mountTrackList({ tracks }));
    await flushPromises();

    await wrapper.find(`[aria-label="${actionsLabel}"]`).trigger("click");
    await flushPromises();

    await clickMenuItem(i18n.global.t("common.favorite"));

    expect(wrapper.emitted("toggle-favorite")?.[0]).toEqual([
      toQueueTrack(tracks[0], { artist_name: "" }),
    ]);
  });

  it("emits play-next and enqueues the track next", async () => {
    const tracks = [makeTrack()];
    ({ wrapper } = mountTrackList({ tracks, context: "Artist" }));
    await flushPromises();

    await wrapper.find(`[aria-label="${actionsLabel}"]`).trigger("click");
    await flushPromises();

    await clickMenuItem(i18n.global.t("browse.contextMenu.playNext"));

    const player = usePlayerStore();
    expect(player.queue.map((t) => t.id)).toEqual(["track-1"]);
    expect(wrapper.emitted("play-next")?.[0]).toEqual([
      toQueueTrack(tracks[0], { artist_name: "Artist" }),
    ]);
  });

  it("emits share from the context menu", async () => {
    const tracks = [makeTrack()];
    ({ wrapper } = mountTrackList({ tracks, context: "Artist" }));
    await flushPromises();

    await wrapper.find(`[aria-label="${actionsLabel}"]`).trigger("click");
    await flushPromises();

    await clickMenuItem(i18n.global.t("common.share"));

    expect(wrapper.emitted("share")?.[0]).toEqual([
      toQueueTrack(tracks[0], { artist_name: "Artist" }),
    ]);
  });

  it("navigates to the artist and album from the context menu", async () => {
    const router = createTestRouter();
    const tracks = [makeTrack()];
    ({ wrapper } = mountTrackList({ tracks, context: "Artist" }, router));
    await flushPromises();

    await wrapper.find(`[aria-label="${actionsLabel}"]`).trigger("click");
    await flushPromises();

    await clickMenuItem(i18n.global.t("browse.contextMenu.goToArtist"));
    expect(router.currentRoute.value.path).toBe("/artists/artist-1");

    await wrapper.find(`[aria-label="${actionsLabel}"]`).trigger("click");
    await flushPromises();

    await clickMenuItem(i18n.global.t("browse.contextMenu.goToAlbum"));
    expect(router.currentRoute.value.path).toBe("/albums/album-1");
  });

  it("updates the player queue when enqueue is selected", async () => {
    const tracks = [makeTrack()];
    ({ wrapper } = mountTrackList({ tracks, context: "Artist" }));
    await flushPromises();

    await wrapper.find(`[aria-label="${actionsLabel}"]`).trigger("click");
    await flushPromises();

    await clickMenuItem(i18n.global.t("browse.contextMenu.enqueue"));

    const player = usePlayerStore();
    expect(player.queue.map((t) => t.id)).toEqual(["track-1"]);
    expect(wrapper.emitted("enqueue")?.[0]).toEqual([
      toQueueTrack(tracks[0], { artist_name: "Artist" }),
    ]);
  });
});
