import { describe, it, expect } from "vitest";
import {
  isFieldEditable,
  isProviderManaged,
  disabledFieldSet,
} from "./editableFields";

describe("editableFields", () => {
  it("treats null/undefined as fully editable and unmanaged", () => {
    for (const fields of [null, undefined]) {
      expect(isProviderManaged(fields)).toBe(false);
      expect(isFieldEditable(fields, "title")).toBe(true);
      expect(isFieldEditable(fields, "tags")).toBe(true);
    }
  });

  it("restricts edits to the declared capabilities", () => {
    const fields = ["genres", "tags"];
    expect(isProviderManaged(fields)).toBe(true);
    expect(isFieldEditable(fields, "title")).toBe(false);
    expect(isFieldEditable(fields, "release_year")).toBe(false);
    expect(isFieldEditable(fields, "genres")).toBe(true);
    expect(isFieldEditable(fields, "tags")).toBe(true);
  });

  it("keeps visibility editable even when not listed", () => {
    expect(isFieldEditable(["genres"], "visibility")).toBe(true);
    expect(isFieldEditable([], "visibility")).toBe(true);
  });

  it("returns the complement of editable fields", () => {
    const all = ["title", "genres", "tags", "visibility"];
    expect([...disabledFieldSet(["genres", "tags"], all)].sort()).toEqual([
      "title",
    ]);
    expect(disabledFieldSet(null, all).size).toBe(0);
  });
});
