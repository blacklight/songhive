import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { mount, flushPromises, type VueWrapper } from "@vue/test-utils";
import { createRouter, createMemoryHistory } from "vue-router";
import type { ActivityAttachment } from "@/api/activities";
import { downloadTrack, getTrack } from "@/api/tracks";
import { usePlayerStore } from "@/stores/player";
import ActivityAudioPlayer from "./ActivityAudioPlayer.vue";

vi.mock("@/api/tracks", () => ({ getTrack: vi.fn(), downloadTrack: vi.fn() }));
const getTrackMock = vi.mocked(getTrack);
const downloadTrackMock = vi.mocked(downloadTrack);

function makeAttachment(
  overrides: Partial<ActivityAttachment> = {},
): ActivityAttachment {
  return {
    type: "Audio",
    mediaType: "audio/mpeg",
    url: "https://audio.example/song.mp3",
    name: "Some audio",
    ...overrides,
  };
}

function createTestRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: "/tracks/:id", name: "track", component: { template: "<div/>" } },
    ],
  });
}

const mountedWrappers: VueWrapper[] = [];

function mountPlayer(
  attachment: ActivityAttachment,
  remote = false,
): VueWrapper {
  const wrapper = mount(ActivityAudioPlayer, {
    props: { attachment, remote },
    global: { plugins: [createTestRouter()] },
    attachTo: document.body,
  });
  mountedWrappers.push(wrapper);
  return wrapper;
}

function audioEl(wrapper: VueWrapper): HTMLAudioElement {
  return wrapper.find("audio").element as HTMLAudioElement;
}

async function fireMediaEvent(wrapper: VueWrapper, name: string) {
  audioEl(wrapper).dispatchEvent(new Event(name));
  await flushPromises();
}

