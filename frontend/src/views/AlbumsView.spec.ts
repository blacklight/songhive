import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { createRouter, createMemoryHistory } from "vue-router";
import { setActivePinia, createPinia } from "pinia";
import { i18n } from "@/i18n";
import { useAuthStore } from "@/stores/auth";
import * as albumsApi from "@/api/albums";
import * as remoteApi from "@/api/remote";
import type { AlbumResponse } from "@/api/albums";
import type { RemoteObject } from "@/api/remote";
import AlbumsView from "./AlbumsView.vue";
import CollectionToggle from "@/components/ui/CollectionToggle.vue";

vi.mock("@/api/albums", () => ({
  listAlbums: vi.fn(),
  deleteAlbum: vi.fn(),
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
      { path: "/albums/:id", component: { template: "<div/>" } },
      { path: "/artists/:id", component: { template: "<div/>" } },
      { path: "/remote/:kind/:id", component: { template: "<div/>" } },
    ],
  });
}

function createAlbum(id: string, title: string): AlbumResponse {
  return {
    id,
    title,
    artist_id: "artist-1",
    release_year: null,
    cover_url: null,
    visibility: "public",
  };
}

function createRemoteAlbum(
  id: string,
  name: string,
  artistName?: string,
): RemoteObject {
  return {
    id,
    canonical_url: `https://remote.example/albums/${id}`,
    object_type: "Album",
    resource_type: "album",
    domain: "remote.example",
    actor_url: "https://remote.example/users/alice",
    name,
    artist_name: artistName ?? null,
    visibility: "public",
    unavailable: false,
    url: `/remote/album/${id}`,
  };
}

function setAuthenticated() {
  const authStore = useAuthStore();
  authStore.status = "authenticated";
  authStore.user = { id: "user-1", username: "alice" } as never;
}

