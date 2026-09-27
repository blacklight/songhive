/**
 * Display names for external-library provider types. These are proper nouns,
 * so they live in code rather than per-locale i18n keys.
 */
const PROVIDER_LABELS: Record<string, string> = {
  tidal: "TIDAL",
  jellyfin: "Jellyfin",
  gdrive: "Google Drive",
  local: "Local files",
};

export function providerDisplayName(
  providerType: string | null | undefined,
): string {
  const key = providerType?.toLowerCase() ?? "";
  if (!key) return "";
  return PROVIDER_LABELS[key] ?? key[0].toUpperCase() + key.slice(1);
}
