import { describe, it, expect, vi, beforeEach } from "vitest";
import { createPinia, setActivePinia } from "pinia";
import router from "./index";
import { useAuthStore } from "@/stores/auth";
import * as instanceApi from "@/api/instance";
import type { UserResponse } from "@/api/users";

vi.mock("@/api/client", () => ({
  setTokenProvider: vi.fn(),
  setRefreshHandler: vi.fn(),
  setLogoutHandler: vi.fn(),
}));

vi.mock("@/api/stream", () => ({
  setStreamTokenProvider: vi.fn(),
}));

vi.mock("@/api/ws", () => ({
  setWsTokenProvider: vi.fn(),
}));

vi.mock("@/api/instance", () => ({
  getInstance: vi.fn(),
}));

describe("router guard", () => {
  beforeEach(async () => {
    setActivePinia(createPinia());
    localStorage.clear();
    const authStore = useAuthStore();
    authStore.registerClientProviders();
    authStore.accessToken = null;
    authStore.refreshToken = null;
    authStore.user = null;
    authStore.role = null;
    authStore.status = "unauthenticated";
    await router.push("/");
  });

  it("redirects unauthenticated from requiresAuth to login", async () => {
    const store = useAuthStore();
    store.status = "unauthenticated";
    await router.push("/history");
    expect(router.currentRoute.value.path).toBe("/login");
    expect(router.currentRoute.value.query.redirect).toBe("/history");
  });

  it("redirects unauthenticated admin visits to login", async () => {
    const store = useAuthStore();
    store.status = "unauthenticated";
    store.accessToken = null;
    store.refreshToken = null;
    store.user = null;
    store.role = null;
    await router.push("/admin");
    expect(router.currentRoute.value.path).toBe("/login");
    expect(router.currentRoute.value.query.redirect).toBe("/admin");
  });

  it("redirects non-admin from admin to 403", async () => {
    const store = useAuthStore();
    store.accessToken = "x";
    store.refreshToken = "y";
    store.user = { id: "u1", username: "bob", links: [] } as UserResponse;
    store.status = "authenticated";
    await router.push("/admin");
    expect(router.currentRoute.value.path).toBe("/403");
  });

  it("allows public routes", async () => {
    await router.push("/");
    expect(router.currentRoute.value.path).toBe("/");
  });

  it("allows /radio and /about without authentication", async () => {
    const store = useAuthStore();
    store.status = "unauthenticated";

    await router.push("/radio");
    expect(router.currentRoute.value.path).toBe("/radio");

    await router.push("/about");
    expect(router.currentRoute.value.path).toBe("/about");
  });

  it("redirects unauthenticated from /files/:id to login", async () => {
    const store = useAuthStore();
    store.status = "unauthenticated";
    await router.push("/files/abc");
    expect(router.currentRoute.value.path).toBe("/login");
    expect(router.currentRoute.value.query.redirect).toBe("/files/abc");
  });

  it("allows /register when public registration is open", async () => {
    vi.mocked(instanceApi.getInstance).mockResolvedValue({
      registrations: true,
    } as unknown as instanceApi.InstanceInfo);

    await router.push("/register");
    expect(router.currentRoute.value.path).toBe("/register");
  });

  it("redirects /register to /login when public registration is closed", async () => {
    vi.mocked(instanceApi.getInstance).mockResolvedValue({
      registrations: false,
    } as unknown as instanceApi.InstanceInfo);

    await router.push("/register");
    expect(router.currentRoute.value.path).toBe("/login");
  });

  it("redirects /register to / for authenticated users when public registration is closed", async () => {
    const store = useAuthStore();
    store.accessToken = "token";
    store.refreshToken = "refresh";
    store.expiresAt = Date.now() + 10000;
    store.user = { id: "u1", username: "bob", links: [] } as UserResponse;
    store.status = "authenticated";

    vi.mocked(instanceApi.getInstance).mockResolvedValue({
      registrations: false,
    } as unknown as instanceApi.InstanceInfo);

    await router.push("/register");
    expect(router.currentRoute.value.path).toBe("/");
  });
});
