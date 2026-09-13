import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { usePlayerStore } from "@/stores/player";
import { toQueueTrack } from "@/player/enrich";
import type { TrackResponse } from "@/player/types";
import QueuePanel from "./QueuePanel.vue";

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

describe("QueuePanel", () => {
  let originalScrollIntoView: typeof Element.prototype.scrollIntoView;

  beforeEach(() => {
    originalScrollIntoView = Element.prototype.scrollIntoView;
    Element.prototype.scrollIntoView =
      vi.fn() as typeof Element.prototype.scrollIntoView;
  });

  afterEach(() => {
    Element.prototype.scrollIntoView = originalScrollIntoView;
    document.body.style.overflow = "";
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
});
