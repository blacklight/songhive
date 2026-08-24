export const API_PREFIX = "/api/v1";
export const WS_URL = "/ws/events";

export function buildUrl(
  path: string,
  query?: Record<string, string | number | undefined | null>,
): string {
  if (!query) return path;

  const pairs: string[] = [];
  for (const [key, value] of Object.entries(query)) {
    if (value === undefined || value === null) continue;
    pairs.push(
      `${encodeURIComponent(key)}=${encodeURIComponent(String(value))}`,
    );
  }

  if (pairs.length === 0) return path;
  const sep = path.includes("?") ? "&" : "?";
  return `${path}${sep}${pairs.join("&")}`;
}
