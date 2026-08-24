import { describe, it, expect, beforeEach } from "vitest";
import { defineComponent, h } from "vue";
import { mount } from "@vue/test-utils";
import { setActivePinia, createPinia } from "pinia";
import { useAuthStore } from "@/stores/auth";
import { useOwnership } from "./useOwnership";

function createOwnership(ownerId?: string | null) {
  return mount(
    defineComponent({
      setup() {
        return useOwnership(ownerId);
      },
      render: () => h("div"),
    }),
  );
}

describe("useOwnership", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
  });

  it("returns false when no owner id is provided", () => {
    const wrapper = createOwnership();
    expect(wrapper.vm.isOwner).toBe(false);
    expect(wrapper.vm.currentUser).toBeNull();
  });

  it("returns false when the user is not authenticated", () => {
    const wrapper = createOwnership("owner-1");
    expect(wrapper.vm.isOwner).toBe(false);
  });

  it("returns true when the current user matches the owner id", () => {
    const authStore = useAuthStore();
    authStore.user = { id: "owner-1", username: "alice" } as never;

    const wrapper = createOwnership("owner-1");
    expect(wrapper.vm.isOwner).toBe(true);
    expect(wrapper.vm.currentUser?.id).toBe("owner-1");
  });

  it("returns false when the current user does not match the owner id", () => {
    const authStore = useAuthStore();
    authStore.user = { id: "owner-2", username: "bob" } as never;

    const wrapper = createOwnership("owner-1");
    expect(wrapper.vm.isOwner).toBe(false);
  });
});
