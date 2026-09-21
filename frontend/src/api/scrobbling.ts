import { apiRequest } from "./client";

export type ScrobbleServiceInfo = {
  id: string;
  name: string;
  available: boolean;
};

export type ScrobbleThresholds = {
  min_seconds: number;
  min_percent: number | null;
};

export type ScrobbleConfig = {
  service: string;
  username: string;
  enabled: boolean;
  min_seconds: number;
  min_percent: number;
  last_scrobbled_at: string | null;
  last_error: string | null;
};

export type ScrobbleStatus = {
  enabled: boolean;
  services: ScrobbleServiceInfo[];
  thresholds: ScrobbleThresholds;
  config: ScrobbleConfig | null;
};

export async function getScrobbleStatus(): Promise<ScrobbleStatus> {
  return apiRequest<ScrobbleStatus>("/scrobbling/");
}

export async function connectScrobbler(params: {
  service: "lastfm" | "librefm";
  username: string;
  password: string;
}): Promise<ScrobbleConfig> {
  return apiRequest<ScrobbleConfig>("/scrobbling/connect", {
    method: "POST",
    body: params,
  });
}

export async function updateScrobbleSettings(params: {
  enabled: boolean;
  min_seconds: number;
  min_percent: number;
}): Promise<ScrobbleConfig> {
  return apiRequest<ScrobbleConfig>("/scrobbling/", {
    method: "PUT",
    body: params,
  });
}

export async function disconnectScrobbler(): Promise<void> {
  await apiRequest<void>("/scrobbling/", { method: "DELETE" });
}

export async function reportNowPlaying(trackId: string): Promise<void> {
  await apiRequest<void>(`/scrobbling/now-playing/${trackId}`, {
    method: "POST",
  });
}
