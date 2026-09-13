import { describe, it, expect, beforeEach, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { setActivePinia, createPinia } from "pinia";
import * as activitiesApi from "@/api/activities";
import type { ActivityResponse } from "@/api/activities";
import { clearActivityCache } from "@/utils/activityFetch";
import NotificationActivityCard from "./NotificationActivityCard.vue";

vi.mock("@/api/activities", () => ({
  getActivity: vi.fn(),
  listActivityReplies: vi.fn(),
}));

const getActivity = vi.mocked(activitiesApi.getActivity);

function createActivity(
  overrides: Partial<ActivityResponse> = {},
): ActivityResponse {
  return {
    id: "act-1",
    entity_type: "user",
    entity_id: "u-1",
    activity_type: "create",
    source_type: "local",
    source_actor: "urn:songhive:user:me",
    source_id: "https://example.com/users/me/objects/o1",
    owner_user_id: "u-1",
    visibility: "public",
    content: "<p>liked post body</p>",
    published_at: "2026-01-01T00:00:00Z",
    mentions: [],
    like_count: 0,
    boost_count: 0,
    reply_count: 0,
    liked: false,
    boosted: false,
    can_interact: true,
    ...overrides,
  };
}

describe("NotificationActivityCard", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    clearActivityCache();
    vi.clearAllMocks();
  });

  it("fetches and renders the referenced activity", async () => {
    getActivity.mockResolvedValueOnce(createActivity());
    const wrapper = mount(NotificationActivityCard, {
      props: { activityId: "act-fetch-1" },
      global: { stubs: { RouterLink: true }, renderStubDefaultSlot: true },
    });
    await flushPromises();
    expect(getActivity).toHaveBeenCalledWith("act-fetch-1");
    expect(wrapper.find(".activity-card").exists()).toBe(true);
    expect(wrapper.text()).toContain("liked post body");
  });

  it("emits error when the fetch fails so the parent can fall back", async () => {
    getActivity.mockRejectedValueOnce(new Error("not found"));
    const wrapper = mount(NotificationActivityCard, {
      props: { activityId: "act-fail-1" },
      global: { stubs: { RouterLink: true } },
    });
    await flushPromises();
    expect(wrapper.emitted("error")).toHaveLength(1);
    expect(wrapper.find(".activity-card").exists()).toBe(false);
  });
});
