import { computed, shallowRef, watch, toValue, type MaybeRef } from "vue";
import { getArtist, type ArtistResponse } from "@/api/artists";
import { getAlbum, type AlbumResponse } from "@/api/albums";
import type { TrackResponse } from "@/api/tracks";
import type { TrackEnrich } from "@/player/enrich";

export function useTrackEnrichment(
  tracks: MaybeRef<TrackResponse[]>,
  fallbackArtistName?: MaybeRef<string | null | undefined>,
) {
  const artistMap = shallowRef<Map<string, ArtistResponse | null>>(new Map());
  const albumMap = shallowRef<Map<string, AlbumResponse | null>>(new Map());

  const enrich = computed<Map<string, TrackEnrich>>(() => {
    const map = new Map<string, TrackEnrich>();
    for (const track of toValue(tracks)) {
      const artist = track.artist_id
        ? artistMap.value.get(track.artist_id)
        : null;
      const album = track.album_id ? albumMap.value.get(track.album_id) : null;
      map.set(track.id, {
        artist_name: artist?.name ?? toValue(fallbackArtistName) ?? "",
        album_title: album?.title,
        artwork_url: album?.cover_url ?? undefined,
      });
    }
    return map;
  });

  async function loadDetails() {
    const trackList = toValue(tracks);
    if (trackList.length === 0) return;

    const missingArtistIds: string[] = [];
    const missingAlbumIds: string[] = [];
    const seenArtists = new Set<string>();
    const seenAlbums = new Set<string>();

    for (const track of trackList) {
      if (
        track.artist_id &&
        !artistMap.value.has(track.artist_id) &&
        !seenArtists.has(track.artist_id)
      ) {
        seenArtists.add(track.artist_id);
        missingArtistIds.push(track.artist_id);
      }
      if (
        track.album_id &&
        !albumMap.value.has(track.album_id) &&
        !seenAlbums.has(track.album_id)
      ) {
        seenAlbums.add(track.album_id);
        missingAlbumIds.push(track.album_id);
      }
    }

    if (missingArtistIds.length === 0 && missingAlbumIds.length === 0) return;

    const [artists, albums] = await Promise.all([
      Promise.all(
        missingArtistIds.map((id) => getArtist(id).catch(() => null)),
      ),
      Promise.all(missingAlbumIds.map((id) => getAlbum(id).catch(() => null))),
    ]);

    const newArtistMap = new Map(artistMap.value);
    const newAlbumMap = new Map(albumMap.value);
    for (let i = 0; i < missingArtistIds.length; i++) {
      newArtistMap.set(missingArtistIds[i], artists[i]);
    }
    for (let i = 0; i < missingAlbumIds.length; i++) {
      newAlbumMap.set(missingAlbumIds[i], albums[i]);
    }
    artistMap.value = newArtistMap;
    albumMap.value = newAlbumMap;
  }

  watch(() => toValue(tracks), loadDetails, { immediate: true });

  return {
    enrich,
    artistMap,
    albumMap,
  };
}
