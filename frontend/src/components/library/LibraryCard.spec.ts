import { describe, it, expect, beforeEach } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { createRouter, createMemoryHistory } from "vue-router";
import { setActivePinia, createPinia } from "pinia";
import { i18n } from "@/i18n";
import { useAuthStore } from "@/stores/auth";
import LibraryCard from "./LibraryCard.vue";

function createTestRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: "/", component: { template: "<div/>" } },
      {
        path: "/libraries/:id",
        name: "library",
        component: { template: "<div/>" },
      },
    ],
  });
}

function createLibrary() {
  return {
    id: "library-1",
    name: "Main Library",
    description: "My personal collection.",
    visibility: "private" as const,
    owner_id: "user-1",
    can_write: true,
  };
}

describe("LibraryCard", () => {
  let router: ReturnType<typeof createTestRouter>;

  beforeEach(() => {
    setActivePinia(createPinia());
    router = createTestRouter();
  });

  it("renders the library metadata and links to the library page", async () => {
    const wrapper = mount(LibraryCard, {
      props: { library: createLibrary() },
      global: { plugins: [router] },
    });
    await flushPromises();

    expect(wrapper.text()).toContain("Main Library");
    expect(wrapper.text()).toContain("My personal collection.");
    expect(wrapper.text()).toContain(
      i18n.global.t("browse.visibility.private"),
    );

    await wrapper.find("a").trigger("click");
    await flushPromises();

    expect(router.currentRoute.value.path).toBe("/libraries/library-1");
  });

  it("emits click when activated", async () => {
    const wrapper = mount(LibraryCard, {
      props: { library: createLibrary() },
      global: { plugins: [router] },
    });
    await flushPromises();

    await wrapper.find("a").trigger("click");
    await flushPromises();

    expect(wrapper.emitted("click")?.[0]).toEqual([createLibrary()]);
  });

  it("shows the current user's name when they own the library", async () => {
    const authStore = useAuthStore();
    authStore.user = {
      id: "user-1",
      username: "alice",
      display_name: "Alice",
    } as never;

    const wrapper = mount(LibraryCard, {
      props: { library: createLibrary() },
      global: { plugins: [router] },
    });
    await flushPromises();

    expect(wrapper.text()).toContain("Alice");
    expect(wrapper.text()).not.toContain("user-1");
  });
});
