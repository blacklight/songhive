import { describe, it, expect, beforeEach } from "vitest";
import { defineComponent, h, ref } from "vue";
import { mount } from "@vue/test-utils";
import { setActivePinia, createPinia } from "pinia";
import { useAuthStore } from "@/stores/auth";
import { useEntityMeta } from "./useEntityMeta";

function createMeta(
  entity: {
    owner_id?: string | null;
    visibility?: string;
    owner?: unknown;
  } | null,
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

  it("returns null owner and empty visibility when the entity is null", () => {
    const wrapper = createMeta(null);
    expect(wrapper.vm.owner).toBeNull();
    expect(wrapper.vm.visibilityText).toBe("");
    expect(wrapper.vm.visibilityIcon).toBe("");
  });

  it("returns the nested owner object when it is present", () => {
    const wrapper = createMeta({
      owner_id: "owner-1",
      visibility: "public",
      owner: {
        id: "owner-1",
        username: "alice",
        display_name: "Alice",
        avatar_url: "https://example.com/alice.png",
      },
    });
    expect(wrapper.vm.owner).toEqual({
      id: "owner-1",
      username: "alice",
      display_name: "Alice",
      avatar_url: "https://example.com/alice.png",
    });
  });

  it("falls back to the current user when the entity only has an owner_id match", () => {
    const authStore = useAuthStore();
    authStore.user = {
      id: "owner-2",
      username: "bob",
      display_name: "Bob",
      avatar_url: "https://example.com/bob.png",
    } as never;

    const wrapper = createMeta({ owner_id: "owner-2", visibility: "public" });
    expect(wrapper.vm.owner).toEqual(authStore.user);
  });

  it("returns null owner when the entity has no owner and the current user is not the owner", () => {
    const authStore = useAuthStore();
    authStore.user = {
      id: "user-1",
      username: "alice",
    } as never;

    const wrapper = createMeta({ owner_id: "owner-1", visibility: "public" });
    expect(wrapper.vm.owner).toBeNull();
  });

  it("translates known visibility values", () => {
    const wrapper = createMeta({ owner_id: "owner-1", visibility: "public" });
    expect(wrapper.vm.visibilityText).toBe("Public");
  });

  it("falls back to the raw visibility value when unknown", () => {
    const wrapper = createMeta({ owner_id: "owner-1", visibility: "custom" });
    expect(wrapper.vm.visibilityText).toBe("custom");
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
