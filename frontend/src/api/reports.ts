import type { components } from "./types";
import { apiRequest } from "./client";

export type ReportCreateRequest = components["schemas"]["ReportCreateRequest"];
export type ReportResponse = components["schemas"]["ReportResponse"];

/**
 * Report reasons accepted by the backend — mirrors ``VALID_REASONS`` in
 * ``songhive/services/reports.py``.
 */
export const REPORT_REASONS = [
  "spam",
  "harassment",
  "copyright",
  "illegal",
  "other",
] as const;
export type ReportReason = (typeof REPORT_REASONS)[number];

/**
 * Submit a report to the instance moderators. For remote actors,
 * ``forward`` additionally delivers an ActivityPub ``Flag`` to the
 * reported account's home instance.
 */
export function createReport(
  body: ReportCreateRequest,
): Promise<ReportResponse> {
  return apiRequest<ReportResponse>("/reports", { method: "POST", body });
}
