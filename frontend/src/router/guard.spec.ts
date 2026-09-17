import { describe, it, expect, vi, beforeEach } from "vitest";
import { createPinia, setActivePinia } from "pinia";
import router from "./index";
import { useAuthStore } from "@/stores/auth";
import { useInstanceStore } from "@/stores/instance";
import * as instanceApi from "@/api/instance";
import type { UserResponse } from "@/api/users";

vi.mock("@/api/client", () => ({
  setTokenProvider: vi.fn(),
  setRefreshHandler: vi.fn(),
  setLogoutHandler: vi.fn(),
  ApiError: class ApiError extends Error {
    status = 0;
  },
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
    store.user = null;
    store.role = null;
    await router.push("/admin");
    expect(router.currentRoute.value.path).toBe("/login");
    expect(router.currentRoute.value.query.redirect).toBe("/admin");
  });

  it("redirects non-admin from admin to 403", async () => {
    const store = useAuthStore();
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
    // The home guard already loaded instance state during the initial
    // push("/"), so force a refetch to pick up this test's mock.
    useInstanceStore().status = "idle";

    await router.push("/register");
    expect(router.currentRoute.value.path).toBe("/register");
  });

  it("redirects /register to /login when public registration is closed", async () => {
    vi.mocked(instanceApi.getInstance).mockResolvedValue({
      registrations: false,
    } as unknown as instanceApi.InstanceInfo);
    useInstanceStore().status = "idle";

    await router.push("/register");
    expect(router.currentRoute.value.path).toBe("/login");
  });

  it("allows /register with an invite code when invites are enabled", async () => {
    vi.mocked(instanceApi.getInstance).mockResolvedValue({
      registrations: false,
      invites_enabled: true,
    } as unknown as instanceApi.InstanceInfo);
    useInstanceStore().status = "idle";

    await router.push("/register?invite_code=ABC");
    expect(router.currentRoute.value.path).toBe("/register");
  });

  it("redirects /register without an invite code when invites are enabled", async () => {
    vi.mocked(instanceApi.getInstance).mockResolvedValue({
      registrations: false,
      invites_enabled: true,
    } as unknown as instanceApi.InstanceInfo);
    useInstanceStore().status = "idle";

    await router.push("/register");
    expect(router.currentRoute.value.path).toBe("/login");
  });

  it("redirects /register with an invite code to /login when invites are disabled", async () => {
    vi.mocked(instanceApi.getInstance).mockResolvedValue({
      registrations: false,
      invites_enabled: false,
    } as unknown as instanceApi.InstanceInfo);
    useInstanceStore().status = "idle";

    await router.push("/register?invite_code=ABC");
    expect(router.currentRoute.value.path).toBe("/login");
  });

  it("redirects anonymous / to the single-user profile", async () => {
    vi.mocked(instanceApi.getInstance).mockResolvedValue({
      single_user: "alice",
    } as unknown as instanceApi.InstanceInfo);
    const store = useAuthStore();
    store.user = null;
    store.status = "unauthenticated";
    // The beforeEach push("/") already populated the instance store, so
    // force it to refetch with the single-user payload.
    const instanceStore = useInstanceStore();
    instanceStore.status = "idle";

    await router.push("/about");
    await router.push("/");

    expect(router.currentRoute.value.path).toBe("/@alice/posts");
  });

  it("keeps authenticated users on / in single-user mode", async () => {
    vi.mocked(instanceApi.getInstance).mockResolvedValue({
      single_user: "alice",
    } as unknown as instanceApi.InstanceInfo);
    const store = useAuthStore();
    store.user = { id: "u1", username: "bob", links: [] } as UserResponse;
    store.status = "authenticated";
    const instanceStore = useInstanceStore();
    instanceStore.status = "idle";

    await router.push("/about");
    await router.push("/");

    expect(router.currentRoute.value.path).toBe("/");
  });

  it("serves / to anonymous users when single-user mode is unset", async () => {
    vi.mocked(instanceApi.getInstance).mockResolvedValue({
      single_user: null,
    } as unknown as instanceApi.InstanceInfo);
    const instanceStore = useInstanceStore();
    instanceStore.status = "idle";

    await router.push("/about");
    await router.push("/");

    expect(router.currentRoute.value.path).toBe("/");
  });

  it("redirects /register to / for authenticated users when public registration is closed", async () => {
    const store = useAuthStore();
    store.user = { id: "u1", username: "bob", links: [] } as UserResponse;
    store.status = "authenticated";

    vi.mocked(instanceApi.getInstance).mockResolvedValue({
      registrations: false,
    } as unknown as instanceApi.InstanceInfo);
    useInstanceStore().status = "idle";

    await router.push("/register");
    expect(router.currentRoute.value.path).toBe("/");
  });
});
