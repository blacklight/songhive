import { describe, it, expect } from "vitest";
import { buildUrl } from "./config";

describe("buildUrl", () => {
  it("returns the path unchanged when there is no query", () => {
    expect(buildUrl("/api/v1/tracks")).toBe("/api/v1/tracks");
  });

  it("serializes string and number query values", () => {
    const url = buildUrl("/api/v1/tracks", {
      q: "hello",
      limit: 20,
      offset: 0,
    });
    expect(url).toBe("/api/v1/tracks?q=hello&limit=20&offset=0");
  });

  it("serializes booleans as 'true' or 'false' and skips undefined/null", () => {
    const url = buildUrl("/api/v1/libraries/lib1/tracks", {
      force: true,
      enrich: false,
      visibility: undefined,
      skip: null,
    });
    expect(url).toBe("/api/v1/libraries/lib1/tracks?force=true&enrich=false");
  });

  it("appends to an existing query string", () => {
    expect(buildUrl("/api/v1/tracks?foo=bar", { q: "baz" })).toBe(
      "/api/v1/tracks?foo=bar&q=baz",
    );
  });
});
