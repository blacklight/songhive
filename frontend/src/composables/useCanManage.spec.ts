import { describe, it, expect, beforeEach } from "vitest";
import { defineComponent, h, nextTick, ref, type MaybeRef } from "vue";
import { mount } from "@vue/test-utils";
import { setActivePinia, createPinia } from "pinia";
import { useAuthStore } from "@/stores/auth";
import { useCanManage } from "./useCanManage";

function createCanManage(ownerId?: MaybeRef<string | null | undefined>) {
  return mount(
    defineComponent({
      setup() {
        return useCanManage(ownerId);
      },
      render: () => h("div"),
    }),
  );
}

describe("useCanManage", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
  });

  it("returns false when the user is not authenticated", () => {
    const wrapper = createCanManage("owner-1");
    expect(wrapper.vm.canManage).toBe(false);
  });

  it("returns true when the current user matches the owner id", () => {
    const authStore = useAuthStore();
    authStore.accessToken = "token";
    authStore.expiresAt = Date.now() + 10000;
    authStore.user = { id: "owner-1", username: "alice" } as never;

    const wrapper = createCanManage("owner-1");
    expect(wrapper.vm.canManage).toBe(true);
  });

  it("returns false when the current user does not match the owner id", () => {
    const authStore = useAuthStore();
    authStore.accessToken = "token";
    authStore.expiresAt = Date.now() + 10000;
    authStore.user = { id: "owner-2", username: "bob" } as never;

    const wrapper = createCanManage("owner-1");
    expect(wrapper.vm.canManage).toBe(false);
  });

  it("returns true for admins even when they are not the owner", () => {
    const authStore = useAuthStore();
    authStore.accessToken = "token";
    authStore.expiresAt = Date.now() + 10000;
    authStore.user = { id: "admin-1", username: "admin" } as never;
    authStore.role = "admin";

    const wrapper = createCanManage("owner-1");
    expect(wrapper.vm.canManage).toBe(true);
  });

  it("recomputes canManage when the owner id is a reactive ref", async () => {
    const authStore = useAuthStore();
    authStore.accessToken = "token";
    authStore.expiresAt = Date.now() + 10000;
    authStore.user = { id: "owner-1", username: "alice" } as never;

    const ownerId = ref<string | null>("owner-2");
    const wrapper = createCanManage(ownerId);

    expect(wrapper.vm.canManage).toBe(false);

    ownerId.value = "owner-1";
    await nextTick();
    expect(wrapper.vm.canManage).toBe(true);
  });
});
