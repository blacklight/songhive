import { describe, it, expect, vi, beforeEach } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { createMemoryHistory, createRouter } from "vue-router";
import SearchView from "./SearchView.vue";
import { setTokenProvider } from "@/api/client";

const routes = [
  { path: "/search", name: "search", component: SearchView },
  { path: "/tracks/:id", name: "track", component: { template: "<div />" } },
  { path: "/albums/:id", name: "album", component: { template: "<div />" } },
  { path: "/artists/:id", name: "artist", component: { template: "<div />" } },
  {
    path: "/playlists/:id",
    name: "playlist",
    component: { template: "<div />" },
  },
  {
    path: "/libraries/:id",
    name: "library",
    component: { template: "<div />" },
  },
  { path: "/@:username", name: "user", component: { template: "<div />" } },
  { path: "/tags/:name", name: "tag", component: { template: "<div />" } },
  { path: "/genres/:name", name: "genre", component: { template: "<div />" } },
];

function makeResponse(body: unknown, headers?: Record<string, string>) {
  return {
    status: 200,
    ok: true,
    text: () => Promise.resolve(JSON.stringify(body)),
    headers: new Headers(headers),
  };
}

function makeFetch(total: number, body: unknown[] = []) {
  return vi.fn().mockImplementation((url: string) => {
    if (url.includes("/tracks/")) {
      return Promise.resolve(
        makeResponse(body, {
          "X-Total-Count": String(total),
          "X-List-Offset": "0",
        }),
      );
    }
    return Promise.resolve(makeResponse([], { "X-Total-Count": "0" }));
  });
}

function makeRemoteFetch(options: {
  remoteAvailable: boolean;
  remoteItems?: unknown[];
  lookup?: unknown;
  lookupStatus?: number;
}) {
  return vi.fn().mockImplementation((url: string) => {
    if (url.includes("/remote/lookup")) {
      if (options.lookupStatus && options.lookupStatus !== 200) {
        return Promise.resolve({
          status: options.lookupStatus,
          ok: false,
          text: () => Promise.resolve(JSON.stringify({ detail: "nope" })),
          headers: new Headers(),
        });
      }
      return Promise.resolve(
        makeResponse(
          options.lookup ?? {
            kind: "actor",
            url: "/@alice@remote.example",
            actor: { handle: "alice@remote.example" },
          },
        ),
      );
    }
    if (url.includes("/search/")) {
      const items = options.remoteItems ?? [];
      return Promise.resolve(
        makeResponse({
          query: "q",
          remote_available: options.remoteAvailable,
          sections: [{ entity: "remote", total: items.length, items }],
        }),
      );
    }
    return Promise.resolve(makeResponse([], { "X-Total-Count": "0" }));
  });
}

async function mountView(query: Record<string, string> = {}) {
  const router = createRouter({
    history: createMemoryHistory(),
    routes,
  });
  await router.push({ path: "/search", query });
  return mount(SearchView, {
    global: {
      plugins: [router],
    },
  });
}

