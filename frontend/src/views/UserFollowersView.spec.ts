import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { createRouter, createMemoryHistory } from "vue-router";
import { setActivePinia, createPinia } from "pinia";
import { i18n } from "@/i18n";
import {
  acceptFollowRequest,
  listFollowers,
  listFollowRequests,
  rejectFollowRequest,
} from "@/api/users";
import type { FollowerResponse, FollowRequestResponse } from "@/api/users";
import { useAuthStore } from "@/stores/auth";
import realRouter from "@/router";
import UserFollowersView from "./UserFollowersView.vue";

vi.mock("@/api/users", () => ({
  listFollowers: vi.fn(),
  listFollowRequests: vi.fn(),
  acceptFollowRequest: vi.fn(),
  rejectFollowRequest: vi.fn(),
}));

function createTestRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      {
        path: "/@:username",
        name: "userProfile",
        component: { template: "<div/>" },
      },
      {
        path: "/@:username/followers",
        name: "userFollowers",
        component: UserFollowersView,
      },
    ],
  });
}

function createFollower(
  actorUrl: string,
  name: string,
  followedAt: string,
): FollowerResponse {
  return {
    actor_url: actorUrl,
    display_name: name,
    avatar_url: null,
    followed_at: followedAt,
  };
}

function createRequest(
  actorUrl: string,
  name: string,
  requestedAt: string,
): FollowRequestResponse {
  return {
    actor_url: actorUrl,
    display_name: name,
    avatar_url: null,
    requested_at: requestedAt,
  };
}

describe("userFollowers route", () => {
  it("resolves /@:username/followers", () => {
    const resolved = realRouter.resolve({
      name: "userFollowers",
      params: { username: "alice" },
    });
    expect(resolved.path).toBe("/@alice/followers");
  });
});

