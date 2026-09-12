import { describe, it, expect, vi, beforeEach } from "vitest";
import { setActivePinia, createPinia } from "pinia";
import { useAuthStore } from "./auth";
import * as authApi from "@/api/auth";
import * as usersApi from "@/api/users";
import { ApiError } from "@/api/client";
import type { UserResponse } from "@/api/users";

vi.mock("@/api/auth", () => ({
  login: vi.fn(),
  refresh: vi.fn(),
  logout: vi.fn(),
}));

vi.mock("@/api/users", () => ({
  getMe: vi.fn(),
  updateMe: vi.fn(),
}));

const alice = {
  id: "u1",
  username: "alice",
  display_name: "Alice",
  bio: "",
  avatar_url: null,
  links: [],
} as UserResponse;

describe("useAuthStore", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.clearAllMocks();
    localStorage.clear();
  });

  it("login relies on server-set cookies and fetches the profile", async () => {
    const store = useAuthStore();
    store.registerClientProviders();

    vi.mocked(authApi.login).mockResolvedValue({
      access_token: "a1",
      refresh_token: "r1",
      token_type: "bearer",
      expires_in: 900,
    });
    vi.mocked(usersApi.getMe).mockResolvedValue(alice);

    await store.login("alice", "secret");

    // Tokens are not exposed to the store; only the profile is persisted.
    expect(authApi.login).toHaveBeenCalledWith({
      username: "alice",
      password: "secret",
    });
    expect(store.user?.username).toBe("alice");
    expect(store.status).toBe("authenticated");
    expect(store.isAuthenticated).toBe(true);
    expect(localStorage.getItem("songhive.auth.access")).toBeNull();
    expect(localStorage.getItem("songhive.auth.refresh")).toBeNull();
    expect(localStorage.getItem("songhive.auth.user")).toContain("alice");
  });

  it("isAdmin is false when role is null", () => {
    const store = useAuthStore();
    store.registerClientProviders();
    expect(store.isAdmin).toBe(false);
  });

  it("refresh delegates to the cookie-based endpoint", async () => {
    const store = useAuthStore();
    store.registerClientProviders();

    vi.mocked(authApi.refresh).mockResolvedValue({
      access_token: "a2",
      refresh_token: "r2",
      token_type: "bearer",
      expires_in: 900,
    });

    const ok = await store.refresh();
    expect(ok).toBe(true);
    expect(authApi.refresh).toHaveBeenCalledWith();
  });

  it("refresh failure logs the user out", async () => {
    const store = useAuthStore();
    store.user = alice;
    store.registerClientProviders();

    vi.mocked(authApi.refresh).mockRejectedValue(new Error("nope"));

    const ok = await store.refresh();
    expect(ok).toBe(false);
    expect(authApi.logout).toHaveBeenCalled();
    expect(store.user).toBeNull();
    expect(store.status).toBe("unauthenticated");
  });

  it("bootstrap fetches the profile through cookie auth", async () => {
    const store = useAuthStore();
    store.registerClientProviders();

    vi.mocked(usersApi.getMe).mockResolvedValue(alice);

    await store.bootstrap();
    expect(store.status).toBe("authenticated");
    expect(store.user?.username).toBe("alice");
  });

  it("bootstrap with no session sets unauthenticated", async () => {
    const store = useAuthStore();
    store.registerClientProviders();
    vi.mocked(usersApi.getMe).mockRejectedValue(
      new ApiError("Unauthorized", 401),
    );

    await store.bootstrap();
    expect(store.status).toBe("unauthenticated");
    expect(store.user).toBeNull();
  });

  it("logout clears the local session state", async () => {
    const store = useAuthStore();
    store.user = alice;
    store.status = "authenticated";
    store.registerClientProviders();

    await store.logout();

    expect(authApi.logout).toHaveBeenCalledWith();
    expect(store.user).toBeNull();
    expect(store.isAuthenticated).toBe(false);
    expect(store.status).toBe("unauthenticated");
    expect(localStorage.getItem("songhive.auth.user")).toBeNull();
  });
});
