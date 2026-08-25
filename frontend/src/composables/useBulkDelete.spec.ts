import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { defineComponent, h } from "vue";
import { mount, flushPromises } from "@vue/test-utils";
import { setActivePinia, createPinia } from "pinia";
import { useBulkDelete } from "./useBulkDelete";
import { useAuthStore } from "@/stores/auth";
import type { UserResponse } from "@/api/users";

interface Item {
  id: string;
  name: string;
  owner_id: string | null;
}

function mountBulkDelete(
  deleteOne: (id: string, recursive: boolean) => Promise<void> = vi.fn(),
  refresh: () => Promise<void> = vi.fn(),
  auth: { isAdmin?: boolean; userId?: string } = {},
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

  let result: ReturnType<typeof useBulkDelete<Item>> | null = null;
  mount(
    defineComponent({
      setup() {
        result = useBulkDelete<Item>({
          deleteOne,
          refresh,
          entitySingular: "item",
          entityPlural: "items",
          getName: (item) => item.name,
          recursive: true,
          recursiveLabel: "Delete contents",
        });
        return result;
      },
      render: () => h("div"),
    }),
    { global: { plugins: [pinia] } },
  );

  return result!;
}

describe("useBulkDelete", () => {
  beforeEach(() => {
    document.body.innerHTML = "";
  });

  afterEach(() => {
    document.body.innerHTML = "";
  });

  it("toggles selection and bulk mode", () => {
    const bulk = mountBulkDelete();
    const item = { id: "1", name: "A", owner_id: "user-1" };

    bulk.enterBulkMode();
    expect(bulk.bulkMode.value).toBe(true);

    bulk.toggleSelect(item.id);
    expect(bulk.selectedIds.value.has(item.id)).toBe(true);

    bulk.toggleSelect(item.id);
    expect(bulk.selectedIds.value.has(item.id)).toBe(false);
  });

  it("computes canManage based on ownership and admin", () => {
    const bulk = mountBulkDelete(vi.fn(), vi.fn(), { userId: "user-1" });
    const ownerItem = { id: "1", name: "A", owner_id: "user-1" };
    const otherItem = { id: "2", name: "B", owner_id: "user-2" };

    expect(bulk.canManage(ownerItem)).toBe(true);
    expect(bulk.canManage(otherItem)).toBe(false);
  });

  it("allows admins to manage any item", () => {
    const bulk = mountBulkDelete(vi.fn(), vi.fn(), { isAdmin: true });
    const item = { id: "1", name: "A", owner_id: "user-2" };

    expect(bulk.canManage(item)).toBe(true);
  });

  it("deletes selected items and calls refresh", async () => {
    const deleteOne = vi.fn().mockResolvedValue(undefined);
    const refresh = vi.fn().mockResolvedValue(undefined);
    const bulk = mountBulkDelete(deleteOne, refresh, { userId: "user-1" });
    const items = [
      { id: "1", name: "A", owner_id: "user-1" },
      { id: "2", name: "B", owner_id: "user-1" },
    ];

    bulk.enterBulkMode();
    bulk.toggleSelect("1");
    bulk.toggleSelect("2");

    bulk.openDeleteBulk(items);
    await flushPromises();

    expect(bulk.deleteModalOpen.value).toBe(true);
    await bulk.confirmDelete(false);

    expect(deleteOne).toHaveBeenCalledWith("1", false);
    expect(deleteOne).toHaveBeenCalledWith("2", false);
    expect(refresh).toHaveBeenCalled();
    expect(bulk.bulkMode.value).toBe(false);
  });
});
