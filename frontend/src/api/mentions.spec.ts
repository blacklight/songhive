import { describe, it, expect, vi, beforeEach } from "vitest";
import * as client from "./client";
import {
  listMentions,
  MENTION_SOURCES,
  type MentionResponse,
} from "./mentions";

vi.mock("./client", () => ({
  apiRequest: vi.fn(),
  apiRequestWithHeaders: vi.fn(),
}));

const apiRequestWithHeaders = vi.mocked(client.apiRequestWithHeaders);

const sampleMention: MentionResponse = {
  id: "m1",
  source: "activitypub",
  actor_url: "https://example.com/users/alice",
  source_url: "https://example.com/objects/1",
  activity_id: null,
  visibility: "public",
  payload: { actor_name: "alice" },
  created_at: "2026-01-01T00:00:00Z",
};

function headersWithTotal(total: number): Headers {
  return new Headers({ "X-Total-Count": String(total) });
}

describe("mentions api", () => {
  beforeEach(() => {
    apiRequestWithHeaders.mockReset();
  });

  it("exposes the three mention sources", () => {
    expect(MENTION_SOURCES).toEqual(["local", "activitypub", "webmention"]);
  });

  it("listMentions fetches the endpoint and reads X-Total-Count", async () => {
    apiRequestWithHeaders.mockResolvedValueOnce({
      body: [sampleMention],
      headers: headersWithTotal(42),
    });
    const result = await listMentions({ limit: 10, offset: 5 });
    expect(apiRequestWithHeaders).toHaveBeenCalledWith("/mentions/", {
      query: {
        limit: 10,
        offset: 5,
        source: undefined,
        visibility: undefined,
      },
    });
    expect(result).toEqual({ items: [sampleMention], total: 42 });
  });

  it("listMentions passes source and private filters", async () => {
    apiRequestWithHeaders.mockResolvedValueOnce({
      body: [],
      headers: headersWithTotal(0),
    });
    await listMentions({ source: "local,webmention", visibility: "private" });
    expect(apiRequestWithHeaders).toHaveBeenCalledWith("/mentions/", {
      query: {
        limit: undefined,
        offset: undefined,
        source: "local,webmention",
        visibility: "private",
      },
    });
  });

  it("listMentions drops the visibility param unless it is private", async () => {
    apiRequestWithHeaders.mockResolvedValueOnce({
      body: [],
      headers: headersWithTotal(0),
    });
    await listMentions({ visibility: "all" });
    expect(apiRequestWithHeaders).toHaveBeenCalledWith("/mentions/", {
      query: {
        limit: undefined,
        offset: undefined,
        source: undefined,
        visibility: undefined,
      },
    });
  });

  it("listMentions falls back to body length without the header", async () => {
    apiRequestWithHeaders.mockResolvedValueOnce({
      body: [sampleMention],
      headers: new Headers(),
    });
    const result = await listMentions();
    expect(result.total).toBe(1);
  });
});
