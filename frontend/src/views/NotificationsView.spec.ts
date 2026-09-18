import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { mount, flushPromises, type VueWrapper } from "@vue/test-utils";
import { createRouter, createMemoryHistory } from "vue-router";
import { setActivePinia, createPinia } from "pinia";
import * as notificationsApi from "@/api/notifications";
import type { NotificationResponse } from "@/api/notifications";
import * as activitiesApi from "@/api/activities";
import type { ActivityResponse } from "@/api/activities";
import { clearActivityCache } from "@/utils/activityFetch";
import * as usersApi from "@/api/users";
import { useNotificationsStore } from "@/stores/notifications";
import NotificationsView from "./NotificationsView.vue";

vi.mock("@/api/notifications", () => ({
  NOTIFICATION_TYPES: [
    "follow",
    "like",
    "boost",
    "quote",
    "reply",
    "mention",
    "share",
    "webmention",
    "activity",
  ],
  listNotifications: vi.fn(),
  getUnreadCount: vi.fn().mockResolvedValue(0),
  markSeen: vi.fn(),
  markUnseen: vi.fn(),
  markAllSeen: vi.fn(),
  deleteNotification: vi.fn(),
  deleteNotifications: vi.fn(),
  clearNotifications: vi.fn(),
  getNotificationPreferences: vi.fn(),
  updateNotificationPreferences: vi.fn(),
}));

vi.mock("@/api/ws", () => ({
  eventBus: {
    on: vi.fn(),
    off: vi.fn(),
    connect: vi.fn(),
    disconnect: vi.fn(),
  },
}));

vi.mock("@/composables/useItemSummary", () => ({
  getItemSummary: vi.fn().mockResolvedValue(null),
}));

vi.mock("@/api/activities", () => ({
  getActivity: vi.fn(),
  listActivityReplies: vi.fn(),
  lookupActivity: vi.fn().mockRejectedValue(new Error("not found")),
}));

vi.mock("@/api/users", () => ({
  acceptFollowRequest: vi.fn(),
  rejectFollowRequest: vi.fn(),
}));

const listNotifications = vi.mocked(notificationsApi.listNotifications);
const getActivity = vi.mocked(activitiesApi.getActivity);
const lookupActivity = vi.mocked(activitiesApi.lookupActivity);
const acceptFollowRequestApi = vi.mocked(usersApi.acceptFollowRequest);
const rejectFollowRequestApi = vi.mocked(usersApi.rejectFollowRequest);
const markSeenApi = vi.mocked(notificationsApi.markSeen);
const markUnseenApi = vi.mocked(notificationsApi.markUnseen);
const markAllSeenApi = vi.mocked(notificationsApi.markAllSeen);
const deleteNotificationApi = vi.mocked(notificationsApi.deleteNotification);
const deleteNotificationsApi = vi.mocked(notificationsApi.deleteNotifications);
const clearNotificationsApi = vi.mocked(notificationsApi.clearNotifications);

type ObserverEntry = {
  target: Element;
  isIntersecting: boolean;
};

let observerCallback: ((entries: ObserverEntry[]) => void) | null = null;
let observed: Set<Element>;
const OriginalIntersectionObserver = window.IntersectionObserver;

class FakeIntersectionObserver {
  constructor(callback: IntersectionObserverCallback) {
    observerCallback = (entries: ObserverEntry[]) =>
      callback(entries as IntersectionObserverEntry[], this as never);
  }
  observe(el: Element) {
    observed.add(el);
  }
  unobserve(el: Element) {
    observed.delete(el);
  }
  disconnect() {
    observed.clear();
  }
}

function createNotification(
  id: string,
  overrides: Partial<NotificationResponse> = {},
): NotificationResponse {
  return {
    id,
    type: "like",
    actor_url: "https://example.com/users/alice",
    source_url: "https://example.com/objects/1",
    payload: { actor_name: "alice" },
    seen_at: null,
    created_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

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
    content: "<p>my post</p>",
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

function createTestRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: "/", component: { template: "<div/>" } },
      { path: "/notifications", component: NotificationsView },
      {
        path: "/@:username",
        name: "userProfile",
        component: { template: "<div/>" },
      },
      { path: "/:pathMatch(.*)*", component: { template: "<div/>" } },
    ],
  });
}

function intersectIds(ids: string[], isIntersecting = true) {
  const entries = ids
    .map((id) => {
      const el = [...observed].find(
        (e) => (e as HTMLElement).dataset.notificationId === id,
      );
      return el ? { target: el, isIntersecting } : null;
    })
    .filter((e): e is ObserverEntry => e !== null);
  observerCallback?.(entries);
}

const mountedWrappers: VueWrapper[] = [];

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

