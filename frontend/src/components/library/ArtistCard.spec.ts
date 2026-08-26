import { describe, it, expect, beforeEach } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { createRouter, createMemoryHistory } from "vue-router";
import ArtistCard from "./ArtistCard.vue";

function createTestRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: "/", component: { template: "<div/>" } },
      {
        path: "/artists/:id",
        name: "artist",
        component: { template: "<div/>" },
      },
    ],
  });
}

function createArtist() {
  return {
    id: "artist-1",
    name: "The Wrens",
    bio: "Short-lived indie rock band.",
    image_url: "https://example.com/wrens.jpg",
  };
}

describe("ArtistCard", () => {
  let router: ReturnType<typeof createTestRouter>;

  beforeEach(() => {
    router = createTestRouter();
  });

  it("renders the artist name and links to the artist page", async () => {
    const wrapper = mount(ArtistCard, {
      props: { artist: createArtist() },
      global: { plugins: [router] },
    });

    expect(wrapper.text()).toContain("The Wrens");
    expect(wrapper.text()).toContain("Short-lived indie rock band.");

    await wrapper.find("a").trigger("click");
    await flushPromises();

    expect(router.currentRoute.value.path).toBe("/artists/artist-1");
  });

  it("emits click when activated", async () => {
    const wrapper = mount(ArtistCard, {
      props: { artist: createArtist() },
      global: { plugins: [router] },
    });

    await wrapper.find("a").trigger("click");

    expect(wrapper.emitted("click")?.[0]).toEqual([createArtist()]);
  });
});
