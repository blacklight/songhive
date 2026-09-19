import { describe, it, expect, vi, beforeEach } from "vitest";
import { defineComponent, h, ref, type Ref } from "vue";
import { mount } from "@vue/test-utils";
import { setActivePinia, createPinia } from "pinia";
import { useCollectionItem } from "./useCollectionItem";
import type { CollectibleEntity } from "./useCollectionItem";
import { useAuthStore } from "@/stores/auth";
import { useToastStore } from "@/stores/toast";
import type { UserProfile } from "@/api/users";
import * as collectionApi from "@/api/collection";

vi.mock("@/api/collection", () => ({
  addToCollection: vi.fn(),
  removeFromCollection: vi.fn(),
}));

function mountCollectionItem(entity: Ref<CollectibleEntity | null>) {
  let result!: ReturnType<typeof useCollectionItem>;
  mount(
    defineComponent({
      setup() {
        result = useCollectionItem("library", entity);
        return () => h("div");
      },
    }),
  );
  return result;
}

describe("useCollectionItem", () => {
  let authStore: ReturnType<typeof useAuthStore>;
  let entity: Ref<CollectibleEntity | null>;

  beforeEach(() => {
    setActivePinia(createPinia());
    vi.resetAllMocks();
    authStore = useAuthStore();
    authStore.user = { id: "user-2", username: "bob" } as UserProfile;
    entity = ref({ id: "lib-1", owner_id: "user-1", in_collection: false });
  });

  it("offers a save action for accessible content owned by others", () => {
    const { collectionAction } = mountCollectionItem(entity);
    expect(collectionAction.value.visible).toBe(true);
    expect(collectionAction.value.icon).toBe("bookmark");
  });

  it("hides the action for content the user owns", () => {
    entity.value = { id: "lib-1", owner_id: "user-2", in_collection: true };
    const { collectionAction } = mountCollectionItem(entity);
    expect(collectionAction.value.visible).toBe(false);
  });

  it("hides the action when signed out", () => {
    authStore.user = null;
    const { collectionAction } = mountCollectionItem(entity);
    expect(collectionAction.value.visible).toBe(false);
  });

  it("saves the item and flips in_collection", async () => {
    const { collectionAction, toggleCollection } = mountCollectionItem(entity);
    await toggleCollection();

    expect(collectionApi.addToCollection).toHaveBeenCalledWith(
      "library",
      "lib-1",
    );
    expect(entity.value?.in_collection).toBe(true);
    expect(collectionAction.value.icon).toBe("xmark");
    expect(useToastStore().toasts[0].type).toBe("success");
  });

  it("removes a saved item", async () => {
    entity.value = { id: "lib-1", owner_id: "user-1", in_collection: true };
    const { toggleCollection } = mountCollectionItem(entity);
    await toggleCollection();

    expect(collectionApi.removeFromCollection).toHaveBeenCalledWith(
      "library",
      "lib-1",
    );
    expect(entity.value?.in_collection).toBe(false);
    expect(useToastStore().toasts[0].type).toBe("success");
  });

  it("reports errors without flipping the saved state", async () => {
    vi.mocked(collectionApi.addToCollection).mockRejectedValue(
      new Error("nope"),
    );
    const { toggleCollection } = mountCollectionItem(entity);
    await toggleCollection();

    expect(entity.value?.in_collection).toBe(false);
    const toasts = useToastStore().toasts;
    expect(toasts[0].type).toBe("error");
    expect(toasts[0].message).toContain("nope");
  });
});
