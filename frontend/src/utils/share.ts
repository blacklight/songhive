import { getArtist } from "@/api/artists";
import { getAlbum } from "@/api/albums";
import type { ShareItemType } from "@/api/shares";

const SHARE_ITEM_TYPES: ShareItemType[] = [
  "track",
  "album",
  "playlist",
  "library",
];

export interface SharePreview {
  type: ShareItemType | "unknown";
  id?: string;
  title: string;
  description?: string;
  artistId?: string;
  albumId?: string;
  artistName?: string;
  albumTitle?: string;
  duration?: number;
  releaseYear?: number;
  ownerId?: string | null;
  visibility?: string;
  coverUrl?: string;
}

function isShareItemType(value: unknown): value is ShareItemType {
  return typeof value === "string" && SHARE_ITEM_TYPES.includes(value as never);
}

function resolveType(value: unknown): ShareItemType | "unknown" | null {
  if (typeof value !== "string" || !value.trim()) return null;
  return isShareItemType(value) ? value : "unknown";
}

function getString(
  item: Record<string, unknown>,
  key: string,
): string | undefined {
  const value = item[key];
  return typeof value === "string" ? value : undefined;
}

function getNumber(
  item: Record<string, unknown>,
  key: string,
): number | undefined {
  const value = item[key];
  if (typeof value === "number") return value;
  if (typeof value === "string") {
    const parsed = Number(value);
    if (!Number.isNaN(parsed)) return parsed;
  }
  return undefined;
}

function inferItemType(
  item: Record<string, unknown>,
): ShareItemType | "unknown" | null {
  if (
    typeof item.audio_url === "string" ||
    typeof item.track_number === "number" ||
    typeof item.duration === "number"
  ) {
    return "track";
  }

  if (
    typeof item.release_year === "number" ||
    typeof item.cover_url === "string" ||
    typeof item.musicbrainz_id === "string"
  ) {
    return "album";
  }

  if (typeof item.name === "string") {
    // Playlists and libraries have the same public shape, so we cannot
    // distinguish them from fields alone. The caller can still render a
    // generic preview with a name.
    return "unknown";
  }

  return null;
}

function parseItem(
  item: Record<string, unknown>,
  type: ShareItemType | "unknown",
): SharePreview | null {
  const id = getString(item, "id");
  const title = getString(item, "title") ?? getString(item, "name");

  if (!id && !title) {
    return null;
  }

  return {
    type,
    id,
    title: title ?? "",
    description: getString(item, "description"),
    artistId: getString(item, "artist_id"),
    albumId: getString(item, "album_id"),
    duration: getNumber(item, "duration"),
    releaseYear: getNumber(item, "release_year"),
    ownerId: getString(item, "owner_id") ?? null,
    visibility: getString(item, "visibility"),
    coverUrl: getString(item, "cover_url"),
  };
}

/**
 * Inspect a share-resolution payload at runtime and return a preview that can
 * be rendered without making assumptions about the backend's response shape.
 *
 * The backend may return `{ item_type, item }`, a nested entity, or a plain
 * entity after following a redirect. This function handles all of those cases
 * defensively.
 */
export function parseSharePayload(payload: unknown): SharePreview | null {
  if (!payload || typeof payload !== "object") {
    return null;
  }

  const top = payload as Record<string, unknown>;

  if (top.item && typeof top.item === "object" && !Array.isArray(top.item)) {
    const item = top.item as Record<string, unknown>;
    const type = resolveType(top.item_type) ?? inferItemType(item) ?? "unknown";
    return parseItem(item, type);
  }

  const type = resolveType(top.item_type) ?? inferItemType(top) ?? "unknown";
  return parseItem(top, type);
}

/**
 * Best-effort enrichment of a share preview with artist/album display names.
 * This is optional and fails silently so the preview still renders if the
 * related entities are not accessible to the current user.
 */
export async function enrichSharePreview(
  preview: SharePreview,
): Promise<SharePreview> {
  if (preview.type === "track") {
    if (preview.artistId) {
      try {
        const artist = await getArtist(preview.artistId);
        preview.artistName = artist.name;
      } catch {
        // Leave artistName undefined; the public page omits the artist
        // rather than leaking a raw UUID.
      }
    }

    if (preview.albumId) {
      try {
        const album = await getAlbum(preview.albumId);
        preview.albumTitle = album.title;
      } catch {
        // Leave albumTitle undefined.
      }
    }
  }

  if (preview.type === "album" && preview.artistId) {
    try {
      const artist = await getArtist(preview.artistId);
      preview.artistName = artist.name;
    } catch {
      // Leave artistName undefined.
    }
  }

  return preview;
}
