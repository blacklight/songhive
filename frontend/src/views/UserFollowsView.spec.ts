import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { createRouter, createMemoryHistory } from "vue-router";
import { setActivePinia, createPinia } from "pinia";
import { i18n } from "@/i18n";
import { listFollows } from "@/api/users";
import type { FollowingResponse } from "@/api/users";
import realRouter from "@/router";
import UserFollowsView from "./UserFollowsView.vue";

vi.mock("@/api/users", () => ({
  listFollows: vi.fn(),
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
        path: "/@:username/follows",
        name: "userFollows",
        component: UserFollowsView,
      },
    ],
  });
}

function createFollow(
  actorUrl: string,
  name: string,
  state: "accepted" | "pending" = "accepted",
): FollowingResponse {
  return {
    actor_url: actorUrl,
    handle: null,
    display_name: name,
    avatar_url: null,
    state,
    followed_at: "2025-01-02T00:00:00Z",
    local_username: null,
  };
}

describe("userFollows route", () => {
  it("resolves /@:username/follows", () => {
    const resolved = realRouter.resolve({
      name: "userFollows",
      params: { username: "alice" },
    });
    expect(resolved.path).toBe("/@alice/follows");
  });
});

describe("UserFollowsView", () => {
  let wrapper: ReturnType<typeof mount>;

  beforeEach(() => {
    setActivePinia(createPinia());
    vi.clearAllMocks();
    vi.mocked(listFollows).mockResolvedValue({
      follows: [],
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
    await router.push("/@alice/follows");
    await router.isReady();
    wrapper = mount(UserFollowsView, {
      global: { plugins: [router, i18n] },
    });
    await flushPromises();
  }

  it("fetches and renders follows for the route username", async () => {
    vi.mocked(listFollows).mockResolvedValue({
      follows: [
        createFollow("https://remote.example/users/bob", "Bob Remote"),
        createFollow("https://remote.example/users/carol", "Carol Remote"),
      ],
      offset: 0,
      total: 2,
    });

    await mountView();

    expect(listFollows).toHaveBeenCalledWith("alice", {
      limit: 20,
      offset: 0,
    });
    expect(wrapper.text()).toContain("Bob Remote");
    expect(wrapper.text()).toContain("Carol Remote");
  });

  it("links back to the profile", async () => {
    await mountView();

    const back = wrapper.find(".user-follows__back");
    expect(back.attributes("href")).toBe("/@alice");
    expect(back.text()).toBe("@alice");
  });

  it("shows the empty state when there are no follows", async () => {
    await mountView();

    expect(wrapper.text()).toContain(
      i18n.global.t("profile.followsPage.empty"),
    );
  });

  it("shows an error when loading fails", async () => {
    vi.mocked(listFollows).mockRejectedValue(new Error("boom"));

    await mountView();

    expect(wrapper.find(".user-follows__error").exists()).toBe(true);
  });

  it("marks pending follows with a badge", async () => {
    vi.mocked(listFollows).mockResolvedValue({
      follows: [
        createFollow("https://remote.example/users/bob", "Bob Remote"),
        createFollow(
          "https://remote.example/users/carol",
          "Carol Remote",
          "pending",
        ),
      ],
      offset: 0,
      total: 2,
    });

    await mountView();

    const badges = wrapper.findAll(".user-follows__pending");
    expect(badges).toHaveLength(1);
    expect(badges[0].text()).toBe(i18n.global.t("profile.followsPage.pending"));
  });

  it("appends the next page when loading more", async () => {
    const fetcher = vi.mocked(listFollows);
    fetcher
      .mockResolvedValueOnce({
        follows: Array.from({ length: 20 }, (_, i) =>
          createFollow(`https://remote.example/users/u${i}`, `User ${i}`),
        ),
        offset: 0,
        total: 21,
      })
      .mockResolvedValueOnce({
        follows: [createFollow("https://remote.example/users/u20", "User 20")],
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
