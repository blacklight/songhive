import type { paths } from "./types";
import { apiRequest } from "./client";

export type UserResponse =
  paths["/api/v1/users/me"]["get"]["responses"]["200"]["content"]["application/json"];
export type UserProfileUpdate =
  paths["/api/v1/users/me"]["patch"]["requestBody"]["content"]["application/json"];
export type PublicUserResponse =
  paths["/api/v1/users/{username}"]["get"]["responses"]["200"]["content"]["application/json"];
export type ChangePasswordRequest =
  paths["/api/v1/users/me/password"]["post"]["requestBody"]["content"]["application/json"];
export type ChangePasswordResponse =
  paths["/api/v1/users/me/password"]["post"]["responses"]["200"]["content"]["application/json"];

export function getMe(): Promise<UserResponse> {
  return apiRequest<UserResponse>("/users/me");
}

export function updateMe(body: UserProfileUpdate): Promise<UserResponse> {
  return apiRequest<UserResponse>("/users/me", { method: "PATCH", body });
}

export function changePassword(
  body: ChangePasswordRequest,
): Promise<ChangePasswordResponse> {
  return apiRequest<ChangePasswordResponse>("/users/me/password", {
    method: "POST",
    body,
  });
}

export function getPublic(username: string): Promise<PublicUserResponse> {
  return apiRequest<PublicUserResponse>(`/users/${username}`);
}
