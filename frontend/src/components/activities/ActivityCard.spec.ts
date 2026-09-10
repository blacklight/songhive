import { describe, it, expect, beforeEach, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import * as activitiesApi from "@/api/activities";
import type { ActivityResponse } from "@/api/activities";
import { useAuthStore } from "@/stores/auth";
import { useConfirmStore } from "@/stores/confirm";
import ActivityCard from "./ActivityCard.vue";

vi.mock("@/api/activities", () => ({
  listEntityActivities: vi.fn(),
  likeActivity: vi.fn(),
  updateActivity: vi.fn(),
  deleteActivity: vi.fn(),
}));

const likeActivity = vi.mocked(activitiesApi.likeActivity);
const deleteActivity = vi.mocked(activitiesApi.deleteActivity);

function createActivity(
  overrides: Partial<ActivityResponse> = {},
): ActivityResponse {
  return {
    id: "a1",
    entity_type: "track",
    entity_id: "t1",
    activity_type: "create",
    source_type: "local",
    source_actor: "urn:songhive:user:alice",
    source_id: "https://example.com/users/alice/objects/o1",
    owner_user_id: "user-1",
    visibility: "public",
    content: '<p>Hello <a href="https://example.com/users/bob">@bob</a></p>',
    content_source: "Hello @bob",
    content_type: "text/markdown",
    published_at: "2026-01-01T00:00:00Z",
    mentions: [],
    ...overrides,
  };
}

function setAuthenticated(userId = "user-1", role: "user" | "admin" = "user") {
  const authStore = useAuthStore();
  authStore.accessToken = "token";
  authStore.refreshToken = "refresh";
  authStore.expiresAt = Date.now() + 10000;
  authStore.status = "authenticated";
  authStore.user = { id: userId, username: "alice" } as never;
  authStore.role = role;
}

function mountCard(overrides: Partial<ActivityResponse> = {}) {
  return mount(ActivityCard, {
    props: { activity: createActivity(overrides) },
    global: {
      stubs: { RouterLink: true },
      renderStubDefaultSlot: true,
    },
  });
}

describe("ActivityCard", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders the actor handle derived from the source actor", () => {
    const wrapper = mountCard();
    expect(wrapper.text()).toContain("@alice");
    expect(wrapper.find(".activity-card__display-name").text()).toBe("alice");
  });

  it("renders remote actors with their host", () => {
    const wrapper = mountCard({
      source_type: "remote",
      source_actor: "https://remote.example/users/carol",
      content: "hi",
      content_type: "text/html",
    });
    expect(wrapper.text()).toContain("@carol@remote.example");
    expect(wrapper.find(".activity-card__display-name").text()).toBe("carol");
  });

  it("renders the source actor display name when provided", () => {
    const wrapper = mountCard({
      source_actor_display_name: "Alice Display",
    });
    expect(wrapper.find(".activity-card__display-name").text()).toBe(
      "Alice Display",
    );
    expect(wrapper.text()).toContain("@alice");
  });

  it("renders local content as trusted HTML", () => {
    const wrapper = mountCard();
    const content = wrapper.find(".activity-card__content");
    expect(content.element.innerHTML).toContain("<a");
  });

  it("strips remote HTML to plain text", () => {
    const wrapper = mountCard({
      source_type: "remote",
      content: "<p>hi <script>alert(1)</script></p>",
      content_type: "text/html",
    });
    const content = wrapper.find(".activity-card__content");
    expect(content.element.innerHTML).not.toContain("<script");
    expect(content.text()).toContain("hi");
  });

  it("shows like edit and copy URL actions for the authenticated owner", () => {
    setAuthenticated("user-1");
    const wrapper = mountCard();
    const buttons = wrapper.findAll(".activity-card__actions button");
    expect(buttons.length).toBe(4);
  });

  it("hides actions for anonymous users", () => {
    const wrapper = mountCard();
    expect(wrapper.find(".activity-card__actions").exists()).toBe(false);
  });

  it("hides the edit action for non-owners", () => {
    setAuthenticated("user-2");
    const wrapper = mountCard();
    const buttons = wrapper.findAll(".activity-card__actions button");
    expect(buttons.length).toBe(2);
    expect(buttons[0].text()).toContain("Like");
  });

  it("likes the activity once and disables the button", async () => {
    setAuthenticated("user-1");
    likeActivity.mockResolvedValue({ status: "ok", activity_id: "l1" });
    const wrapper = mountCard();
    const likeButton = wrapper.find(".activity-card__actions button");
    await likeButton.trigger("click");
    await flushPromises();
    expect(likeActivity).toHaveBeenCalledWith("a1");
    expect(wrapper.text()).toContain("Liked");
    await likeButton.trigger("click");
    expect(likeActivity).toHaveBeenCalledTimes(1);
  });

  it("deletes the activity after confirmation", async () => {
    setAuthenticated("user-1");
    deleteActivity.mockResolvedValue({ status: "ok" });
    const confirmStore = useConfirmStore();
    const wrapper = mountCard();
    const deleteButton = wrapper
      .findAll(".activity-card__actions button")
      .at(-2)!;
    await deleteButton.trigger("click");
    expect(confirmStore.state?.open).toBe(true);
    confirmStore.confirm();
    await flushPromises();
    expect(deleteActivity).toHaveBeenCalledWith("a1");
  });

  it("does not delete when the confirmation is cancelled", async () => {
    setAuthenticated("user-1");
    const confirmStore = useConfirmStore();
    const wrapper = mountCard();
    const deleteButton = wrapper
      .findAll(".activity-card__actions button")
      .at(-2)!;
    await deleteButton.trigger("click");
    confirmStore.cancel();
    await flushPromises();
    expect(deleteActivity).not.toHaveBeenCalled();
  });

  it("shows a type badge for non-create activities", () => {
    const wrapper = mountCard({
      activity_type: "announce",
      content: null,
    });
    expect(wrapper.text()).toContain("Boosted");
  });

  it("renders the source actor avatar when one is provided", () => {
    const wrapper = mountCard({
      source_actor_avatar_url: "https://example.com/avatar.png",
    });
    const img = wrapper.find("img");
    expect(img.exists()).toBe(true);
    expect(img.attributes("src")).toBe("https://example.com/avatar.png");
  });

  it("falls back to initials when no avatar is provided", () => {
    const wrapper = mountCard();
    expect(wrapper.find("img").exists()).toBe(false);
    const avatar = wrapper.find(".app-avatar--initials");
    expect(avatar.exists()).toBe(true);
    expect(avatar.text()).toBe("A");
  });
});
