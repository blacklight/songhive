import { describe, it, expect, beforeEach, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import * as activitiesApi from "@/api/activities";
import type { ActivityResponse } from "@/api/activities";
import { useActivitiesStore } from "@/stores/activities";
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
const updateActivity = vi.mocked(activitiesApi.updateActivity);
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

  it("renders content links as anchors", () => {
    const wrapper = mountCard();
    const content = wrapper.find(".activity-card__content");
    const link = content.find("a");
    expect(link.exists()).toBe(true);
    expect(link.attributes("href")).toBe("https://example.com/users/bob");
    expect(link.text()).toBe("@bob");
  });

  it("routes local mention anchors to the profile route", () => {
    const wrapper = mountCard({
      content: 'Hello <a href="/users/bob">@bob</a>',
    });
    const links = wrapper.findAllComponents({ name: "RouterLink" });
    const mention = links.find(
      (link) => link.props("to")?.name === "userProfile",
    );
    expect(mention?.props("to")).toEqual({
      name: "userProfile",
      params: { username: "bob" },
    });
    expect(mention?.text()).toBe("@bob");
  });

  it("routes same-host actor URLs to the profile route", () => {
    const wrapper = mountCard({
      content: `Hello <a href="http://${window.location.host}/users/bob">@bob</a>`,
    });
    const links = wrapper.findAllComponents({ name: "RouterLink" });
    expect(
      links
        .find((link) => link.props("to")?.name === "userProfile")
        ?.props("to"),
    ).toEqual({ name: "userProfile", params: { username: "bob" } });
  });

  it("renders remote mention anchors as external links", () => {
    const wrapper = mountCard({
      source_type: "remote",
      content:
        '<p>hi <a href="https://remote.example/@bob" class="u-url mention">@bob</a></p>',
      content_type: "text/html",
    });
    const link = wrapper.find(".activity-card__content a");
    expect(link.attributes("href")).toBe("https://remote.example/@bob");
    expect(link.attributes("target")).toBe("_blank");
    expect(link.text()).toBe("@bob");
  });

  it("linkifies bare remote handles using the mentions list", () => {
    const wrapper = mountCard({
      source_type: "remote",
      content: "hi @bob@remote.example",
      mentions: [
        {
          handle: "@bob@remote.example",
          actor_url: "https://remote.example/users/bob",
        },
      ],
    });
    const link = wrapper.find(".activity-card__content a");
    expect(link.attributes("href")).toBe("https://remote.example/users/bob");
    expect(link.text()).toBe("@bob@remote.example");
  });

  it("linkifies bare local handles to the profile route", () => {
    const wrapper = mountCard({
      source_type: "remote",
      content: "hi @bob",
      content_source: null,
    });
    const links = wrapper.findAllComponents({ name: "RouterLink" });
    expect(
      links
        .find((link) => link.props("to")?.name === "userProfile")
        ?.props("to"),
    ).toEqual({ name: "userProfile", params: { username: "bob" } });
  });

  it("routes hashtag anchors to the tag route", () => {
    const wrapper = mountCard({
      content:
        '<a href="https://remote.example/tags/music" rel="tag">#music</a>',
    });
    const links = wrapper.findAllComponents({ name: "RouterLink" });
    expect(
      links.find((link) => link.props("to")?.name === "tag")?.props("to"),
    ).toEqual({ name: "tag", params: { name: "music" } });
  });

  it("never renders remote HTML verbatim", () => {
    const wrapper = mountCard({
      source_type: "remote",
      content: "<p>hi <script>alert(1)</script><b>bold</b></p>",
      content_type: "text/html",
    });
    const content = wrapper.find(".activity-card__content");
    expect(content.element.innerHTML).not.toContain("<script");
    expect(content.element.innerHTML).not.toContain("<b>");
    expect(content.text()).toContain("hi");
    expect(content.text()).toContain("bold");
  });

  it("renders inline font formatting as styled spans", () => {
    const wrapper = mountCard({
      content:
        "<p>plain <strong>bold</strong> <em>italic</em> <code>mono</code></p>",
      content_type: "text/markdown",
    });
    const content = wrapper.find(".activity-card__content");
    const bold = content.find(".activity-card__mark--bold");
    const italic = content.find(".activity-card__mark--italic");
    const code = content.find(".activity-card__mark--code");
    expect(bold.text()).toBe("bold");
    expect(italic.text()).toBe("italic");
    expect(code.text()).toBe("mono");
    // Marks never surface as real markup — remote HTML is not rendered
    // verbatim.
    expect(content.element.innerHTML).not.toContain("<strong>");
  });

  it("applies marks to mention and link segments", () => {
    const wrapper = mountCard({
      content:
        '<p><strong><a href="/users/bob">@bob</a> ' +
        '<a href="https://remote.example/p">link</a></strong></p>',
      content_type: "text/markdown",
    });
    const content = wrapper.find(".activity-card__content");
    const mention = content.find(".activity-card__mention");
    const link = content.find('a[href="https://remote.example/p"]');
    expect(mention.classes()).toContain("activity-card__mark--bold");
    expect(link.classes()).toContain("activity-card__mark--bold");
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

  it("re-renders with the updated activity after an edit", async () => {
    updateActivity.mockResolvedValue(
      createActivity({
        content: "<p>edited body</p>",
        content_source: "edited body",
        visibility: "followers",
      }),
    );
    const store = useActivitiesStore();
    const wrapper = mountCard();
    expect(wrapper.find(".activity-card__content").text()).toContain(
      "Hello @bob",
    );
    // The card's activity is not in store.items here (e.g. profile tabs);
    // the update cache must still refresh what it renders.
    await store.update("a1", {
      content: "edited body",
      visibility: "followers",
    });
    await flushPromises();
    expect(wrapper.find(".activity-card__content").text()).toContain(
      "edited body",
    );
    expect(wrapper.find(".activity-card__content").text()).not.toContain(
      "Hello @bob",
    );
  });

  it("hides the card after the activity is deleted", async () => {
    setAuthenticated("user-1");
    deleteActivity.mockResolvedValue({ status: "ok" });
    const confirmStore = useConfirmStore();
    const wrapper = mountCard();
    const deleteButton = wrapper
      .findAll(".activity-card__actions button")
      .at(-2)!;
    await deleteButton.trigger("click");
    confirmStore.confirm();
    await flushPromises();
    expect(wrapper.find("article.activity-card").exists()).toBe(false);
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
