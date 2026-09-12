import { getAlbum } from "@/api/albums";
import { getArtist } from "@/api/artists";
import { getFile } from "@/api/files";
import { getLibrary } from "@/api/libraries";
import { getPlaylist } from "@/api/playlists";
import { getRadio } from "@/api/radios";
import { getTrack } from "@/api/tracks";

export interface ItemSummary {
  title: string | null;
  imageUrl: string | null;
}

// Notification rows for the same item share one fetch; results are cached
// for the lifetime of the page so re-renders and paginated rows are free.
const cache = new Map<string, Promise<ItemSummary | null>>();

const fetchers: Record<string, (id: string) => Promise<ItemSummary>> = {
  track: async (id) => {
    const track = await getTrack(id);
    return { title: track.title ?? null, imageUrl: track.image_url ?? null };
  },
  album: async (id) => {
    const album = await getAlbum(id);
    return { title: album.title ?? null, imageUrl: album.cover_url ?? null };
  },
  artist: async (id) => {
    const artist = await getArtist(id);
    return { title: artist.name ?? null, imageUrl: artist.image_url ?? null };
  },
  playlist: async (id) => {
    const playlist = await getPlaylist(id);
    return {
      title: playlist.name ?? null,
      imageUrl: playlist.image_url ?? null,
    };
  },
  library: async (id) => {
    const library = await getLibrary(id);
    return {
      title: library.name ?? null,
      imageUrl: library.image_url ?? null,
    };
  },
  radio: async (id) => {
    const radio = await getRadio(id);
    return { title: radio.name ?? null, imageUrl: null };
  },
  file: async (id) => {
    const file = await getFile(id);
    return {
      title: file.original_filename ?? null,
      imageUrl: file.content_type?.startsWith("image/") ? file.url : null,
    };
  },
};

/**
 * Fetch a display summary (title + cover/art URL) for a shareable item.
 *
 * Unknown item types and fetch failures resolve to ``null`` so callers can
 * fall back to notification payload fields.
 */
export function getItemSummary(
  itemType: string,
  itemId: string,
): Promise<ItemSummary | null> {
  const key = `${itemType}:${itemId}`;
  let pending = cache.get(key);
  if (!pending) {
    const fetcher = fetchers[itemType];
    pending = fetcher
      ? fetcher(itemId).catch(() => null)
      : Promise.resolve(null);
    cache.set(key, pending);
  }
  return pending;
}
