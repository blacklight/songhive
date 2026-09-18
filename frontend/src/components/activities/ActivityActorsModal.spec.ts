import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { createRouter, createMemoryHistory } from "vue-router";
import { setActivePinia, createPinia } from "pinia";
import { i18n } from "@/i18n";
import { listActivityBoosts, listActivityLikes } from "@/api/activities";
import type { ActivityActorResponse } from "@/api/activities";
import ActivityActorsModal from "./ActivityActorsModal.vue";

vi.mock("@/api/activities", () => ({
  listActivityLikes: vi.fn(),
  listActivityBoosts: vi.fn(),
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
      { path: "/:pathMatch(.*)*", component: { template: "<div/>" } },
    ],
  });
}

function createActor(
  overrides: Partial<ActivityActorResponse>,
): ActivityActorResponse {
  return {
    actor: "https://remote.example/users/bob",
    handle: "@bob@remote.example",
    display_name: "Bob Remote",
    avatar_url: null,
    username: null,
    profile_url: "https://remote.example/@bob",
    ...overrides,
  };
}

describe("ActivityActorsModal", () => {
  let wrapper: ReturnType<typeof mount>;

  beforeEach(() => {
    setActivePinia(createPinia());
    vi.clearAllMocks();
    vi.mocked(listActivityLikes).mockResolvedValue({ actors: [] });
    vi.mocked(listActivityBoosts).mockResolvedValue({ actors: [] });
  });

  afterEach(() => {
    wrapper?.unmount();
    document.body.innerHTML = "";
  });

  async function mountModal(kind: "likes" | "boosts" = "likes") {
    wrapper = mount(ActivityActorsModal, {
      props: { open: true, activityId: "act-1", kind },
      global: { plugins: [createTestRouter(), i18n] },
      attachTo: document.body,
    });
    await flushPromises();
  }

  it("routes remote actors to the internal remote profile", async () => {
    vi.mocked(listActivityLikes).mockResolvedValue({
      actors: [createActor({})],
    });

    await mountModal();

    const link = document.body.querySelector<HTMLAnchorElement>(
      "a.activity-actors__actor",
    );
    expect(link?.getAttribute("href")).toBe("/@bob@remote.example");
    expect(link?.textContent).toContain("Bob Remote");
    expect(link?.textContent).toContain("@bob@remote.example");
  });

  it("routes local actors to their profile page", async () => {
    vi.mocked(listActivityLikes).mockResolvedValue({
      actors: [
        createActor({
          actor: "urn:songhive:user:alice",
          handle: "@alice",
          username: "alice",
          profile_url: null,
        }),
      ],
    });

    await mountModal();

    const link = document.body.querySelector<HTMLAnchorElement>(
      "a.activity-actors__actor",
    );
    expect(link?.getAttribute("href")).toBe("/@alice");
  });

  it("keeps an external link for handles without a host part", async () => {
    vi.mocked(listActivityLikes).mockResolvedValue({
      actors: [
        createActor({
          handle: "@bob",
          profile_url: "https://remote.example/@bob",
        }),
      ],
    });

    await mountModal();

    const link = document.body.querySelector<HTMLAnchorElement>(
      "a.activity-actors__actor",
    );
    expect(link?.getAttribute("href")).toBe("https://remote.example/@bob");
    expect(link?.getAttribute("target")).toBe("_blank");
  });

  it("fetches boosts when kind is boosts", async () => {
    await mountModal("boosts");

    expect(listActivityBoosts).toHaveBeenCalledWith("act-1");
    expect(listActivityLikes).not.toHaveBeenCalled();
  });
});
