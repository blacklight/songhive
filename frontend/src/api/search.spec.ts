import { describe, it, expect, vi, beforeEach } from "vitest";
import { searchPreview } from "./search";
import { setTokenProvider } from "./client";

const mockResponse = {
  query: "rep",
  sections: [
    {
      entity: "tracks",
      total: 1,
      items: [
        {
          type: "track",
          id: "track-1",
          name: "Repeater",
          title: "Repeater",
          subtitle: "Fugazi",
          url: "/tracks/track-1",
          image_url: null,
        },
      ],
    },
  ],
};

describe("searchPreview", () => {
  beforeEach(() => {
    setTokenProvider(() => null);
  });

  it("calls the aggregate search endpoint with query and limit", async () => {
    const fetch = vi.fn().mockResolvedValue({
      status: 200,
      ok: true,
      text: () => Promise.resolve(JSON.stringify(mockResponse)),
    });
    vi.stubGlobal("fetch", fetch);

    const result = await searchPreview("rep", undefined, 5);

    expect(result).toEqual(mockResponse);
    const call = fetch.mock.calls[0];
    expect(call[0]).toContain("/api/v1/search/");
    expect(call[0]).toContain("q=rep");
    expect(call[0]).toContain("limit=5");
    expect(call[0]).not.toContain("entities");
  });

  it("includes the entities allowlist when provided", async () => {
    const fetch = vi.fn().mockResolvedValue({
      status: 200,
      ok: true,
      text: () => Promise.resolve(JSON.stringify(mockResponse)),
    });
    vi.stubGlobal("fetch", fetch);

    await searchPreview("rep", ["tracks", "albums"], 5);

    expect(fetch.mock.calls[0][0]).toContain("entities=tracks%2Calbums");
  });
});
