import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { setActivePinia, createPinia } from "pinia";
import { i18n } from "@/i18n";
import { useToastStore } from "@/stores/toast";
import * as adminApi from "@/api/admin";
import type { AdminInviteResponse } from "@/api/admin";
import InvitesView from "./InvitesView.vue";

vi.mock("@/api/admin", () => ({
  listInvites: vi.fn(),
  createInvite: vi.fn(),
  deleteInvite: vi.fn(),
}));

vi.mock("@/composables/useConfirm", () => ({
  useConfirm: vi.fn(),
}));

import { useConfirm } from "@/composables/useConfirm";

function createInviteResponse(code: string): AdminInviteResponse {
  return {
    id: `invite-${code}`,
    code,
    created_by: "admin-1",
    max_uses: 10,
    uses: 0,
    expires_at: null,
    created_at: "2024-01-01T00:00:00Z",
  };
}

function getInviteUrl(code: string): string {
  return `http://localhost:3000/register?invite_code=${encodeURIComponent(code)}`;
}

describe("InvitesView", () => {
  let wrapper: ReturnType<typeof mount>;
  const confirm = vi.fn();
  let writeText: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    setActivePinia(createPinia());
    vi.useFakeTimers();
    vi.clearAllMocks();
    confirm.mockResolvedValue(true);
    vi.mocked(useConfirm).mockReturnValue({ confirm, store: {} as never });
    vi.mocked(adminApi.listInvites).mockResolvedValue([]);

    writeText = vi.fn();
    Object.defineProperty(globalThis.navigator, "clipboard", {
      value: { writeText },
      configurable: true,
    });
  });

  afterEach(() => {
    vi.useRealTimers();
    wrapper?.unmount();
    document.body.innerHTML = "";
  });

  it("lists invites on mount", async () => {
    vi.mocked(adminApi.listInvites).mockResolvedValue([
      createInviteResponse("ABC"),
    ]);

    wrapper = mount(InvitesView);
    await flushPromises();

    expect(adminApi.listInvites).toHaveBeenCalledWith({ limit: 25, offset: 0 });
    expect(wrapper.text()).toContain("ABC");

    const tokenLink = wrapper.find(".invites-view__token");
    expect(tokenLink.attributes("href")).toBe(getInviteUrl("ABC"));
  });

  it("creates an invite and refreshes", async () => {
    vi.mocked(adminApi.listInvites)
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce([createInviteResponse("NEW")]);
    vi.mocked(adminApi.createInvite).mockResolvedValue(
      createInviteResponse("NEW"),
    );

    wrapper = mount(InvitesView, {
      attachTo: document.body,
    });
    await flushPromises();

    const createButton = wrapper
      .findAll("button")
      .find((b) => b.text() === i18n.global.t("pages.admin.invites.create"));
    await createButton?.trigger("click");
    await flushPromises();

    const inputs = document.body.querySelectorAll(
      '#create-invite-form input:not([type="hidden"])',
    );
    const maxUsesInput = inputs[0] as HTMLInputElement;
    maxUsesInput.value = "5";
    maxUsesInput.dispatchEvent(new Event("input"));
    await flushPromises();

    const saveButton = Array.from(
      document.body.querySelectorAll("button"),
    ).find((b) => b.textContent === i18n.global.t("common.save"));
    await saveButton?.click();
    await flushPromises();

    expect(adminApi.createInvite).toHaveBeenCalledWith({
      max_uses: 5,
      expires_at: null,
    });

    const toastStore = useToastStore();
    expect(toastStore.toasts[0].message).toBe(
      i18n.global.t("pages.admin.invites.createSuccess"),
    );
  });

  it("revokes an invite after confirmation", async () => {
    vi.mocked(adminApi.listInvites).mockResolvedValue([
      createInviteResponse("ABC"),
    ]);
    vi.mocked(adminApi.deleteInvite).mockResolvedValue(undefined);

    wrapper = mount(InvitesView);
    await flushPromises();

    const revokeButton = wrapper.find(
      `button[aria-label="${i18n.global.t("pages.admin.invites.revoke")}"]`,
    );
    await revokeButton?.trigger("click");
    await flushPromises();

    expect(confirm).toHaveBeenCalled();
    expect(adminApi.deleteInvite).toHaveBeenCalledWith("ABC");
  });

  it("opens a detail modal when a card is clicked", async () => {
    vi.mocked(adminApi.listInvites).mockResolvedValue([
      createInviteResponse("ABC"),
    ]);

    wrapper = mount(InvitesView);
    await flushPromises();

    await wrapper.find(".invites-view__card").trigger("click");
    await flushPromises();

    const dialog = document.body.querySelector('[role="dialog"]');
    expect(dialog).not.toBeNull();
    expect(dialog?.textContent).toContain(
      i18n.global.t("pages.admin.invites.detailsTitle"),
    );

    const inputs = dialog?.querySelectorAll("input");
    expect(inputs?.[0].value).toBe("ABC");
    expect(inputs?.[1].value).toBe(getInviteUrl("ABC"));
  });

  it("copies the invite URL from the detail modal", async () => {
    vi.mocked(adminApi.listInvites).mockResolvedValue([
      createInviteResponse("ABC"),
    ]);

    wrapper = mount(InvitesView);
    await flushPromises();

    await wrapper.find(".invites-view__card").trigger("click");
    await flushPromises();

    const dialog = document.body.querySelector('[role="dialog"]');
    const urlCopyLabel = `${i18n.global.t("common.copy")} ${i18n.global.t(
      "pages.admin.invites.url",
    )}`;
    const copyButton = dialog?.querySelector(
      `button[aria-label="${urlCopyLabel}"]`,
    );
    (copyButton as HTMLButtonElement | null | undefined)?.click();
    await flushPromises();

    expect(writeText).toHaveBeenCalledWith(getInviteUrl("ABC"));

    const toastStore = useToastStore();
    expect(toastStore.toasts[0].message).toBe(
      i18n.global.t("pages.admin.invites.urlCopied"),
    );
  });

  it("copies the raw token from the detail modal", async () => {
    vi.mocked(adminApi.listInvites).mockResolvedValue([
      createInviteResponse("ABC"),
    ]);

    wrapper = mount(InvitesView);
    await flushPromises();

    await wrapper.find(".invites-view__card").trigger("click");
    await flushPromises();

    const dialog = document.body.querySelector('[role="dialog"]');
    const tokenCopyLabel = `${i18n.global.t("common.copy")} ${i18n.global.t(
      "pages.admin.invites.token",
    )}`;
    const copyButton = dialog?.querySelector(
      `button[aria-label="${tokenCopyLabel}"]`,
    );
    (copyButton as HTMLButtonElement | null | undefined)?.click();
    await flushPromises();

    expect(writeText).toHaveBeenCalledWith("ABC");

    const toastStore = useToastStore();
    expect(toastStore.toasts[0].message).toBe(
      i18n.global.t("pages.admin.invites.tokenCopied"),
    );
  });
});
