import type { paths } from "./types";
import { apiRequest } from "./client";

export type RegisterRequest =
  paths["/api/v1/auth/register"]["post"]["requestBody"]["content"]["application/json"];
export type RegisterResponse =
  paths["/api/v1/auth/register"]["post"]["responses"]["201"]["content"]["application/json"];
export type LoginRequest =
  paths["/api/v1/auth/login"]["post"]["requestBody"]["content"]["application/json"];
export type TokenPairResponse =
  paths["/api/v1/auth/login"]["post"]["responses"]["200"]["content"]["application/json"];
export type RefreshRequest =
  paths["/api/v1/auth/refresh"]["post"]["requestBody"]["content"]["application/json"];
export type LogoutRequest =
  paths["/api/v1/auth/logout"]["post"]["requestBody"]["content"]["application/json"];
export type LogoutResponse =
  paths["/api/v1/auth/logout"]["post"]["responses"]["200"]["content"]["application/json"];
export type VerifyEmailRequest =
  paths["/api/v1/auth/verify-email"]["post"]["requestBody"]["content"]["application/json"];
export type PasswordResetRequestRequest =
  paths["/api/v1/auth/password-reset/request"]["post"]["requestBody"]["content"]["application/json"];
export type PasswordResetConfirmRequest =
  paths["/api/v1/auth/password-reset/confirm"]["post"]["requestBody"]["content"]["application/json"];

// OAuth / API tokens
export type ApiTokenListResponse =
  paths["/api/v1/auth/api-tokens"]["get"]["responses"]["200"]["content"]["application/json"];
export type ApiTokenCreateRequest =
  paths["/api/v1/auth/api-tokens"]["post"]["requestBody"]["content"]["application/json"];
export type ApiTokenCreateResponse =
  paths["/api/v1/auth/api-tokens"]["post"]["responses"]["201"]["content"]["application/json"];

export function login(body: LoginRequest): Promise<TokenPairResponse> {
  return apiRequest<TokenPairResponse>("/auth/login", {
    method: "POST",
    body,
    skipAuth: true,
  });
}

export function refresh(body: RefreshRequest): Promise<TokenPairResponse> {
  return apiRequest<TokenPairResponse>("/auth/refresh", {
    method: "POST",
    body,
    skipAuth: true,
  });
}

export function logout(body: LogoutRequest): Promise<LogoutResponse> {
  return apiRequest<LogoutResponse>("/auth/logout", {
    method: "POST",
    body,
    skipAuth: true,
  });
}

export function register(body: RegisterRequest): Promise<RegisterResponse> {
  return apiRequest<RegisterResponse>("/auth/register", {
    method: "POST",
    body,
    skipAuth: true,
  });
}

export function verifyEmail(body: VerifyEmailRequest): Promise<unknown> {
  return apiRequest<unknown>("/auth/verify-email", {
    method: "POST",
    body,
    skipAuth: true,
  });
}

export interface ResendVerificationRequest {
  username_or_email: string;
}

export interface ResendVerificationResponse {
  success: boolean;
}

export function resendVerificationEmail(
  body: ResendVerificationRequest,
): Promise<ResendVerificationResponse> {
  return apiRequest<ResendVerificationResponse>("/auth/verify-email/resend", {
    method: "POST",
    body,
    skipAuth: true,
  });
}

export function passwordResetRequest(
  body: PasswordResetRequestRequest,
): Promise<unknown> {
  return apiRequest<unknown>("/auth/password-reset/request", {
    method: "POST",
    body,
    skipAuth: true,
  });
}

export function passwordResetConfirm(
  body: PasswordResetConfirmRequest,
): Promise<unknown> {
  return apiRequest<unknown>("/auth/password-reset/confirm", {
    method: "POST",
    body,
    skipAuth: true,
  });
}

export interface SessionSummary {
  id: string;
  ip_address: string | null;
  user_agent: string | null;
  created_at: string | null;
  expires_at: string | null;
  is_current: boolean;
}

export interface SessionListResponse {
  items: SessionSummary[];
  total: number;
}

export interface RevokeSessionResponse {
  success: boolean;
}

export function listApiTokens(): Promise<ApiTokenListResponse> {
  return apiRequest<ApiTokenListResponse>("/auth/api-tokens");
}

export function createApiToken(
  body: ApiTokenCreateRequest,
): Promise<ApiTokenCreateResponse> {
  return apiRequest<ApiTokenCreateResponse>("/auth/api-tokens", {
    method: "POST",
    body,
  });
}

export function revokeApiToken(id: string): Promise<unknown> {
  return apiRequest<unknown>(`/auth/api-tokens/${id}`, { method: "DELETE" });
}

export async function sha256Hex(value: string): Promise<string> {
  if (typeof crypto === "undefined" || !crypto.subtle) {
    return "";
  }
  const data = new TextEncoder().encode(value);
  const digest = await crypto.subtle.digest("SHA-256", data);
  return Array.from(new Uint8Array(digest))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

export function listSessions(
  currentSessionId?: string,
): Promise<SessionListResponse> {
  return apiRequest<SessionListResponse>("/auth/sessions", {
    query: currentSessionId ? { current_session_id: currentSessionId } : {},
  });
}

export function revokeSession(id: string): Promise<RevokeSessionResponse> {
  return apiRequest<RevokeSessionResponse>(`/auth/sessions/${id}`, {
    method: "DELETE",
  });
}
