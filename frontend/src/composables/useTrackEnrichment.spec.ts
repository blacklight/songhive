import { describe, it, expect, beforeEach, vi } from "vitest";
import { defineComponent, h, isRef, ref, nextTick, type MaybeRef } from "vue";
import { mount, flushPromises } from "@vue/test-utils";
import { useTrackEnrichment } from "./useTrackEnrichment";
import * as artistsApi from "@/api/artists";
import * as albumsApi from "@/api/albums";
import type { TrackResponse } from "@/api/tracks";
import type { ArtistResponse } from "@/api/artists";
import type { AlbumResponse } from "@/api/albums";

vi.mock("@/api/artists", () => ({
  getArtist: vi.fn(),
}));

vi.mock("@/api/albums", () => ({
  getAlbum: vi.fn(),
}));

function createTrack(
  id: string,
  title: string,
  artistId?: string,
  albumId?: string,
): TrackResponse {
  return {
    id,
    title,
    artist_id: artistId ?? "",
    album_id: albumId ?? null,
    track_number: 1,
    disc_number: null,
    duration: 185,
    genre: null,
    audio_url: "https://example.com/audio.mp3",
    visibility: "public",
    owner_id: "user-1",
  };
}

function createArtist(id: string, name: string): ArtistResponse {
  return {
    id,
    name,
    musicbrainz_id: null,
    bio: null,
    image_file_id: null,
    image_url: null,
  };
}

function createAlbum(
  id: string,
  title: string,
  cover?: string | null,
): AlbumResponse {
  return {
    id,
    title,
    artist_id: "artist-1",
    musicbrainz_id: null,
    release_year: 2024,
    cover_url: cover ?? null,
    description: null,
    owner_id: "user-1",
    visibility: "public",
  };
}

function createEnrichment(
  tracks: MaybeRef<TrackResponse[]>,
  fallback?: string,
) {
  const tracksRef = isRef(tracks) ? tracks : ref(tracks);
  return mount(
    defineComponent({
      setup() {
        return useTrackEnrichment(tracksRef, fallback);
      },
      render: () => h("div"),
    }),
  );
}

describe("useTrackEnrichment", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(artistsApi.getArtist).mockResolvedValue(
      createArtist("artist-1", "The Larks"),
    );
    vi.mocked(albumsApi.getAlbum).mockResolvedValue(
      createAlbum("album-1", "Meadowland", "https://example.com/cover.jpg"),
    );
  });

  it("fetches artist and album details and builds an enrich map", async () => {
    const wrapper = createEnrichment([
      createTrack("track-1", "Song One", "artist-1", "album-1"),
    ]);

    await flushPromises();

    expect(artistsApi.getArtist).toHaveBeenCalledWith("artist-1");
    expect(albumsApi.getAlbum).toHaveBeenCalledWith("album-1");
    expect(wrapper.vm.enrich.get("track-1")).toEqual({
      artist_name: "The Larks",
      album_title: "Meadowland",
      artwork_url: "https://example.com/cover.jpg",
    });
  });

  it("falls back to the provided name when an artist is missing", async () => {
    const wrapper = createEnrichment(
      [createTrack("track-1", "Song One", undefined, "album-1")],
      "Library",
    );

    await flushPromises();

    expect(wrapper.vm.enrich.get("track-1")).toMatchObject({
      artist_name: "Library",
      album_title: "Meadowland",
    });
  });

  it("reuses cached artist and album lookups", async () => {
    createEnrichment([
      createTrack("track-1", "Song One", "artist-1", "album-1"),
      createTrack("track-2", "Song Two", "artist-1", "album-1"),
    ]);

    await flushPromises();

    expect(artistsApi.getArtist).toHaveBeenCalledTimes(1);
    expect(albumsApi.getAlbum).toHaveBeenCalledTimes(1);
  });

  it("does not fetch when there are no tracks", async () => {
    createEnrichment([]);

    await flushPromises();

    expect(artistsApi.getArtist).not.toHaveBeenCalled();
    expect(albumsApi.getAlbum).not.toHaveBeenCalled();
  });

  it("gracefully handles missing artist/album fetches", async () => {
    vi.mocked(artistsApi.getArtist).mockRejectedValue(new Error("not found"));
    vi.mocked(albumsApi.getAlbum).mockRejectedValue(new Error("not found"));

    const wrapper = createEnrichment([
      createTrack("track-1", "Song One", "artist-1", "album-1"),
    ]);

    await flushPromises();

    expect(wrapper.vm.enrich.get("track-1")).toEqual({
      artist_name: "",
      album_title: undefined,
      artwork_url: undefined,
    });
  });

  it("updates the map when tracks change", async () => {
    const tracks = ref<TrackResponse[]>([
      createTrack("track-1", "Song One", "artist-1"),
    ]);

    const wrapper = createEnrichment(tracks);

    await flushPromises();

    expect(wrapper.vm.enrich.get("track-1")).toMatchObject({
      artist_name: "The Larks",
    });

    tracks.value = [createTrack("track-2", "Song Two", "artist-2")];
    vi.mocked(artistsApi.getArtist).mockResolvedValue(
      createArtist("artist-2", "Nightingale"),
    );

    await nextTick();
    await flushPromises();

    expect(artistsApi.getArtist).toHaveBeenCalledWith("artist-2");
    expect(wrapper.vm.enrich.get("track-2")).toMatchObject({
      artist_name: "Nightingale",
    });
  });
});
