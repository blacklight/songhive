import { describe, it, expect } from "vitest";
import {
  INDEX_LETTERS,
  OTHER_INDEX_LETTER,
  indexLetterOf,
} from "./indexLetters";

describe("INDEX_LETTERS", () => {
  it("contains '#' followed by A–Z", () => {
    expect(INDEX_LETTERS).toHaveLength(27);
    expect(INDEX_LETTERS[0]).toBe("#");
    expect(INDEX_LETTERS[1]).toBe("A");
    expect(INDEX_LETTERS[26]).toBe("Z");
  });
});

describe("indexLetterOf", () => {
  it("uppercases the first letter", () => {
    expect(indexLetterOf("apple")).toBe("A");
    expect(indexLetterOf("Zebra")).toBe("Z");
  });

  it("skips leading whitespace", () => {
    expect(indexLetterOf("  Madonna")).toBe("M");
  });

  it("folds accented letters onto their base", () => {
    expect(indexLetterOf("Éliane")).toBe("E");
    expect(indexLetterOf("álbum")).toBe("A");
    expect(indexLetterOf("Ümlaut")).toBe("U");
  });

  it("buckets digits, symbols and non-Latin scripts under '#'", () => {
    expect(indexLetterOf("10cc")).toBe(OTHER_INDEX_LETTER);
    expect(indexLetterOf("!important")).toBe(OTHER_INDEX_LETTER);
    expect(indexLetterOf("Владимир")).toBe(OTHER_INDEX_LETTER);
    expect(indexLetterOf("あいう")).toBe(OTHER_INDEX_LETTER);
    expect(indexLetterOf("موسيقى")).toBe(OTHER_INDEX_LETTER);
  });

  it("buckets empty and missing keys under '#'", () => {
    expect(indexLetterOf("")).toBe(OTHER_INDEX_LETTER);
    expect(indexLetterOf("   ")).toBe(OTHER_INDEX_LETTER);
    expect(indexLetterOf(null)).toBe(OTHER_INDEX_LETTER);
    expect(indexLetterOf(undefined)).toBe(OTHER_INDEX_LETTER);
  });
});
