import { describe, it, expect, vi, beforeEach } from "vitest";
import { addHistory, listHistory } from "./history";
import * as client from "./client";

vi.mock("./client", () => ({
  apiRequest: vi.fn(),
}));

const apiRequest = vi.mocked(client.apiRequest);

describe("history api", () => {
  beforeEach(() => {
    apiRequest.mockReset();
  });

  it("addHistory posts to the history endpoint", async () => {
    apiRequest.mockResolvedValueOnce(null);
    await addHistory("track-123");
    expect(apiRequest).toHaveBeenCalledWith("/history/track-123", {
      method: "POST",
    });
  });

  it("addHistory resolves on success", async () => {
    apiRequest.mockResolvedValueOnce(undefined);
    await expect(addHistory("track-123")).resolves.toBeUndefined();
  });

  it("addHistory rejects on a 4xx response", async () => {
    apiRequest.mockRejectedValueOnce(new Error("Bad Request"));
    await expect(addHistory("track-123")).rejects.toThrow("Bad Request");
  });

  it("listHistory fetches with pagination", async () => {
    apiRequest.mockResolvedValueOnce([]);
    await listHistory({ page: 2, pageSize: 10 });
    expect(apiRequest).toHaveBeenCalledWith("/history/", {
      query: { limit: 10, offset: 10 },
    });
  });

  it("listHistory uses defaults", async () => {
    apiRequest.mockResolvedValueOnce([]);
    await listHistory();
    expect(apiRequest).toHaveBeenCalledWith("/history/", {
      query: { limit: 20, offset: 0 },
    });
  });
});