describe("AlbumsView", () => {
  let wrapper: ReturnType<typeof mount>;

  beforeEach(() => {
    setActivePinia(createPinia());
    vi.useFakeTimers();
    vi.mocked(albumsApi.listAlbums).mockResolvedValue([]);
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

  it("fetches albums on mount", async () => {
    vi.mocked(albumsApi.listAlbums).mockResolvedValue([
      createAlbum("album-1", "Meadowland"),
    ]);

    wrapper = mount(AlbumsView, {
      global: { plugins: [createTestRouter()] },
    });
    await flushPromises();

    expect(albumsApi.listAlbums).toHaveBeenCalledWith({
      q: "",
      limit: 20,
      offset: 0,
      include: "artist",
      sort_by: "title",
      sort_dir: "asc",
    });
    expect(wrapper.text()).toContain("Meadowland");
  });

  it("shows the empty state", async () => {
    wrapper = mount(AlbumsView, {
      global: { plugins: [createTestRouter()] },
    });
    await flushPromises();

    expect(wrapper.text()).toContain(
      i18n.global.t("browse.list.empty", {
        entity: i18n.global.t("browse.entities.albums"),
      }),
    );
  });

  it("shows an error banner with a retry button", async () => {
    vi.mocked(albumsApi.listAlbums).mockRejectedValue(
      new Error("network failure"),
    );

    wrapper = mount(AlbumsView, {
      global: { plugins: [createTestRouter()] },
    });
    await flushPromises();

    expect(wrapper.text()).toContain("network failure");

    vi.mocked(albumsApi.listAlbums).mockResolvedValue([
      createAlbum("album-1", "Meadowland"),
    ]);
    await wrapper.find("button").trigger("click");
    await flushPromises();

    expect(wrapper.text()).toContain("Meadowland");
    expect(wrapper.text()).not.toContain("network failure");
  });

  it("debounces search and resets the list", async () => {
    const fetcher = vi.mocked(albumsApi.listAlbums);
    fetcher
      .mockResolvedValueOnce([createAlbum("album-1", "First Album")])
      .mockResolvedValueOnce([createAlbum("album-2", "Searched Album")]);

    wrapper = mount(AlbumsView, {
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
      include: "artist",
      sort_by: "title",
      sort_dir: "asc",
    });
    expect(wrapper.text()).toContain("Searched Album");
    expect(wrapper.text()).not.toContain("First Album");
  });

  it("loads the next page", async () => {
    const fetcher = vi.mocked(albumsApi.listAlbums);
    fetcher
      .mockResolvedValueOnce(
        Array.from({ length: 20 }, (_, i) =>
          createAlbum(`album-${i}`, `Album ${i}`),
        ),
      )
      .mockResolvedValueOnce([createAlbum("album-20", "Album 20")]);

    wrapper = mount(AlbumsView, {
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
      include: "artist",
      sort_by: "title",
      sort_dir: "asc",
    });
    expect(wrapper.text()).toContain("Album 19");
    expect(wrapper.text()).toContain("Album 20");
  });

  it("hides the collection toggle when signed out", async () => {
    wrapper = mount(AlbumsView, {
      global: { plugins: [createTestRouter()] },
    });
    await flushPromises();

    const toggle = wrapper.findComponent(CollectionToggle);
    expect(toggle.find('input[type="checkbox"]').exists()).toBe(false);
    expect(albumsApi.listAlbums).toHaveBeenLastCalledWith(
      expect.objectContaining({ collection: undefined }),
    );
  });

  it("enables the collection filter by default when signed in", async () => {
    setAuthenticated();
    const fetcher = vi.mocked(albumsApi.listAlbums);

    wrapper = mount(AlbumsView, {
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
    const fetcher = vi.mocked(albumsApi.listAlbums);

    wrapper = mount(AlbumsView, {
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

  it("renders remote albums in the grid with their domain", async () => {
    vi.mocked(albumsApi.listAlbums).mockResolvedValue([
      createAlbum("album-1", "Local Album"),
    ]);
    vi.mocked(remoteApi.listRemoteObjectsWithMeta).mockResolvedValue({
      items: [createRemoteAlbum("ro-1", "Federated Album", "Remote Artist")],
      offset: 0,
      total: 1,
    });

    wrapper = mount(AlbumsView, {
      global: { plugins: [createTestRouter()] },
    });
    await flushPromises();

    expect(remoteApi.listRemoteObjectsWithMeta).toHaveBeenCalledWith(
      expect.objectContaining({ resource_type: "album" }),
    );
    const remoteCard = wrapper.find(".remote-entity-card");
    expect(remoteCard.exists()).toBe(true);
    expect(remoteCard.text()).toContain("Federated Album");
    expect(remoteCard.text()).toContain("remote.example");
  });

  it("keeps remote albums through Load More and pages remote in lockstep", async () => {
    vi.mocked(albumsApi.listAlbums).mockResolvedValue([
      createAlbum("album-1", "Local Album"),
    ]);
    vi.mocked(remoteApi.listRemoteObjectsWithMeta)
      .mockResolvedValueOnce({
        items: [createRemoteAlbum("ro-1", "Federated Album", "Remote Artist")],
        offset: 0,
        total: 2,
      })
      .mockResolvedValueOnce({
        items: [
          createRemoteAlbum("ro-2", "Second Remote Album", "Remote Artist"),
        ],
        offset: 1,
        total: 2,
      });

    wrapper = mount(AlbumsView, {
      global: { plugins: [createTestRouter()] },
    });
    await flushPromises();

    // Remote items arrive in the view's sort order so merged pagination
    // interleaves correctly.
    expect(remoteApi.listRemoteObjectsWithMeta).toHaveBeenCalledWith(
      expect.objectContaining({
        resource_type: "album",
        offset: 0,
        sort_by: "title",
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
    expect(wrapper.text()).toContain("Federated Album");
    expect(wrapper.text()).toContain("Second Remote Album");
  });
});
