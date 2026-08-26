import { describe, it, expect, beforeEach } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { createRouter, createMemoryHistory } from "vue-router";
import { i18n } from "@/i18n";
import AlbumCard from "./AlbumCard.vue";

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
      { path: "/albums/:id", name: "album", component: { template: "<div/>" } },
    ],
  });
}

function createAlbum() {
  return {
    id: "album-1",
    title: "Meadowland",
    artist_id: "artist-1",
    release_year: 2003,
    cover_url: "https://example.com/cover.jpg",
    visibility: "public" as const,
  };
}

describe("AlbumCard", () => {
  let router: ReturnType<typeof createTestRouter>;

  beforeEach(() => {
    router = createTestRouter();
  });

  it("renders the album title, year, and cover", async () => {
    const wrapper = mount(AlbumCard, {
      props: { album: createAlbum(), artistName: "The Wrens" },
      global: { plugins: [router] },
    });
    await flushPromises();

    expect(wrapper.text()).toContain("Meadowland");
    expect(wrapper.text()).toContain("2003");
    expect(wrapper.text()).toContain("The Wrens");
    expect(wrapper.find("img").attributes("src")).toBe(
      "https://example.com/cover.jpg",
    );
  });

  it("links to the album page and emits click", async () => {
    const wrapper = mount(AlbumCard, {
      props: { album: createAlbum() },
      global: { plugins: [router] },
    });
    await flushPromises();

    const main = wrapper.find(".album-card__main");
    await main.trigger("click");
    await flushPromises();

    expect(wrapper.emitted("click")?.[0]).toEqual([createAlbum()]);
    expect(router.currentRoute.value.path).toBe("/albums/album-1");
  });

  it("links to the artist page when artist_id is present", async () => {
    const wrapper = mount(AlbumCard, {
      props: { album: createAlbum() },
      global: { plugins: [router] },
    });
    await flushPromises();

    const artistLink = wrapper.find(".album-card__artist");
    await artistLink.trigger("click");
    await flushPromises();

    expect(router.currentRoute.value.path).toBe("/artists/artist-1");
  });

  it("falls back to the artist entity label when no resolved name is given", async () => {
    const wrapper = mount(AlbumCard, {
      props: { album: createAlbum() },
      global: { plugins: [router] },
    });
    await flushPromises();

    expect(wrapper.find(".album-card__artist").text()).toContain(
      i18n.global.t("browse.entities.artist"),
    );
  });
});
