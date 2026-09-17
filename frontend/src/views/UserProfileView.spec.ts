import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { createRouter, createMemoryHistory } from "vue-router";
import { setActivePinia, createPinia } from "pinia";
import { i18n } from "@/i18n";
import { getPublic } from "@/api/users";
import type { PublicUserResponse } from "@/api/users";
import UserProfileView from "./UserProfileView.vue";

vi.mock("@/api/users", () => ({
  getPublic: vi.fn(),
}));

vi.mock("@/api/remote", () => ({
  getRemoteActor: vi.fn(),
  getRemoteActorActivities: vi.fn(),
  getRemoteObject: vi.fn(),
  getRemoteResource: vi.fn(),
  remoteLookup: vi.fn(),
}));

vi.mock("@/api/instance", () => ({
  getInstance: vi.fn().mockResolvedValue({ uri: "https://music.example.com" }),
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
        component: { template: "<div/>" },
      },
      ...[
        "userProfilePosts",
        "userProfileActivity",
        "userProfileTracks",
        "userProfileAlbums",
        "userProfileLibraries",
        "userProfilePlaylists",
      ].map((name) => ({
        path: `/@:username/${name}`,
        name,
        component: { template: "<div/>" },
      })),
    ],
  });
}

function createProfile(overrides?: Partial<PublicUserResponse>) {
  return {
    id: "user-1",
    username: "alice",
    display_name: "Alice",
    avatar_url: null,
    bio: null,
    links: [],
    role: "user",
    created_at: "2025-01-01T00:00:00Z",
    followers_count: 0,
    ...overrides,
  } as PublicUserResponse;
}

describe("UserProfileView", () => {
  let wrapper: ReturnType<typeof mount>;

  beforeEach(() => {
    setActivePinia(createPinia());
    vi.clearAllMocks();
  });

  afterEach(() => {
    wrapper?.unmount();
    document.body.innerHTML = "";
  });

  async function mountView(profile: PublicUserResponse) {
    vi.mocked(getPublic).mockResolvedValue(profile);
    const router = createTestRouter();
    await router.push("/@alice");
    await router.isReady();
    wrapper = mount(UserProfileView, {
      global: { plugins: [router, i18n], stubs: { RouterView: true } },
    });
    await flushPromises();
  }

  it("links the follower count to the followers page", async () => {
    await mountView(createProfile({ followers_count: 3 }));

    const link = wrapper.find(".user-profile__followers");
    expect(link.attributes("href")).toBe("/@alice/followers");
    expect(link.text()).toBe(i18n.global.t("profile.followers", { count: 3 }));
  });

  it("renders the pluralized count when there are no followers", async () => {
    await mountView(createProfile({ followers_count: 0 }));

    const link = wrapper.find(".user-profile__followers");
    expect(link.text()).toBe(i18n.global.t("profile.followers", { count: 0 }));
  });

  it("dispatches user@domain handles to the remote profile view", async () => {
    const { getRemoteActor, getRemoteActorActivities } =
      await import("@/api/remote");
    vi.mocked(getRemoteActor).mockResolvedValue({
      handle: "bob@remote.example",
      username: "bob",
      domain: "remote.example",
      actor_url: "https://remote.example/users/bob",
      display_name: "Bob",
      unavailable: false,
      url: "/@bob@remote.example",
    });
    vi.mocked(getRemoteActorActivities).mockResolvedValue({
      activities: [],
      total: 0,
    });

    const router = createTestRouter();
    await router.push("/@bob@remote.example");
    await router.isReady();
    wrapper = mount(UserProfileView, {
      global: { plugins: [router, i18n], stubs: { RouterView: true } },
    });
    await flushPromises();

    expect(getPublic).not.toHaveBeenCalled();
    expect(getRemoteActor).toHaveBeenCalledWith("bob@remote.example");
    expect(wrapper.find(".remote-profile__handle").text()).toBe(
      "@bob@remote.example",
    );
  });
});
