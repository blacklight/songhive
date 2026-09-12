import { computed, ref, type Ref } from "vue";
import { defineStore } from "pinia";
import * as authApi from "@/api/auth";
import * as usersApi from "@/api/users";
import { ApiError, setLogoutHandler, setRefreshHandler } from "@/api/client";
import type { UserResponse } from "@/api/users";

// Only non-sensitive session state is persisted. The access and refresh
// tokens live in server-managed HttpOnly cookies, so JavaScript can never
// read them; the API client authenticates requests with credentials:
// "same-origin" plus the double-submit X-CSRF-Token header.
const STORAGE_USER = "songhive.auth.user";

// One-time cleanup for sessions upgrading from the localStorage token scheme:
// drop the credentials older clients persisted.
for (const key of [
  "songhive.auth.access",
  "songhive.auth.refresh",
  "songhive.auth.expiresAt",
]) {
  localStorage.removeItem(key);
}

// Older builds also mirrored the access token into a JavaScript-readable
// cookie. Delete it only when it is visible: an HttpOnly cookie of the same
// name (the new server-managed session) never appears in document.cookie, so
// this cannot clobber it.
if (/(?:^|;\s*)access_token=/.test(document.cookie)) {
  document.cookie =
    "access_token=; Path=/; SameSite=Lax; Expires=Thu, 01 Jan 1970 00:00:00 GMT";
}

export type AuthStatus =
  "idle" | "loading" | "authenticated" | "unauthenticated" | "error";

type UserProfile = UserResponse;
type UserRole = "user" | "moderator" | "admin";

let bootstrapped: Promise<void> | null = null;

export const useAuthStore = defineStore("auth", () => {
  const user: Ref<UserProfile | null> = ref(readJson(STORAGE_USER));
  const role: Ref<UserRole | null> = ref(
    (user.value?.role as UserRole) ?? null,
  );
  const status: Ref<AuthStatus> = ref("idle");

  // The persisted user profile mirrors the old "token in localStorage"
  // behaviour: the UI renders as signed-in until bootstrap() confirms the
  // cookie session or clears the stale profile.
  const isAuthenticated = computed(() => user.value !== null);

  const isAdmin = computed(() => role.value === "admin");

  function persist() {
    if (user.value)
      localStorage.setItem(STORAGE_USER, JSON.stringify(user.value));
    else localStorage.removeItem(STORAGE_USER);
  }

  async function fetchProfile() {
    const profile = await usersApi.getMe();
    user.value = profile;
    role.value = profile.role ?? null;
    persist();
  }

  async function login(username: string, password: string) {
    status.value = "loading";
    try {
      // The response sets the HttpOnly auth cookies; the token pair in the
      // body is intentionally unused by the SPA.
      await authApi.login({ username, password });
      await fetchProfile();
      status.value = "authenticated";
    } catch (err) {
      status.value = "error";
      if (err instanceof ApiError) {
        throw err;
      }
      throw new Error("Login failed", { cause: err });
    }
  }

  async function refresh(): Promise<boolean> {
    try {
      // Cookie-based refresh: no token in the request body.
      await authApi.refresh();
      return true;
    } catch {
      await logout();
      return false;
    }
  }

  async function logout() {
    try {
      // Revokes the refresh session and clears the auth cookies server-side.
      await authApi.logout();
    } catch {
      // Best effort.
    }
    user.value = null;
    role.value = null;
    persist();
    status.value = "unauthenticated";
  }

  function bootstrap(): Promise<void> {
    if (bootstrapped && status.value !== "idle") return bootstrapped;

    bootstrapped = (async () => {
      status.value = "loading";
      try {
        await fetchProfile();
        status.value = "authenticated";
      } catch (err) {
        // The API client already tried a cookie refresh; when it cannot
        // recover it invokes the logout handler, which clears the profile
        // and flips the status to "unauthenticated". A bare 401 (e.g. no
        // session at all) maps to the same state; anything else (network
        // errors, 5xx) is surfaced as a generic error state.
        if ((status.value as string) !== "unauthenticated") {
          status.value =
            err instanceof ApiError && err.status === 401
              ? "unauthenticated"
              : "error";
        }
      }
    })();

    return bootstrapped;
  }

  async function updateProfile(patch: usersApi.UserProfileUpdate) {
    const updated = await usersApi.updateMe(patch);
    user.value = updated;
    persist();
  }

  function registerClientProviders() {
    setRefreshHandler(() => refresh());
    setLogoutHandler(() => logout());
  }

  return {
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

function readJson<T>(key: string): T | null {
  const raw = localStorage.getItem(key);
  if (!raw) return null;
  try {
    return JSON.parse(raw) as T;
  } catch {
    return null;
  }
}
