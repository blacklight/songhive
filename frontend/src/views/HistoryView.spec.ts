import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { setActivePinia, createPinia } from "pinia";
import { i18n } from "@/i18n";
import { usePlayerStore } from "@/stores/player";
import { useToastStore } from "@/stores/toast";
import * as historyApi from "@/api/history";
import * as tracksApi from "@/api/tracks";
import type { TrackResponse } from "@/api/tracks";
import HistoryView from "./HistoryView.vue";

vi.mock("@/api/history", () => ({
  listHistory: vi.fn(),
}));

vi.mock("@/api/tracks", () => ({
  getTrack: vi.fn(),
}));

interface HistoryEntry {
  id: string;
  track_id: string;
  title?: string | null;
  artist?: string | null;
  created_at: string;
}

function createHistoryEntry(
  id: string,
  trackId: string,
  title: string | null,
  artist: string | null,
  createdAt: string,
): HistoryEntry {
  return { id, track_id: trackId, title, artist, created_at: createdAt };
}

function createTrack(
  id: string,
  title: string,
  artistId = "artist-1",
): TrackResponse {
  return {
    id,
    title,
    artist_id: artistId,
    album_id: null,
    track_number: null,
    disc_number: null,
    duration: 185,
    genre: null,
    audio_url: "https://example.com/audio.mp3",
    visibility: "public",
    owner_id: "user-1",
  };
}

