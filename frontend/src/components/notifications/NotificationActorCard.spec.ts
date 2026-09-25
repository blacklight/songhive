import { describe, it, expect, beforeEach } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { createRouter, createMemoryHistory } from "vue-router";
import { setActivePinia, createPinia } from "pinia";
import NotificationActorCard from "./NotificationActorCard.vue";

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
      { path: "/:pathMatch(.*)*", component: { template: "<div/>" } },
    ],
  });
}

function mountCard(props: {
  actorUrl?: string | null;
  handle?: string | null;
  displayName?: string | null;
  avatarUrl?: string | null;
}) {
  return mount(NotificationActorCard, {
    props,
    global: { plugins: [createTestRouter()] },
  });
}

describe("NotificationActorCard", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
  });

  it("routes remote actor URLs to the internal remote profile", async () => {
    const wrapper = mountCard({
      actorUrl: "https://remote.example/users/bob",
      displayName: "Bob Rocker",
      avatarUrl: "https://remote.example/avatars/bob.png",
    });
    await flushPromises();

    const card = wrapper.find("a.actor-card");
    expect(card.attributes("href")).toBe("/@bob@remote.example");
    expect(card.text()).toContain("Bob Rocker");
    expect(card.text()).toContain("@bob@remote.example");
    expect(card.find("img").attributes("src")).toBe(
      "https://remote.example/avatars/bob.png",
    );
  });

  it("routes local /users/ paths to the profile page", async () => {
    const wrapper = mountCard({
      actorUrl: "/users/alice",
      displayName: null,
    });
    await flushPromises();

    const card = wrapper.find("a.actor-card");
    expect(card.attributes("href")).toBe("/@alice");
    expect(card.text()).toContain("alice");
    expect(card.text()).toContain("@alice");
  });

  it("routes the urn fallback form to the profile page", async () => {
    const wrapper = mountCard({
      actorUrl: "urn:songhive:user:carol",
      displayName: "Carol",
    });
    await flushPromises();

    const card = wrapper.find("a.actor-card");
    expect(card.attributes("href")).toBe("/@carol");
    expect(card.text()).toContain("Carol");
  });

  it("prefers an explicit handle over an opaque actor URL tail", async () => {
    // Mastodon's opaque ``/ap/users/<id>`` actor ids carry no username.
    const wrapper = mountCard({
      actorUrl: "https://remote.example/ap/users/117220292797596489",
      handle: "amber@remote.example",
    });
    await flushPromises();

    const card = wrapper.find("a.actor-card");
    expect(card.attributes("href")).toBe("/@amber@remote.example");
    expect(card.text()).toContain("amber");
    expect(card.text()).toContain("@amber@remote.example");
    expect(card.text()).not.toContain("117220292797596489");
  });

  it("routes a bare local handle to the local profile", async () => {
    const wrapper = mountCard({
      actorUrl: "https://songhive.example/users/alice",
      handle: "alice",
    });
    await flushPromises();

    expect(wrapper.find("a.actor-card").attributes("href")).toBe("/@alice");
  });

  it("falls back to the URL-derived handle without a display name", async () => {
    const wrapper = mountCard({
      actorUrl: "https://remote.example/users/dave",
    });
    await flushPromises();

    expect(wrapper.text()).toContain("@dave@remote.example");
  });

  it("renders a non-link card when the actor URL is missing", async () => {
    const wrapper = mountCard({ actorUrl: null, displayName: "Mystery" });
    await flushPromises();

    expect(wrapper.find("a.actor-card").exists()).toBe(false);
    const card = wrapper.find(".actor-card--plain");
    expect(card.exists()).toBe(true);
    expect(card.text()).toContain("Mystery");
  });
});