describe("UserFollowersView", () => {
  let wrapper: ReturnType<typeof mount>;

  beforeEach(() => {
    setActivePinia(createPinia());
    vi.clearAllMocks();
    vi.mocked(listFollowers).mockResolvedValue({
      followers: [],
      offset: 0,
      total: 0,
    });
    vi.mocked(listFollowRequests).mockResolvedValue({
      requests: [],
      offset: 0,
      total: 0,
    });
    vi.mocked(acceptFollowRequest).mockResolvedValue(undefined);
    vi.mocked(rejectFollowRequest).mockResolvedValue(undefined);
  });

  afterEach(() => {
    wrapper?.unmount();
    document.body.innerHTML = "";
  });

  async function mountView() {
    const router = createTestRouter();
    await router.push("/@alice/followers");
    await router.isReady();
    wrapper = mount(UserFollowersView, {
      global: { plugins: [router, i18n] },
    });
    await flushPromises();
  }

  it("fetches and renders followers for the route username", async () => {
    vi.mocked(listFollowers).mockResolvedValue({
      followers: [
        createFollower(
          "https://remote.example/users/bob",
          "Bob Remote",
          "2025-01-02T00:00:00Z",
        ),
        createFollower(
          "https://remote.example/users/carol",
          "Carol Remote",
          "2025-01-01T00:00:00Z",
        ),
      ],
      offset: 0,
      total: 2,
    });

    await mountView();

    expect(listFollowers).toHaveBeenCalledWith("alice", {
      limit: 20,
      offset: 0,
    });
    expect(wrapper.text()).toContain("Bob Remote");
    expect(wrapper.text()).toContain("Carol Remote");
  });

  it("links back to the profile", async () => {
    await mountView();

    const back = wrapper.find(".user-followers__back");
    expect(back.attributes("href")).toBe("/@alice");
    expect(back.text()).toBe("@alice");
  });

  it("shows the empty state when there are no followers", async () => {
    await mountView();

    expect(wrapper.text()).toContain(
      i18n.global.t("profile.followersPage.empty"),
    );
  });

  it("shows an error when loading fails", async () => {
    vi.mocked(listFollowers).mockRejectedValue(new Error("boom"));

    await mountView();

    expect(wrapper.find(".user-followers__error").exists()).toBe(true);
  });

  it("appends the next page when loading more", async () => {
    const fetcher = vi.mocked(listFollowers);
    fetcher
      .mockResolvedValueOnce({
        followers: Array.from({ length: 20 }, (_, i) =>
          createFollower(
            `https://remote.example/users/u${i}`,
            `User ${i}`,
            "2025-01-01T00:00:00Z",
          ),
        ),
        offset: 0,
        total: 21,
      })
      .mockResolvedValueOnce({
        followers: [
          createFollower(
            "https://remote.example/users/u20",
            "User 20",
            "2024-12-31T00:00:00Z",
          ),
        ],
        offset: 20,
        total: 21,
      });

    await mountView();

    expect(wrapper.text()).toContain("User 0");
    const loadMore = wrapper
      .findAll("button")
      .find((b) => b.text() === i18n.global.t("common.loadMore"));
    expect(loadMore).toBeDefined();

    await loadMore?.trigger("click");
    await flushPromises();

    expect(fetcher).toHaveBeenLastCalledWith("alice", {
      limit: 20,
      offset: 20,
    });
    expect(wrapper.text()).toContain("User 0");
    expect(wrapper.text()).toContain("User 20");
    expect(
      wrapper
        .findAll("button")
        .find((b) => b.text() === i18n.global.t("common.loadMore")),
    ).toBeUndefined();
  });

  it("hides the requests tab for non-owners", async () => {
    await mountView();

    expect(wrapper.find(".user-followers__tabs").exists()).toBe(false);
    expect(listFollowRequests).not.toHaveBeenCalled();
  });

  describe("as the profile owner", () => {
    beforeEach(() => {
      const store = useAuthStore();
      store.user = { username: "alice" } as never;
    });

    function requestsTab() {
      return wrapper
        .findAll("button")
        .find(
          (b) =>
            b.text() === i18n.global.t("profile.followersPage.tabs.requests"),
        );
    }

    it("shows the requests tab only to the owner", async () => {
      await mountView();

      expect(wrapper.find(".user-followers__tabs").exists()).toBe(true);
      expect(requestsTab()).toBeDefined();
      // Requests are loaded lazily when the tab is opened.
      expect(listFollowRequests).not.toHaveBeenCalled();
    });

    it("lists pending requests with accept and reject actions", async () => {
      vi.mocked(listFollowRequests).mockResolvedValue({
        requests: [
          createRequest(
            "https://remote.example/users/bob",
            "Bob Remote",
            "2025-01-02T00:00:00Z",
          ),
        ],
        offset: 0,
        total: 1,
      });

      await mountView();
      await requestsTab()!.trigger("click");
      await flushPromises();

      expect(listFollowRequests).toHaveBeenCalledWith({
        limit: 20,
        offset: 0,
      });
      expect(wrapper.text()).toContain("Bob Remote");
      expect(
        wrapper
          .findAll("button")
          .some(
            (b) => b.text() === i18n.global.t("profile.followersPage.accept"),
          ),
      ).toBe(true);
      expect(
        wrapper
          .findAll("button")
          .some(
            (b) => b.text() === i18n.global.t("profile.followersPage.reject"),
          ),
      ).toBe(true);
    });

    it("accepting a request calls the API and removes the row", async () => {
      vi.mocked(listFollowRequests).mockResolvedValue({
        requests: [
          createRequest(
            "https://remote.example/users/bob",
            "Bob Remote",
            "2025-01-02T00:00:00Z",
          ),
        ],
        offset: 0,
        total: 1,
      });

      await mountView();
      await requestsTab()!.trigger("click");
      await flushPromises();

      const accept = wrapper
        .findAll("button")
        .find(
          (b) => b.text() === i18n.global.t("profile.followersPage.accept"),
        );
      await accept!.trigger("click");
      await flushPromises();

      expect(acceptFollowRequest).toHaveBeenCalledWith(
        "https://remote.example/users/bob",
      );
      expect(wrapper.text()).not.toContain("Bob Remote");
      expect(wrapper.text()).toContain(
        i18n.global.t("profile.followersPage.requestsEmpty"),
      );
      // The followers list refreshes to pick up the new follower.
      expect(listFollowers).toHaveBeenCalledTimes(2);
    });

    it("rejecting a request calls the API and removes the row", async () => {
      vi.mocked(listFollowRequests).mockResolvedValue({
        requests: [
          createRequest(
            "https://remote.example/users/bob",
            "Bob Remote",
            "2025-01-02T00:00:00Z",
          ),
        ],
        offset: 0,
        total: 1,
      });

      await mountView();
      await requestsTab()!.trigger("click");
      await flushPromises();

      const reject = wrapper
        .findAll("button")
        .find(
          (b) => b.text() === i18n.global.t("profile.followersPage.reject"),
        );
      await reject!.trigger("click");
      await flushPromises();

      expect(rejectFollowRequest).toHaveBeenCalledWith(
        "https://remote.example/users/bob",
      );
      expect(wrapper.text()).not.toContain("Bob Remote");
    });

    it("shows the empty state for a resolved requests list", async () => {
      await mountView();
      await requestsTab()!.trigger("click");
      await flushPromises();

      expect(wrapper.text()).toContain(
        i18n.global.t("profile.followersPage.requestsEmpty"),
      );
    });
  });
});
