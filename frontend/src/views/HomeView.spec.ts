import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { createRouter, createMemoryHistory } from "vue-router";
import { setActivePinia, createPinia } from "pinia";
import { i18n } from "@/i18n";
import { useAuthStore } from "@/stores/auth";
import * as timelineApi from "@/api/timeline";
import * as instanceApi from "@/api/instance";
import * as historyApi from "@/api/history";
import * as tracksApi from "@/api/tracks";
import * as albumsApi from "@/api/albums";
import * as librariesApi from "@/api/libraries";
import * as usersApi from "@/api/users";
import * as genresApi from "@/api/genres";
import type { UserResponse } from "@/api/users";
import type { ActivityResponse } from "@/api/activities";
import HomeView from "./HomeView.vue";

vi.mock("@/api/timeline", () => ({ listTimeline: vi.fn() }));
vi.mock("@/api/instance", () => ({
  getInstance: vi.fn(),
  getInstanceStats: vi.fn(),
}));
vi.mock("@/api/history", () => ({ listHistory: vi.fn() }));
vi.mock("@/api/tracks", () => ({
  listTracks: vi.fn(),
  getTrack: vi.fn(),
}));
vi.mock("@/api/albums", () => ({ listAlbums: vi.fn() }));
vi.mock("@/api/libraries", () => ({ listLibraries: vi.fn() }));
vi.mock("@/api/users", () => ({ listPublicUsers: vi.fn() }));
vi.mock("@/api/genres", () => ({ listGenres: vi.fn() }));

function createTestRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: "/", component: { template: "<div/>" } },
      { path: "/:pathMatch(.*)*", component: { template: "<div/>" } },
    ],
  });
}

const activityCardStub = {
  template: '<div class="activity-card-stub">{{ activity.id }}</div>',
  props: ["activity"],
};

function makeActivity(id: string): ActivityResponse {
  return {
    id,
    entity_type: "track",
    entity_id: "track-1",
    activity_type: "create",
    source_type: "local",
    source_actor: "https://example.com/users/alice",
    source_id: `https://example.com/objects/${id}`,
    visibility: "public",
    published_at: "2026-01-01T00:00:00Z",
    mentions: [],
    like_count: 0,
    boost_count: 0,
    reply_count: 0,
    quote_count: 0,
    liked: false,
    boosted: false,
    can_interact: false,
  };
}

function makeInstance(overrides: Record<string, unknown> = {}) {
  return {
    uri: "test.example.com",
    title: "Test Hive",
    description: "A test instance.",
    short_description: "A test instance.",
    email: "",
    version: "Songhive 1.2.3",
    songhive_version: "1.2.3",
    stats: { user_count: 0, status_count: 0, domain_count: 0 },
    thumbnail: null,
    languages: ["en"],
    registrations: true,
    approval_required: false,
    invites_enabled: false,
    federation_enabled: false,
    single_user: null,
    ...overrides,
  } as instanceApi.InstanceInfo;
}

function signIn() {
  const authStore = useAuthStore();
  authStore.user = {
    id: "u1",
    username: "bob",
    display_name: "Bob",
    links: [],
  } as UserResponse;
  authStore.status = "authenticated";
}

