import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { createRouter, createMemoryHistory } from "vue-router";
import { setActivePinia, createPinia } from "pinia";
import { i18n } from "@/i18n";
import {
  getPublic,
  subscribeToUserActivity,
  unsubscribeFromUserActivity,
} from "@/api/users";
import type { PublicUserResponse, UserResponse } from "@/api/users";
import { useAuthStore } from "@/stores/auth";
import UserProfileView from "./UserProfileView.vue";

vi.mock("@/api/users", () => ({
  getPublic: vi.fn(),
  followActor: vi.fn(),
  unfollowActor: vi.fn(),
  subscribeToUserActivity: vi.fn(),
  unsubscribeFromUserActivity: vi.fn(),
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
      {
        path: "/@:username/follows",
        name: "userFollows",
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
    activity_subscribed: false,
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

  it("renders the fully-qualified @user@domain handle", async () => {
    await mountView(createProfile());

    expect(wrapper.find(".user-profile__handle").text()).toContain(
      "@alice@music.example.com",
    );
  });

  it("copies the fully-qualified @user@domain handle", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });
    await mountView(createProfile());

    await wrapper.find(".user-profile__handle button").trigger("click");

    expect(writeText).toHaveBeenCalledWith("@alice@music.example.com");
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

  function signInAs(username: string) {
    const authStore = useAuthStore();
    authStore.user = {
      id: "viewer-1",
      username,
      role: "user",
    } as UserResponse;
  }

  it("hides the activity bell for anonymous visitors", async () => {
    await mountView(createProfile());
    expect(wrapper.find(".user-profile__activity-bell").exists()).toBe(false);
  });

  it("hides the activity bell on the viewer's own profile", async () => {
    signInAs("alice");
    await mountView(createProfile());
    expect(wrapper.find(".user-profile__activity-bell").exists()).toBe(false);
  });

  it("shows the activity bell to signed-in visitors on another profile", async () => {
    signInAs("viewer");
    await mountView(createProfile());

    const bell = wrapper.find(".user-profile__activity-bell");
    expect(bell.exists()).toBe(true);
    expect(bell.attributes("aria-pressed")).toBe("false");
    expect(bell.attributes("title")).toBe(
      i18n.global.t("profile.activityNotifications.subscribe"),
    );
  });

  it("reflects the subscribed state on the activity bell", async () => {
    signInAs("viewer");
    await mountView(createProfile({ activity_subscribed: true }));

    const bell = wrapper.find(".user-profile__activity-bell");
    expect(bell.attributes("aria-pressed")).toBe("true");
    expect(bell.attributes("title")).toBe(
      i18n.global.t("profile.activityNotifications.unsubscribe"),
    );
  });

  it("toggles the activity subscription through the bell", async () => {
    signInAs("viewer");
    vi.mocked(subscribeToUserActivity).mockResolvedValue({
      activity_subscribed: true,
    });
    await mountView(createProfile());

    const bell = wrapper.find(".user-profile__activity-bell");
    await bell.trigger("click");
    await flushPromises();

    expect(subscribeToUserActivity).toHaveBeenCalledWith("alice");
    expect(bell.attributes("aria-pressed")).toBe("true");

    await bell.trigger("click");
    await flushPromises();

    expect(unsubscribeFromUserActivity).toHaveBeenCalledWith("alice");
    expect(bell.attributes("aria-pressed")).toBe("false");
  });
});
