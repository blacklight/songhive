import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { mount, flushPromises, type VueWrapper } from "@vue/test-utils";
import { createRouter, createMemoryHistory } from "vue-router";
import { setActivePinia, createPinia } from "pinia";
import * as mentionsApi from "@/api/mentions";
import type { MentionResponse } from "@/api/mentions";
import MentionsView from "./MentionsView.vue";

vi.mock("@/api/mentions", () => ({
  MENTION_SOURCES: ["local", "activitypub", "webmention"],
  listMentions: vi.fn(),
}));

vi.mock("@/api/activities", () => ({
  getActivity: vi.fn().mockRejectedValue(new Error("not found")),
  lookupActivity: vi.fn().mockRejectedValue(new Error("not found")),
}));

const listMentions = vi.mocked(mentionsApi.listMentions);

function createMention(
  id: string,
  overrides: Partial<MentionResponse> = {},
): MentionResponse {
  return {
    id,
    source: "activitypub",
    actor_url: "https://remote.example/users/alice",
    source_url: `urn:songhive:test:${id}`,
    activity_id: null,
    visibility: "public",
    payload: { actor_name: "alice" },
    created_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

function createTestRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: "/", component: { template: "<div/>" } },
      { path: "/mentions", component: MentionsView },
      { path: "/:pathMatch(.*)*", component: { template: "<div/>" } },
    ],
  });
}

const mountedWrappers: VueWrapper[] = [];

describe("MentionsView", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.clearAllMocks();
    listMentions.mockResolvedValue({ items: [], total: 0 });
  });

  afterEach(() => {
    for (const wrapper of mountedWrappers.splice(0)) wrapper.unmount();
  });

  async function mountView() {
    const router = createTestRouter();
    const wrapper = mount(MentionsView, {
      global: { plugins: [router] },
    });
    mountedWrappers.push(wrapper);
    await flushPromises();
    return { wrapper, router };
  }

  it("loads and renders the mention list", async () => {
    listMentions.mockResolvedValueOnce({
      items: [createMention("m1"), createMention("m2", { source: "local" })],
      total: 2,
    });
    const { wrapper } = await mountView();

    expect(listMentions).toHaveBeenCalledWith({
      limit: 20,
      offset: 0,
      source: undefined,
      visibility: "all",
    });
    const rows = wrapper.findAll(".mentions-view__row");
    expect(rows).toHaveLength(2);
    expect(wrapper.text()).toContain("alice");
    expect(wrapper.text()).toContain("mentioned you");
  });

  it("shows the empty state when there are no mentions", async () => {
    const { wrapper } = await mountView();
    expect(wrapper.text()).toContain("No mentions yet.");
  });

  it("shows the filtered empty state with active filters", async () => {
    const { wrapper } = await mountView();
    const buttons = wrapper.findAll(".mentions-view__filters button");
    await buttons[1].trigger("click"); // first source filter after "all"
    await flushPromises();
    expect(wrapper.text()).toContain("No mentions match the selected filters.");
  });

  it("filters by source", async () => {
    const { wrapper } = await mountView();
    const groups = wrapper.findAll(".mentions-view__filters");
    const sourceButtons = groups[0].findAll("button");
    // "ActivityPub" is the third button (all, local, activitypub, webmention)
    await sourceButtons[2].trigger("click");
    await flushPromises();

    expect(listMentions).toHaveBeenLastCalledWith({
      limit: 20,
      offset: 0,
      source: "activitypub",
      visibility: "all",
    });
  });

  it("filters by private visibility", async () => {
    const { wrapper } = await mountView();
    const groups = wrapper.findAll(".mentions-view__filters");
    const visibilityButtons = groups[1].findAll("button");
    await visibilityButtons[1].trigger("click");
    await flushPromises();

    expect(listMentions).toHaveBeenLastCalledWith({
      limit: 20,
      offset: 0,
      source: undefined,
      visibility: "private",
    });
  });

  it("shows an error and retries", async () => {
    listMentions.mockRejectedValueOnce(new Error("boom"));
    const { wrapper } = await mountView();
    expect(wrapper.find(".mentions-view__error").exists()).toBe(true);

    listMentions.mockResolvedValueOnce({ items: [], total: 0 });
    await wrapper.find(".mentions-view__error button").trigger("click");
    await flushPromises();
    expect(listMentions).toHaveBeenCalledTimes(2);
    expect(wrapper.find(".mentions-view__error").exists()).toBe(false);
  });

  it("loads more pages", async () => {
    listMentions.mockResolvedValueOnce({
      items: [createMention("m1")],
      total: 2,
    });
    const { wrapper } = await mountView();

    const more = wrapper.find(".mentions-view__footer button");
    expect(more.exists()).toBe(true);

    listMentions.mockResolvedValueOnce({
      items: [createMention("m2")],
      total: 2,
    });
    await more.trigger("click");
    await flushPromises();

    expect(listMentions).toHaveBeenLastCalledWith({
      limit: 20,
      offset: 1,
      source: undefined,
      visibility: "all",
    });
    expect(wrapper.findAll(".mentions-view__row")).toHaveLength(2);
    expect(wrapper.find(".mentions-view__footer").exists()).toBe(false);
  });

  it("badges private mentions", async () => {
    listMentions.mockResolvedValueOnce({
      items: [createMention("m1", { visibility: "mentioned" })],
      total: 1,
    });
    const { wrapper } = await mountView();
    expect(wrapper.find(".mentions-view__badge--private").exists()).toBe(true);
  });
});