describe("HomeView", () => {
  let wrapper: ReturnType<typeof mount> | undefined;

  beforeEach(() => {
    setActivePinia(createPinia());
    localStorage.clear();
    vi.clearAllMocks();

    vi.mocked(instanceApi.getInstance).mockResolvedValue(makeInstance());
    vi.mocked(instanceApi.getInstanceStats).mockRejectedValue(
      new Error("not found"),
    );
    vi.mocked(timelineApi.listTimeline).mockResolvedValue({
      activities: [],
      next_cursor: null,
    });
    vi.mocked(historyApi.listHistory).mockResolvedValue({
      items: [],
      page: 1,
      pageSize: 12,
    });
    vi.mocked(tracksApi.listTracks).mockResolvedValue([]);
    vi.mocked(albumsApi.listAlbums).mockResolvedValue([]);
    vi.mocked(librariesApi.listLibraries).mockResolvedValue([]);
    vi.mocked(usersApi.listPublicUsers).mockResolvedValue({
      users: [],
      offset: 0,
      total: 0,
    });
    vi.mocked(genresApi.listGenres).mockResolvedValue({
      items: [],
      offset: 0,
      total: 0,
    });
  });

  afterEach(() => {
    wrapper?.unmount();
    document.body.innerHTML = "";
  });

  async function mountHome() {
    wrapper = mount(HomeView, {
      attachTo: document.body,
      global: {
        plugins: [createTestRouter()],
        stubs: { ActivityCard: activityCardStub },
      },
    });
    await flushPromises();
    return wrapper;
  }

  it("renders the anonymous landing page", async () => {
    const w = await mountHome();

    expect(w.text()).toContain("Test Hive");
    expect(w.text()).toContain(
      i18n.global.t("pages.home.feed.latestOnInstance"),
    );
    // Anonymous visitors get no greeting and no "mine" scope, but they can
    // switch between the instance and federated feeds.
    expect(w.find(".home-greeting").exists()).toBe(false);
    expect(
      w.text().includes(i18n.global.t("pages.home.feed.scopes.mine")),
    ).toBe(false);
    expect(w.text()).toContain(
      i18n.global.t("pages.home.feed.scopes.instance"),
    );
    expect(w.text()).toContain(
      i18n.global.t("pages.home.feed.scopes.federated"),
    );

    expect(timelineApi.listTimeline).toHaveBeenCalledWith(
      expect.objectContaining({ scope: "instance" }),
    );
    // Anonymous visitors never fetch private data.
    expect(historyApi.listHistory).not.toHaveBeenCalled();
  });

  it("omits empty shelves entirely", async () => {
    const w = await mountHome();

    expect(
      w.text().includes(i18n.global.t("pages.home.shelves.recentAlbums")),
    ).toBe(false);
    expect(
      w.text().includes(i18n.global.t("pages.home.shelves.publicLibraries")),
    ).toBe(false);
  });

  it("shows anonymous shelves when public data exists", async () => {
    vi.mocked(albumsApi.listAlbums).mockResolvedValue([
      {
        id: "album-1",
        title: "Meadowland",
        artist_id: null,
        release_year: null,
        cover_url: null,
        visibility: "public",
      } as albumsApi.AlbumResponse,
    ]);
    vi.mocked(usersApi.listPublicUsers).mockResolvedValue({
      users: [
        {
          username: "alice",
          display_name: "Alice",
        } as usersApi.PublicUserResponse,
      ],
      offset: 0,
      total: 1,
    });

    const w = await mountHome();

    expect(w.text()).toContain("Meadowland");
    expect(w.text()).toContain("Alice");
  });

  it("renders the authenticated home with greeting and scope tabs", async () => {
    signIn();
    const w = await mountHome();

    expect(w.find(".home-greeting").exists()).toBe(true);
    expect(w.text()).toContain("Bob");
    expect(w.text()).toContain(i18n.global.t("pages.home.feed.scopes.mine"));
    expect(w.text()).toContain(
      i18n.global.t("pages.home.feed.scopes.instance"),
    );
    expect(w.text()).toContain(
      i18n.global.t("pages.home.feed.scopes.federated"),
    );
    // The hero is anonymous-only.
    expect(w.find(".home-hero").exists()).toBe(false);
  });

  it("reloads the feed with the federated scope", async () => {
    signIn();
    const w = await mountHome();
    vi.clearAllMocks();

    const federatedTab = w
      .findAll('[role="tab"]')
      .find(
        (el) => el.text() === i18n.global.t("pages.home.feed.scopes.federated"),
      );
    expect(federatedTab).toBeDefined();
    await federatedTab!.trigger("click");

    expect(timelineApi.listTimeline).toHaveBeenCalledWith(
      expect.objectContaining({ scope: "federated" }),
    );
  });

  it("puts shelves and the feed behind separate tabs", async () => {
    vi.mocked(albumsApi.listAlbums).mockResolvedValue([
      {
        id: "album-1",
        title: "Meadowland",
        artist_id: null,
        release_year: null,
        cover_url: null,
        visibility: "public",
      } as albumsApi.AlbumResponse,
    ]);

    const w = await mountHome();

    const tab = (label: string) =>
      w.findAll('[role="tab"]').find((el) => el.text() === label);
    const musicTab = tab(i18n.global.t("pages.home.tabs.music"));
    const activityTab = tab(i18n.global.t("pages.home.tabs.activity"));
    expect(musicTab).toBeDefined();
    expect(activityTab).toBeDefined();

    const panels = w.findAll(".home__panel");
    expect(panels).toHaveLength(2);

    // Music shelves are the default panel; the feed stays mounted but hidden.
    expect(musicTab!.attributes("aria-selected")).toBe("true");
    expect(activityTab!.attributes("aria-selected")).toBe("false");
    expect(panels[0].isVisible()).toBe(true);
    expect(panels[1].isVisible()).toBe(false);
    expect(panels[0].text()).toContain("Meadowland");

    await activityTab!.trigger("click");

    expect(panels[0].isVisible()).toBe(false);
    expect(panels[1].isVisible()).toBe(true);
    expect(musicTab!.attributes("aria-selected")).toBe("false");
    expect(activityTab!.attributes("aria-selected")).toBe("true");

    await musicTab!.trigger("click");
    expect(panels[0].isVisible()).toBe(true);
    expect(panels[1].isVisible()).toBe(false);
  });

  it("loads the mine scope for authenticated users", async () => {
    signIn();
    vi.mocked(timelineApi.listTimeline).mockResolvedValue({
      activities: [makeActivity("a1")],
      next_cursor: null,
    });

    const w = await mountHome();

    expect(timelineApi.listTimeline).toHaveBeenCalledWith(
      expect.objectContaining({ scope: "mine" }),
    );
    expect(w.find(".activity-card-stub").exists()).toBe(true);
  });

  it("fetches authenticated shelves", async () => {
    signIn();
    vi.mocked(historyApi.listHistory).mockResolvedValue({
      items: [
        {
          id: "h1",
          track_id: "track-1",
          title: "Song One",
          artist: "The Larks",
          created_at: "2026-01-01T00:00:00Z",
        },
        {
          id: "h2",
          track_id: "track-1",
          title: "Song One",
          artist: "The Larks",
          created_at: "2026-01-02T00:00:00Z",
        },
      ],
      page: 1,
      pageSize: 12,
    });

    const w = await mountHome();

    // Jump-back-in dedupes history entries by track.
    expect(w.text()).toContain(i18n.global.t("pages.home.shelves.jumpBackIn"));
    expect(w.findAll(".home-track-card")).toHaveLength(1);
    expect(w.text()).toContain("Song One");
  });
});
