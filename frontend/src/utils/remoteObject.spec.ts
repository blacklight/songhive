import { describe, it, expect } from "vitest";
import {
  remoteObjectToQueueTrack,
  remoteObjectPlayableTracks,
} from "./remoteObject";
import type { RemoteObject } from "@/api/remote";

function remoteObject(overrides: Partial<RemoteObject> = {}): RemoteObject {
  return {
    id: "obj-1",
    canonical_url: "https://remote.example/objects/1",
    object_type: "Audio",
    resource_type: "track",
    domain: "remote.example",
    actor_url: "https://remote.example/actors/u",
    visibility: "public",
    unavailable: false,
    url: "/remote/track/obj-1",
    ...overrides,
  };
}

describe("remoteObjectToQueueTrack", () => {
  it("maps a playable remote track", () => {
    const track = remoteObjectToQueueTrack(
      remoteObject({
        name: "Remote Song",
        audio_url: "https://remote.example/media/song.mp3",
        image_url: "https://remote.example/media/cover.jpg",
        duration: 250,
        artist_name: "Remote Artist",
        album_name: "Remote Album",
      }),
    );
    expect(track).not.toBeNull();
    expect(track?.id).toBe("obj-1");
    expect(track?.title).toBe("Remote Song");
    expect(track?.stream_url).toBe("https://remote.example/media/song.mp3");
    expect(track?.remote).toBe(true);
    expect(track?.artist_name).toBe("Remote Artist");
    expect(track?.album_title).toBe("Remote Album");
    expect(track?.duration).toBe(250);
    expect(track?.artwork_url).toBe("https://remote.example/media/cover.jpg");
    expect(track?.remote_url).toBe("https://remote.example/objects/1");
    expect(track?.remote_object_id).toBe("obj-1");
    expect(track?.remote_domain).toBe("remote.example");
    expect(track?.remote_page_url).toBe("/remote/track/obj-1");
  });

  it("falls back to the parent name for the album title", () => {
    const track = remoteObjectToQueueTrack(
      remoteObject({
        audio_url: "https://remote.example/media/song.mp3",
        parent: remoteObject({ name: "Parent Album" }),
      }),
    );
    expect(track?.album_title).toBe("Parent Album");
  });

  it("returns null without an audio URL", () => {
    expect(remoteObjectToQueueTrack(remoteObject())).toBeNull();
  });
});

describe("remoteObjectPlayableTracks", () => {
  it("returns the object itself when it has audio", () => {
    const obj = remoteObject({ audio_url: "https://remote.example/a.mp3" });
    expect(remoteObjectPlayableTracks(obj)).toHaveLength(1);
  });

  it("collects playable children for containers", () => {
    const album = remoteObject({
      resource_type: "album",
      items: [
        remoteObject({ id: "t1", audio_url: "https://remote.example/1.mp3" }),
        remoteObject({ id: "t2" }),
        remoteObject({ id: "t3", audio_url: "https://remote.example/3.mp3" }),
      ],
    });
    const tracks = remoteObjectPlayableTracks(album);
    expect(tracks.map((t) => t.stream_url)).toEqual([
      "https://remote.example/1.mp3",
      "https://remote.example/3.mp3",
    ]);
  });

  it("returns an empty list when nothing is playable", () => {
    expect(remoteObjectPlayableTracks(remoteObject())).toEqual([]);
  });
});
