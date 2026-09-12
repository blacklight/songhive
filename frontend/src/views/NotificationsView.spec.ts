import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { createRouter, createMemoryHistory } from "vue-router";
import { setActivePinia, createPinia } from "pinia";
import * as notificationsApi from "@/api/notifications";
import type { NotificationResponse } from "@/api/notifications";
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

const listNotifications = vi.mocked(notificationsApi.listNotifications);
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

describe("NotificationsView", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.clearAllMocks();
    observed = new Set();
    observerCallback = null;
    window.IntersectionObserver =
      FakeIntersectionObserver as unknown as typeof IntersectionObserver;
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
    window.IntersectionObserver = OriginalIntersectionObserver;
  });

  async function mountView() {
    const router = createTestRouter();
    const wrapper = mount(NotificationsView, {
      global: { plugins: [router] },
    });
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
    const link = wrapper.find("a.notifications-view__text");
    expect(link.attributes("href")).toBe("https://remote.example/users/bob");
    expect(link.attributes("target")).toBe("_blank");
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
    expect(card.attributes("href")).toBe("https://remote.example/users/bob");
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
    // Read-only: no like/edit actions.
    expect(card.find(".activity-card__actions").exists()).toBe(false);
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
    expect(link.attributes("href")).toBe(
      "https://elsewhere.example/users/carol",
    );
    expect(link.text()).toBe("@carol@elsewhere.example");
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
    const link = wrapper.find("a.notifications-view__text");
    expect(link.attributes("href")).toBe("/tracks/t-1");
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
    expect(card.attributes("href")).toBe("https://remote.example/users/bob");
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
    expect(pills).toHaveLength(7);
    expect(pills.map((b) => b.text())).toEqual([
      "Follows",
      "Likes",
      "Boosts",
      "Quotes",
      "Replies",
      "Mentions",
      "Shares",
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

    const toggle = wrapper.find(".notifications-view__row button");
    await toggle.trigger("click");
    await flushPromises();
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

  it("toggles a seen row back to unseen via the row button", async () => {
    listNotifications.mockResolvedValueOnce({
      items: [createNotification("n1", { seen_at: "2026-01-02T00:00:00Z" })],
      total: 1,
    });
    markUnseenApi.mockResolvedValue(undefined);
    const { wrapper } = await mountView();
    const store = useNotificationsStore();

    const toggle = wrapper.find(".notifications-view__row button");
    expect(toggle.attributes("aria-label")).toBe("Mark as unread");
    await toggle.trigger("click");
    await flushPromises();
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

  it("dismisses a single notification via the row button", async () => {
    listNotifications.mockResolvedValueOnce({
      items: [createNotification("n1"), createNotification("n2")],
      total: 2,
    });
    deleteNotificationApi.mockResolvedValue(undefined);
    const { wrapper } = await mountView();
    const store = useNotificationsStore();

    const row = wrapper.findAll(".notifications-view__row")[0];
    const dismiss = row
      .findAll("button")
      .find((b) => b.attributes("aria-label") === "Dismiss");
    expect(dismiss).toBeDefined();
    await dismiss!.trigger("click");
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
