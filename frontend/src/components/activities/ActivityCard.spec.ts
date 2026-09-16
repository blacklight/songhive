import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { mount, flushPromises, type VueWrapper } from "@vue/test-utils";
import { createRouter, createMemoryHistory } from "vue-router";
import * as activitiesApi from "@/api/activities";
import type { ActivityResponse } from "@/api/activities";
import { useActivitiesStore } from "@/stores/activities";
import { useAuthStore } from "@/stores/auth";
import { useConfirmStore } from "@/stores/confirm";
import { clearActivityCache } from "@/utils/activityFetch";
import ActivityCard from "./ActivityCard.vue";

vi.mock("@/api/activities", () => ({
  listEntityActivities: vi.fn(),
  getActivity: vi.fn(),
  likeActivity: vi.fn(),
  unlikeActivity: vi.fn(),
  boostActivity: vi.fn(),
  unboostActivity: vi.fn(),
  replyToActivity: vi.fn(),
  quoteActivity: vi.fn(),
  listActivityLikes: vi.fn(),
  listActivityBoosts: vi.fn(),
  listActivityReplies: vi.fn(),
  listActivityQuotes: vi.fn(),
  updateActivity: vi.fn(),
  deleteActivity: vi.fn(),
}));

vi.mock("@/api/search", () => ({
  searchPreview: vi.fn().mockResolvedValue({ query: "", sections: [] }),
}));

vi.mock("@/api/files", () => ({
  uploadFile: vi.fn(),
}));

const getActivity = vi.mocked(activitiesApi.getActivity);
const likeActivity = vi.mocked(activitiesApi.likeActivity);
const unlikeActivity = vi.mocked(activitiesApi.unlikeActivity);
const boostActivity = vi.mocked(activitiesApi.boostActivity);
const unboostActivity = vi.mocked(activitiesApi.unboostActivity);
const replyToActivity = vi.mocked(activitiesApi.replyToActivity);
const quoteActivity = vi.mocked(activitiesApi.quoteActivity);
const listActivityLikes = vi.mocked(activitiesApi.listActivityLikes);
const listActivityBoosts = vi.mocked(activitiesApi.listActivityBoosts);
const listActivityReplies = vi.mocked(activitiesApi.listActivityReplies);
const listActivityQuotes = vi.mocked(activitiesApi.listActivityQuotes);
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
    like_count: 0,
    boost_count: 0,
    reply_count: 0,
    quote_count: 0,
    liked: false,
    boosted: false,
    can_interact: true,
    ...overrides,
  };
}

function setAuthenticated(
  userId = "user-1",
  role: "user" | "admin" = "user",
  username = "alice",
) {
  const authStore = useAuthStore();
  authStore.status = "authenticated";
  authStore.user = { id: userId, username } as never;
  authStore.role = role;
}

// Wrappers stay mounted between tests; their context menus teleport to
// <body> and keep document-level click listeners, so they must be unmounted
// before the body is wiped or a later test's click re-renders detached DOM.
const mountedWrappers: VueWrapper[] = [];

function mountCard(overrides: Partial<ActivityResponse> = {}) {
  const wrapper = mount(ActivityCard, {
    props: { activity: createActivity(overrides) },
    global: {
      stubs: { RouterLink: true },
      renderStubDefaultSlot: true,
    },
  });
  mountedWrappers.push(wrapper);
  return wrapper;
}

async function openCardMenu(wrapper: ReturnType<typeof mountCard>) {
  await wrapper.find('button[aria-label="Open menu"]').trigger("click");
}

function findMenuLabel(text: string) {
  const menu = document.body.querySelector(".context-menu");
  const labels = Array.from(
    menu?.querySelectorAll(".context-menu__label") ?? [],
  );
  return labels.find((el) => el.textContent === text);
}

async function clickMenuItem(text: string) {
  const item = findMenuLabel(text)?.parentElement as HTMLElement | null;
  item?.click();
  await flushPromises();
}

