import { describe, it, expect, vi, beforeEach } from "vitest";
import { setTokenProvider } from "@/api/client";
import { useSearchSections, SEARCH_ENTITIES } from "./useSearchSections";

function makeResponse(body: unknown, headers?: Record<string, string>) {
  return {
    status: 200,
    ok: true,
    text: () => Promise.resolve(JSON.stringify(body)),
    headers: new Headers(headers),
  };
}

function makeFetch(tracks = 1, albums = 1) {
  return vi.fn().mockImplementation((url: string) => {
    const offset = url.match(/offset=(\d+)/)?.[1] ?? "0";
    if (url.includes("/tracks/")) {
      return Promise.resolve(
        makeResponse(
          [
            {
              id: "track-1",
              title: "Waiting Room",
              artist_id: "artist-1",
              album_id: "album-1",
            },
          ],
          {
            "X-Total-Count": String(tracks),
            "X-List-Offset": offset,
          },
        ),
      );
    }
    if (url.includes("/albums/")) {
      return Promise.resolve(
        makeResponse([{ id: "album-1", title: "Repeater" }], {
          "X-Total-Count": String(albums),
          "X-List-Offset": offset,
        }),
      );
    }
    return Promise.resolve(
      makeResponse([], {
        "X-Total-Count": "0",
        "X-List-Offset": offset,
      }),
    );
  });
}

describe("useSearchSections", () => {
  beforeEach(() => {
    setTokenProvider(() => null);
  });

  it("searches across all active entity sections", async () => {
    vi.stubGlobal("fetch", makeFetch(1, 1));
    const { searchAll, sections } = useSearchSections();

    await searchAll("rep", ["tracks", "albums"]);

    expect(sections.tracks.items).toHaveLength(1);
    expect(sections.tracks.total).toBe(1);
    expect(sections.albums.items).toHaveLength(1);
    expect(sections.albums.total).toBe(1);
    expect(sections.artists.items).toHaveLength(0);
  });

  it("paginates a single section independently", async () => {
    vi.stubGlobal("fetch", makeFetch(5, 1));
    const { searchAll, setPage, sections } = useSearchSections();

    await searchAll("rep", ["tracks"]);
    expect(sections.tracks.page).toBe(0);

    await setPage("tracks", 1);
    expect(sections.tracks.page).toBe(1);
    expect(sections.tracks.offset).toBe(10);
  });

  it("sorts a section and resets pagination", async () => {
    vi.stubGlobal("fetch", makeFetch(2, 1));
    const { searchAll, setSort, setPage, sections } = useSearchSections();

    await searchAll("rep", ["tracks"]);
    await setPage("tracks", 1);
    await setSort("tracks", "artist_name", "desc");

    expect(sections.tracks.sortBy).toBe("artist_name");
    expect(sections.tracks.sortDir).toBe("desc");
    expect(sections.tracks.offset).toBe(0);
    expect(sections.tracks.page).toBe(0);
  });

  it("ignores unknown sort fields", async () => {
    vi.stubGlobal("fetch", makeFetch(2, 1));
    const { searchAll, setSort, sections } = useSearchSections();

    await searchAll("rep", ["tracks"]);
    await setSort("tracks", "invalid_field", "desc");

    expect(sections.tracks.sortBy).toBe("title");
  });

  it("resets state for empty queries", async () => {
    vi.stubGlobal("fetch", makeFetch(1, 1));
    const { searchAll, sections } = useSearchSections();

    await searchAll("rep", ["tracks"]);
    await searchAll("   ", ["tracks"]);

    expect(sections.tracks.items).toHaveLength(0);
    expect(sections.tracks.total).toBe(0);
  });

  it("exposes the canonical search entity order", () => {
    expect(SEARCH_ENTITIES).toEqual([
      "tracks",
      "albums",
      "artists",
      "playlists",
      "libraries",
      "users",
      "tags",
      "genres",
    ]);
  });
});
