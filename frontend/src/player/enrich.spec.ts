import { describe, it, expect } from "vitest";
import type { TrackResponse } from "./types";
import { toQueueTrack } from "./enrich";

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

describe("toQueueTrack", () => {
  it("uses the track's image_url as artwork_url when present", () => {
    const track = makeTrack({
      image_url: "https://example.com/track-image.jpg",
    });

    const queueTrack = toQueueTrack(track);
    expect(queueTrack.artwork_url).toBe("https://example.com/track-image.jpg");
  });

  it("prefers track image_url over the enrich artwork_url", () => {
    const track = makeTrack({
      image_url: "https://example.com/track-image.jpg",
    });

    const queueTrack = toQueueTrack(track, {
      artwork_url: "https://example.com/enrich-art.jpg",
    });
    expect(queueTrack.artwork_url).toBe("https://example.com/track-image.jpg");
  });

  it("falls back to the enrich artwork_url when the track has no image_url", () => {
    const track = makeTrack({ image_url: null });

    const queueTrack = toQueueTrack(track, {
      artwork_url: "https://example.com/enrich-art.jpg",
    });
    expect(queueTrack.artwork_url).toBe("https://example.com/enrich-art.jpg");
  });

  it("leaves artwork_url undefined when no image is available", () => {
    const track = makeTrack({ image_url: null });

    const queueTrack = toQueueTrack(track);
    expect(queueTrack.artwork_url).toBeUndefined();
  });
});
