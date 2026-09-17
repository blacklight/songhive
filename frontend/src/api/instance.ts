import type { components } from "./types";
import { apiRequest } from "./client";

export type InstanceInfo = components["schemas"]["InstanceV1"];
export type InstanceStats = components["schemas"]["InstanceStats"];

export function getInstance(): Promise<InstanceInfo> {
  return apiRequest<InstanceInfo>("/instance");
}

/**
 * Visibility-filtered content counts for the instance. The endpoint is
 * gated behind the ``public_stats_enabled`` setting and answers 404 when
 * it is disabled — callers should treat any error as "no stats to show".
 */
export function getInstanceStats(): Promise<InstanceStats> {
  return apiRequest<InstanceStats>("/instance/stats");
}
