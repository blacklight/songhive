import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { createRouter, createMemoryHistory } from "vue-router";
import { setActivePinia, createPinia } from "pinia";
import { i18n } from "@/i18n";
import { listFollowers } from "@/api/users";
import type { FollowerResponse } from "@/api/users";
import realRouter from "@/router";
import UserFollowersView from "./UserFollowersView.vue";

vi.mock("@/api/users", () => ({
  listFollowers: vi.fn(),
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
});
