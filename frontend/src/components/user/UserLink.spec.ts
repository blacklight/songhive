import { describe, it, expect, beforeEach } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { createRouter, createMemoryHistory } from "vue-router";
import { setActivePinia, createPinia } from "pinia";
import UserLink from "./UserLink.vue";

function createTestRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: "/", component: { template: "<div/>" } },
      {
        path: "/@:username",
        name: "userProfile",
        component: { template: "<div/>" },
      },
    ],
  });
}

describe("UserLink", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
  });

  async function mountUserLink(props: Record<string, unknown>) {
    const router = createTestRouter();
    await router.push("/");
    await router.isReady();
    return mount(UserLink, {
      props,
      global: { plugins: [router] },
    });
  }

  it("renders a local RouterLink when given a local owner", async () => {
    const wrapper = await mountUserLink({
      owner: {
        id: "user-1",
        username: "alice",
        display_name: "Alice",
        avatar_url: "https://example.com/alice.png",
      },
      size: "md",
    });
    await flushPromises();

    const link = wrapper.find("a.user-link");
    expect(link.exists()).toBe(true);
    expect(link.text()).toContain("Alice");
    expect(link.text()).toContain("@alice");
    expect(link.attributes("href")).toBe("/@alice");
  });

  it("renders a remote external link when given an owner with a remote actor_url", async () => {
    const wrapper = await mountUserLink({
      owner: {
        id: "user-2",
        username: "bob",
        display_name: "Bob",
        avatar_url: "https://example.com/bob.png",
        actor_url: "https://remote.example/users/bob",
      },
      size: "md",
    });
    await flushPromises();

    const link = wrapper.find("a.user-link--remote");
    expect(link.exists()).toBe(true);
    expect(link.attributes("href")).toBe("https://remote.example/users/bob");
    expect(link.attributes("target")).toBe("_blank");
    expect(link.text()).toContain("Bob");
    expect(link.text()).not.toContain("@bob");
  });

  it("hides the username handle at small size", async () => {
    const wrapper = await mountUserLink({
      owner: {
        id: "user-1",
        username: "alice",
        display_name: "Alice",
      },
      size: "sm",
    });
    await flushPromises();

    expect(wrapper.text()).toContain("Alice");
    expect(wrapper.text()).not.toContain("@alice");
  });

  it("falls back to the username when no display name is provided", async () => {
    const wrapper = await mountUserLink({
      owner: {
        id: "user-1",
        username: "alice",
        display_name: null,
      },
      size: "md",
    });
    await flushPromises();

    expect(wrapper.text()).toContain("alice");
  });
});
