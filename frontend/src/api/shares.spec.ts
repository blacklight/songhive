import { describe, it, expect, vi, beforeEach } from "vitest";
import * as client from "./client";
import {
  listShareGrants,
  createShareGrant,
  deleteShareGrant,
  listShareUrls,
  createShareUrl,
  deleteShareUrl,
  resolveShareUrl,
  type ShareGrantCreate,
  type ShareGrantResponse,
  type ShareTokenCreate,
  type ShareTokenCreated,
  type ShareTokenResponse,
} from "./shares";

vi.mock("./client", () => ({
  apiRequest: vi.fn(),
}));

const apiRequest = vi.mocked(client.apiRequest);

const sampleGrant: ShareGrantResponse = {
  id: "sg1",
  item_type: "track",
  item_id: "t1",
  user_id: "u2",
  created_at: "2026-01-01T00:00:00Z",
};

const sampleToken: ShareTokenResponse = {
  id: "st1",
  expires_at: null,
  revoked_at: null,
  created_at: "2026-01-01T00:00:00Z",
};

const sampleCreated: ShareTokenCreated = {
  id: "st1",
  url: "http://localhost/share/abc",
  token: "abc",
  expires_at: null,
};

describe("shares api", () => {
  beforeEach(() => {
    apiRequest.mockReset();
  });

  it("listShareGrants requires item_type and item_id", async () => {
    apiRequest.mockResolvedValueOnce([sampleGrant]);
    await listShareGrants({ item_type: "track", item_id: "t1", limit: 10 });
    expect(apiRequest).toHaveBeenCalledWith("/shares", {
      query: { item_type: "track", item_id: "t1", limit: 10 },
    });
  });

  it("createShareGrant posts the grant body", async () => {
    apiRequest.mockResolvedValueOnce(sampleGrant);
    const body: ShareGrantCreate = {
      item_type: "album",
      item_id: "al1",
      user_id: "u2",
    };
    await createShareGrant(body);
    expect(apiRequest).toHaveBeenCalledWith("/shares", {
      method: "POST",
      body,
    });
  });

  it("deleteShareGrant sends a DELETE request", async () => {
    apiRequest.mockResolvedValueOnce(undefined);
    await deleteShareGrant("sg1");
    expect(apiRequest).toHaveBeenCalledWith("/shares/sg1", {
      method: "DELETE",
    });
  });

  it("listShareUrls requires item_type and item_id", async () => {
    apiRequest.mockResolvedValueOnce([sampleToken]);
    await listShareUrls({ item_type: "playlist", item_id: "p1" });
    expect(apiRequest).toHaveBeenCalledWith("/share-urls", {
      query: { item_type: "playlist", item_id: "p1" },
    });
  });

  it("createShareUrl posts the token body", async () => {
    apiRequest.mockResolvedValueOnce(sampleCreated);
    const body: ShareTokenCreate = {
      item_type: "library",
      item_id: "lib1",
      expires_at: null,
    };
    await createShareUrl(body);
    expect(apiRequest).toHaveBeenCalledWith("/share-urls", {
      method: "POST",
      body,
    });
  });

  it("deleteShareUrl sends a DELETE request", async () => {
    apiRequest.mockResolvedValueOnce(undefined);
    await deleteShareUrl("st1");
    expect(apiRequest).toHaveBeenCalledWith("/share-urls/st1", {
      method: "DELETE",
    });
  });

  it("resolveShareUrl fetches the public share route without auth", async () => {
    apiRequest.mockResolvedValueOnce({ item_type: "track", item_id: "t1" });
    const result = await resolveShareUrl("abc");
    expect(apiRequest).toHaveBeenCalledWith("/share/abc", {
      skipAuth: true,
    });
    expect(result).toEqual({ item_type: "track", item_id: "t1" });
  });
});
