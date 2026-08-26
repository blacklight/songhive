import { describe, it, expect, beforeEach } from "vitest";
import { defineComponent, h, ref } from "vue";
import { mount } from "@vue/test-utils";
import { setActivePinia, createPinia } from "pinia";
import { useAuthStore } from "@/stores/auth";
import { useEntityMeta } from "./useEntityMeta";

function createMeta(
  entity: { owner_id?: string | null; visibility?: string } | null,
) {
  return mount(
    defineComponent({
      setup() {
        return useEntityMeta(ref(entity));
      },
      render: () => h("div"),
    }),
  );
}

describe("useEntityMeta", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
  });

  it("returns empty strings when the entity is null", () => {
    const wrapper = createMeta(null);
    expect(wrapper.vm.ownerName).toBe("");
    expect(wrapper.vm.visibilityText).toBe("");
  });

  it("returns the owner id when the current user is not the owner", () => {
    const wrapper = createMeta({ owner_id: "owner-1", visibility: "public" });
    expect(wrapper.vm.ownerName).toBe("owner-1");
  });

  it("returns the current user's display name when they own the entity", () => {
    const authStore = useAuthStore();
    authStore.user = {
      id: "owner-1",
      username: "alice",
      display_name: "Alice Doe",
    } as never;

    const wrapper = createMeta({ owner_id: "owner-1", visibility: "public" });
    expect(wrapper.vm.ownerName).toBe("Alice Doe");
  });

  it("falls back to username when the user has no display name", () => {
    const authStore = useAuthStore();
    authStore.user = { id: "owner-2", username: "bob" } as never;

    const wrapper = createMeta({ owner_id: "owner-2", visibility: "public" });
    expect(wrapper.vm.ownerName).toBe("bob");
  });

  it("translates known visibility values", () => {
    const wrapper = createMeta({ owner_id: "owner-1", visibility: "public" });
    expect(wrapper.vm.visibilityText).toBe("Public");
  });

  it("falls back to the raw visibility value when unknown", () => {
    const wrapper = createMeta({ owner_id: "owner-1", visibility: "custom" });
    expect(wrapper.vm.visibilityText).toBe("custom");
  });

  it("returns an empty avatar URL when the entity is not the current user", () => {
    const authStore = useAuthStore();
    authStore.user = {
      id: "user-1",
      username: "alice",
      avatar_url: "https://example.com/alice.png",
    } as never;

    const wrapper = createMeta({ owner_id: "owner-1", visibility: "public" });
    expect(wrapper.vm.ownerAvatarUrl).toBe("");
  });

  it("returns the current user's avatar URL when they own the entity", () => {
    const authStore = useAuthStore();
    authStore.user = {
      id: "owner-1",
      username: "alice",
      avatar_url: "https://example.com/alice.png",
    } as never;

    const wrapper = createMeta({ owner_id: "owner-1", visibility: "public" });
    expect(wrapper.vm.ownerAvatarUrl).toBe("https://example.com/alice.png");
  });

  it("returns the correct visibility icon", () => {
    expect(
      createMeta({ owner_id: "owner-1", visibility: "public" }).vm
        .visibilityIcon,
    ).toBe("fas fa-globe");
    expect(
      createMeta({ owner_id: "owner-1", visibility: "local" }).vm
        .visibilityIcon,
    ).toBe("fas fa-home");
    expect(
      createMeta({ owner_id: "owner-1", visibility: "private" }).vm
        .visibilityIcon,
    ).toBe("fas fa-lock");
  });
});
