import { describe, it, expect } from "vitest";
import { parseNumber, toVisibility, formatBytes } from "./entity";

describe("parseNumber", () => {
  it("parses a numeric string", () => {
    expect(parseNumber("42")).toBe(42);
  });

  it("returns null for an empty string", () => {
    expect(parseNumber("")).toBeNull();
  });

  it("returns null for a non-numeric string", () => {
    expect(parseNumber("abc")).toBeNull();
  });
});

describe("toVisibility", () => {
  it("returns a valid visibility value", () => {
    expect(toVisibility("public")).toBe("public");
    expect(toVisibility("local")).toBe("local");
    expect(toVisibility("private")).toBe("private");
  });

  it("falls back to private for invalid or missing values", () => {
    expect(toVisibility("unknown")).toBe("private");
    expect(toVisibility(null)).toBe("private");
    expect(toVisibility(undefined)).toBe("private");
  });
});

describe("formatBytes", () => {
  it('renders bytes as "B"', () => {
    expect(formatBytes(0)).toBe("0 B");
    expect(formatBytes(1)).toBe("1 B");
    expect(formatBytes(512)).toBe("512 B");
  });

  it("converts to larger units", () => {
    expect(formatBytes(1536)).toBe("1.5 KB");
    expect(formatBytes(1024 * 1024)).toBe("1 MB");
    expect(formatBytes(1572864)).toBe("1.5 MB");
    expect(formatBytes(1024 * 1024 * 1024)).toBe("1 GB");
  });

  it("respects the decimals option", () => {
    expect(formatBytes(1572864, 2)).toBe("1.50 MB");
  });

  it("formats using the provided locale", () => {
    expect(formatBytes(1572864, 2, "de-DE")).toBe("1,50 MB");
  });

  it("handles negative and NaN inputs", () => {
    expect(formatBytes(-1)).toBe("0 B");
    expect(formatBytes(Number.NaN)).toBe("0 B");
  });
});
