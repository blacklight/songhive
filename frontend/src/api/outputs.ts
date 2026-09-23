import { apiRequest } from "./client";

export interface OutputCapabilities {
  metadata_updates: boolean;
  pause_supported: boolean;
  seek_supported: boolean;
  multi_listener: boolean;
  user_configurable: boolean;
}

export interface OutputResponse {
  id: string;
  user_id: string;
  provider_type: string;
  name: string;
  config: Record<string, unknown>;
  capabilities: OutputCapabilities | null;
  enabled: boolean;
  last_error: string | null;
  stream_url?: string | null;
  created_at: string;
  updated_at: string;
}

export interface ProviderField {
  name: string;
  type: string;
  label?: string;
  required?: boolean;
  options?: Array<{ value: string | number; label: string }>;
  help?: string;
  secret?: boolean;
  default?: unknown;
}

export interface ProviderResponse {
  provider_type: string;
  label: string;
  user_configurable: boolean;
  can_create: boolean;
  fields: ProviderField[];
}

export interface OutputCreate {
  provider_type: string;
  name: string;
  config: Record<string, unknown>;
  enabled?: boolean;
}

export interface OutputUpdate {
  name?: string;
  config?: Record<string, unknown>;
  enabled?: boolean;
}

export interface ValidationResponse {
  ok: boolean;
  capabilities?: OutputCapabilities | null;
  error?: string | null;
}

export function listOutputProviders(): Promise<ProviderResponse[]> {
  return apiRequest<ProviderResponse[]>("/outputs/providers");
}

export function listOutputs(): Promise<OutputResponse[]> {
  return apiRequest<OutputResponse[]>("/outputs/");
}

export function createOutput(body: OutputCreate): Promise<OutputResponse> {
  return apiRequest<OutputResponse>("/outputs/", { method: "POST", body });
}

export function getOutput(id: string): Promise<OutputResponse> {
  return apiRequest<OutputResponse>(`/outputs/${id}`);
}

export function updateOutput(
  id: string,
  body: OutputUpdate,
): Promise<OutputResponse> {
  return apiRequest<OutputResponse>(`/outputs/${id}`, {
    method: "PATCH",
    body,
  });
}

export function deleteOutput(id: string): Promise<void> {
  return apiRequest<void>(`/outputs/${id}`, { method: "DELETE" });
}

export function validateOutput(id: string): Promise<ValidationResponse> {
  return apiRequest<ValidationResponse>(`/outputs/${id}/validate`, {
    method: "POST",
  });
}