describe("ActivityCard", () => {
  beforeEach(() => {
    clearActivityCache();
    vi.clearAllMocks();
  });

  afterEach(() => {
    for (const wrapper of mountedWrappers.splice(0)) wrapper.unmount();
    // The actors modal and the overflow menu teleport to <body>; drop
    // leftovers between tests.
    document.body.innerHTML = "";
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

  it("shows reply quote boost and like actions for the authenticated owner", () => {
    setAuthenticated("user-1");
    const wrapper = mountCard({
      like_count: 2,
      boost_count: 1,
      reply_count: 3,
      quote_count: 4,
    });
    const buttons = wrapper.findAll(".activity-card__actions button");
    // 4 interaction icons + 4 counters — edit, delete and copy URL live in
    // the header overflow menu.
    expect(buttons.length).toBe(8);
    expect(wrapper.text()).toContain("2");
  });

  it("moves edit, delete and copy URL into the header overflow menu", async () => {
    setAuthenticated("user-1");
    const wrapper = mountCard();
    // The footer keeps only the interaction row.
    expect(wrapper.find('button[aria-label="Edit"]').exists()).toBe(false);
    expect(wrapper.find('button[aria-label="Delete"]').exists()).toBe(false);
    await openCardMenu(wrapper);
    expect(findMenuLabel("Copy link")).toBeTruthy();
    expect(findMenuLabel("Edit")).toBeTruthy();
    expect(findMenuLabel("Delete")).toBeTruthy();
  });

  it("shows counters and disabled action buttons for anonymous users", async () => {
    const wrapper = mountCard({
      like_count: 2,
      boost_count: 1,
      reply_count: 3,
      quote_count: 4,
    });
    const buttons = wrapper.findAll(".activity-card__actions button");
    // 4 interaction icons + 4 counters
    expect(buttons.length).toBe(8);
    const counts = wrapper.findAll(".activity-card__count");
    // reply, quote, boost, like
    expect(counts.map((count) => count.text())).toEqual(["3", "4", "1", "2"]);
    const icons = wrapper.findAll(".activity-card__action .app-btn");
    expect(icons.length).toBe(4);
    for (const icon of icons) {
      expect(icon.attributes("disabled")).toBeDefined();
      expect(icon.attributes("title")).toBe("Log in to interact");
    }
    // Anonymous viewers still get the overflow menu for the copy URL entry.
    await openCardMenu(wrapper);
    expect(findMenuLabel("Copy link")).toBeTruthy();
    expect(findMenuLabel("Edit")).toBeFalsy();
    expect(findMenuLabel("Delete")).toBeFalsy();
  });

  it("lets anonymous users expand replies via the counter", async () => {
    listActivityReplies.mockResolvedValue({
      activities: [
        createActivity({
          id: "r1",
          activity_type: "reply",
          in_reply_to_activity_id: "a1",
          content: "<p>a reply</p>",
          content_source: "a reply",
          published_at: "2026-01-02T00:00:00Z",
        }),
      ],
      remote_replies: [],
    });
    const wrapper = mountCard({ reply_count: 1 });
    const counts = wrapper.findAll(".activity-card__count");
    await counts[0].trigger("click");
    await flushPromises();
    expect(listActivityReplies).toHaveBeenCalledWith("a1");
    expect(wrapper.find(".activity-card__replies").text()).toContain("a reply");
  });

  it("lets anonymous users open the actors modal via the counter", async () => {
    listActivityLikes.mockResolvedValue({
      actors: [
        {
          actor: "urn:songhive:user:bob",
          handle: "@bob",
          display_name: "Bob",
          username: "bob",
        },
      ],
    });
    const wrapper = mountCard({ like_count: 1 });
    const counts = wrapper.findAll(".activity-card__count");
    await counts[3].trigger("click");
    await flushPromises();
    expect(listActivityLikes).toHaveBeenCalledWith("a1");
    expect(document.body.textContent).toContain("Bob");
  });

  it("hides the edit action for non-owners", async () => {
    setAuthenticated("user-2");
    const wrapper = mountCard();
    const buttons = wrapper.findAll(".activity-card__actions button");
    // 4 interaction icons + 4 counters
    expect(buttons.length).toBe(8);
    await openCardMenu(wrapper);
    expect(findMenuLabel("Copy link")).toBeTruthy();
    expect(findMenuLabel("Edit")).toBeFalsy();
    expect(findMenuLabel("Delete")).toBeFalsy();
  });

  it("hides interaction buttons when the activity cannot be interacted with", async () => {
    setAuthenticated("user-1");
    const wrapper = mountCard({ activity_type: "like", can_interact: false });
    // Reactions are not editable: delete + copy URL in the overflow menu,
    // no footer row at all.
    expect(wrapper.find(".activity-card__actions").exists()).toBe(false);
    await openCardMenu(wrapper);
    expect(findMenuLabel("Delete")).toBeTruthy();
    expect(findMenuLabel("Copy link")).toBeTruthy();
    expect(findMenuLabel("Edit")).toBeFalsy();
  });

  it("renders the reacted activity inside a like card", async () => {
    setAuthenticated("user-1");
    // The target belongs to someone else so the only editable card is the
    // reaction itself — and reactions render no edit button.
    getActivity.mockResolvedValue(
      createActivity({
        id: "target-1",
        owner_user_id: "user-2",
        content: "<p>the liked post</p>",
        content_source: "the liked post",
      }),
    );
    const wrapper = mountCard({
      activity_type: "like",
      can_interact: false,
      content: null,
      content_source: null,
      in_reply_to_activity_id: "target-1",
      object_url: "https://example.com/users/bob/objects/target-1",
    });
    await flushPromises();
    expect(getActivity).toHaveBeenCalledWith("target-1");
    expect(wrapper.text()).toContain("the liked post");
    // The timestamp links to the like's own permalink page.
    const timeLink = wrapper
      .findAllComponents({ name: "RouterLink" })
      .find((link) => link.classes().includes("activity-card__time"));
    expect(timeLink?.props("to")).toBe("/activities/a1");
    // No edit button on a reaction; the only interaction groups are the
    // embedded card's own action bar.
    expect(wrapper.find('button[aria-label="Edit"]').exists()).toBe(false);
    expect(wrapper.findAll(".activity-card__action").length).toBe(4);
  });

  it("renders the reacted activity inside a boost card", async () => {
    setAuthenticated("user-1");
    getActivity.mockResolvedValue(
      createActivity({
        id: "target-2",
        content: "<p>the boosted post</p>",
        content_source: "the boosted post",
      }),
    );
    const wrapper = mountCard({
      activity_type: "announce",
      can_interact: false,
      content: null,
      content_source: null,
      in_reply_to_activity_id: "target-2",
    });
    await flushPromises();
    expect(getActivity).toHaveBeenCalledWith("target-2");
    expect(wrapper.text()).toContain("the boosted post");
  });

  it("shows an unavailable placeholder when the reacted activity cannot be loaded", async () => {
    setAuthenticated("user-1");
    getActivity.mockRejectedValue(new Error("gone"));
    const wrapper = mountCard({
      activity_type: "announce",
      can_interact: false,
      content: null,
      in_reply_to_activity_id: "target-gone",
    });
    await flushPromises();
    expect(wrapper.text()).toContain("This content is not available.");
  });

  it("retracts one's own like instead of deleting the liked activity", async () => {
    setAuthenticated("user-1");
    const target = createActivity({
      id: "target-3",
      owner_user_id: "user-2",
      liked: true,
      like_count: 1,
    });
    getActivity.mockResolvedValue(target);
    unlikeActivity.mockResolvedValue({ status: "ok" });
    const confirmStore = useConfirmStore();
    const wrapper = mountCard({
      activity_type: "like",
      can_interact: false,
      content: null,
      in_reply_to_activity_id: "target-3",
    });
    await flushPromises();
    await openCardMenu(wrapper);
    await clickMenuItem("Delete");
    expect(confirmStore.state?.open).toBe(true);
    confirmStore.confirm();
    await flushPromises();
    expect(unlikeActivity).toHaveBeenCalledWith("target-3");
    expect(deleteActivity).not.toHaveBeenCalled();
    expect(wrapper.find("article.activity-card").exists()).toBe(false);
  });

  it("retracts one's own boost instead of deleting the boosted activity", async () => {
    setAuthenticated("user-1");
    getActivity.mockResolvedValue(
      createActivity({
        id: "target-4",
        owner_user_id: "user-2",
        boosted: true,
        boost_count: 1,
      }),
    );
    unboostActivity.mockResolvedValue({ status: "ok" });
    const confirmStore = useConfirmStore();
    const wrapper = mountCard({
      activity_type: "announce",
      can_interact: false,
      content: null,
      in_reply_to_activity_id: "target-4",
    });
    await flushPromises();
    await openCardMenu(wrapper);
    await clickMenuItem("Delete");
    confirmStore.confirm();
    await flushPromises();
    expect(unboostActivity).toHaveBeenCalledWith("target-4");
    expect(deleteActivity).not.toHaveBeenCalled();
    expect(wrapper.find("article.activity-card").exists()).toBe(false);
  });

  it("likes the activity once and bumps the counter", async () => {
    setAuthenticated("user-1");
    likeActivity.mockResolvedValue({ status: "ok", activity_id: "l1" });
    const wrapper = mountCard();
    const likeButton = wrapper.find('button[aria-label="Like"]');
    await likeButton.trigger("click");
    await flushPromises();
    expect(likeActivity).toHaveBeenCalledWith("a1");
    expect(wrapper.find('button[aria-label="Unlike"]').exists()).toBe(true);
    // The counter next to the icon reflects the new like.
    const counts = wrapper.findAll(".activity-card__count");
    expect(counts[3].text()).toBe("1");
  });

  it("unlikes a liked activity and drops the counter", async () => {
    setAuthenticated("user-1");
    unlikeActivity.mockResolvedValue({ status: "ok" });
    const wrapper = mountCard({ liked: true, like_count: 1 });
    const unlikeButton = wrapper.find('button[aria-label="Unlike"]');
    expect(unlikeButton.exists()).toBe(true);
    await unlikeButton.trigger("click");
    await flushPromises();
    expect(unlikeActivity).toHaveBeenCalledWith("a1");
    expect(likeActivity).not.toHaveBeenCalled();
    expect(wrapper.find('button[aria-label="Like"]').exists()).toBe(true);
    const counts = wrapper.findAll(".activity-card__count");
    expect(counts[3].text()).toBe("0");
  });

  it("boosts the activity once and bumps the counter", async () => {
    setAuthenticated("user-1");
    boostActivity.mockResolvedValue({ status: "ok", activity_id: "b1" });
    const wrapper = mountCard();
    const boostButton = wrapper.find('button[aria-label="Boost"]');
    await boostButton.trigger("click");
    await flushPromises();
    expect(boostActivity).toHaveBeenCalledWith("a1");
    expect(wrapper.find('button[aria-label="Unboost"]').exists()).toBe(true);
    const counts = wrapper.findAll(".activity-card__count");
    expect(counts[2].text()).toBe("1");
  });

  it("unboosts a boosted activity and drops the counter", async () => {
    setAuthenticated("user-1");
    unboostActivity.mockResolvedValue({ status: "ok" });
    const wrapper = mountCard({ boosted: true, boost_count: 1 });
    const unboostButton = wrapper.find('button[aria-label="Unboost"]');
    expect(unboostButton.exists()).toBe(true);
    await unboostButton.trigger("click");
    await flushPromises();
    expect(unboostActivity).toHaveBeenCalledWith("a1");
    expect(boostActivity).not.toHaveBeenCalled();
    expect(wrapper.find('button[aria-label="Boost"]').exists()).toBe(true);
    const counts = wrapper.findAll(".activity-card__count");
    expect(counts[2].text()).toBe("0");
  });

  it("opens the actors modal when the like counter is clicked", async () => {
    setAuthenticated("user-1");
    listActivityLikes.mockResolvedValue({
      actors: [
        {
          actor: "urn:songhive:user:bob",
          handle: "@bob",
          display_name: "Bob",
          username: "bob",
        },
      ],
    });
    const wrapper = mountCard({ like_count: 1 });
    const counts = wrapper.findAll(".activity-card__count");
    await counts[3].trigger("click");
    await flushPromises();
    expect(listActivityLikes).toHaveBeenCalledWith("a1");
    expect(document.body.textContent).toContain("Liked by");
    expect(document.body.textContent).toContain("Bob");
  });

  it("opens the actors modal when the boost counter is clicked", async () => {
    setAuthenticated("user-1");
    listActivityBoosts.mockResolvedValue({ actors: [] });
    const wrapper = mountCard({ boost_count: 1 });
    const counts = wrapper.findAll(".activity-card__count");
    await counts[2].trigger("click");
    await flushPromises();
    expect(listActivityBoosts).toHaveBeenCalledWith("a1");
    expect(document.body.textContent).toContain("Boosted by");
  });

  it("expands replies when the reply counter is clicked", async () => {
    setAuthenticated("user-1");
    listActivityReplies.mockResolvedValue({
      activities: [
        createActivity({
          id: "r1",
          activity_type: "reply",
          in_reply_to_activity_id: "a1",
          content: "<p>a reply</p>",
          content_source: "a reply",
          published_at: "2026-01-02T00:00:00Z",
        }),
      ],
      remote_replies: [
        {
          id: "rr1",
          source_actor: "https://remote.example/users/carol",
          source_actor_name: "Carol",
          content: "<p>remote reply</p>",
          attachments: [],
          published_at: "2026-01-03T00:00:00Z",
        },
      ],
    });
    const wrapper = mountCard({ reply_count: 2 });
    const counts = wrapper.findAll(".activity-card__count");
    await counts[0].trigger("click");
    await flushPromises();
    expect(listActivityReplies).toHaveBeenCalledWith("a1");
    const replies = wrapper.find(".activity-card__replies");
    expect(replies.exists()).toBe(true);
    expect(replies.text()).toContain("a reply");
    expect(replies.text()).toContain("remote reply");
    expect(replies.text()).toContain("Carol");
  });

  it("flattens nested replies into one thread per root reply", async () => {
    setAuthenticated("user-1");
    listActivityReplies.mockResolvedValue({
      activities: [
        createActivity({
          id: "b1",
          activity_type: "reply",
          in_reply_to_activity_id: "a1",
          content: "<p>reply b</p>",
          content_source: "reply b",
          published_at: "2026-01-02T00:00:00Z",
        }),
        createActivity({
          id: "c1",
          activity_type: "reply",
          in_reply_to_activity_id: "b1",
          content: "<p>reply c</p>",
          content_source: "reply c",
          published_at: "2026-01-03T00:00:00Z",
        }),
        createActivity({
          id: "d1",
          activity_type: "reply",
          in_reply_to_activity_id: "a1",
          content: "<p>reply d</p>",
          content_source: "reply d",
          published_at: "2026-01-04T00:00:00Z",
        }),
      ],
      remote_replies: [],
    });
    const wrapper = mountCard({ reply_count: 3 });
    await wrapper.findAll(".activity-card__count")[0].trigger("click");
    await flushPromises();

    const threads = wrapper.findAll(".activity-card__thread");
    expect(threads.length).toBe(2);
    // b's thread unfolds c flat as a sibling — not nested inside b's card.
    const bThread = threads[0].findAll(".activity-card__reply");
    expect(bThread.length).toBe(2);
    expect(threads[0].text()).toContain("reply b");
    expect(threads[0].text()).toContain("reply c");
    expect(bThread[0].find(".activity-card__replies").exists()).toBe(false);
    expect(threads[1].text()).toContain("reply d");
    expect(threads[1].findAll(".activity-card__reply").length).toBe(1);
  });

  it("threads remote replies under their replied-to node", async () => {
    setAuthenticated("user-1");
    listActivityReplies.mockResolvedValue({
      activities: [
        createActivity({
          id: "b1",
          activity_type: "reply",
          in_reply_to_activity_id: "a1",
          source_id: "https://example.com/users/alice/objects/b1",
          content: "<p>reply b</p>",
          content_source: "reply b",
          published_at: "2026-01-02T00:00:00Z",
        }),
      ],
      remote_replies: [
        {
          id: "rr1",
          object_id: "https://remote.example/objects/rr1",
          in_reply_to: "https://example.com/users/alice/objects/o1",
          source_actor: "https://remote.example/users/carol",
          content: "<p>remote on root</p>",
          attachments: [],
          published_at: "2026-01-03T00:00:00Z",
        },
        {
          id: "rr2",
          object_id: "https://remote.example/objects/rr2",
          in_reply_to: "https://example.com/users/alice/objects/b1",
          source_actor: "https://remote.example/users/dan",
          content: "<p>remote on b</p>",
          attachments: [],
          published_at: "2026-01-04T00:00:00Z",
        },
      ],
    });
    const wrapper = mountCard({ reply_count: 3 });
    await wrapper.findAll(".activity-card__count")[0].trigger("click");
    await flushPromises();

    const threads = wrapper.findAll(".activity-card__thread");
    expect(threads.length).toBe(2);
    expect(threads[0].text()).toContain("reply b");
    expect(threads[0].text()).toContain("remote on b");
    expect(threads[1].text()).toContain("remote on root");
  });

  it("shows an empty state when there are no replies", async () => {
    setAuthenticated("user-1");
    listActivityReplies.mockResolvedValue({
      activities: [],
      remote_replies: [],
    });
    const wrapper = mountCard();
    const counts = wrapper.findAll(".activity-card__count");
    await counts[0].trigger("click");
    await flushPromises();
    expect(wrapper.text()).toContain("No replies yet.");
  });

  it("toggles the reply composer when the reply icon is clicked", async () => {
    setAuthenticated("user-1");
    const wrapper = mountCard();
    expect(wrapper.find(".activity-card__reply-composer").exists()).toBe(false);
    await wrapper.find('button[aria-label="Reply"]').trigger("click");
    expect(wrapper.find(".activity-card__reply-composer").exists()).toBe(true);
    expect(wrapper.find("textarea").exists()).toBe(true);
  });

  it("posts a reply through the composer and bumps the counter", async () => {
    setAuthenticated("user-1");
    const created = createActivity({
      id: "r2",
      activity_type: "reply",
      in_reply_to_activity_id: "a1",
      content: "<p>nice track</p>",
      content_source: "nice track",
    });
    replyToActivity.mockResolvedValue(created);
    listActivityReplies.mockResolvedValue({
      activities: [created],
      remote_replies: [],
    });
    const wrapper = mountCard();
    await wrapper.find('button[aria-label="Reply"]').trigger("click");
    const textarea = wrapper.find("textarea");
    await textarea.setValue("nice track");
    await wrapper.find("form.status-composer").trigger("submit");
    await flushPromises();
    expect(replyToActivity).toHaveBeenCalledWith(
      "a1",
      expect.objectContaining({ status: "nice track" }),
    );
    // The composer closes, the reply list opens and the counter bumps.
    expect(wrapper.find(".activity-card__reply-composer").exists()).toBe(false);
    expect(wrapper.find(".activity-card__replies").exists()).toBe(true);
    expect(wrapper.find(".activity-card__replies").text()).toContain(
      "nice track",
    );
    expect(wrapper.findAll(".activity-card__count")[0].text()).toBe("1");
  });

  it("prefills the reply composer with the author and the mentioned actors", async () => {
    setAuthenticated("user-9", "user", "dave");
    const wrapper = mountCard({
      mentions: [
        {
          handle: "@bob",
          actor_url: "https://example.com/users/bob",
          user_id: "user-2",
        },
        {
          handle: "@carol@remote.example",
          actor_url: "https://remote.example/users/carol",
        },
      ],
    });
    await wrapper.find('button[aria-label="Reply"]').trigger("click");
    const textarea = wrapper.find("textarea");
    expect((textarea.element as HTMLTextAreaElement).value).toBe(
      "@alice @bob @carol@remote.example ",
    );
  });

  it("prefills a remote author with their full handle", async () => {
    setAuthenticated("user-9", "user", "dave");
    const wrapper = mountCard({
      source_type: "remote",
      source_actor: "https://remote.example/users/carol",
      owner_user_id: null,
      mentions: [
        {
          handle: "@dan@other.example",
          actor_url: "https://other.example/users/dan",
        },
      ],
    });
    await wrapper.find('button[aria-label="Reply"]').trigger("click");
    const textarea = wrapper.find("textarea");
    expect((textarea.element as HTMLTextAreaElement).value).toBe(
      "@carol@remote.example @dan@other.example ",
    );
  });

  it("does not repeat actors already covered by the author or earlier mentions", async () => {
    setAuthenticated("user-9", "user", "dave");
    const wrapper = mountCard({
      mentions: [
        // The author, already first.
        { handle: "@alice", user_id: "user-1" },
        {
          handle: "@bob",
          actor_url: "https://example.com/users/bob",
          user_id: "user-2",
        },
        // Case-insensitive handle duplicate.
        { handle: "@BOB", user_id: "user-2" },
        // Same actor behind a different handle spelling.
        {
          handle: "@bob@elsewhere.example",
          actor_url: "https://example.com/users/bob",
        },
      ],
    });
    await wrapper.find('button[aria-label="Reply"]').trigger("click");
    const textarea = wrapper.find("textarea");
    expect((textarea.element as HTMLTextAreaElement).value).toBe(
      "@alice @bob ",
    );
  });

  it("excludes the replying user from the prefill", async () => {
    setAuthenticated("user-2", "user", "bob");
    const wrapper = mountCard({
      mentions: [
        {
          handle: "@bob",
          actor_url: "https://example.com/users/bob",
          user_id: "user-2",
        },
        {
          handle: "@carol@remote.example",
          actor_url: "https://remote.example/users/carol",
        },
      ],
    });
    await wrapper.find('button[aria-label="Reply"]').trigger("click");
    const textarea = wrapper.find("textarea");
    expect((textarea.element as HTMLTextAreaElement).value).toBe(
      "@alice @carol@remote.example ",
    );
  });

  it("does not tag oneself when replying to one's own activity", async () => {
    setAuthenticated("user-1");
    const wrapper = mountCard({
      mentions: [
        {
          handle: "@bob",
          actor_url: "https://example.com/users/bob",
          user_id: "user-2",
        },
      ],
    });
    await wrapper.find('button[aria-label="Reply"]').trigger("click");
    const textarea = wrapper.find("textarea");
    expect((textarea.element as HTMLTextAreaElement).value).toBe("@bob ");
  });

  it("toggles the quote composer when the quote icon is clicked", async () => {
    setAuthenticated("user-1");
    const wrapper = mountCard();
    expect(wrapper.find(".activity-card__quote-composer").exists()).toBe(false);
    await wrapper.find('button[aria-label="Quote"]').trigger("click");
    expect(wrapper.find(".activity-card__quote-composer").exists()).toBe(true);
    expect(wrapper.find("textarea").exists()).toBe(true);
  });

  it("posts a quote through the composer and bumps the counter", async () => {
    setAuthenticated("user-1");
    const created = createActivity({
      id: "q1",
      activity_type: "quote",
      in_reply_to_activity_id: "a1",
      content: "<p>worth a listen</p>",
      content_source: "worth a listen",
    });
    quoteActivity.mockResolvedValue(created);
    listActivityQuotes.mockResolvedValue({
      activities: [created],
      remote_quotes: [],
    });
    const wrapper = mountCard();
    await wrapper.find('button[aria-label="Quote"]').trigger("click");
    const textarea = wrapper.find("textarea");
    await textarea.setValue("worth a listen");
    await wrapper.find("form.status-composer").trigger("submit");
    await flushPromises();
    expect(quoteActivity).toHaveBeenCalledWith(
      "a1",
      expect.objectContaining({ status: "worth a listen" }),
    );
    // The composer closes, the quote list opens and the counter bumps.
    expect(wrapper.find(".activity-card__quote-composer").exists()).toBe(false);
    expect(wrapper.find(".activity-card__quotes").exists()).toBe(true);
    expect(wrapper.find(".activity-card__quotes").text()).toContain(
      "worth a listen",
    );
    expect(wrapper.findAll(".activity-card__count")[1].text()).toBe("1");
  });

  it("expands quotes when the quote counter is clicked", async () => {
    setAuthenticated("user-1");
    listActivityQuotes.mockResolvedValue({
      activities: [
        createActivity({
          id: "q1",
          activity_type: "quote",
          in_reply_to_activity_id: "a1",
          content: "<p>a quote</p>",
          content_source: "a quote",
          published_at: "2026-01-02T00:00:00Z",
        }),
      ],
      remote_quotes: [
        {
          id: "rq1",
          object_id: "https://remote.example/objects/rq1",
          quoted: "https://example.com/users/alice/objects/o1",
          source_actor: "https://remote.example/users/carol",
          source_actor_name: "Carol",
          content: "<p>remote quote</p>",
          attachments: [],
          published_at: "2026-01-03T00:00:00Z",
        },
      ],
    });
    const wrapper = mountCard({ quote_count: 2 });
    const counts = wrapper.findAll(".activity-card__count");
    await counts[1].trigger("click");
    await flushPromises();
    expect(listActivityQuotes).toHaveBeenCalledWith("a1");
    const quotes = wrapper.find(".activity-card__quotes");
    expect(quotes.exists()).toBe(true);
    expect(quotes.text()).toContain("a quote");
    expect(quotes.text()).toContain("remote quote");
    expect(quotes.text()).toContain("Carol");
  });

  it("shows an empty state when there are no quotes", async () => {
    setAuthenticated("user-1");
    listActivityQuotes.mockResolvedValue({
      activities: [],
      remote_quotes: [],
    });
    const wrapper = mountCard();
    const counts = wrapper.findAll(".activity-card__count");
    await counts[1].trigger("click");
    await flushPromises();
    expect(wrapper.text()).toContain("No quotes yet.");
  });

  it("embeds the quoted activity inside a quote card", async () => {
    setAuthenticated("user-1");
    getActivity.mockResolvedValue(
      createActivity({
        id: "target-q",
        owner_user_id: "user-2",
        content: "<p>the quoted post</p>",
        content_source: "the quoted post",
      }),
    );
    const wrapper = mountCard({
      activity_type: "quote",
      in_reply_to_activity_id: "target-q",
      content: "<p>my quote text</p>",
      content_source: "my quote text",
    });
    await flushPromises();
    expect(getActivity).toHaveBeenCalledWith("target-q");
    expect(wrapper.text()).toContain("my quote text");
    expect(wrapper.text()).toContain("the quoted post");
  });

  it("deletes the activity after confirmation", async () => {
    setAuthenticated("user-1");
    deleteActivity.mockResolvedValue({ status: "ok" });
    const confirmStore = useConfirmStore();
    const wrapper = mountCard();
    await openCardMenu(wrapper);
    await clickMenuItem("Delete");
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
    await openCardMenu(wrapper);
    await clickMenuItem("Delete");
    confirmStore.confirm();
    await flushPromises();
    expect(wrapper.find("article.activity-card").exists()).toBe(false);
  });

  it("does not delete when the confirmation is cancelled", async () => {
    setAuthenticated("user-1");
    const confirmStore = useConfirmStore();
    const wrapper = mountCard();
    await openCardMenu(wrapper);
    await clickMenuItem("Delete");
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

  it("renders a link preview card when the activity carries one", () => {
    const wrapper = mountCard({
      preview_card: {
        url: "https://site.example/track/1",
        title: "A great track",
        description: "By a great band",
        image_url: "https://cdn.example/cover.jpg",
        site_name: "MusicSite",
        type: "link",
      },
    });
    const card = wrapper.find(".activity-card__preview-card");
    expect(card.exists()).toBe(true);
    expect(card.attributes("href")).toBe("https://site.example/track/1");
    expect(card.text()).toContain("MusicSite");
    expect(card.text()).toContain("A great track");
    expect(card.text()).toContain("By a great band");
    const img = card.find("img");
    expect(img.exists()).toBe(true);
    expect(img.attributes("src")).toBe("https://cdn.example/cover.jpg");
  });

  it("renders no preview card when the activity has none", () => {
    const wrapper = mountCard();
    expect(wrapper.find(".activity-card__preview-card").exists()).toBe(false);
  });

  it("falls back to the card's domain when no site name is set", () => {
    const wrapper = mountCard({
      preview_card: {
        url: "https://site.example/track/1",
        title: "site.example",
        type: "link",
      },
    });
    const card = wrapper.find(".activity-card__preview-card");
    expect(card.find(".activity-card__preview-card-site").text()).toBe(
      "site.example",
    );
    expect(card.find("img").exists()).toBe(false);
  });

  it("renders audio attachments in the activity audio player", () => {
    const wrapper = mountCard({
      attachments: [
        {
          type: "Audio",
          mediaType: "audio/mpeg",
          url: "https://audio.example/song.mp3",
          name: "Artist - Song",
          "songhive:trackTitle": "Song",
          "songhive:artistName": "Artist",
          "songhive:albumName": "Album",
        },
      ],
    });
    const player = wrapper.find(".audio-player");
    expect(player.exists()).toBe(true);
    // Vue binds ``src`` as a property; jsdom's media mock does not reflect
    // it to a content attribute.
    expect((player.find("audio").element as HTMLAudioElement).src).toBe(
      "https://audio.example/song.mp3",
    );
    expect(player.find(".audio-player__title").text()).toBe("Song");
    expect(player.find(".audio-player__subtitle").text()).toBe(
      "Artist · Album",
    );
  });

  it("does not navigate when interacting with the audio player", async () => {
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: "/", component: { template: "<div/>" } },
        { path: "/activities/:id", component: { template: "<div/>" } },
      ],
    });
    await router.push("/");
    const wrapper = mount(ActivityCard, {
      props: {
        activity: createActivity({
          attachments: [
            {
              type: "Audio",
              mediaType: "audio/mpeg",
              url: "https://audio.example/song.mp3",
              name: "Some audio",
            },
          ],
        }),
      },
      global: {
        plugins: [router],
        stubs: { RouterLink: true },
        renderStubDefaultSlot: true,
      },
    });
    mountedWrappers.push(wrapper);
    const push = vi.spyOn(router, "push");

    // Controls inside the player must not trigger card navigation.
    await wrapper
      .find('.audio-player button[aria-label="Play"]')
      .trigger("click");
    expect(push).not.toHaveBeenCalled();

    // Clicking elsewhere on the card still navigates to the permalink.
    await wrapper.find(".activity-card").trigger("click");
    expect(push).toHaveBeenCalledWith("/activities/a1");
  });
});
