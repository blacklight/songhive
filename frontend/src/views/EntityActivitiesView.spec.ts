import { describe, it, expect, beforeEach, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { createRouter, createMemoryHistory } from "vue-router";
import * as activitiesApi from "@/api/activities";
import EntityActivitiesView from "./EntityActivitiesView.vue";

vi.mock("@/api/activities", () => ({
  listEntityActivities: vi.fn(),
  likeActivity: vi.fn(),
  updateActivity: vi.fn(),
}));

const listEntityActivities = vi.mocked(activitiesApi.listEntityActivities);

function createTestRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: "/tracks/:id", component: { template: "<div/>" } },
      {
        path: "/tracks/:id/activities",
        component: EntityActivitiesView,
        props: (route) => ({
          entityType: "track",
          entityId: String(route.params.id),
        }),
      },
    ],
  });
}

describe("EntityActivitiesView", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders the feed for the given entity and links back to it", async () => {
    listEntityActivities.mockResolvedValueOnce({
      activities: [],
      next_cursor: null,
    });
    const router = createTestRouter();
    router.push("/tracks/t1/activities");
    await router.isReady();
    const wrapper = mount(EntityActivitiesView, {
      props: { entityType: "track", entityId: "t1" },
      global: { plugins: [router] },
    });
    await flushPromises();
    expect(listEntityActivities).toHaveBeenCalledWith("track", "t1", {
      cursor: undefined,
    });
    const back = wrapper.find(".entity-activities-view__back");
    expect(back.attributes("href")).toBe("/tracks/t1");
    expect(back.text()).toContain("Back to Track");
  });
});
