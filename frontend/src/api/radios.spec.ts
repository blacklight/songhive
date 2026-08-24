import { describe, it, expect, vi, beforeEach } from "vitest";
import * as client from "./client";
import {
  listRadios,
  createRadio,
  getRadio,
  getRadioTracks,
  type RadioResponse,
  type RadioCreate,
} from "./radios";

vi.mock("./client", () => ({
  apiRequest: vi.fn(),
}));

const apiRequest = vi.mocked(client.apiRequest);

const sampleRadio: RadioResponse = {
  id: "r1",
  name: "Test Radio",
  description: null,
  owner_id: "u1",
  visibility: "public",
};

const sampleTrack = {
  id: "t1",
  title: "Test Track",
  artist_id: "a1",
  album_id: null,
  audio_url: "/api/v1/stream/t1",
};

describe("radios api", () => {
  beforeEach(() => {
    apiRequest.mockReset();
  });

  it("listRadios fetches the radios endpoint", async () => {
    apiRequest.mockResolvedValueOnce([sampleRadio]);
    const result = await listRadios();
    expect(apiRequest).toHaveBeenCalledWith("/radios", { query: undefined });
    expect(result).toEqual([sampleRadio]);
  });

  it("listRadios passes pagination query params", async () => {
    apiRequest.mockResolvedValueOnce([]);
    await listRadios({ limit: 10, offset: 5 });
    expect(apiRequest).toHaveBeenCalledWith("/radios", {
      query: { limit: 10, offset: 5 },
    });
  });

  it("createRadio posts the body and passes visibility as a query param", async () => {
    apiRequest.mockResolvedValueOnce(sampleRadio);
    const body: RadioCreate = { name: "New Radio", description: "A station" };
    await createRadio(body, "public");
    expect(apiRequest).toHaveBeenCalledWith("/radios", {
      method: "POST",
      body,
      query: { visibility: "public" },
    });
  });

  it("createRadio omits visibility from query when not provided", async () => {
    apiRequest.mockResolvedValueOnce(sampleRadio);
    const body: RadioCreate = { name: "New Radio" };
    await createRadio(body);
    expect(apiRequest).toHaveBeenCalledWith("/radios", {
      method: "POST",
      body,
      query: { visibility: undefined },
    });
  });

  it("getRadio fetches by id", async () => {
    apiRequest.mockResolvedValueOnce(sampleRadio);
    const result = await getRadio("r1");
    expect(apiRequest).toHaveBeenCalledWith("/radios/r1");
    expect(result).toEqual(sampleRadio);
  });

  it("getRadioTracks requests the tracks endpoint and casts the unknown response", async () => {
    apiRequest.mockResolvedValueOnce([sampleTrack]);
    const result = await getRadioTracks("r1", { count: 5 });
    expect(apiRequest).toHaveBeenCalledWith("/radios/r1/tracks", {
      query: { count: 5 },
    });
    expect(result).toEqual([sampleTrack]);
  });
});
