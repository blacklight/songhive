import type { Visibility } from "@/api/libraries";

export function parseNumber(value: string): number | null {
  const trimmed = value.trim();
  if (!trimmed) return null;
  const parsed = Number(trimmed);
  return Number.isNaN(parsed) ? null : parsed;
}

function isVisibility(value: string): value is Visibility {
  return value === "private" || value === "local" || value === "public";
}

export function toVisibility(value: string | null | undefined): Visibility {
  if (value && isVisibility(value)) {
    return value;
  }
  return "private";
}