describe("ActivityAudioPlayer", () => {
  beforeEach(() => {
    getTrackMock.mockReset();
    downloadTrackMock.mockReset();
    document.body.innerHTML = "";
  });

  afterEach(() => {
    for (const wrapper of mountedWrappers.splice(0)) wrapper.unmount();
  });

  it("renders structured metadata and cover art from songhive:* keys", () => {
    const wrapper = mountPlayer(
      makeAttachment({
        "songhive:trackTitle": "Tiger Girl",
        "songhive:artistName": "65daysofstatic",
        "songhive:albumName": "Anthem",
        "songhive:trackUrl": "https://music.example/tracks/t1",
        image: {
          type: "Image",
          mediaType: "image/jpeg",
          url: "https://audio.example/cover.jpg",
        },
        duration: "PT3M42S",
      }),
    );

    expect(wrapper.find(".audio-player__title").text()).toBe("Tiger Girl");
    expect(wrapper.find(".audio-player__subtitle").text()).toBe(
      "65daysofstatic · Anthem",
    );
    const img = wrapper.find<HTMLImageElement>(".audio-player__artwork-img");
    expect(img.exists()).toBe(true);
    expect(img.attributes("src")).toBe("https://audio.example/cover.jpg");
    // The declared duration is shown before media metadata loads.
    expect(wrapper.text()).toContain("3:42");
  });

  it("falls back to the attachment name and a placeholder icon", () => {
    const wrapper = mountPlayer(makeAttachment({ name: "Just a file.mp3" }));

    expect(wrapper.find(".audio-player__title").text()).toBe("Just a file.mp3");
    expect(wrapper.find(".audio-player__subtitle").exists()).toBe(false);
    expect(wrapper.find(".audio-player__artwork-img").exists()).toBe(false);
  });

  it("falls back to the author avatar when the attachment has no image", () => {
    const wrapper = mount(ActivityAudioPlayer, {
      props: {
        attachment: makeAttachment(),
        avatarUrl: "https://example.com/avatar.png",
      },
      global: { plugins: [createTestRouter()] },
      attachTo: document.body,
    });
    mountedWrappers.push(wrapper);

    const img = wrapper.find<HTMLImageElement>(".audio-player__artwork-img");
    expect(img.exists()).toBe(true);
    expect(img.attributes("src")).toBe("https://example.com/avatar.png");
  });

  it("prefers the attachment image over the author avatar", () => {
    const wrapper = mount(ActivityAudioPlayer, {
      props: {
        attachment: makeAttachment({
          image: { type: "Image", url: "https://audio.example/cover.jpg" },
        }),
        avatarUrl: "https://example.com/avatar.png",
      },
      global: { plugins: [createTestRouter()] },
      attachTo: document.body,
    });
    mountedWrappers.push(wrapper);

    expect(wrapper.find(".audio-player__artwork-img").attributes("src")).toBe(
      "https://audio.example/cover.jpg",
    );
  });

  it("links a local track attachment to the internal track page", () => {
    const wrapper = mountPlayer(
      makeAttachment({ "songhive:trackId": "track-9" }),
    );
    expect(
      wrapper.find('a.audio-player__title[href="/tracks/track-9"]').exists(),
    ).toBe(true);
  });

  it("links a remote attachment to the origin track page", () => {
    const wrapper = mountPlayer(
      makeAttachment({
        "songhive:trackId": "remote-track-9",
        "songhive:trackUrl": "https://origin.example/tracks/remote-track-9",
      }),
      true,
    );
    const link = wrapper.find("a.audio-player__title");
    expect(link.attributes("href")).toBe(
      "https://origin.example/tracks/remote-track-9",
    );
    expect(link.attributes("target")).toBe("_blank");
  });

  it("plays and pauses the media element", async () => {
    const wrapper = mountPlayer(makeAttachment());
    const el = audioEl(wrapper);
    el.play = vi.fn().mockResolvedValue(undefined);
    el.pause = vi.fn();

    await wrapper.find('button[aria-label="Play"]').trigger("click");
    expect(el.play).toHaveBeenCalled();

    // The play event flips the control to a pause affordance.
    await fireMediaEvent(wrapper, "play");
    expect(wrapper.find('button[aria-label="Pause"]').exists()).toBe(true);

    el.paused = false;
    await wrapper.find('button[aria-label="Pause"]').trigger("click");
    expect(el.pause).toHaveBeenCalled();
  });

  it("pauses the global player when the preview starts, and vice versa", async () => {
    const store = usePlayerStore();
    const wrapper = mountPlayer(makeAttachment());
    const el = audioEl(wrapper);
    el.pause = vi.fn();

    // Embedded playback pauses the global player.
    store.playAll([
      {
        id: "q1",
        title: "Queued",
        artist_id: "a1",
        artist_name: "A",
        visibility: "public",
        tags: [],
        genres: [],
        is_external: false,
      },
    ]);
    expect(store.isPlaying).toBe(true);
    await fireMediaEvent(wrapper, "play");
    expect(store.isPlaying).toBe(false);

    // The global player resuming pauses the embedded preview.
    el.pause.mockClear();
    el.paused = false;
    store.isPlaying = true;
    await flushPromises();
    expect(el.pause).toHaveBeenCalled();
  });

  it("sends a local track to the player resolved through the API", async () => {
    getTrackMock.mockResolvedValue({
      id: "track-9",
      title: "Tiger Girl",
      artist_id: "artist-1",
      artist: { id: "artist-1", name: "65daysofstatic" },
      album: { id: "album-1", title: "Anthem" },
      image_url: "https://audio.example/cover.jpg",
      duration: 222,
      visibility: "public",
      tags: [],
      genres: [],
      is_external: false,
    } as never);

    const store = usePlayerStore();
    const playTrack = vi.spyOn(store, "playTrack");
    const wrapper = mountPlayer(
      makeAttachment({ "songhive:trackId": "track-9" }),
    );

    await wrapper.find('button[aria-label="Play in player"]').trigger("click");
    await flushPromises();

    expect(getTrackMock).toHaveBeenCalledWith("track-9");
    expect(playTrack).toHaveBeenCalledWith(
      expect.objectContaining({
        id: "track-9",
        title: "Tiger Girl",
        artist_name: "65daysofstatic",
        album_title: "Anthem",
        artwork_url: "https://audio.example/cover.jpg",
      }),
    );
  });

  it("streams remote attachments directly without a local lookup", async () => {
    const store = usePlayerStore();
    const playTrack = vi.spyOn(store, "playTrack");
    const wrapper = mountPlayer(
      makeAttachment({
        "songhive:trackId": "remote-track-9",
        "songhive:trackTitle": "Remote Song",
        "songhive:artistName": "Remote Artist",
      }),
      true,
    );

    await wrapper.find('button[aria-label="Play in player"]').trigger("click");
    await flushPromises();

    expect(getTrackMock).not.toHaveBeenCalled();
    expect(playTrack).toHaveBeenCalledWith(
      expect.objectContaining({
        title: "Remote Song",
        artist_name: "Remote Artist",
        stream_url: "https://audio.example/song.mp3",
        remote: true,
      }),
    );
  });

  it("falls back to a remote stream when the local track is gone", async () => {
    getTrackMock.mockRejectedValue(new Error("404"));
    const store = usePlayerStore();
    const playTrack = vi.spyOn(store, "playTrack");
    const wrapper = mountPlayer(
      makeAttachment({ "songhive:trackId": "deleted-track" }),
    );

    await wrapper.find('button[aria-label="Play in player"]').trigger("click");
    await flushPromises();

    expect(playTrack).toHaveBeenCalledWith(
      expect.objectContaining({
        stream_url: "https://audio.example/song.mp3",
        remote: true,
      }),
    );
  });

  it("enqueues the resolved track", async () => {
    const store = usePlayerStore();
    const wrapper = mountPlayer(makeAttachment({ name: "Queued song" }));

    await wrapper.find('button[aria-label="Add to queue"]').trigger("click");
    await flushPromises();

    expect(store.queue).toHaveLength(1);
    expect(store.queue[0]).toMatchObject({
      title: "Queued song",
      stream_url: "https://audio.example/song.mp3",
      remote: true,
    });
  });

  it("downloads the attachment media URL", async () => {
    downloadTrackMock.mockResolvedValue(undefined);
    const wrapper = mountPlayer(
      makeAttachment({ "songhive:trackTitle": "Tiger Girl" }),
    );

    await wrapper.find('button[aria-label="Download"]').trigger("click");
    await flushPromises();

    expect(downloadTrackMock).toHaveBeenCalledWith(
      "https://audio.example/song.mp3",
      "Tiger Girl",
    );
  });

  it("pauses other embedded players when one starts", async () => {
    const first = mountPlayer(makeAttachment());
    const second = mountPlayer(makeAttachment());
    // jsdom shares one pause() mock on HTMLMediaElement.prototype, so assign
    // per-element mocks to attribute calls correctly.
    const firstPause = (audioEl(first).pause = vi.fn());
    const secondPause = (audioEl(second).pause = vi.fn());

    await fireMediaEvent(first, "play");
    expect(secondPause).toHaveBeenCalled();
    expect(firstPause).not.toHaveBeenCalled();
  });
});
