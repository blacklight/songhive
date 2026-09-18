import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { createRouter, createMemoryHistory } from "vue-router";
import { setActivePinia, createPinia } from "pinia";
import { i18n } from "@/i18n";
import { useAuthStore } from "@/stores/auth";
import * as artistsApi from "@/api/artists";
import type { ArtistResponse } from "@/api/artists";
import ArtistsView from "./ArtistsView.vue";
import OnlyMineToggle from "@/components/ui/OnlyMineToggle.vue";

vi.mock("@/api/artists", () => ({
  listArtists: vi.fn(),
  deleteArtist: vi.fn(),
}));

function createTestRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: "/", component: { template: "<div/>" } },
      { path: "/artists/:id", component: { template: "<div/>" } },
    ],
  });
}

function createArtist(id: string, name: string): ArtistResponse {
  return {
    id,
    name,
    bio: null,
    image_url: null,
  };
}

function setAuthenticated() {
  const authStore = useAuthStore();
  authStore.status = "authenticated";
  authStore.user = { id: "user-1", username: "alice" } as never;
}

describe("ArtistsView", () => {
  let wrapper: ReturnType<typeof mount>;

  beforeEach(() => {
    setActivePinia(createPinia());
    vi.useFakeTimers();
    vi.mocked(artistsApi.listArtists).mockResolvedValue([]);
  });

  afterEach(() => {
    vi.useRealTimers();
    wrapper?.unmount();
    document.body.innerHTML = "";
  });

  it("fetches artists on mount", async () => {
    vi.mocked(artistsApi.listArtists).mockResolvedValue([
      createArtist("artist-1", "Artist One"),
    ]);

    wrapper = mount(ArtistsView, {
      global: { plugins: [createTestRouter()] },
    });
    await flushPromises();

    expect(artistsApi.listArtists).toHaveBeenCalledWith({
      q: "",
      limit: 20,
      offset: 0,
      sort_by: "name",
      sort_dir: "asc",
    });
    expect(wrapper.text()).toContain("Artist One");
  });

  it("shows the empty state", async () => {
    wrapper = mount(ArtistsView, {
      global: { plugins: [createTestRouter()] },
    });
    await flushPromises();

    expect(wrapper.text()).toContain(
      i18n.global.t("browse.list.empty", {
        entity: i18n.global.t("browse.entities.artists"),
      }),
    );
  });

  it("shows an error banner with a retry button", async () => {
    vi.mocked(artistsApi.listArtists).mockRejectedValue(
      new Error("network failure"),
    );

    wrapper = mount(ArtistsView, {
      global: { plugins: [createTestRouter()] },
    });
    await flushPromises();

    expect(wrapper.text()).toContain("network failure");

    vi.mocked(artistsApi.listArtists).mockResolvedValue([
      createArtist("artist-1", "Artist One"),
    ]);
    await wrapper.find("button").trigger("click");
    await flushPromises();

    expect(wrapper.text()).toContain("Artist One");
    expect(wrapper.text()).not.toContain("network failure");
  });

  it("debounces search and resets the list", async () => {
    const fetcher = vi.mocked(artistsApi.listArtists);
    fetcher
      .mockResolvedValueOnce([createArtist("artist-1", "First Artist")])
      .mockResolvedValueOnce([createArtist("artist-2", "Searched Artist")]);

    wrapper = mount(ArtistsView, {
      global: { plugins: [createTestRouter()] },
    });
    await flushPromises();

    const input = wrapper.find('input[type="search"]');
    await input.setValue("query");

    vi.advanceTimersByTime(0);
    vi.advanceTimersByTime(300);
    await flushPromises();

    expect(fetcher).toHaveBeenLastCalledWith({
      q: "query",
      limit: 20,
      offset: 0,
      sort_by: "name",
      sort_dir: "asc",
    });
    expect(wrapper.text()).toContain("Searched Artist");
    expect(wrapper.text()).not.toContain("First Artist");
  });

  it("loads the next page", async () => {
    const fetcher = vi.mocked(artistsApi.listArtists);
    fetcher
      .mockResolvedValueOnce(
        Array.from({ length: 20 }, (_, i) =>
          createArtist(`artist-${i}`, `Artist ${i}`),
        ),
      )
      .mockResolvedValueOnce([createArtist("artist-20", "Artist 20")]);

    wrapper = mount(ArtistsView, {
      global: { plugins: [createTestRouter()] },
    });
    await flushPromises();

    const loadMore = wrapper
      .findAll("button")
      .find((b) => b.text() === i18n.global.t("browse.list.loadMore"));
    expect(loadMore).toBeDefined();

    await loadMore?.trigger("click");
    await flushPromises();

    expect(fetcher).toHaveBeenLastCalledWith({
      q: "",
      limit: 20,
      offset: 20,
      sort_by: "name",
      sort_dir: "asc",
    });
    expect(wrapper.text()).toContain("Artist 19");
    expect(wrapper.text()).toContain("Artist 20");
  });

  it("hides the only-mine toggle when signed out", async () => {
    wrapper = mount(ArtistsView, {
      global: { plugins: [createTestRouter()] },
    });
    await flushPromises();

    const toggle = wrapper.findComponent(OnlyMineToggle);
    expect(toggle.find('input[type="checkbox"]').exists()).toBe(false);
  });

  it("filters by the current user when the only-mine toggle is enabled", async () => {
    setAuthenticated();
    const fetcher = vi.mocked(artistsApi.listArtists);

    wrapper = mount(ArtistsView, {
      global: { plugins: [createTestRouter()] },
    });
    await flushPromises();

    const checkbox = wrapper
      .findComponent(OnlyMineToggle)
      .find('input[type="checkbox"]');
    expect(checkbox.exists()).toBe(true);

    await checkbox.setValue(true);
    await flushPromises();

    expect(fetcher).toHaveBeenLastCalledWith(
      expect.objectContaining({ owner_username: "alice" }),
    );
  });
});
