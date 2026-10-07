import type { components } from "./types";
import { apiRequest, apiRequestWithHeaders } from "./client";

export type ShareGrantCreate = components["schemas"]["ShareGrantCreate"];
export type ShareGrantResponse = components["schemas"]["ShareGrantResponse"];
export type ShareTokenCreate = components["schemas"]["ShareTokenCreate"];
export type ShareTokenCreated = components["schemas"]["ShareTokenCreated"];
export type ShareTokenResponse = components["schemas"]["ShareTokenResponse"];
export type ShareGrantUpdate = components["schemas"]["ShareGrantUpdate"];
export type CreatedShareResponse =
  components["schemas"]["CreatedShareResponse"];
export type ReceivedShareResponse =
  components["schemas"]["ReceivedShareResponse"];

export type ShareItemType =
  "track" | "album" | "artist" | "playlist" | "library";

export function listMyShares(params?: {
  limit?: number;
  offset?: number;
  include_revoked?: boolean;
}): Promise<CreatedShareResponse[]> {
  return apiRequest<CreatedShareResponse[]>("/shares/mine", { query: params });
}

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

export function updateShareGrant(
  shareId: string,
  body: ShareGrantUpdate,
): Promise<ShareGrantResponse> {
  return apiRequest<ShareGrantResponse>(`/shares/${shareId}`, {
    method: "PATCH",
    body,
  });
}

export interface ListReceivedSharesResult {
  items: ReceivedShareResponse[];
  offset: number;
  total: number;
}

export async function listReceivedShares(params?: {
  item_type?: ShareItemType;
  limit?: number;
  offset?: number;
}): Promise<ListReceivedSharesResult> {
  const response = await apiRequestWithHeaders<ReceivedShareResponse[]>(
    "/shares/received",
    { query: params },
  );
  const offsetHeader = response.headers.get("X-List-Offset");
  const totalHeader = response.headers.get("X-Total-Count");
  return {
    items: response.body,
    offset: offsetHeader ? parseInt(offsetHeader, 10) : (params?.offset ?? 0),
    total: totalHeader ? parseInt(totalHeader, 10) : response.body.length,
  };
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