describe("NotificationsView", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.clearAllMocks();
    clearActivityCache();
    observed = new Set();
    observerCallback = null;
    window.IntersectionObserver =
      FakeIntersectionObserver as unknown as typeof IntersectionObserver;
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
    window.IntersectionObserver = OriginalIntersectionObserver;
    // Unmount views first so their teleported overflow menus detach
    // cleanly, then drop leftovers in <body>.
    for (const wrapper of mountedWrappers.splice(0)) wrapper.unmount();
    document.body.innerHTML = "";
  });

  async function mountView() {
    const router = createTestRouter();
    const wrapper = mount(NotificationsView, {
      global: { plugins: [router] },
    });
    mountedWrappers.push(wrapper);
    await flushPromises();
    return { wrapper, router };
  }

  it("loads and renders the notification list", async () => {
    listNotifications.mockResolvedValueOnce({
      items: [createNotification("n1")],
      total: 1,
    });
    const { wrapper } = await mountView();
    expect(listNotifications).toHaveBeenCalledWith({
      limit: 20,
      offset: 0,
      seen: undefined,
    });
    expect(wrapper.text()).toContain("alice");
    expect(wrapper.text()).toContain("liked your post");
  });

  it("links follow notifications to the actor profile", async () => {
    listNotifications.mockResolvedValueOnce({
      items: [
        createNotification("n1", {
          type: "follow",
          actor_url: "https://remote.example/users/bob",
          source_url: "https://music.example.com/users/me",
        }),
      ],
      total: 1,
    });
    const { wrapper } = await mountView();
    const link = wrapper.find("a.notifications-view__actor");
    expect(link.attributes("href")).toBe("/@bob@remote.example");
  });

  it("links object-scoped follow notifications to the followed object", async () => {
    listNotifications.mockResolvedValueOnce({
      items: [
        createNotification("n1", {
          type: "follow",
          actor_url: "https://remote.example/users/bob",
          source_url: "https://remote.example/users/bob",
          payload: {
            actor_name: "bob",
            target_url: "https://music.example.com/users/me/objects/t1",
            target_local_url: "/tracks/t1",
            target_item_type: "track",
            target_item_title: "Track",
            target_object_page_url: "/activities/act-9",
          },
        }),
      ],
      total: 1,
    });
    const { wrapper } = await mountView();
    const action = wrapper.find("a.notifications-view__action");
    expect(action.text()).toContain("followed your track");
    expect(action.attributes("href")).toBe("/activities/act-9");
    const chip = wrapper.find("a.notifications-view__target");
    expect(chip.exists()).toBe(true);
    expect(chip.text()).toContain("Track");
    // The follower's actor card still renders.
    expect(wrapper.find(".actor-card").exists()).toBe(true);
  });

  it("renders a user card for follow notifications", async () => {
    listNotifications.mockResolvedValueOnce({
      items: [
        createNotification("n1", {
          type: "follow",
          actor_url: "https://remote.example/users/bob",
          source_url: "https://remote.example/users/bob",
          payload: {
            actor_name: "bob",
            actor_display_name: "Bob Rocker",
            actor_avatar_url: "https://remote.example/avatars/bob.png",
          },
        }),
      ],
      total: 1,
    });
    const { wrapper } = await mountView();
    const card = wrapper.find(".actor-card");
    expect(card.exists()).toBe(true);
    expect(card.attributes("href")).toBe("/@bob@remote.example");
    expect(card.text()).toContain("Bob Rocker");
    expect(card.text()).toContain("@bob@remote.example");
    expect(card.find("img").attributes("src")).toBe(
      "https://remote.example/avatars/bob.png",
    );
  });

  it("renders a local user card linking to the profile route", async () => {
    listNotifications.mockResolvedValueOnce({
      items: [
        createNotification("n1", {
          type: "follow",
          actor_url: "/users/alice",
          source_url: "/users/alice",
          payload: { actor_name: "alice" },
        }),
      ],
      total: 1,
    });
    const { wrapper } = await mountView();
    const card = wrapper.find(".actor-card");
    expect(card.exists()).toBe(true);
    expect(card.attributes("href")).toBe("/@alice");
  });

  it("shows accept/reject actions on a pending follow request", async () => {
    listNotifications.mockResolvedValueOnce({
      items: [
        createNotification("n1", {
          type: "follow",
          actor_url: "https://remote.example/users/bob",
          source_url: "https://remote.example/users/bob",
          payload: { actor_name: "bob", follow_request_pending: true },
        }),
      ],
      total: 1,
    });
    const { wrapper } = await mountView();

    expect(wrapper.text()).toContain("requested to follow you");
    const actions = wrapper.find(".notifications-view__request-actions");
    expect(actions.exists()).toBe(true);
    expect(actions.findAll("button").some((b) => b.text() === "Accept")).toBe(
      true,
    );
    expect(actions.findAll("button").some((b) => b.text() === "Reject")).toBe(
      true,
    );
  });

  it("accepting a pending follow request calls the API and updates the row", async () => {
    listNotifications.mockResolvedValueOnce({
      items: [
        createNotification("n1", {
          type: "follow",
          actor_url: "https://remote.example/users/bob",
          source_url: "https://remote.example/users/bob",
          payload: { actor_name: "bob", follow_request_pending: true },
        }),
      ],
      total: 1,
    });
    const { wrapper } = await mountView();

    const accept = wrapper
      .findAll(".notifications-view__request-actions button")
      .find((b) => b.text() === "Accept");
    await accept!.trigger("click");
    await flushPromises();

    expect(acceptFollowRequestApi).toHaveBeenCalledWith(
      "https://remote.example/users/bob",
    );
    expect(wrapper.find(".notifications-view__request-actions").exists()).toBe(
      false,
    );
    expect(wrapper.find(".notifications-view__request-status").text()).toBe(
      "Accepted",
    );
    // Once approved, the row reads as a regular follow.
    expect(wrapper.text()).toContain("started following you");
  });

  it("rejecting a pending follow request calls the API and records the outcome", async () => {
    listNotifications.mockResolvedValueOnce({
      items: [
        createNotification("n1", {
          type: "follow",
          actor_url: "https://remote.example/users/bob",
          source_url: "https://remote.example/users/bob",
          payload: { actor_name: "bob", follow_request_pending: true },
        }),
      ],
      total: 1,
    });
    const { wrapper } = await mountView();

    const reject = wrapper
      .findAll(".notifications-view__request-actions button")
      .find((b) => b.text() === "Reject");
    await reject!.trigger("click");
    await flushPromises();

    expect(rejectFollowRequestApi).toHaveBeenCalledWith(
      "https://remote.example/users/bob",
    );
    expect(wrapper.find(".notifications-view__request-actions").exists()).toBe(
      false,
    );
    expect(wrapper.find(".notifications-view__request-status").text()).toBe(
      "Rejected",
    );
    expect(wrapper.text()).toContain("requested to follow you");
  });

  it("renders a resolved follow request without actions", async () => {
    listNotifications.mockResolvedValueOnce({
      items: [
        createNotification("n1", {
          type: "follow",
          actor_url: "https://remote.example/users/bob",
          source_url: "https://remote.example/users/bob",
          payload: {
            actor_name: "bob",
            follow_request_status: "rejected",
          },
        }),
      ],
      total: 1,
    });
    const { wrapper } = await mountView();

    expect(wrapper.find(".notifications-view__request-actions").exists()).toBe(
      false,
    );
    expect(wrapper.find(".notifications-view__request-status").text()).toBe(
      "Rejected",
    );
  });

  it("renders mention notifications as a read-only activity card", async () => {
    listNotifications.mockResolvedValueOnce({
      items: [
        createNotification("n1", {
          type: "mention",
          actor_url: "https://remote.example/users/bob",
          source_url: "https://remote.example/objects/note-1",
          payload: {
            actor_name: "bob",
            actor_display_name: "Bob",
            object_url: "https://remote.example/objects/note-1",
            object_content: "<p>Hey @alice, <b>check this</b></p>",
            published: "2026-01-01T00:00:00Z",
          },
        }),
      ],
      total: 1,
    });
    const { wrapper } = await mountView();
    const card = wrapper.find(".activity-card");
    expect(card.exists()).toBe(true);
    // Remote HTML is reduced to plain text.
    expect(card.text()).toContain("Hey @alice, check this");
    expect(
      card.find(".activity-card__content").element.innerHTML,
    ).not.toContain("<b>");
    // Read-only: no like/edit actions; the copy-URL entry lives in the
    // card's overflow menu.
    expect(card.find(".activity-card__action").exists()).toBe(false);
    await card.find('button[aria-label="Open menu"]').trigger("click");
    expect(document.body.querySelector(".context-menu")?.textContent).toContain(
      "Copy link",
    );
    // Timestamp links to the original remote object.
    expect(card.find("a.activity-card__time").attributes("href")).toBe(
      "https://remote.example/objects/note-1",
    );
  });

  it("links mentions in note content to the snapshotted actor URL", async () => {
    listNotifications.mockResolvedValueOnce({
      items: [
        createNotification("n1", {
          type: "mention",
          actor_url: "https://remote.example/users/bob",
          source_url: "https://remote.example/objects/note-1",
          payload: {
            actor_name: "bob",
            object_url: "https://remote.example/objects/note-1",
            object_content: "<p>Hey @carol@elsewhere.example, hi</p>",
            object_mentions: [
              {
                handle: "@carol@elsewhere.example",
                actor_url: "https://elsewhere.example/users/carol",
              },
            ],
          },
        }),
      ],
      total: 1,
    });
    const { wrapper } = await mountView();
    const link = wrapper.find(".activity-card__content a");
    expect(link.attributes("href")).toBe("/@carol@elsewhere.example");
    expect(link.text()).toBe("@carol@elsewhere.example");
  });

  it("links replies to the replied-to activity page", async () => {
    listNotifications.mockResolvedValueOnce({
      items: [
        createNotification("n1", {
          type: "reply",
          actor_url: "urn:songhive:user:bob",
          source_url: "https://example.com/users/bob/objects/r1",
          payload: {
            actor_name: "bob",
            object_activity_id: "act-reply-1",
            object_type: "Note",
            target_url: "https://example.com/users/me/objects/t-1",
            target_object_activity_id: "act-target-1",
            target_object_type: "Note",
          },
        }),
      ],
      total: 1,
    });
    getActivity.mockResolvedValueOnce(createActivity({ id: "act-reply-1" }));
    const { wrapper } = await mountView();
    await flushPromises();
    // "replied to your post" opens the replied-to activity's page.
    const action = wrapper.find("a.notifications-view__action");
    expect(action.attributes("href")).toBe("/activities/act-target-1");
    expect(action.text()).toBe("replied to your post");
  });

  it("links activity notifications to the authored activity page", async () => {
    listNotifications.mockResolvedValueOnce({
      items: [
        createNotification("n1", {
          type: "activity",
          actor_url: "urn:songhive:user:bob",
          source_url: "https://example.com/users/bob/objects/s-1",
          payload: {
            actor_name: "bob",
            activity_type: "create",
            object_activity_id: "act-9",
            object_type: "Note",
            object_page_url: "/activities/act-9",
          },
        }),
      ],
      total: 1,
    });
    getActivity.mockResolvedValueOnce(createActivity({ id: "act-9" }));
    const { wrapper } = await mountView();
    await flushPromises();
    const action = wrapper.find("a.notifications-view__action");
    expect(action.attributes("href")).toBe("/activities/act-9");
    expect(action.text()).toBe("shared a post");
  });

  it("phrases authored likes on activity notifications", async () => {
    listNotifications.mockResolvedValueOnce({
      items: [
        createNotification("n1", {
          type: "activity",
          actor_url: "urn:songhive:user:bob",
          source_url: "https://example.com/users/bob/likes/l-1",
          payload: {
            actor_name: "bob",
            activity_type: "like",
            object_activity_id: "act-9",
            object_type: "Note",
          },
        }),
      ],
      total: 1,
    });
    getActivity.mockResolvedValueOnce(createActivity({ id: "act-9" }));
    const { wrapper } = await mountView();
    await flushPromises();
    const action = wrapper.find("a.notifications-view__action");
    expect(action.text()).toBe("liked a post");
  });

  it("renders a real card for activity notifications", async () => {
    listNotifications.mockResolvedValueOnce({
      items: [
        createNotification("n1", {
          type: "activity",
          actor_url: "urn:songhive:user:bob",
          source_url: "https://example.com/users/bob/objects/s-1",
          payload: {
            actor_name: "bob",
            activity_type: "create",
            object_activity_id: "act-note-9",
            object_type: "Note",
          },
        }),
      ],
      total: 1,
    });
    getActivity.mockResolvedValueOnce(
      createActivity({
        id: "act-note-9",
        content: "<p>the post</p>",
      }),
    );
    const { wrapper } = await mountView();
    await flushPromises();
    expect(getActivity).toHaveBeenCalledWith("act-note-9");
    const card = wrapper.find(".activity-card");
    expect(card.exists()).toBe(true);
    expect(card.text()).toContain("the post");
  });

  it("renders a real card for mentions that resolve to a stored activity", async () => {
    listNotifications.mockResolvedValueOnce({
      items: [
        createNotification("n1", {
          type: "mention",
          actor_url: "https://remote.example/users/bob",
          source_url: "https://remote.example/objects/note-9",
          payload: {
            actor_name: "bob",
            object_url: "https://remote.example/objects/note-9",
            object_activity_id: "act-note-9",
            object_type: "Note",
          },
        }),
      ],
      total: 1,
    });
    getActivity.mockResolvedValueOnce(
      createActivity({
        id: "act-note-9",
        source_type: "remote",
        source_actor: "https://remote.example/users/bob",
        content: "<p>the mention</p>",
        content_source: null,
      }),
    );
    const { wrapper } = await mountView();
    await flushPromises();
    expect(getActivity).toHaveBeenCalledWith("act-note-9");
    const card = wrapper.find(".activity-card");
    expect(card.exists()).toBe(true);
    expect(card.text()).toContain("the mention");
    // The action links to the note's permalink page, not its object id.
    expect(
      wrapper.find("a.notifications-view__action").attributes("href"),
    ).toBe("/activities/act-note-9");
  });

  it("resolves legacy note notifications through the lookup endpoint", async () => {
    listNotifications.mockResolvedValueOnce({
      items: [
        createNotification("n1", {
          type: "reply",
          actor_url: "https://remote.example/users/bob",
          source_url: "https://remote.example/objects/note-8",
          payload: {
            actor_name: "bob",
            object_url: "https://remote.example/objects/note-8",
            object_content: "<p>a reply</p>",
            target_url: "https://example.com/users/me/objects/t-1",
          },
        }),
      ],
      total: 1,
    });
    lookupActivity.mockResolvedValueOnce(
      createActivity({
        id: "act-note-8",
        source_type: "remote",
        source_actor: "https://remote.example/users/bob",
        source_id: "https://remote.example/objects/note-8",
      }),
    );
    getActivity.mockResolvedValueOnce(
      createActivity({ id: "act-note-8", content: "<p>a reply</p>" }),
    );
    const { wrapper } = await mountView();
    await flushPromises();
    expect(lookupActivity).toHaveBeenCalledWith(
      "https://remote.example/objects/note-8",
    );
    expect(getActivity).toHaveBeenCalledWith("act-note-8");
    const card = wrapper.find(".activity-card");
    expect(card.exists()).toBe(true);
    expect(card.text()).toContain("a reply");
  });

  it("keeps same-host object permalinks as backend redirects", async () => {
    listNotifications.mockResolvedValueOnce({
      items: [
        createNotification("n1", {
          type: "mention",
          actor_url: "urn:songhive:user:bob",
          source_url: `${window.location.origin}/users/bob/objects/n-1`,
          payload: {
            actor_name: "bob",
            object_url: `${window.location.origin}/users/bob/objects/n-1`,
            object_content: "<p>hi</p>",
          },
        }),
      ],
      total: 1,
    });
    const { wrapper } = await mountView();
    const action = wrapper.find("a.notifications-view__action");
    expect(action.attributes("href")).toBe(
      `${window.location.origin}/users/bob/objects/n-1`,
    );
    expect(action.attributes("target")).toBe("_blank");
  });

  it("shows a copy URL menu entry on snapshot note cards", async () => {
    listNotifications.mockResolvedValueOnce({
      items: [
        createNotification("n1", {
          type: "mention",
          actor_url: "https://remote.example/users/bob",
          source_url: "https://remote.example/objects/note-1",
          payload: {
            actor_name: "bob",
            object_url: "https://remote.example/objects/note-1",
            object_content: "<p>hey</p>",
          },
        }),
      ],
      total: 1,
    });
    const { wrapper } = await mountView();
    await wrapper
      .find(".activity-card")
      .find('button[aria-label="Open menu"]')
      .trigger("click");
    expect(document.body.querySelector(".context-menu")?.textContent).toContain(
      "Copy link",
    );
  });

  it("renders a target link for replies that reference a local item", async () => {
    listNotifications.mockResolvedValueOnce({
      items: [
        createNotification("n1", {
          type: "reply",
          actor_url: "https://remote.example/users/bob",
          source_url: "https://remote.example/objects/note-2",
          payload: {
            actor_name: "bob",
            object_content: "Nice track!",
            target_url: "https://remote.example/objects/orig",
            target_local_url: "/tracks/t-1",
            target_item_title: "My Song",
          },
        }),
      ],
      total: 1,
    });
    const { wrapper } = await mountView();
    const target = wrapper.find(".notifications-view__target");
    expect(target.exists()).toBe(true);
    expect(target.attributes("href")).toBe("/tracks/t-1");
    expect(target.text()).toContain("My Song");
  });

  it("renders a quote notification as a quote card embedding the quoted activity", async () => {
    listNotifications.mockResolvedValueOnce({
      items: [
        createNotification("n1", {
          type: "quote",
          actor_url: "https://remote.example/users/bob",
          source_url: "https://remote.example/objects/note-9",
          payload: {
            actor_name: "bob",
            object_url: "https://remote.example/objects/note-9",
            object_content: "<p>my take on this</p>",
            target_url: "https://example.com/users/me/objects/o-1",
            target_object_activity_id: "act-quoted",
          },
        }),
      ],
      total: 1,
    });
    getActivity.mockResolvedValueOnce(
      createActivity({ id: "act-quoted", content: "<p>the original</p>" }),
    );
    const { wrapper } = await mountView();
    await flushPromises();

    // "quoted your post" links to the quoted activity's page.
    const action = wrapper.find(".notifications-view__action");
    expect(action.attributes("href")).toBe("/activities/act-quoted");

    // The snapshot card is a quote card: the quoter's message is the
    // primary content, with the quoted activity embedded inside.
    const card = wrapper.find(".activity-card");
    expect(card.exists()).toBe(true);
    expect(card.text()).toContain("my take on this");
    expect(getActivity).toHaveBeenCalledWith("act-quoted");
    expect(card.text()).toContain("the original");
  });

  it("renders an item card for share notifications", async () => {
    listNotifications.mockResolvedValueOnce({
      items: [
        createNotification("n1", {
          type: "share",
          source_url: "/albums/alb-1",
          payload: {
            actor_name: "alice",
            item_type: "album",
            item_id: "alb-1",
            item_title: "Cool Album",
          },
        }),
      ],
      total: 1,
    });
    const { wrapper } = await mountView();
    const card = wrapper.find(".item-card");
    expect(card.exists()).toBe(true);
    expect(card.attributes("href")).toBe("/albums/alb-1");
    expect(card.text()).toContain("Cool Album");
  });

  it("renders an item card for likes that resolve to a local item", async () => {
    listNotifications.mockResolvedValueOnce({
      items: [
        createNotification("n1", {
          type: "like",
          source_url: "https://remote.example/objects/t-1",
          payload: {
            actor_name: "bob",
            item_type: "track",
            item_id: "t-1",
            item_title: "My Song",
            local_url: "/tracks/t-1",
          },
        }),
      ],
      total: 1,
    });
    const { wrapper } = await mountView();
    const card = wrapper.find(".item-card");
    expect(card.exists()).toBe(true);
    expect(card.attributes("href")).toBe("/tracks/t-1");
    // The action line prefers the resolved local page over the remote object.
    const link = wrapper.find("a.notifications-view__action");
    expect(link.attributes("href")).toBe("/tracks/t-1");
    expect(link.text()).toBe("liked your track");
  });

  it("falls back to an actor card for likes on remote objects", async () => {
    listNotifications.mockResolvedValueOnce({
      items: [
        createNotification("n1", {
          type: "like",
          actor_url: "https://remote.example/users/bob",
          source_url: "https://remote.example/objects/thing",
          payload: { actor_name: "bob" },
        }),
      ],
      total: 1,
    });
    const { wrapper } = await mountView();
    expect(wrapper.find(".item-card").exists()).toBe(false);
    const card = wrapper.find(".actor-card");
    expect(card.exists()).toBe(true);
    expect(card.attributes("href")).toBe("/@bob@remote.example");
  });

  it("links the actor name and the action text separately for likes", async () => {
    listNotifications.mockResolvedValueOnce({
      items: [
        createNotification("n1", {
          type: "like",
          actor_url: "urn:songhive:user:testuser",
          source_url: "https://example.com/users/me/objects/o1",
          payload: {
            actor_name: "testuser",
            object_activity_id: "act-note-1",
            object_type: "Note",
            object_page_url: "/@me",
          },
        }),
      ],
      total: 1,
    });
    getActivity.mockResolvedValueOnce(createActivity({ id: "act-note-1" }));
    const { wrapper } = await mountView();
    await flushPromises();
    const actorLink = wrapper.find("a.notifications-view__actor");
    expect(actorLink.attributes("href")).toBe("/@testuser");
    const actionLink = wrapper.find("a.notifications-view__action");
    expect(actionLink.attributes("href")).toBe("/@me");
    expect(actionLink.text()).toBe("liked your post");
  });

  it("renders the reacted activity card for likes on Note objects", async () => {
    listNotifications.mockResolvedValueOnce({
      items: [
        createNotification("n1", {
          type: "like",
          actor_url: "urn:songhive:user:testuser",
          source_url: "https://example.com/users/me/objects/o2",
          payload: {
            actor_name: "testuser",
            object_activity_id: "act-note-2",
            object_type: "Note",
            object_page_url: "/@me",
            item_title: "me",
            local_url: "/@me",
          },
        }),
      ],
      total: 1,
    });
    getActivity.mockResolvedValueOnce(
      createActivity({ id: "act-note-2", content: "<p>liked post body</p>" }),
    );
    const { wrapper } = await mountView();
    await flushPromises();
    expect(getActivity).toHaveBeenCalledWith("act-note-2");
    const card = wrapper.find(".activity-card");
    expect(card.exists()).toBe(true);
    expect(card.text()).toContain("liked post body");
    // The entity card is not rendered on top of the activity card.
    expect(wrapper.find(".item-card").exists()).toBe(false);
  });

  it("renders the track item card for likes on Audio objects", async () => {
    listNotifications.mockResolvedValueOnce({
      items: [
        createNotification("n1", {
          type: "boost",
          actor_url: "urn:songhive:user:testuser",
          source_url: "https://example.com/users/me/objects/t-9",
          payload: {
            actor_name: "testuser",
            object_activity_id: "act-audio-1",
            object_type: "Audio",
            object_page_url: "/tracks/t-9/activities",
            item_type: "track",
            item_id: "t-9",
            item_title: "Enigma",
            local_url: "/tracks/t-9",
          },
        }),
      ],
      total: 1,
    });
    const { wrapper } = await mountView();
    await flushPromises();
    expect(getActivity).not.toHaveBeenCalled();
    const card = wrapper.find(".item-card");
    expect(card.exists()).toBe(true);
    expect(card.attributes("href")).toBe("/tracks/t-9");
    // The action line links to the activity's feed page.
    const actionLink = wrapper.find("a.notifications-view__action");
    expect(actionLink.attributes("href")).toBe("/tracks/t-9/activities");
    expect(actionLink.text()).toBe("boosted your track");
  });

  it("names the reply target in the action text", async () => {
    listNotifications.mockResolvedValueOnce({
      items: [
        createNotification("n1", {
          type: "reply",
          actor_url: "https://remote.example/users/bob",
          source_url: "https://remote.example/objects/note-2",
          payload: {
            actor_name: "bob",
            object_content: "Nice track!",
            target_url: "https://example.com/users/me/objects/t-1",
            target_item_type: "track",
            target_item_id: "t-1",
            target_item_title: "My Song",
            target_local_url: "/tracks/t-1",
          },
        }),
        createNotification("n2", {
          type: "reply",
          actor_url: "https://remote.example/users/bob",
          source_url: "https://remote.example/objects/note-3",
          payload: {
            actor_name: "bob",
            object_content: "Nice post!",
            target_object_type: "Note",
            target_object_activity_id: "act-1",
          },
        }),
      ],
      total: 2,
    });
    const { wrapper } = await mountView();
    const actions = wrapper.findAll(".notifications-view__action");
    expect(actions[0].text()).toBe("replied to your track");
    expect(actions[1].text()).toBe("replied to your post");
  });

  it("does not render a broken user item card for status likes", async () => {
    // Legacy payloads recorded item_type "user" for reactions on
    // user-entity statuses; there is no /users/{id} page to link to.
    listNotifications.mockResolvedValueOnce({
      items: [
        createNotification("n1", {
          type: "boost",
          actor_url: "urn:songhive:user:testuser",
          source_url: "https://example.com/users/me/objects/o4",
          payload: {
            actor_name: "testuser",
            item_type: "user",
            item_id: "status-1",
            item_title: "me",
            local_url: "/@me",
          },
        }),
      ],
      total: 1,
    });
    const { wrapper } = await mountView();
    await flushPromises();
    expect(wrapper.find(".item-card").exists()).toBe(false);
    // Falls back to the actor card.
    expect(wrapper.find(".actor-card").exists()).toBe(true);
  });

  it("falls back to the item card when the activity fetch fails", async () => {
    listNotifications.mockResolvedValueOnce({
      items: [
        createNotification("n1", {
          type: "like",
          actor_url: "urn:songhive:user:testuser",
          source_url: "https://example.com/users/me/objects/o3",
          payload: {
            actor_name: "testuser",
            object_activity_id: "act-gone-1",
            object_type: "Note",
            item_type: "track",
            item_id: "t-1",
            item_title: "My Song",
            local_url: "/tracks/t-1",
          },
        }),
      ],
      total: 1,
    });
    getActivity.mockRejectedValueOnce(new Error("not found"));
    const { wrapper } = await mountView();
    await flushPromises();
    expect(getActivity).toHaveBeenCalledWith("act-gone-1");
    const card = wrapper.find(".item-card");
    expect(card.exists()).toBe(true);
    expect(card.attributes("href")).toBe("/tracks/t-1");
  });

  it("shows the unread count when there are unseen notifications", async () => {
    listNotifications.mockResolvedValueOnce({
      items: [createNotification("n1")],
      total: 1,
    });
    const { wrapper } = await mountView();
    const store = useNotificationsStore();
    expect(wrapper.find(".notifications-view__unread").exists()).toBe(false);
    store.unreadCount = 3;
    await wrapper.vm.$nextTick();
    expect(wrapper.find(".notifications-view__unread").text()).toBe(
      "3 unread notifications",
    );
  });

  it("shows the empty state when there are no notifications", async () => {
    listNotifications.mockResolvedValueOnce({ items: [], total: 0 });
    const { wrapper } = await mountView();
    expect(wrapper.find(".notifications-view__empty").exists()).toBe(true);
  });

  it("filters unread notifications", async () => {
    listNotifications.mockResolvedValue({ items: [], total: 0 });
    const { wrapper } = await mountView();
    const unread = wrapper
      .findAll(".notifications-view__filters button")
      .find((b) => b.text() === "Unread");
    await unread!.trigger("click");
    await flushPromises();
    expect(listNotifications).toHaveBeenLastCalledWith({
      limit: 20,
      offset: 0,
      seen: false,
    });
  });

  it("filters notifications by type via multi-select pills", async () => {
    listNotifications.mockResolvedValue({ items: [], total: 0 });
    const { wrapper } = await mountView();
    const pills = wrapper.findAll(".notifications-view__filters--types button");
    expect(pills).toHaveLength(9);
    expect(pills.map((b) => b.text())).toEqual([
      "Follows",
      "Likes",
      "Boosts",
      "Quotes",
      "Replies",
      "Mentions",
      "Shares",
      "Webmentions",
      "User activity",
    ]);

    await pills[0].trigger("click");
    await pills[4].trigger("click");
    await flushPromises();
    expect(listNotifications).toHaveBeenLastCalledWith({
      limit: 20,
      offset: 0,
      seen: undefined,
      type: "follow,reply",
    });
    expect(pills[0].attributes("aria-pressed")).toBe("true");
    expect(pills[1].attributes("aria-pressed")).toBe("false");
  });

  it("marks visible unseen rows as seen after the debounce", async () => {
    listNotifications.mockResolvedValueOnce({
      items: [createNotification("n1"), createNotification("n2")],
      total: 2,
    });
    markSeenApi.mockResolvedValue(undefined);
    const { wrapper } = await mountView();
    const store = useNotificationsStore();
    store.unreadCount = 2;

    intersectIds(["n1", "n2"]);
    expect(markSeenApi).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(600);
    await flushPromises();
    expect(markSeenApi).toHaveBeenCalledTimes(1);
    expect(markSeenApi).toHaveBeenCalledWith(["n1", "n2"]);
    expect(store.unreadCount).toBe(0);
    expect(wrapper.findAll(".notifications-view__row--unseen")).toHaveLength(0);
  });

  it("does not re-mark a row the user just toggled to unseen", async () => {
    listNotifications.mockResolvedValueOnce({
      items: [createNotification("n1")],
      total: 1,
    });
    markSeenApi.mockResolvedValue(undefined);
    markUnseenApi.mockResolvedValue(undefined);
    const { wrapper } = await mountView();
    const store = useNotificationsStore();
    store.unreadCount = 1;

    intersectIds(["n1"]);
    await vi.advanceTimersByTimeAsync(600);
    await flushPromises();
    expect(store.items[0].seen_at).not.toBeNull();

    const menuBtn = wrapper.find(".notifications-view__row button");
    expect(menuBtn.attributes("aria-label")).toBe("Open menu");
    await menuBtn.trigger("click");
    await clickMenuItem("Mark as unread");
    expect(markUnseenApi).toHaveBeenCalledWith(["n1"]);
    expect(store.items[0].seen_at).toBeNull();

    // Still visible but manually unseened: the observer must not mark it.
    intersectIds(["n1"]);
    await vi.advanceTimersByTimeAsync(600);
    expect(markSeenApi).toHaveBeenCalledTimes(1);

    // After scrolling out and back in, auto-seen resumes.
    intersectIds(["n1"], false);
    intersectIds(["n1"]);
    await vi.advanceTimersByTimeAsync(600);
    await flushPromises();
    expect(markSeenApi).toHaveBeenCalledTimes(2);
    expect(markSeenApi).toHaveBeenLastCalledWith(["n1"]);
  });

  it("toggles a seen row back to unseen via the row menu", async () => {
    listNotifications.mockResolvedValueOnce({
      items: [createNotification("n1", { seen_at: "2026-01-02T00:00:00Z" })],
      total: 1,
    });
    markUnseenApi.mockResolvedValue(undefined);
    const { wrapper } = await mountView();
    const store = useNotificationsStore();

    const menuBtn = wrapper.find(".notifications-view__row button");
    expect(menuBtn.attributes("aria-label")).toBe("Open menu");
    await menuBtn.trigger("click");
    await clickMenuItem("Mark as unread");
    expect(markUnseenApi).toHaveBeenCalledWith(["n1"]);
    expect(store.items[0].seen_at).toBeNull();
    expect(store.unreadCount).toBe(1);
  });

  it("marks all notifications as read", async () => {
    listNotifications.mockResolvedValueOnce({
      items: [createNotification("n1")],
      total: 1,
    });
    markAllSeenApi.mockResolvedValue(undefined);
    const { wrapper } = await mountView();
    const store = useNotificationsStore();
    store.unreadCount = 1;
    await flushPromises();

    const markAll = wrapper
      .findAll("button")
      .find((b) => b.text() === "Mark all as read");
    await markAll!.trigger("click");
    await flushPromises();
    expect(markAllSeenApi).toHaveBeenCalled();
    expect(store.unreadCount).toBe(0);
  });

  it("dismisses a single notification via the row menu", async () => {
    listNotifications.mockResolvedValueOnce({
      items: [createNotification("n1"), createNotification("n2")],
      total: 2,
    });
    deleteNotificationApi.mockResolvedValue(undefined);
    const { wrapper } = await mountView();
    const store = useNotificationsStore();

    const row = wrapper.findAll(".notifications-view__row")[0];
    const menuBtn = row.find('button[aria-label="Open menu"]');
    expect(menuBtn.exists()).toBe(true);
    await menuBtn.trigger("click");
    await clickMenuItem("Dismiss");
    await flushPromises();

    expect(deleteNotificationApi).toHaveBeenCalledWith("n1");
    expect(store.items.map((i) => i.id)).toEqual(["n2"]);
  });

  it("bulk-selects rows and deletes them", async () => {
    listNotifications.mockResolvedValueOnce({
      items: [createNotification("n1"), createNotification("n2")],
      total: 2,
    });
    deleteNotificationsApi.mockResolvedValue(2);
    const { wrapper } = await mountView();
    const store = useNotificationsStore();

    const selectToggle = wrapper
      .findAll("button")
      .find((b) => b.text() === "Select");
    await selectToggle!.trigger("click");
    await flushPromises();

    const checkboxes = wrapper.findAll(
      ".notifications-view__row input[type='checkbox']",
    );
    expect(checkboxes).toHaveLength(2);
    await checkboxes[0].setValue(true);
    await checkboxes[1].setValue(true);

    const deleteSelected = wrapper
      .findAll("button")
      .find((b) => b.text() === "Delete selected");
    await deleteSelected!.trigger("click");
    await flushPromises();

    expect(deleteNotificationsApi).toHaveBeenCalledWith(["n1", "n2"]);
    expect(store.items).toEqual([]);
  });

  it("bulk-marks selected rows as read and unread", async () => {
    listNotifications.mockResolvedValueOnce({
      items: [createNotification("n1")],
      total: 1,
    });
    markSeenApi.mockResolvedValue(undefined);
    markUnseenApi.mockResolvedValue(undefined);
    const { wrapper } = await mountView();

    const selectToggle = wrapper
      .findAll("button")
      .find((b) => b.text() === "Select");
    await selectToggle!.trigger("click");
    await flushPromises();

    const checkbox = wrapper.find(
      ".notifications-view__row input[type='checkbox']",
    );
    await checkbox.setValue(true);

    const markRead = wrapper
      .findAll("button")
      .find((b) => b.text() === "Mark as read");
    await markRead!.trigger("click");
    await flushPromises();
    expect(markSeenApi).toHaveBeenCalledWith(["n1"]);

    const markUnread = wrapper
      .findAll("button")
      .find((b) => b.text() === "Mark as unread");
    await markUnread!.trigger("click");
    await flushPromises();
    expect(markUnseenApi).toHaveBeenCalledWith(["n1"]);
  });

  it("select-all toggles every row", async () => {
    listNotifications.mockResolvedValueOnce({
      items: [createNotification("n1"), createNotification("n2")],
      total: 2,
    });
    deleteNotificationsApi.mockResolvedValue(2);
    const { wrapper } = await mountView();

    await wrapper
      .findAll("button")
      .find((b) => b.text() === "Select")!
      .trigger("click");
    await flushPromises();

    const selectAll = wrapper.find(
      ".notifications-view__bulk input[type='checkbox']",
    );
    await selectAll.setValue(true);

    const deleteSelected = wrapper
      .findAll("button")
      .find((b) => b.text() === "Delete selected");
    await deleteSelected!.trigger("click");
    await flushPromises();
    expect(deleteNotificationsApi).toHaveBeenCalledWith(["n1", "n2"]);
  });

  it("clears all notifications after confirming the modal", async () => {
    listNotifications.mockResolvedValueOnce({
      items: [createNotification("n1")],
      total: 1,
    });
    clearNotificationsApi.mockResolvedValue(1);
    const { wrapper } = await mountView();
    const store = useNotificationsStore();

    const clearAll = wrapper
      .findAll("button")
      .find((b) => b.text() === "Clear all");
    await clearAll!.trigger("click");
    await flushPromises();
    const overlay = document.body.querySelector(".app-modal__overlay");
    expect(overlay).not.toBeNull();
    expect(overlay!.textContent).toContain("Clear all notifications?");

    // Cancel path first: nothing happens.
    const cancel = [...overlay!.querySelectorAll("button")].find(
      (b) => b.textContent === "Cancel",
    );
    (cancel as HTMLElement).click();
    await flushPromises();
    expect(clearNotificationsApi).not.toHaveBeenCalled();
    expect(store.items).toHaveLength(1);

    // Reopen and confirm.
    await clearAll!.trigger("click");
    await flushPromises();
    const confirm = [
      ...document.body.querySelectorAll(".app-modal__overlay button"),
    ].find((b) => b.textContent === "Clear all");
    (confirm as HTMLElement).click();
    await flushPromises();

    expect(clearNotificationsApi).toHaveBeenCalled();
    expect(store.items).toEqual([]);
    document.body.innerHTML = "";
  });

  it("shows an error state with retry", async () => {
    listNotifications
      .mockRejectedValueOnce(new Error("boom"))
      .mockResolvedValueOnce({ items: [], total: 0 });
    const { wrapper } = await mountView();
    expect(wrapper.find('[role="alert"]').exists()).toBe(true);
    const retry = wrapper.findAll("button").find((b) => b.text() === "Retry");
    await retry!.trigger("click");
    await flushPromises();
    expect(listNotifications).toHaveBeenCalledTimes(2);
  });
});
