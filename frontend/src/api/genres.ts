import { apiRequest, apiRequestWithHeaders } from "./client";
import type { components } from "./types";

export type GenreSummary = components["schemas"]["GenreSummaryResponse"];
export type GenreItem = components["schemas"]["GenreItemResponse"];
export type GenreListRequest = components["schemas"]["GenreListRequest"];

export interface ListGenresParams {
  [key: string]: string | number | undefined;
  q?: string;
  limit?: number;
  offset?: number;
  sort_by?: string;
  sort_dir?: "asc" | "desc";
}

export interface ListGenresResult {
  items: GenreSummary[];
  total: number;
  offset: number;
}

export interface ListGenreItemsParams {
  [key: string]: string | number | undefined;
  limit?: number;
  offset?: number;
  sort_by?: string;
  sort_dir?: "asc" | "desc";
}

export interface ListGenreItemsResult {
  items: GenreItem[];
  total: number;
  offset: number;
}

export type GenreEntityType = "tracks" | "albums";

export async function listGenres(
  params?: ListGenresParams,
): Promise<ListGenresResult> {
  const response = await apiRequestWithHeaders<GenreSummary[]>("/genres", {
    query: params,
  });
  const offsetHeader = response.headers.get("X-List-Offset");
  const totalHeader = response.headers.get("X-Total-Count");
  return {
    items: response.body,
    offset: offsetHeader ? parseInt(offsetHeader, 10) : (params?.offset ?? 0),
    total: totalHeader ? parseInt(totalHeader, 10) : response.body.length,
  };
}

export async function listGenreItems(
  genre: string,
  params?: ListGenreItemsParams,
): Promise<ListGenreItemsResult> {
  const response = await apiRequestWithHeaders<GenreItem[]>(
    `/genres/${encodeURIComponent(genre)}`,
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

export function deleteGenre(genre: string): Promise<unknown> {
  return apiRequest<unknown>(`/genres/${encodeURIComponent(genre)}`, {
    method: "DELETE",
  });
}

export function addGenres(
  type: GenreEntityType,
  id: string,
  body: GenreListRequest,
): Promise<unknown> {
  return apiRequest<unknown>(`/${type}/${id}/genres`, {
    method: "POST",
    body,
  });
}

export function removeGenre(
  type: GenreEntityType,
  id: string,
  genre: string,
): Promise<unknown> {
  return apiRequest<unknown>(
    `/${type}/${id}/genres/${encodeURIComponent(genre)}`,
    { method: "DELETE" },
  );
}
