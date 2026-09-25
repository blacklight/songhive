import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { createRouter, createMemoryHistory } from "vue-router";
import { setActivePinia, createPinia } from "pinia";
import { i18n } from "@/i18n";
import { useAuthStore } from "@/stores/auth";
import * as artistsApi from "@/api/artists";
import * as remoteApi from "@/api/remote";
import type { ArtistResponse } from "@/api/artists";
import type { RemoteObject } from "@/api/remote";
import ArtistsView from "./ArtistsView.vue";
import CollectionToggle from "@/components/ui/CollectionToggle.vue";

vi.mock("@/api/artists", () => ({
  listArtists: vi.fn(),
  deleteArtist: vi.fn(),
}));

vi.mock("@/api/remote", () => ({
  listRemoteObjects: vi.fn(),
  listRemoteObjectsWithMeta: vi.fn(),
}));

function createTestRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: "/", component: { template: "<div/>" } },
      { path: "/artists/:id", component: { template: "<div/>" } },
      { path: "/remote/:kind/:id", component: { template: "<div/>" } },
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

function createRemoteArtist(id: string, name: string): RemoteObject {
  return {
    id,
    canonical_url: `https://remote.example/artists/${id}`,
    object_type: "Person",
    resource_type: "artist",
    domain: "remote.example",
    actor_url: "https://remote.example/users/alice",
    name,
    visibility: "public",
    unavailable: false,
    url: `/remote/artist/${id}`,
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
    vi.mocked(remoteApi.listRemoteObjectsWithMeta).mockResolvedValue({
      items: [],
      offset: 0,
      total: 0,
    });
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

  it("hides the collection toggle when signed out", async () => {
    wrapper = mount(ArtistsView, {
      global: { plugins: [createTestRouter()] },
    });
    await flushPromises();

    const toggle = wrapper.findComponent(CollectionToggle);
    expect(toggle.find('input[type="checkbox"]').exists()).toBe(false);
    expect(artistsApi.listArtists).toHaveBeenLastCalledWith(
      expect.objectContaining({ collection: undefined }),
    );
  });

  it("enables the collection filter by default when signed in", async () => {
    setAuthenticated();
    const fetcher = vi.mocked(artistsApi.listArtists);

    wrapper = mount(ArtistsView, {
      global: { plugins: [createTestRouter()] },
    });
    await flushPromises();

    const checkbox = wrapper
      .findComponent(CollectionToggle)
      .find('input[type="checkbox"]');
    expect(checkbox.exists()).toBe(true);
    expect((checkbox.element as HTMLInputElement).checked).toBe(true);
    expect(fetcher).toHaveBeenLastCalledWith(
      expect.objectContaining({ collection: true }),
    );
  });

  it("drops the collection filter when the toggle is disabled", async () => {
    setAuthenticated();
    const fetcher = vi.mocked(artistsApi.listArtists);

    wrapper = mount(ArtistsView, {
      global: { plugins: [createTestRouter()] },
    });
    await flushPromises();

    const checkbox = wrapper
      .findComponent(CollectionToggle)
      .find('input[type="checkbox"]');
    await checkbox.setValue(false);
    await flushPromises();

    expect(fetcher).toHaveBeenLastCalledWith(
      expect.objectContaining({ collection: undefined }),
    );
  });

  it("renders remote artists in the grid with their domain", async () => {
    vi.mocked(artistsApi.listArtists).mockResolvedValue([
      createArtist("artist-1", "Local Artist"),
    ]);
    vi.mocked(remoteApi.listRemoteObjectsWithMeta).mockResolvedValue({
      items: [createRemoteArtist("ro-1", "Federated Artist")],
      offset: 0,
      total: 1,
    });

    wrapper = mount(ArtistsView, {
      global: { plugins: [createTestRouter()] },
    });
    await flushPromises();

    expect(remoteApi.listRemoteObjectsWithMeta).toHaveBeenCalledWith(
      expect.objectContaining({ resource_type: "artist" }),
    );
    const remoteCard = wrapper.find(".remote-entity-card");
    expect(remoteCard.exists()).toBe(true);
    expect(remoteCard.text()).toContain("Federated Artist");
    expect(remoteCard.text()).toContain("remote.example");
  });

  it("clusters a same-named remote artist under the local one", async () => {
    vi.mocked(artistsApi.listArtists).mockResolvedValue([
      createArtist("artist-1", "Twin"),
      createArtist("artist-2", "Zeta"),
    ]);
    vi.mocked(remoteApi.listRemoteObjectsWithMeta).mockResolvedValue({
      items: [createRemoteArtist("ro-1", "Twin")],
      offset: 0,
      total: 1,
    });

    wrapper = mount(ArtistsView, {
      global: { plugins: [createTestRouter()] },
    });
    await flushPromises();

    const cards = wrapper.findAll(".artists-view__card");
    expect(cards).toHaveLength(3);
    expect(cards[0].text()).toContain("Twin");
    expect(cards[0].text()).not.toContain("remote.example");
    expect(cards[1].text()).toContain("Twin");
    expect(cards[1].text()).toContain("remote.example");
    expect(cards[2].text()).toContain("Zeta");
  });

  it("forwards the search query to the remote listing", async () => {
    wrapper = mount(ArtistsView, {
      global: { plugins: [createTestRouter()] },
    });
    await flushPromises();

    const input = wrapper.find('input[type="search"]');
    await input.setValue("query");

    vi.advanceTimersByTime(300);
    await flushPromises();

    expect(remoteApi.listRemoteObjectsWithMeta).toHaveBeenLastCalledWith(
      expect.objectContaining({ q: "query", resource_type: "artist" }),
    );
  });

  it("keeps remote artists through Load More and pages remote in lockstep", async () => {
    vi.mocked(artistsApi.listArtists).mockResolvedValue([
      createArtist("artist-1", "Local Artist"),
    ]);
    vi.mocked(remoteApi.listRemoteObjectsWithMeta)
      .mockResolvedValueOnce({
        items: [createRemoteArtist("ro-1", "Federated Artist")],
        offset: 0,
        total: 2,
      })
      .mockResolvedValueOnce({
        items: [createRemoteArtist("ro-2", "Second Remote Artist")],
        offset: 1,
        total: 2,
      });

    wrapper = mount(ArtistsView, {
      global: { plugins: [createTestRouter()] },
    });
    await flushPromises();

    // Remote items arrive in the view's sort order so merged pagination
    // interleaves correctly.
    expect(remoteApi.listRemoteObjectsWithMeta).toHaveBeenCalledWith(
      expect.objectContaining({
        resource_type: "artist",
        offset: 0,
        sort_by: "name",
        sort_dir: "asc",
      }),
    );

    // The local list is exhausted but remote still has a page — the
    // merged list's Load More stays available.
    const loadMore = wrapper
      .findAll("button")
      .find((b) => b.text() === i18n.global.t("browse.list.loadMore"));
    expect(loadMore).toBeDefined();

    await loadMore?.trigger("click");
    await flushPromises();

    expect(remoteApi.listRemoteObjectsWithMeta).toHaveBeenLastCalledWith(
      expect.objectContaining({ offset: 1 }),
    );
    expect(wrapper.text()).toContain("Federated Artist");
    expect(wrapper.text()).toContain("Second Remote Artist");
  });
});
