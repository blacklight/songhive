import { describe, it, expect } from "vitest";
import {
  activityFeedUrls,
  artistFeedUrls,
  genreFeedUrls,
  libraryFeedUrls,
  playlistFeedUrls,
  tagFeedUrls,
  userFeedUrls,
} from "./feeds";

describe("feed URL builders", () => {
  it("builds RSS and Atom variants for each feed", () => {
    expect(userFeedUrls("alice")).toEqual({
      rss: "/feeds/users/alice.rss",
      atom: "/feeds/users/alice.atom",
    });
    expect(artistFeedUrls("a1")).toEqual({
      rss: "/feeds/artists/a1.rss",
      atom: "/feeds/artists/a1.atom",
    });
    expect(playlistFeedUrls("p1")).toEqual({
      rss: "/feeds/playlists/p1.rss",
      atom: "/feeds/playlists/p1.atom",
    });
    expect(libraryFeedUrls("l1")).toEqual({
      rss: "/feeds/libraries/l1.rss",
      atom: "/feeds/libraries/l1.atom",
    });
    expect(tagFeedUrls("rock")).toEqual({
      rss: "/feeds/tags/rock.rss",
      atom: "/feeds/tags/rock.atom",
    });
    expect(genreFeedUrls("jazz")).toEqual({
      rss: "/feeds/genres/jazz.rss",
      atom: "/feeds/genres/jazz.atom",
    });
  });

  it("builds activities feed URLs under the entity collection", () => {
    expect(activityFeedUrls("tracks", "t1")).toEqual({
      rss: "/feeds/tracks/t1/activities.rss",
      atom: "/feeds/tracks/t1/activities.atom",
    });
  });

  it("percent-encodes names that are not URL-safe", () => {
    expect(tagFeedUrls("drum & bass").rss).toBe(
      "/feeds/tags/drum%20%26%20bass.rss",
    );
    expect(genreFeedUrls("hip hop").atom).toBe("/feeds/genres/hip%20hop.atom");
  });
});