describe("SearchView", () => {
  beforeEach(() => {
    setTokenProvider(() => null);
  });

  it("renders the search input and entity filters", async () => {
    const wrapper = await mountView();
    expect(wrapper.find("h1").text()).toBe("Search");
    expect(wrapper.findAll(".search-view__filter").length).toBe(8);
    expect(wrapper.find(".search-bar__input").exists()).toBe(true);
  });

  it("restores the query from the route and searches on mount", async () => {
    vi.stubGlobal(
      "fetch",
      makeFetch(1, [{ id: "track-1", title: "Waiting Room" }]),
    );
    const wrapper = await mountView({ q: "waiting", entities: "tracks" });
    await flushPromises();

    expect(wrapper.find(".search-view__link").text()).toBe("Waiting Room");
    expect(wrapper.find(".search-view__section-count").text()).toContain("1");
  });

  it("paginates a single section without changing others", async () => {
    vi.stubGlobal(
      "fetch",
      makeFetch(15, [{ id: "track-1", title: "Waiting Room" }]),
    );
    const wrapper = await mountView({ q: "waiting", entities: "tracks" });
    await flushPromises();

    const pagination = wrapper.findComponent({ name: "AppPagination" });
    expect(pagination.exists()).toBe(true);
    pagination.vm.$emit("update:page", 2);
    await flushPromises();

    const lastCall = vi.mocked(fetch).mock.calls.at(-1)?.[0];
    expect(lastCall).toContain("offset=10");
    expect(lastCall).toContain("limit=10");
  });

  it("toggles entity filters while keeping at least one active", async () => {
    vi.stubGlobal("fetch", makeFetch(1, []));
    const wrapper = await mountView({ q: "waiting", entities: "tracks" });
    await flushPromises();

    const labels = wrapper.findAll(".search-view__filter");
    const albums = labels.find((label) => label.text().includes("Albums"))!;
    const albumsInput = albums.find("input");
    expect(albumsInput.element.checked).toBe(false);

    await albumsInput.trigger("change");
    await flushPromises();

    expect(wrapper.findAll(".search-view__section").length).toBe(2);
    expect(wrapper.vm.activeEntities).toContain("albums");

    const tracksInput = labels
      .find((label) => label.text().includes("Tracks"))!
      .find("input");
    expect(tracksInput.element.checked).toBe(true);
  });

  it("navigates to an autocomplete suggestion when selected", async () => {
    vi.stubGlobal(
      "fetch",
      makeFetch(1, [{ id: "track-1", title: "Waiting Room" }]),
    );
    const wrapper = await mountView({ q: "waiting", entities: "tracks" });
    await flushPromises();

    const router = wrapper.vm.$router;
    const push = vi.spyOn(router, "push");

    const searchBar = wrapper.findComponent({ name: "SearchBar" });
    searchBar.vm.$emit("select-suggestion", {
      type: "track",
      id: "track-1",
      title: "Waiting Room",
      url: "/tracks/track-1",
    });
    await flushPromises();

    expect(push).toHaveBeenCalledWith("/tracks/track-1");
  });

  it("exposes the active entity list to the search bar", async () => {
    const wrapper = await mountView({ entities: "tracks,albums" });
    await flushPromises();

    const searchBar = wrapper.findComponent({ name: "SearchBar" });
    expect(searchBar.props("autocompleteEntities")).toEqual([
      "tracks",
      "albums",
    ]);
  });

  it("enables the fediverse suggestion entry when remote lookup is allowed", async () => {
    vi.stubGlobal("fetch", makeRemoteFetch({ remoteAvailable: true }));
    const wrapper = await mountView({ q: "@alice@remote.example" });
    await flushPromises();

    const searchBar = wrapper.findComponent({ name: "SearchBar" });
    expect(searchBar.props("remote")).toBe(true);
    const lookupCall = vi
      .mocked(fetch)
      .mock.calls.find(([url]) => String(url).includes("entities=remote"));
    expect(lookupCall).toBeDefined();
  });

  it("navigates to the internal URL returned by the remote lookup", async () => {
    vi.stubGlobal("fetch", makeRemoteFetch({ remoteAvailable: true }));
    const wrapper = await mountView({ q: "@alice@remote.example" });
    await flushPromises();

    const push = vi.spyOn(wrapper.vm.$router, "push");
    wrapper
      .findComponent({ name: "SearchBar" })
      .vm.$emit("remote-lookup", "@alice@remote.example");
    await flushPromises();

    const lookupCall = vi
      .mocked(fetch)
      .mock.calls.find(([url]) => String(url).includes("/remote/lookup"));
    expect(lookupCall).toBeDefined();
    expect(String(lookupCall![0])).toContain(
      encodeURIComponent("@alice@remote.example"),
    );
    expect(push).toHaveBeenCalledWith("/@alice@remote.example");
  });

  it("disables the fediverse entry when remote lookup is not available", async () => {
    vi.stubGlobal("fetch", makeRemoteFetch({ remoteAvailable: false }));
    const wrapper = await mountView({ q: "@alice@remote.example" });
    await flushPromises();

    const searchBar = wrapper.findComponent({ name: "SearchBar" });
    expect(searchBar.props("remote")).toBe(false);
  });

  it("shows the lookup error without remote internals", async () => {
    vi.stubGlobal(
      "fetch",
      makeRemoteFetch({ remoteAvailable: true, lookupStatus: 404 }),
    );
    const wrapper = await mountView({ q: "@alice@remote.example" });
    await flushPromises();

    wrapper
      .findComponent({ name: "SearchBar" })
      .vm.$emit("remote-lookup", "@alice@remote.example");
    await flushPromises();

    expect(wrapper.find(".search-view__remote-error").exists()).toBe(true);
  });

  function makeProviderFetch(options: {
    groups?: unknown[];
    importStatus?: number;
  }) {
    return vi.fn().mockImplementation((url: string, init?: RequestInit) => {
      if (url.includes("/search/providers")) {
        return Promise.resolve(
          makeResponse({ query: "q", providers: options.groups ?? [] }),
        );
      }
      if (url.includes("/import") && init?.method === "POST") {
        if (options.importStatus && options.importStatus !== 200) {
          return Promise.resolve({
            status: options.importStatus,
            ok: false,
            text: () => Promise.resolve(JSON.stringify({ detail: "nope" })),
            headers: new Headers(),
          });
        }
        return Promise.resolve(
          makeResponse({ kind: "track", provider_key: "p1", entity_id: "e1" }),
        );
      }
      return Promise.resolve(makeResponse([], { "X-Total-Count": "0" }));
    });
  }

  const providerGroup = {
    external_library_id: "lib-1",
    provider_type: "tidal",
    library_name: "My TIDAL",
    results: [
      {
        kind: "track",
        provider_key: "p1",
        title: "Provider Song",
        subtitle: "Artist",
        external_url: "https://tidal.com/browse/track/p1",
      },
    ],
    error: null,
  };

  it("renders provider result groups with an add action", async () => {
    vi.stubGlobal("fetch", makeProviderFetch({ groups: [providerGroup] }));
    const wrapper = await mountView({ q: "song", entities: "tracks" });
    await flushPromises();

    const section = wrapper.find(".search-view__section--provider");
    expect(section.exists()).toBe(true);
    expect(section.text()).toContain("Provider Song");
    const link = section.find("a.search-view__link");
    expect(link.attributes("href")).toBe("https://tidal.com/browse/track/p1");

    const addButton = section.findComponent({ name: "AppButton" });
    await addButton.trigger("click");
    await flushPromises();

    const importCall = vi
      .mocked(fetch)
      .mock.calls.find(([url]) => String(url).includes("/import"));
    expect(importCall).toBeDefined();
    expect(String(importCall![0])).toContain(
      "/external-libraries/lib-1/import",
    );
    expect(JSON.parse(String(importCall![1]?.body))).toEqual({
      kind: "track",
      provider_key: "p1",
    });
    expect(addButton.text()).toContain("Added");
    expect(addButton.props("disabled")).toBe(true);
  });

  it("surfaces a provider group error without breaking the page", async () => {
    vi.stubGlobal(
      "fetch",
      makeProviderFetch({
        groups: [{ ...providerGroup, results: [], error: "provider exploded" }],
      }),
    );
    const wrapper = await mountView({ q: "song", entities: "tracks" });
    await flushPromises();

    const section = wrapper.find(".search-view__section--provider");
    expect(section.exists()).toBe(true);
    expect(section.find(".search-view__section-error").text()).toContain(
      "provider exploded",
    );
  });

  it("shows an inline error when the import fails", async () => {
    vi.stubGlobal(
      "fetch",
      makeProviderFetch({ groups: [providerGroup], importStatus: 422 }),
    );
    const wrapper = await mountView({ q: "song", entities: "tracks" });
    await flushPromises();

    const section = wrapper.find(".search-view__section--provider");
    await section.findComponent({ name: "AppButton" }).trigger("click");
    await flushPromises();

    expect(section.find(".search-view__provider-error").exists()).toBe(true);
  });

  describe("direct URL lookup", () => {
    function makeUrlFetch(sections: unknown[] = [], providers: unknown[] = []) {
      return vi.fn().mockImplementation((url: string) => {
        if (url.includes("/search/providers")) {
          return Promise.resolve(makeResponse({ query: "q", providers }));
        }
        if (url.includes("/search/")) {
          return Promise.resolve(
            makeResponse({
              query: "q",
              remote_available: false,
              sections,
            }),
          );
        }
        return Promise.resolve(makeResponse([], { "X-Total-Count": "0" }));
      });
    }

    const trackSection = {
      entity: "tracks",
      total: 1,
      items: [
        {
          type: "track",
          id: "t-1",
          title: "Linked Track",
          subtitle: "URL Artist",
          image_url: null,
          url: "/tracks/t-1",
        },
      ],
    };

    it("resolves a pasted URL to a single direct result", async () => {
      vi.stubGlobal("fetch", makeUrlFetch([trackSection]));
      const wrapper = await mountView({
        q: "https://manganiello.music/tracks/t-1",
      });
      await flushPromises();

      const section = wrapper.find(".search-view__section");
      expect(section.find(".search-view__section-title").text()).toContain(
        "Tracks",
      );
      const link = wrapper.find(".search-view__link");
      expect(link.text()).toBe("Linked Track");
      expect(link.attributes("href")).toBe("/tracks/t-1");
      expect(section.find(".search-view__meta").text()).toBe("URL Artist");
    });

    it("hits the aggregate and provider endpoints — no per-entity lists", async () => {
      vi.stubGlobal("fetch", makeUrlFetch([trackSection]));
      await mountView({ q: "https://manganiello.music/tracks/t-1" });
      await flushPromises();

      const calledUrls = vi
        .mocked(fetch)
        .mock.calls.map(([url]) => String(url));
      expect(
        calledUrls.filter((u) => u.includes("/search/providers")),
      ).toHaveLength(1);
      expect(calledUrls.filter((u) => u.includes("/search/?"))).toHaveLength(1);
      expect(calledUrls.filter((u) => !u.includes("/search/"))).toHaveLength(0);
    });

    it("renders provider results for a pasted provider URL", async () => {
      const youtubeGroup = {
        ...providerGroup,
        provider_type: "youtube",
        library_name: "My YouTube",
        results: [
          {
            kind: "track",
            provider_key: "vid001",
            title: "YouTube Video",
            subtitle: "Channel",
            external_url: "https://www.youtube.com/watch?v=vid001",
          },
        ],
      };
      vi.stubGlobal("fetch", makeUrlFetch([], [youtubeGroup]));
      const wrapper = await mountView({
        q: "https://www.youtube.com/watch?v=vid001",
      });
      await flushPromises();

      const section = wrapper.find(".search-view__section--provider");
      expect(section.exists()).toBe(true);
      expect(section.text()).toContain("YouTube Video");
      // The direct lookup resolved to nothing, but the provider hit means
      // the page is not empty — no "No results" fallback.
      expect(wrapper.find(".search-view__section-empty").exists()).toBe(false);
    });

    it("hides provider groups with no results for a URL query", async () => {
      vi.stubGlobal(
        "fetch",
        makeUrlFetch([], [{ ...providerGroup, results: [] }]),
      );
      const wrapper = await mountView({
        q: "https://manganiello.music/tracks/missing",
      });
      await flushPromises();

      expect(wrapper.find(".search-view__section--provider").exists()).toBe(
        false,
      );
      expect(wrapper.find(".search-view__section-empty").text()).toBe(
        "No results found.",
      );
    });

    it("shows a no-results state when the URL resolves to nothing", async () => {
      vi.stubGlobal("fetch", makeUrlFetch([]));
      const wrapper = await mountView({
        q: "https://manganiello.music/tracks/missing",
      });
      await flushPromises();

      expect(wrapper.find(".search-view__section-empty").text()).toBe(
        "No results found.",
      );
      expect(wrapper.find(".search-view__link").exists()).toBe(false);
    });

    it("does not render unrelated entity sections for a URL query", async () => {
      vi.stubGlobal("fetch", makeUrlFetch([trackSection]));
      const wrapper = await mountView({
        q: "https://manganiello.music/tracks/t-1",
      });
      await flushPromises();

      const sections = wrapper.findAll(".search-view__section");
      expect(sections).toHaveLength(1);
      expect(sections[0].find(".search-view__section-title").text()).toContain(
        "Tracks",
      );
    });
  });
});
