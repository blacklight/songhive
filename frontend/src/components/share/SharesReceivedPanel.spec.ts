import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { createRouter, createMemoryHistory } from "vue-router";
import { createPinia, setActivePinia } from "pinia";
import { i18n } from "@/i18n";
import { useAuthStore } from "@/stores/auth";
import { useConfirmStore } from "@/stores/confirm";
import * as sharesApi from "@/api/shares";
import type { ReceivedShareResponse } from "@/api/shares";
import type { UserResponse } from "@/api/users";
import SharesReceivedPanel from "./SharesReceivedPanel.vue";

vi.mock("@/api/shares", () => ({
  listReceivedShares: vi.fn(),
  deleteShareGrant: vi.fn(),
}));

function createTestRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      {
        path: "/users/:username",
        name: "userProfile",
        component: { template: "<div/>" },
      },
      { path: "/:pathMatch(.*)*", component: { template: "<div/>" } },
    ],
  });
}

function makeShare(
  overrides: Partial<ReceivedShareResponse> = {},
): ReceivedShareResponse {
  return {
    id: "g1",
    item_type: "playlist",
    item_id: "pl-1",
    item_title: "Road Trip",
    item_url: "/playlists/pl-1",
    collaborator: false,
    created_at: "2026-01-01T00:00:00Z",
    shared_by: {
      id: "owner-1",
      username: "carol",
      display_name: "Carol",
    },
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

describe("SharesReceivedPanel", () => {
  let wrapper: ReturnType<typeof mount>;

  beforeEach(() => {
    setActivePinia(createPinia());
    const authStore = useAuthStore();
    authStore.$patch({
      user: { id: "user-1", username: "test" } as UserResponse,
    });
    vi.clearAllMocks();
    mockMatchMedia(true);
    vi.mocked(sharesApi.listReceivedShares).mockResolvedValue({
      items: [],
      offset: 0,
      total: 0,
    });
    vi.mocked(sharesApi.deleteShareGrant).mockResolvedValue(undefined);
  });

  afterEach(() => {
    wrapper?.unmount();
    document.body.innerHTML = "";
  });

  function mountPanel() {
    wrapper = mount(SharesReceivedPanel, {
      attachTo: document.body,
      global: { plugins: [createTestRouter()] },
    });
    return wrapper;
  }

  it("lists received shares with role and shared-by columns", async () => {
    vi.mocked(sharesApi.listReceivedShares).mockResolvedValue({
      items: [
        makeShare(),
        makeShare({
          id: "g2",
          item_type: "library",
          item_id: "lib-1",
          item_title: "Crate",
          item_url: "/libraries/lib-1",
          collaborator: true,
        }),
      ],
      offset: 0,
      total: 2,
    });

    mountPanel();
    await flushPromises();

    expect(sharesApi.listReceivedShares).toHaveBeenCalledWith({
      item_type: undefined,
      limit: 50,
      offset: 0,
    });
    expect(wrapper.text()).toContain("Road Trip");
    expect(wrapper.text()).toContain("Crate");
    expect(wrapper.text()).toContain("Carol");
    expect(wrapper.text()).toContain(i18n.global.t("browse.share.roleViewer"));
    expect(wrapper.text()).toContain(
      i18n.global.t("browse.share.roleCollaborator"),
    );
  });

  it("shows the empty state", async () => {
    mountPanel();
    await flushPromises();
    expect(wrapper.text()).toContain(
      i18n.global.t("pages.shares.received.empty"),
    );
  });

  it("leaves a share after confirmation", async () => {
    vi.mocked(sharesApi.listReceivedShares).mockResolvedValue({
      items: [makeShare()],
      offset: 0,
      total: 1,
    });
    const confirmStore = useConfirmStore();
    vi.spyOn(confirmStore, "open").mockResolvedValue(true);

    mountPanel();
    await flushPromises();

    const leave = wrapper
      .findAll("button")
      .find((b) => b.text() === i18n.global.t("browse.share.leave"));
    expect(leave).toBeDefined();
    await leave?.trigger("click");
    await flushPromises();

    expect(confirmStore.open).toHaveBeenCalled();
    expect(sharesApi.deleteShareGrant).toHaveBeenCalledWith("g1");
    // Refreshes after leaving.
    expect(sharesApi.listReceivedShares).toHaveBeenCalledTimes(2);
  });

  it("does not leave when the confirmation is declined", async () => {
    vi.mocked(sharesApi.listReceivedShares).mockResolvedValue({
      items: [makeShare()],
      offset: 0,
      total: 1,
    });
    const confirmStore = useConfirmStore();
    vi.spyOn(confirmStore, "open").mockResolvedValue(false);

    mountPanel();
    await flushPromises();

    const leave = wrapper
      .findAll("button")
      .find((b) => b.text() === i18n.global.t("browse.share.leave"));
    await leave?.trigger("click");
    await flushPromises();

    expect(sharesApi.deleteShareGrant).not.toHaveBeenCalled();
  });

  it("filters by item type", async () => {
    mountPanel();
    await flushPromises();

    const select = wrapper.find("select");
    expect(select.exists()).toBe(true);
    await select.setValue("playlist");
    await flushPromises();

    expect(sharesApi.listReceivedShares).toHaveBeenLastCalledWith(
      expect.objectContaining({ item_type: "playlist" }),
    );
  });
});
