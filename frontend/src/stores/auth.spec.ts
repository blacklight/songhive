import { describe, it, expect, vi, beforeEach } from "vitest";
import { setActivePinia, createPinia } from "pinia";
import { useAuthStore } from "./auth";
import * as authApi from "@/api/auth";
import * as usersApi from "@/api/users";
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

describe("useAuthStore", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.clearAllMocks();
    localStorage.clear();
  });

  it("login stores tokens and fetches profile", async () => {
    const store = useAuthStore();
    store.registerClientProviders();

    vi.mocked(authApi.login).mockResolvedValue({
      access_token: "a1",
      refresh_token: "r1",
      token_type: "bearer",
      expires_in: 900,
    });

    vi.mocked(usersApi.getMe).mockResolvedValue({
      id: "u1",
      username: "alice",
      display_name: "Alice",
      bio: "",
      avatar_url: null,
      links: [],
    } as UserResponse);

    await store.login("alice", "secret");

    expect(store.accessToken).toBe("a1");
    expect(store.refreshToken).toBe("r1");
    expect(store.user?.username).toBe("alice");
    expect(store.status).toBe("authenticated");
  });

  it("isAdmin is false when role is null", () => {
    const store = useAuthStore();
    store.registerClientProviders();
    expect(store.isAdmin).toBe(false);
  });

  it("refresh rotates tokens", async () => {
    const store = useAuthStore();
    store.refreshToken = "r1";
    store.registerClientProviders();

    vi.mocked(authApi.refresh).mockResolvedValue({
      access_token: "a2",
      refresh_token: "r2",
      token_type: "bearer",
      expires_in: 900,
    });

    const ok = await store.refresh();
    expect(ok).toBe(true);
    expect(store.accessToken).toBe("a2");
    expect(store.refreshToken).toBe("r2");
  });

  it("bootstrap with stored tokens fetches profile", async () => {
    const store = useAuthStore();
    store.accessToken = "a1";
    store.refreshToken = "r1";
    store.registerClientProviders();

    vi.mocked(usersApi.getMe).mockResolvedValue({
      id: "u1",
      username: "alice",
      display_name: null,
      bio: null,
      avatar_url: null,
      links: [],
    } as UserResponse);

    await store.bootstrap();
    expect(store.status).toBe("authenticated");
    expect(store.user?.username).toBe("alice");
  });

  it("bootstrap with no tokens sets unauthenticated", async () => {
    const store = useAuthStore();
    store.registerClientProviders();
    await store.bootstrap();
    expect(store.status).toBe("unauthenticated");
  });
});
