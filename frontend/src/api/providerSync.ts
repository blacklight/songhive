import { apiRequest } from "./client";

/**
 * Lazy-contents state reported on provider-backed playlist/album responses
 * (see ``songhive/external/lazy.py``).
 */
export interface ProviderSyncStatus {
  provider_type: string;
  state: "fresh" | "refreshing" | "never_fetched" | "error";
  fetched_at?: string | null;
  ttl_seconds?: number | null;
  error?: string | null;
}

export function providerSyncPlaylist(id: string): Promise<ProviderSyncStatus> {
  return apiRequest<ProviderSyncStatus>(`/playlists/${id}/provider-sync`, {
    method: "POST",
  });
}

export function providerSyncAlbum(id: string): Promise<ProviderSyncStatus> {
  return apiRequest<ProviderSyncStatus>(`/albums/${id}/provider-sync`, {
    method: "POST",
  });
}
