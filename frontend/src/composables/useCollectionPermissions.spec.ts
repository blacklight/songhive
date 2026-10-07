import { describe, it, expect } from "vitest";
import { defineComponent, h, nextTick, ref, type MaybeRef } from "vue";
import { mount } from "@vue/test-utils";
import {
  useCollectionPermissions,
  type CollectionPermissions,
} from "./useCollectionPermissions";

function createPermissions(
  collection?: MaybeRef<CollectionPermissions | null | undefined>,
) {
  return mount(
    defineComponent({
      setup() {
        return useCollectionPermissions(collection);
      },
      render: () => h("div"),
    }),
  );
}

describe("useCollectionPermissions", () => {
  it("reports no capabilities for a null collection", () => {
    const wrapper = createPermissions(null);
    expect(wrapper.vm.canWrite).toBe(false);
    expect(wrapper.vm.canManage).toBe(false);
    expect(wrapper.vm.isCollaborator).toBe(false);
    expect(wrapper.vm.shareGrantId).toBeNull();
  });

  it("exposes owner capabilities", () => {
    const wrapper = createPermissions({
      can_write: true,
      can_manage: true,
      is_collaborator: false,
      share_grant_id: null,
    });
    expect(wrapper.vm.canWrite).toBe(true);
    expect(wrapper.vm.canManage).toBe(true);
    expect(wrapper.vm.isCollaborator).toBe(false);
    expect(wrapper.vm.shareGrantId).toBeNull();
  });

  it("exposes collaborator capabilities without manage", () => {
    const wrapper = createPermissions({
      can_write: true,
      can_manage: false,
      is_collaborator: true,
      share_grant_id: "grant-1",
    });
    expect(wrapper.vm.canWrite).toBe(true);
    expect(wrapper.vm.canManage).toBe(false);
    expect(wrapper.vm.isCollaborator).toBe(true);
    expect(wrapper.vm.shareGrantId).toBe("grant-1");
  });

  it("keeps a read-only grantee out of write and manage", () => {
    const wrapper = createPermissions({
      can_write: false,
      can_manage: false,
      is_collaborator: false,
      share_grant_id: "grant-2",
    });
    expect(wrapper.vm.canWrite).toBe(false);
    expect(wrapper.vm.canManage).toBe(false);
    expect(wrapper.vm.shareGrantId).toBe("grant-2");
  });

  it("recomputes when the collection ref changes", async () => {
    const collection = ref<CollectionPermissions | null>(null);
    const wrapper = createPermissions(collection);
    expect(wrapper.vm.canWrite).toBe(false);

    collection.value = { can_write: true, can_manage: false };
    await nextTick();
    expect(wrapper.vm.canWrite).toBe(true);
    expect(wrapper.vm.canManage).toBe(false);
  });
});
