import type { components } from "./types";
import { apiRequest } from "./client";

export type ShareGrantCreate = components["schemas"]["ShareGrantCreate"];
export type ShareGrantResponse = components["schemas"]["ShareGrantResponse"];
export type ShareTokenCreate = components["schemas"]["ShareTokenCreate"];
export type ShareTokenCreated = components["schemas"]["ShareTokenCreated"];
export type ShareTokenResponse = components["schemas"]["ShareTokenResponse"];

export type ShareItemType =
  "track" | "album" | "artist" | "playlist" | "library";

export function listShareGrants(params: {
  item_type: string;
  item_id: string;
  limit?: number;
  offset?: number;
}): Promise<ShareGrantResponse[]> {
  return apiRequest<ShareGrantResponse[]>("/shares/", { query: params });
}

export function createShareGrant(
  body: ShareGrantCreate,
): Promise<ShareGrantResponse> {
  return apiRequest<ShareGrantResponse>("/shares/", { method: "POST", body });
}

export function deleteShareGrant(shareId: string): Promise<void> {
  return apiRequest<void>(`/shares/${shareId}`, { method: "DELETE" });
}

export function listShareUrls(params: {
  item_type: string;
  item_id: string;
  limit?: number;
  offset?: number;
}): Promise<ShareTokenResponse[]> {
  return apiRequest<ShareTokenResponse[]>("/share-urls/", { query: params });
}

export function createShareUrl(
  body: ShareTokenCreate,
): Promise<ShareTokenCreated> {
  return apiRequest<ShareTokenCreated>("/share-urls/", {
    method: "POST",
    body,
  });
}

export function deleteShareUrl(tokenId: string): Promise<void> {
  return apiRequest<void>(`/share-urls/${tokenId}`, { method: "DELETE" });
}

export function resolveShareUrl(token: string): Promise<unknown> {
  return apiRequest<unknown>(`/share/${token}`, { skipAuth: true });
}
