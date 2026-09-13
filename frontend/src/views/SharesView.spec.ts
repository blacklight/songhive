import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { createRouter, createMemoryHistory } from "vue-router";
import { createPinia, setActivePinia } from "pinia";
import { i18n } from "@/i18n";
import { useAuthStore } from "@/stores/auth";
import * as sharesApi from "@/api/shares";
import type { CreatedShareResponse } from "@/api/shares";
import type { UserResponse } from "@/api/users";
import SharesView from "./SharesView.vue";

vi.mock("@/api/shares", () => ({
  listMyShares: vi.fn(),
  deleteShareGrant: vi.fn(),
  deleteShareUrl: vi.fn(),
}));

function createTestRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: "/shares", component: SharesView },
      { path: "/:pathMatch(.*)*", component: { template: "<div/>" } },
    ],
  });
}

function makeShare(
  overrides: Partial<CreatedShareResponse> = {},
): CreatedShareResponse {
  return {
    id: "s1",
    kind: "grant",
    item_type: "track",
    item_id: "t1",
    item_title: "Song One",
    item_url: "/tracks/t1",
    user_id: "u2",
    username: "other",
    created_at: "2026-01-01T00:00:00Z",
    expires_at: null,
    revoked_at: null,
    ...overrides,
  };
}

