import type { components } from "./types";
import { apiRequest } from "./client";

export type InstanceInfo = components["schemas"]["InstanceV1"];

export function getInstance(): Promise<InstanceInfo> {
  return apiRequest<InstanceInfo>("/instance");
}
