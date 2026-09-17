import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { i18n } from "@/i18n";
import {
  getRemoteActor,
  getRemoteActorActivities,
  type RemoteActor,
} from "@/api/remote";
import { ApiError } from "@/api/client";
import RemoteProfileView from "./RemoteProfileView.vue";

vi.mock("@/api/remote", () => ({
  getRemoteActor: vi.fn(),
  getRemoteActorActivities: vi.fn(),
}));

function createActor(overrides?: Partial<RemoteActor>): RemoteActor {
  return {
    handle: "alice@remote.example",
    username: "alice",
    domain: "remote.example",
    actor_url: "https://remote.example/users/alice",
    display_name: "Alice Remote",
    summary: "A remote musician",
    avatar_url: null,
    profile_url: "https://remote.example/@alice",
    unavailable: false,
    url: "/@alice@remote.example",
    ...overrides,
  };
}

describe("RemoteProfileView", () => {
  let wrapper: ReturnType<typeof mount>;

  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(getRemoteActorActivities).mockResolvedValue({
      activities: [],
      total: 0,
    });
  });

  afterEach(() => {
    wrapper?.unmount();
    document.body.innerHTML = "";
  });

  async function mountView(handle = "alice@remote.example") {
    wrapper = mount(RemoteProfileView, {
      props: { handle },
      global: {
        plugins: [i18n],
        stubs: { ActivityCard: true },
      },
    });
    await flushPromises();
  }

  it("renders the remote actor profile", async () => {
    vi.mocked(getRemoteActor).mockResolvedValue(createActor());
    await mountView();

    expect(getRemoteActor).toHaveBeenCalledWith("alice@remote.example");
    expect(wrapper.find("h1").text()).toBe("Alice Remote");
    expect(wrapper.find(".remote-profile__handle").text()).toBe(
      "@alice@remote.example",
    );
    expect(wrapper.find(".remote-profile__badge").exists()).toBe(true);
    expect(wrapper.find(".remote-profile__origin").attributes("href")).toBe(
      "https://remote.example/@alice",
    );
  });

  it("strips a leading @ from the handle", async () => {
    vi.mocked(getRemoteActor).mockResolvedValue(createActor());
    await mountView("@alice@remote.example");
    expect(getRemoteActor).toHaveBeenCalledWith("alice@remote.example");
  });

  it("lists cached materialized activities", async () => {
    vi.mocked(getRemoteActor).mockResolvedValue(createActor());
    vi.mocked(getRemoteActorActivities).mockResolvedValue({
      activities: [{ id: "a1" } as never],
      total: 1,
    });
    await mountView();

    const cards = wrapper.findAllComponents({ name: "ActivityCard" });
    expect(cards.length).toBe(1);
  });

  it("shows the empty state when no activities are cached", async () => {
    vi.mocked(getRemoteActor).mockResolvedValue(createActor());
    await mountView();
    expect(wrapper.find(".remote-profile__empty").exists()).toBe(true);
  });

  it("renders the actor summary HTML as safe segments", async () => {
    vi.mocked(getRemoteActor).mockResolvedValue(
      createActor({
        summary:
          "<p>A remote <strong>musician</strong> " +
          '<a href="https://remote.example/page">page</a></p>' +
          "<script>alert(1)</script>",
      }),
    );
    await mountView();

    const summary = wrapper.find(".remote-profile__summary");
    expect(summary.exists()).toBe(true);
    // HTML renders as text/links/formatting — tags and scripts never do.
    expect(summary.text()).toContain("A remote musician");
    expect(summary.find("strong").exists()).toBe(false);
    expect(summary.find(".rich-content__mark--bold").text()).toBe("musician");
    expect(summary.find("a").attributes("href")).toBe(
      "https://remote.example/page",
    );
    expect(summary.text()).not.toContain("alert");
  });

  it("shows the unavailable notice for tombstoned actors", async () => {
    vi.mocked(getRemoteActor).mockResolvedValue(
      createActor({ unavailable: true }),
    );
    await mountView();
    expect(wrapper.find(".remote-profile__unavailable").exists()).toBe(true);
  });

  it("renders an error when the actor cannot be resolved", async () => {
    vi.mocked(getRemoteActor).mockRejectedValue(
      new ApiError("Remote actor not found", 404),
    );
    await mountView();
    expect(wrapper.find(".remote-profile__error").exists()).toBe(true);
  });
});