function mockMatchMedia(matches: boolean) {
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    value: vi.fn((query: string) => ({
      matches,
      media: query,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  });
}

async function confirmDeleteModal() {
  await flushPromises();
  const confirm = Array.from(document.body.querySelectorAll("button")).find(
    (b) => b.textContent === i18n.global.t("common.delete"),
  );
  expect(confirm).toBeDefined();
  confirm?.click();
  await flushPromises();
}

describe("SharesView", () => {
  let wrapper: ReturnType<typeof mount>;

  beforeEach(() => {
    setActivePinia(createPinia());
    const authStore = useAuthStore();
    authStore.$patch({
      user: { id: "user-1", username: "test" } as UserResponse,
    });
    vi.clearAllMocks();
    mockMatchMedia(true);
    vi.mocked(sharesApi.listMyShares).mockResolvedValue([]);
    vi.mocked(sharesApi.deleteShareGrant).mockResolvedValue(undefined);
    vi.mocked(sharesApi.deleteShareUrl).mockResolvedValue(undefined);
  });

  afterEach(() => {
    wrapper?.unmount();
    document.body.innerHTML = "";
  });

  function mountView() {
    wrapper = mount(SharesView, {
      attachTo: document.body,
      global: { plugins: [createTestRouter()] },
    });
    return wrapper;
  }

  it("lists the user's share grants and public links", async () => {
    vi.mocked(sharesApi.listMyShares).mockResolvedValue([
      makeShare(),
      makeShare({
        id: "s2",
        kind: "url",
        item_type: "album",
        item_id: "al1",
        item_title: "Meadowland",
        item_url: "/albums/al1",
        user_id: null,
        username: null,
      }),
    ]);

    mountView();
    await flushPromises();

    expect(sharesApi.listMyShares).toHaveBeenCalledWith({
      limit: 50,
      offset: 0,
    });
    expect(wrapper.text()).toContain("Song One");
    expect(wrapper.text()).toContain("Meadowland");
    expect(wrapper.text()).toContain("other");
    expect(wrapper.text()).toContain(
      i18n.global.t("pages.shares.anyoneWithLink"),
    );
    expect(wrapper.text()).toContain(i18n.global.t("pages.shares.types.grant"));
    expect(wrapper.text()).toContain(i18n.global.t("pages.shares.types.url"));
  });

  it("shows status labels for active, expired, and revoked links", async () => {
    vi.mocked(sharesApi.listMyShares).mockResolvedValue([
      makeShare({ id: "s1" }),
      makeShare({
        id: "s2",
        kind: "url",
        expires_at: "2020-01-01T00:00:00Z",
      }),
      makeShare({
        id: "s3",
        kind: "url",
        revoked_at: "2026-01-02T00:00:00Z",
      }),
    ]);

    mountView();
    await flushPromises();

    expect(wrapper.text()).toContain(
      i18n.global.t("pages.shares.status.active"),
    );
    expect(wrapper.text()).toContain(
      i18n.global.t("pages.shares.status.expired"),
    );
    expect(wrapper.text()).toContain(
      i18n.global.t("pages.shares.status.revoked"),
    );
  });

  it("renders cards instead of a table on narrow viewports", async () => {
    mockMatchMedia(false);
    vi.mocked(sharesApi.listMyShares).mockResolvedValue([
      makeShare(),
      makeShare({
        id: "s2",
        kind: "url",
        user_id: null,
        username: null,
      }),
    ]);

    mountView();
    await flushPromises();

    expect(wrapper.find("table").exists()).toBe(false);
    const cards = wrapper.findAll(".shares-view__card");
    expect(cards).toHaveLength(2);
    expect(wrapper.text()).toContain("Song One");
    expect(wrapper.text()).toContain(
      i18n.global.t("pages.shares.anyoneWithLink"),
    );

    // Bulk selection is offered per card in bulk mode.
    const bulkEdit = wrapper
      .findAll("button")
      .find((b) => b.text() === i18n.global.t("browse.bulkEdit.start"));
    await bulkEdit?.trigger("click");
    await flushPromises();
    const cardCheckboxes = wrapper.findAll(
      ".shares-view__card input[type='checkbox']",
    );
    expect(cardCheckboxes.length).toBe(2);
  });

  it("shows the empty state", async () => {
    mountView();
    await flushPromises();
    expect(wrapper.text()).toContain(i18n.global.t("pages.shares.empty"));
  });

  it("shows an error with retry", async () => {
    vi.mocked(sharesApi.listMyShares).mockRejectedValue(
      new Error("network failure"),
    );

    mountView();
    await flushPromises();
    expect(wrapper.text()).toContain("network failure");

    vi.mocked(sharesApi.listMyShares).mockResolvedValue([makeShare()]);
    const retry = wrapper
      .findAll("button")
      .find((b) => b.text() === i18n.global.t("common.retry"));
    await retry?.trigger("click");
    await flushPromises();
    expect(wrapper.text()).toContain("Song One");
  });

  it("revokes a single share grant after confirmation", async () => {
    vi.mocked(sharesApi.listMyShares).mockResolvedValue([makeShare()]);

    mountView();
    await flushPromises();

    const revoke = wrapper
      .findAll("button")
      .find((b) => b.text() === i18n.global.t("browse.share.revoke"));
    expect(revoke).toBeDefined();
    await revoke?.trigger("click");
    await confirmDeleteModal();

    expect(sharesApi.deleteShareGrant).toHaveBeenCalledWith("s1");
    expect(sharesApi.deleteShareUrl).not.toHaveBeenCalled();
    expect(sharesApi.listMyShares).toHaveBeenCalledTimes(2);
  });

  it("revokes a single share URL after confirmation", async () => {
    vi.mocked(sharesApi.listMyShares).mockResolvedValue([
      makeShare({ id: "s9", kind: "url", user_id: null, username: null }),
    ]);

    mountView();
    await flushPromises();

    const revoke = wrapper
      .findAll("button")
      .find((b) => b.text() === i18n.global.t("browse.share.revoke"));
    await revoke?.trigger("click");
    await confirmDeleteModal();

    expect(sharesApi.deleteShareUrl).toHaveBeenCalledWith("s9");
    expect(sharesApi.deleteShareGrant).not.toHaveBeenCalled();
  });

  it("reloads with include_revoked when the filter toggle is enabled", async () => {
    mountView();
    await flushPromises();

    expect(sharesApi.listMyShares).toHaveBeenCalledWith({
      limit: 50,
      offset: 0,
      include_revoked: undefined,
    });

    const toggle = wrapper.find(".shares-view__actions input[type='checkbox']");
    expect(toggle.exists()).toBe(true);
    await toggle.setValue(true);
    await flushPromises();

    expect(sharesApi.listMyShares).toHaveBeenLastCalledWith({
      limit: 50,
      offset: 0,
      include_revoked: true,
    });
  });

  it("does not offer revocation for already-revoked links", async () => {
    vi.mocked(sharesApi.listMyShares).mockResolvedValue([
      makeShare({ id: "s1", kind: "url", revoked_at: "2026-01-02T00:00:00Z" }),
    ]);

    mountView();
    await flushPromises();

    const revoke = wrapper
      .findAll("button")
      .find((b) => b.text() === i18n.global.t("browse.share.revoke"));
    expect(revoke).toBeUndefined();
  });

  it("bulk-revokes the selected shares", async () => {
    vi.mocked(sharesApi.listMyShares).mockResolvedValue([
      makeShare({ id: "s1" }),
      makeShare({ id: "s2", kind: "url", user_id: null, username: null }),
    ]);

    mountView();
    await flushPromises();

    const bulkEdit = wrapper
      .findAll("button")
      .find((b) => b.text() === i18n.global.t("browse.bulkEdit.start"));
    expect(bulkEdit).toBeDefined();
    await bulkEdit?.trigger("click");
    await flushPromises();

    // Select-all checkbox lives in the table header once bulk mode is on.
    const selectAll = wrapper.find("thead input[type='checkbox']");
    expect(selectAll.exists()).toBe(true);
    await selectAll.setValue(true);
    await flushPromises();

    const deleteSelected = wrapper
      .findAll("button")
      .find(
        (b) => b.text() === i18n.global.t("browse.bulkEdit.deleteSelected"),
      );
    await deleteSelected?.trigger("click");
    await confirmDeleteModal();

    expect(sharesApi.deleteShareGrant).toHaveBeenCalledWith("s1");
    expect(sharesApi.deleteShareUrl).toHaveBeenCalledWith("s2");
  });
});
