import type { paths } from "./types";
import { apiRequest, apiRequestWithHeaders } from "./client";

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
export type UserListResponse =
  paths["/api/v1/users"]["get"]["responses"]["200"]["content"]["application/json"];
export type FollowerResponse =
  paths["/api/v1/users/{username}/followers"]["get"]["responses"]["200"]["content"]["application/json"][number];

export interface DeleteAccountRequest {
  confirmation: string;
  recursive: boolean;
}

export interface ListUsersParams {
  q?: string;
  limit?: number;
  offset?: number;
  sort_by?: string;
  sort_dir?: "asc" | "desc";
}

export interface ListUsersResult {
  users: PublicUserResponse[];
  offset: number;
  total: number;
}

export interface ListFollowersParams {
  limit?: number;
  offset?: number;
}

export interface ListFollowersResult {
  followers: FollowerResponse[];
  offset: number;
  total: number;
}

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

export async function listPublicUsers(
  params?: ListUsersParams,
): Promise<ListUsersResult> {
  const response = await apiRequestWithHeaders<UserListResponse>("/users", {
    query: params as
      Record<string, string | number | boolean | undefined | null> | undefined,
  });
  const offsetHeader = response.headers.get("X-List-Offset");
  const total = response.headers.get("X-Total-Count");
  return {
    users: response.body,
    offset: offsetHeader ? parseInt(offsetHeader, 10) : (params?.offset ?? 0),
    total: total ? parseInt(total, 10) : response.body.length,
  };
}

export async function listFollowers(
  username: string,
  params?: ListFollowersParams,
): Promise<ListFollowersResult> {
  const response = await apiRequestWithHeaders<FollowerResponse[]>(
    `/users/${username}/followers`,
    {
      query: params as
        | Record<string, string | number | boolean | undefined | null>
        | undefined,
    },
  );
  const offsetHeader = response.headers.get("X-List-Offset");
  const total = response.headers.get("X-Total-Count");
  return {
    followers: response.body,
    offset: offsetHeader ? parseInt(offsetHeader, 10) : (params?.offset ?? 0),
    total: total ? parseInt(total, 10) : response.body.length,
  };
}

export function deleteMe(body: DeleteAccountRequest): Promise<void> {
  return apiRequest<void>("/users/me", { method: "DELETE", body });
}
