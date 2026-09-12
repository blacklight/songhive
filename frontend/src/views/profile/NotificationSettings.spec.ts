import { describe, it, expect, beforeEach, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { setActivePinia, createPinia } from "pinia";
import * as notificationsApi from "@/api/notifications";
import type {
  NotificationPreferenceItem,
  NotificationType,
} from "@/api/notifications";
import { useAuthStore } from "@/stores/auth";
import { useToastStore } from "@/stores/toast";
import NotificationSettings from "./NotificationSettings.vue";

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
  getNotificationPreferences: vi.fn(),
  updateNotificationPreferences: vi.fn(),
}));

const getPreferences = vi.mocked(notificationsApi.getNotificationPreferences);
const updatePreferences = vi.mocked(
  notificationsApi.updateNotificationPreferences,
);

const TYPES: NotificationType[] = [
  "follow",
  "like",
  "boost",
  "quote",
  "reply",
  "mention",
  "share",
];

function defaultPrefs(
  overrides: Partial<NotificationPreferenceItem> = {},
): NotificationPreferenceItem[] {
  return TYPES.map((type) => ({
    type,
    in_app: true,
    email: false,
    email_digest: false,
    ...overrides,
  }));
}

function authenticate(emailVerified: boolean) {
  const store = useAuthStore();
  store.user = {
    id: "u1",
    username: "alice",
    display_name: null,
    bio: null,
    avatar_url: null,
    email_verified: emailVerified,
    links: [],
  };
  store.status = "authenticated";
}

describe("NotificationSettings", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.clearAllMocks();
    getPreferences.mockResolvedValue(defaultPrefs());
  });

  it("loads and renders the preference matrix", async () => {
    authenticate(true);
    const wrapper = mount(NotificationSettings);
    await flushPromises();

    expect(getPreferences).toHaveBeenCalled();
    const rows = wrapper.findAll("tbody tr");
    expect(rows).toHaveLength(7);
    expect(rows[0].text()).toContain("Follows");
    expect(wrapper.findAll("input[type='checkbox']")).toHaveLength(21);
  });

  it("saves preferences and shows a success toast", async () => {
    authenticate(true);
    updatePreferences.mockResolvedValue(defaultPrefs());
    const wrapper = mount(NotificationSettings);
    const toast = useToastStore();
    await flushPromises();

    const followEmail = wrapper.find(
      "tbody tr:first-child td:nth-child(3) input",
    );
    await followEmail.setValue(true);
    await wrapper
      .findAll("button")
      .find((b) => b.text() === "Save preferences")!
      .trigger("click");
    await flushPromises();

    expect(updatePreferences).toHaveBeenCalledWith(
      expect.arrayContaining([
        expect.objectContaining({ type: "follow", email: true }),
      ]),
    );
    expect(toast.toasts.some((t) => t.type === "success")).toBe(true);
  });

  it("shows an error toast when saving fails", async () => {
    authenticate(true);
    updatePreferences.mockRejectedValue(new Error("boom"));
    const wrapper = mount(NotificationSettings);
    const toast = useToastStore();
    await flushPromises();

    await wrapper
      .findAll("button")
      .find((b) => b.text() === "Save preferences")!
      .trigger("click");
    await flushPromises();

    expect(toast.toasts.some((t) => t.type === "error")).toBe(true);
  });

  it("disables email columns with a hint when email is unverified", async () => {
    authenticate(false);
    const wrapper = mount(NotificationSettings);
    await flushPromises();

    expect(wrapper.find(".notification-settings__email-warning").exists()).toBe(
      true,
    );

    const rows = wrapper.findAll("tbody tr");
    for (const row of rows) {
      const checkboxes = row.findAll("input[type='checkbox']");
      expect(checkboxes[1].attributes("disabled")).toBeDefined();
      expect(checkboxes[2].attributes("disabled")).toBeDefined();
      expect(checkboxes[0].attributes("disabled")).toBeUndefined();
    }
  });

  it("enables email columns when email is verified", async () => {
    authenticate(true);
    const wrapper = mount(NotificationSettings);
    await flushPromises();

    expect(wrapper.find(".notification-settings__email-warning").exists()).toBe(
      false,
    );
    const first = wrapper.find("tbody tr:first-child");
    const checkboxes = first.findAll("input[type='checkbox']");
    expect(checkboxes[1].attributes("disabled")).toBeUndefined();
    expect(checkboxes[2].attributes("disabled")).toBeUndefined();
  });

  it("shows the error state when loading fails", async () => {
    authenticate(true);
    getPreferences.mockRejectedValueOnce(new Error("boom"));
    const wrapper = mount(NotificationSettings);
    await flushPromises();

    expect(wrapper.find('[role="alert"]').exists()).toBe(true);
  });
});
