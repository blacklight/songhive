import { computed, ref, type Ref } from "vue";
import { defineStore } from "pinia";
import * as authApi from "@/api/auth";
import * as usersApi from "@/api/users";
import {
  ApiError,
  setLogoutHandler,
  setRefreshHandler,
  setTokenProvider,
} from "@/api/client";
import type { UserResponse } from "@/api/users";
import { setStreamTokenProvider } from "@/api/stream";
import { setWsTokenProvider } from "@/api/ws";

// Tokens are persisted in localStorage for SPA convenience. This exposes them
// to XSS; a future httpOnly-cookie migration is the safer path. Store only
// short-lived access tokens and refresh tokens here.
const STORAGE_ACCESS = "songhive.auth.access";
const STORAGE_REFRESH = "songhive.auth.refresh";
const STORAGE_EXPIRES = "songhive.auth.expiresAt";
const STORAGE_USER = "songhive.auth.user";

export type AuthStatus =
  "idle" | "loading" | "authenticated" | "unauthenticated" | "error";

type UserProfile = UserResponse & { role?: "admin" | null };

let bootstrapped: Promise<void> | null = null;

export const useAuthStore = defineStore("auth", () => {
  const accessToken: Ref<string | null> = ref(
    localStorage.getItem(STORAGE_ACCESS),
  );
  const refreshToken: Ref<string | null> = ref(
    localStorage.getItem(STORAGE_REFRESH),
  );
  const expiresAt: Ref<number | null> = ref(readNumber(STORAGE_EXPIRES));
  const user: Ref<UserProfile | null> = ref(readJson(STORAGE_USER));
  const role: Ref<"admin" | null> = ref((user.value?.role as "admin") ?? null);
  const status: Ref<AuthStatus> = ref("idle");

  const isAuthenticated = computed(() => {
    if (!accessToken.value) return false;
    if (expiresAt.value && Date.now() >= expiresAt.value) return false;
    return true;
  });

  const isAdmin = computed(() => role.value === "admin");

  function persist() {
    if (accessToken.value)
      localStorage.setItem(STORAGE_ACCESS, accessToken.value);
    else localStorage.removeItem(STORAGE_ACCESS);

    if (refreshToken.value)
      localStorage.setItem(STORAGE_REFRESH, refreshToken.value);
    else localStorage.removeItem(STORAGE_REFRESH);

    if (expiresAt.value)
      localStorage.setItem(STORAGE_EXPIRES, String(expiresAt.value));
    else localStorage.removeItem(STORAGE_EXPIRES);

    if (user.value)
      localStorage.setItem(STORAGE_USER, JSON.stringify(user.value));
    else localStorage.removeItem(STORAGE_USER);
  }

  function setTokens(access: string, refresh: string, expiresIn: number) {
    accessToken.value = access;
    refreshToken.value = refresh;
    expiresAt.value = Date.now() + expiresIn * 1000;
    persist();
  }

  async function fetchProfile() {
    const profile = await usersApi.getMe();
    user.value = { ...profile, role: (profile as UserProfile).role ?? null };
    role.value = (profile as UserProfile).role ?? null;
    persist();
  }

  async function login(username: string, password: string) {
    status.value = "loading";
    try {
      const data = await authApi.login({ username, password });
      setTokens(data.access_token, data.refresh_token, data.expires_in);
      await fetchProfile();
      status.value = "authenticated";
    } catch (err) {
      status.value = "error";
      if (err instanceof ApiError) {
        throw err;
      }
      throw new Error("Login failed");
    }
  }

  async function refresh(): Promise<boolean> {
    const current = refreshToken.value;
    if (!current) return false;
    try {
      const data = await authApi.refresh({ refresh_token: current });
      setTokens(data.access_token, data.refresh_token, data.expires_in);
      return true;
    } catch {
      await logout();
      return false;
    }
  }

  async function logout() {
    const current = refreshToken.value;
    if (current) {
      try {
        await authApi.logout({ refresh_token: current });
      } catch {
        // Best effort.
      }
    }
    accessToken.value = null;
    refreshToken.value = null;
    expiresAt.value = null;
    user.value = null;
    role.value = null;
    persist();
    status.value = "unauthenticated";
  }

  function bootstrap(): Promise<void> {
    if (bootstrapped && status.value !== "idle") return bootstrapped;

    bootstrapped = (async () => {
      if (accessToken.value && refreshToken.value) {
        status.value = "loading";
        try {
          await fetchProfile();
          status.value = "authenticated";
        } catch {
          // If profile fetch fails with 401, the client will try to refresh.
          // If refresh is impossible, logout was already called by client.
          if ((status.value as string) !== "unauthenticated") {
            status.value = "error";
          }
        }
      } else {
        status.value = "unauthenticated";
      }
    })();

    return bootstrapped;
  }

  async function updateProfile(patch: usersApi.UserProfileUpdate) {
    const updated = await usersApi.updateMe(patch);
    user.value = { ...updated, role: (updated as UserProfile).role ?? null };
    persist();
  }

  function registerClientProviders() {
    setTokenProvider(() => accessToken.value);
    setRefreshHandler(() => refresh());
    setLogoutHandler(() => logout());
    setWsTokenProvider(() => accessToken.value);
    setStreamTokenProvider(() => accessToken.value);
  }

  return {
    accessToken,
    refreshToken,
    expiresAt,
    user,
    role,
    status,
    isAuthenticated,
    isAdmin,
    login,
    logout,
    refresh,
    bootstrap,
    fetchProfile,
    updateProfile,
    registerClientProviders,
  };
});

function readNumber(key: string): number | null {
  const raw = localStorage.getItem(key);
  if (!raw) return null;
  const value = Number(raw);
  return Number.isNaN(value) ? null : value;
}

function readJson<T>(key: string): T | null {
  const raw = localStorage.getItem(key);
  if (!raw) return null;
  try {
    return JSON.parse(raw) as T;
  } catch {
    return null;
  }
}
