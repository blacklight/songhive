import type { paths } from "./types";
import { apiRequest } from "./client";

export type HistoryListResponse =
  paths["/api/v1/history/"]["get"]["responses"]["200"]["content"]["application/json"];

export type HistoryPage = {
  items: HistoryListResponse;
  page: number;
  pageSize: number;
};

export async function addHistory(trackId: string): Promise<void> {
  await apiRequest<void>(`/history/${trackId}`, { method: "POST" });
}

// TODO(phase-5): fully implement pagination and wire into the history view.
export function listHistory(params?: {
  page?: number;
  pageSize?: number;
}): Promise<HistoryPage> {
  const page = params?.page ?? 1;
  const pageSize = params?.pageSize ?? 20;
  return apiRequest<HistoryListResponse>("/history", {
    query: {
      limit: pageSize,
      offset: (page - 1) * pageSize,
    },
  }).then((items) => ({ items, page, pageSize }));
}
