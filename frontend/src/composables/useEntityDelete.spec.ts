import { describe, it, expect, vi } from "vitest";
import { defineComponent, h } from "vue";
import { mount } from "@vue/test-utils";
import { setActivePinia, createPinia } from "pinia";
import { useEntityDelete } from "./useEntityDelete";
import { useAuthStore } from "@/stores/auth";
import type { UserResponse } from "@/api/users";
import { createRouter, createMemoryHistory } from "vue-router";

function mountEntityDelete(
  deleteFn: (id: string, recursive: boolean) => Promise<void> = vi.fn(),
  ownerId: string | null = "user-1",
  auth: { isAdmin?: boolean; userId?: string } = { userId: "user-1" },
) {
  const pinia = createPinia();
  setActivePinia(pinia);
  const authStore = useAuthStore();
  if (auth.isAdmin || auth.userId) {
    authStore.$patch({
      accessToken: "token",
      expiresAt: Date.now() + 60000,
      user: {
        id: auth.userId ?? "user-1",
        username: "test",
      } as UserResponse,
    });
  }
  if (auth.isAdmin) {
    authStore.role = "admin";
  }

  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: "/", component: { template: "<div/>" } },
      { path: "/items", component: { template: "<div/>" } },
    ],
  });

  let result: ReturnType<typeof useEntityDelete> | null = null;
  mount(
    defineComponent({
      setup() {
        result = useEntityDelete({
          delete: deleteFn,
          entity: "item",
          redirectTo: "/items",
          allowRecursive: true,
          recursiveLabel: "Delete contents",
          getName: () => "Test item",
          getOwnerId: () => ownerId,
        });
        return {};
      },
      render: () => h("div"),
    }),
    { global: { plugins: [pinia, router] } },
  );

  return result!;
}

describe("useEntityDelete", () => {
  it("allows owners to delete", () => {
    const state = mountEntityDelete(vi.fn(), "user-1", { userId: "user-1" });
    expect(state.canDelete.value).toBe(true);
  });

  it("disallows non-owners", () => {
    const state = mountEntityDelete(vi.fn(), "user-2", { userId: "user-1" });
    expect(state.canDelete.value).toBe(false);
  });

  it("allows admins regardless of ownership", () => {
    const state = mountEntityDelete(vi.fn(), "user-2", { isAdmin: true });
    expect(state.canDelete.value).toBe(true);
  });

  it("opens the modal and deletes when confirmed", async () => {
    const deleteFn = vi.fn().mockResolvedValue(undefined);
    const state = mountEntityDelete(deleteFn, "user-1", { userId: "user-1" });

    state.open("item-1");
    expect(state.modalOpen.value).toBe(true);

    await state.confirm(false);
    expect(deleteFn).toHaveBeenCalledWith("item-1", false);
    expect(state.modalOpen.value).toBe(false);
  });

  it("passes recursive flag through", async () => {
    const deleteFn = vi.fn().mockResolvedValue(undefined);
    const state = mountEntityDelete(deleteFn, "user-1", { userId: "user-1" });

    state.open("item-1");
    await state.confirm(true);
    expect(deleteFn).toHaveBeenCalledWith("item-1", true);
  });
});