describe("HistoryView", () => {
  let wrapper: ReturnType<typeof mount>;
  let player: ReturnType<typeof usePlayerStore>;

  beforeEach(() => {
    setActivePinia(createPinia());
    player = usePlayerStore();
    vi.spyOn(player, "playTrack");
    vi.spyOn(player, "playAll");
    vi.clearAllMocks();
    vi.mocked(historyApi.listHistory).mockResolvedValue({
      items: [],
      page: 1,
      pageSize: 20,
    });
    vi.mocked(tracksApi.getTrack).mockResolvedValue(
      createTrack("track-1", "Song One"),
    );
  });

  afterEach(() => {
    wrapper?.unmount();
    document.body.innerHTML = "";
  });

  it("renders listening history on mount", async () => {
    vi.mocked(historyApi.listHistory).mockResolvedValue({
      items: [
        createHistoryEntry(
          "h1",
          "track-1",
          "Song One",
          "The Larks",
          "2024-01-15T10:30:00Z",
        ),
        createHistoryEntry(
          "h2",
          "track-2",
          "Song Two",
          "The Larks",
          "2024-01-14T09:00:00Z",
        ),
      ],
      page: 1,
      pageSize: 20,
    });

    wrapper = mount(HistoryView);
    await flushPromises();

    expect(historyApi.listHistory).toHaveBeenCalledWith({
      page: 1,
      pageSize: 20,
    });
    expect(wrapper.text()).toContain("Song One");
    expect(wrapper.text()).toContain("Song Two");
    expect(wrapper.text()).toContain("The Larks");
  });

  it("filters entries client-side by title or artist", async () => {
    vi.mocked(historyApi.listHistory).mockResolvedValue({
      items: [
        createHistoryEntry(
          "h1",
          "track-1",
          "Song One",
          "The Larks",
          "2024-01-15T10:30:00Z",
        ),
        createHistoryEntry(
          "h2",
          "track-2",
          "Song Two",
          "The Larks",
          "2024-01-14T09:00:00Z",
        ),
      ],
      page: 1,
      pageSize: 20,
    });

    wrapper = mount(HistoryView);
    await flushPromises();

    const input = wrapper.find('input[type="search"]');
    await input.setValue("two");
    await flushPromises();

    expect(wrapper.text()).toContain("Song Two");
    expect(wrapper.text()).not.toContain("Song One");
  });

  it("plays a track again", async () => {
    const track = createTrack("track-1", "Song One", "artist-1");
    vi.mocked(historyApi.listHistory).mockResolvedValue({
      items: [
        createHistoryEntry(
          "h1",
          "track-1",
          "Song One",
          "The Larks",
          "2024-01-15T10:30:00Z",
        ),
      ],
      page: 1,
      pageSize: 20,
    });
    vi.mocked(tracksApi.getTrack).mockResolvedValue(track);

    wrapper = mount(HistoryView);
    await flushPromises();

    const playAgain = wrapper
      .findAll("button")
      .find((b) => b.text() === i18n.global.t("pages.history.playAgain"));
    expect(playAgain).toBeDefined();
    await playAgain?.trigger("click");
    await flushPromises();

    expect(tracksApi.getTrack).toHaveBeenCalledWith("track-1");
    expect(player.playTrack).toHaveBeenCalledWith(
      expect.objectContaining({
        id: "track-1",
        title: "Song One",
        artist_name: "The Larks",
      }),
    );
  });

  it("shows an error banner with a retry button", async () => {
    vi.mocked(historyApi.listHistory).mockRejectedValue(
      new Error("network failure"),
    );

    wrapper = mount(HistoryView);
    await flushPromises();

    expect(wrapper.text()).toContain("network failure");

    vi.mocked(historyApi.listHistory).mockResolvedValue({
      items: [
        createHistoryEntry(
          "h1",
          "track-1",
          "Song One",
          "The Larks",
          "2024-01-15T10:30:00Z",
        ),
      ],
      page: 1,
      pageSize: 20,
    });

    const retry = wrapper
      .findAll("button")
      .find((b) => b.text() === i18n.global.t("common.retry"));
    expect(retry).toBeDefined();
    await retry?.trigger("click");
    await flushPromises();

    expect(wrapper.text()).toContain("Song One");
    expect(wrapper.text()).not.toContain("network failure");
  });

  it("loads the next page", async () => {
    const listHistory = vi.mocked(historyApi.listHistory);
    listHistory
      .mockResolvedValueOnce({
        items: Array.from({ length: 20 }, (_, i) =>
          createHistoryEntry(
            `h${i}`,
            `track-${i}`,
            `Song ${i}`,
            `Artist ${i}`,
            "2024-01-15T10:30:00Z",
          ),
        ),
        page: 1,
        pageSize: 20,
      })
      .mockResolvedValueOnce({
        items: [
          createHistoryEntry(
            "h20",
            "track-20",
            "Song 20",
            "Artist 20",
            "2024-01-16T10:30:00Z",
          ),
        ],
        page: 2,
        pageSize: 20,
      });

    wrapper = mount(HistoryView);
    await flushPromises();

    const next = wrapper
      .findAll("button")
      .find((b) => b.text() === i18n.global.t("pages.history.next"));
    expect(next).toBeDefined();
    await next?.trigger("click");
    await flushPromises();

    expect(listHistory).toHaveBeenLastCalledWith({ page: 2, pageSize: 20 });
    expect(wrapper.text()).toContain("Song 20");
    expect(wrapper.text()).not.toContain("Song 0");
  });

  it("plays all visible tracks", async () => {
    vi.mocked(historyApi.listHistory).mockResolvedValue({
      items: [
        createHistoryEntry(
          "h1",
          "track-1",
          "Song One",
          "The Larks",
          "2024-01-15T10:30:00Z",
        ),
        createHistoryEntry(
          "h2",
          "track-2",
          "Song Two",
          "The Larks",
          "2024-01-15T10:30:00Z",
        ),
      ],
      page: 1,
      pageSize: 20,
    });
    const t1 = createTrack("track-1", "Song One", "artist-1");
    const t2 = createTrack("track-2", "Song Two", "artist-2");
    vi.mocked(tracksApi.getTrack)
      .mockResolvedValueOnce(t1)
      .mockResolvedValueOnce(t2);

    wrapper = mount(HistoryView);
    await flushPromises();

    const playAll = wrapper
      .findAll("button")
      .find((b) => b.text() === i18n.global.t("browse.detail.playAll"));
    expect(playAll).toBeDefined();
    await playAll?.trigger("click");
    await flushPromises();

    expect(tracksApi.getTrack).toHaveBeenCalledTimes(2);
    expect(player.playAll).toHaveBeenCalled();
  });

  it("shows a toast when getTrack fails for play again", async () => {
    const toast = useToastStore();
    vi.mocked(historyApi.listHistory).mockResolvedValue({
      items: [
        createHistoryEntry(
          "h1",
          "track-1",
          "Song One",
          "The Larks",
          "2024-01-15T10:30:00Z",
        ),
      ],
      page: 1,
      pageSize: 20,
    });
    vi.mocked(tracksApi.getTrack).mockRejectedValue(new Error("missing"));

    wrapper = mount(HistoryView);
    await flushPromises();

    const playAgain = wrapper
      .findAll("button")
      .find((b) => b.text() === i18n.global.t("pages.history.playAgain"));
    await playAgain?.trigger("click");
    await flushPromises();

    expect(player.playTrack).not.toHaveBeenCalled();
    expect(toast.toasts).toHaveLength(1);
    expect(toast.toasts[0].type).toBe("error");
    expect(toast.toasts[0].message).toContain("missing");
  });

  it("navigates to the previous page after moving forward", async () => {
    const listHistory = vi.mocked(historyApi.listHistory);
    listHistory
      .mockResolvedValueOnce({
        items: Array.from({ length: 20 }, (_, i) =>
          createHistoryEntry(
            `h${i}`,
            `track-${i}`,
            `Song ${i}`,
            `Artist ${i}`,
            "2024-01-15T10:30:00Z",
          ),
        ),
        page: 1,
        pageSize: 20,
      })
      .mockResolvedValueOnce({
        items: [
          createHistoryEntry(
            "h20",
            "track-20",
            "Song 20",
            "Artist 20",
            "2024-01-16T10:30:00Z",
          ),
        ],
        page: 2,
        pageSize: 20,
      })
      .mockResolvedValueOnce({
        items: Array.from({ length: 20 }, (_, i) =>
          createHistoryEntry(
            `h${i}`,
            `track-${i}`,
            `Song ${i}`,
            `Artist ${i}`,
            "2024-01-15T10:30:00Z",
          ),
        ),
        page: 1,
        pageSize: 20,
      });

    wrapper = mount(HistoryView);
    await flushPromises();

    const previous = wrapper
      .findAll("button")
      .find((b) => b.text() === i18n.global.t("pages.history.previous"));
    expect(previous).toBeDefined();
    expect(previous?.attributes("disabled")).toBeDefined();

    const next = wrapper
      .findAll("button")
      .find((b) => b.text() === i18n.global.t("pages.history.next"));
    await next?.trigger("click");
    await flushPromises();
    expect(listHistory).toHaveBeenLastCalledWith({ page: 2, pageSize: 20 });

    const previousAfterNext = wrapper
      .findAll("button")
      .find((b) => b.text() === i18n.global.t("pages.history.previous"));
    expect(previousAfterNext?.attributes("disabled")).toBeUndefined();
    await previousAfterNext?.trigger("click");
    await flushPromises();
    expect(listHistory).toHaveBeenLastCalledWith({ page: 1, pageSize: 20 });
  });

  it("shows the empty label and the empty-search label", async () => {
    vi.mocked(historyApi.listHistory).mockResolvedValue({
      items: [],
      page: 1,
      pageSize: 20,
    });

    wrapper = mount(HistoryView);
    await flushPromises();

    expect(wrapper.text()).toContain(i18n.global.t("pages.history.empty"));

    const input = wrapper.find('input[type="search"]');
    await input.setValue("nomatch");
    await flushPromises();

    expect(wrapper.text()).toContain(
      i18n.global.t("pages.history.emptySearch"),
    );
  });

  it("shows an error toast when play all cannot resolve any tracks", async () => {
    const toast = useToastStore();
    vi.mocked(historyApi.listHistory).mockResolvedValue({
      items: [
        createHistoryEntry(
          "h1",
          "track-1",
          "Song One",
          "The Larks",
          "2024-01-15T10:30:00Z",
        ),
        createHistoryEntry(
          "h2",
          "track-2",
          "Song Two",
          "The Larks",
          "2024-01-15T10:30:00Z",
        ),
      ],
      page: 1,
      pageSize: 20,
    });
    vi.mocked(tracksApi.getTrack).mockRejectedValue(new Error("missing"));

    wrapper = mount(HistoryView);
    await flushPromises();

    const playAll = wrapper
      .findAll("button")
      .find((b) => b.text() === i18n.global.t("browse.detail.playAll"));
    await playAll?.trigger("click");
    await flushPromises();

    expect(player.playAll).not.toHaveBeenCalled();
    expect(toast.toasts).toHaveLength(1);
    expect(toast.toasts[0].type).toBe("error");
    expect(toast.toasts[0].message).toContain(i18n.global.t("errors.unknown"));
  });

  it("shows a warning toast and plays the surviving tracks when play all partially fails", async () => {
    const toast = useToastStore();
    vi.mocked(historyApi.listHistory).mockResolvedValue({
      items: [
        createHistoryEntry(
          "h1",
          "track-1",
          "Song One",
          "The Larks",
          "2024-01-15T10:30:00Z",
        ),
        createHistoryEntry(
          "h2",
          "track-2",
          "Song Two",
          "The Larks",
          "2024-01-15T10:30:00Z",
        ),
      ],
      page: 1,
      pageSize: 20,
    });
    const t1 = createTrack("track-1", "Song One", "artist-1");
    vi.mocked(tracksApi.getTrack)
      .mockResolvedValueOnce(t1)
      .mockRejectedValueOnce(new Error("missing"));

    wrapper = mount(HistoryView);
    await flushPromises();

    const playAll = wrapper
      .findAll("button")
      .find((b) => b.text() === i18n.global.t("browse.detail.playAll"));
    await playAll?.trigger("click");
    await flushPromises();

    expect(player.playAll).toHaveBeenCalled();
    expect(toast.toasts).toHaveLength(1);
    expect(toast.toasts[0].type).toBe("warning");
    expect(toast.toasts[0].message).toContain(
      i18n.global.t("pages.history.playAllPartial", { count: 1, total: 2 }),
    );
  });
});
