import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { setActivePinia, createPinia } from "pinia";
import { nextTick } from "vue";
import { i18n } from "@/i18n";
import { formatDateTime } from "@/i18n";
import { useAuthStore } from "@/stores/auth";
import { useConfirmStore } from "@/stores/confirm";
import * as sharesApi from "@/api/shares";
import ConfirmDialog from "@/components/feedback/ConfirmDialog.vue";
import ShareDialog from "./ShareDialog.vue";

vi.mock("@/api/shares", () => ({
  listShareGrants: vi.fn(),
  createShareGrant: vi.fn(),
  deleteShareGrant: vi.fn(),
  listShareUrls: vi.fn(),
  createShareUrl: vi.fn(),
  deleteShareUrl: vi.fn(),
}));

function createGrant(
  id: string,
  userId: string,
  createdAt = "2026-01-01T00:00:00Z",
) {
  return {
    id,
    item_type: "album",
    item_id: "album-1",
    user_id: userId,
    created_at: createdAt,
  };
}

function createToken(
  id: string,
  expiresAt: string | null = null,
  createdAt = "2026-01-01T00:00:00Z",
) {
  return {
    id,
    expires_at: expiresAt,
    revoked_at: null,
    created_at: createdAt,
  };
}

function setAuthenticated(userId = "user-1") {
  const authStore = useAuthStore();
  authStore.accessToken = "token";
  authStore.refreshToken = "refresh";
  authStore.expiresAt = Date.now() + 10000;
  authStore.status = "authenticated";
  authStore.user = { id: userId, username: "alice" } as never;
}

function mountOpen(props: Record<string, unknown> = {}) {
  return mount(ShareDialog, {
    attachTo: document.body,
    props: {
      open: true,
      itemType: "album",
      itemId: "album-1",
      title: "Meadowland",
      ...props,
    },
  });
}

describe("ShareDialog", () => {
  let wrapper: ReturnType<typeof mount>;

  beforeEach(() => {
    setActivePinia(createPinia());
    vi.clearAllMocks();
    vi.mocked(sharesApi.listShareGrants).mockResolvedValue([]);
    vi.mocked(sharesApi.listShareUrls).mockResolvedValue([]);
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText: vi.fn() },
      configurable: true,
    });
  });

  afterEach(() => {
    wrapper?.unmount();
    document.body.innerHTML = "";
  });

  it("lists share grants on open", async () => {
    vi.mocked(sharesApi.listShareGrants).mockResolvedValue([
      createGrant("sg1", "user-2"),
    ]);

    wrapper = mountOpen({ ownerId: "user-1" });
    setAuthenticated("user-1");
    await flushPromises();

    expect(sharesApi.listShareGrants).toHaveBeenCalledWith({
      item_type: "album",
      item_id: "album-1",
      limit: 100,
    });

    expect(document.body.textContent).toContain("user-2");
  });

  it("switches to the public links tab", async () => {
    vi.mocked(sharesApi.listShareUrls).mockResolvedValue([createToken("st1")]);

    wrapper = mountOpen({ ownerId: "user-1" });
    setAuthenticated("user-1");
    await flushPromises();

    const urlsTab = Array.from(document.body.querySelectorAll("button")).find(
      (b) => b.textContent === i18n.global.t("browse.share.shareUrls"),
    );
    expect(urlsTab).toBeDefined();
    await urlsTab?.click();
    await flushPromises();

    expect(sharesApi.listShareUrls).toHaveBeenCalledWith({
      item_type: "album",
      item_id: "album-1",
      limit: 100,
    });

    expect(document.body.textContent).toContain(
      formatDateTime("2026-01-01T00:00:00Z"),
    );
    expect(
      Array.from(document.body.querySelectorAll("button")).some(
        (b) => b.textContent === i18n.global.t("browse.share.revoke"),
      ),
    ).toBe(true);
  });

  it("creates a share grant for the owner", async () => {
    setAuthenticated("user-1");
    wrapper = mountOpen({ ownerId: "user-1" });
    await flushPromises();

    const input = document.body.querySelector(
      'input[type="text"]',
    ) as HTMLInputElement;
    input.value = "user-2";
    input.dispatchEvent(new Event("input"));
    await flushPromises();

    const createButton = Array.from(
      document.body.querySelectorAll("button"),
    ).find((b) => b.textContent === i18n.global.t("browse.share.createGrant"));
    expect(createButton).toBeDefined();
    await createButton?.click();
    await flushPromises();

    expect(sharesApi.createShareGrant).toHaveBeenCalledWith({
      item_type: "album",
      item_id: "album-1",
      user_id: "user-2",
    });
  });

  it("revokes a share grant after confirmation", async () => {
    const confirm = useConfirmStore();
    vi.spyOn(confirm, "open").mockResolvedValue(true);
    vi.mocked(sharesApi.listShareGrants).mockResolvedValue([
      createGrant("sg1", "user-2"),
    ]);

    setAuthenticated("user-1");
    wrapper = mountOpen({ ownerId: "user-1" });
    await flushPromises();

    const revokeButton = Array.from(
      document.body.querySelectorAll("button"),
    ).find((b) => b.textContent === i18n.global.t("browse.share.revoke"));
    expect(revokeButton).toBeDefined();
    await revokeButton?.click();
    await flushPromises();

    expect(confirm.open).toHaveBeenCalledWith(
      expect.objectContaining({
        message: expect.stringContaining("user-2"),
      }),
    );
    expect(sharesApi.deleteShareGrant).toHaveBeenCalledWith("sg1");
  });

  it("creates a public share URL and copies it", async () => {
    const writeText = vi.fn();
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText },
      configurable: true,
    });

    vi.mocked(sharesApi.createShareUrl).mockResolvedValue({
      id: "st1",
      url: "http://localhost/share/abc",
      token: "abc",
      expires_at: null,
    });

    setAuthenticated("user-1");
    wrapper = mountOpen({ ownerId: "user-1" });
    await flushPromises();

    const urlsTab = Array.from(document.body.querySelectorAll("button")).find(
      (b) => b.textContent === i18n.global.t("browse.share.shareUrls"),
    );
    await urlsTab?.click();
    await flushPromises();

    const createButton = Array.from(
      document.body.querySelectorAll("button"),
    ).find(
      (b) => b.textContent === i18n.global.t("browse.share.createShareUrl"),
    );
    expect(createButton).toBeDefined();
    await createButton?.click();
    await flushPromises();

    expect(sharesApi.createShareUrl).toHaveBeenCalledWith(
      expect.objectContaining({
        item_type: "album",
        item_id: "album-1",
        expires_at: null,
      }),
    );

    const inputs = Array.from(
      document.body.querySelectorAll("input"),
    ) as HTMLInputElement[];
    const urlInput = inputs.find(
      (i) => i.value === "http://localhost/share/abc",
    );
    const tokenInput = inputs.find((i) => i.value === "abc");
    expect(urlInput).toBeDefined();
    expect(tokenInput).toBeDefined();

    const copyButtons = Array.from(
      document.body.querySelectorAll("button"),
    ).filter((b) => b.textContent === i18n.global.t("common.copy"));
    expect(copyButtons.length).toBeGreaterThanOrEqual(1);
    await copyButtons[0]?.click();
    await flushPromises();

    expect(writeText).toHaveBeenCalledWith("http://localhost/share/abc");
  });

  it("revokes a public share URL after confirmation", async () => {
    const confirm = useConfirmStore();
    vi.spyOn(confirm, "open").mockResolvedValue(true);
    vi.mocked(sharesApi.listShareUrls).mockResolvedValue([createToken("st1")]);

    setAuthenticated("user-1");
    wrapper = mountOpen({ ownerId: "user-1" });
    await flushPromises();

    const urlsTab = Array.from(document.body.querySelectorAll("button")).find(
      (b) => b.textContent === i18n.global.t("browse.share.shareUrls"),
    );
    await urlsTab?.click();
    await flushPromises();

    const revokeButton = Array.from(
      document.body.querySelectorAll("button"),
    ).find((b) => b.textContent === i18n.global.t("browse.share.revoke"));
    expect(revokeButton).toBeDefined();
    await revokeButton?.click();
    await flushPromises();

    expect(sharesApi.deleteShareUrl).toHaveBeenCalledWith("st1");
  });

  it("surfaces a grant revoke error inline", async () => {
    const confirm = useConfirmStore();
    vi.spyOn(confirm, "open").mockResolvedValue(true);
    vi.mocked(sharesApi.listShareGrants).mockResolvedValue([
      createGrant("sg1", "user-2"),
    ]);
    vi.mocked(sharesApi.deleteShareGrant).mockRejectedValue(
      new Error("revoke failed"),
    );

    setAuthenticated("user-1");
    wrapper = mountOpen({ ownerId: "user-1" });
    await flushPromises();

    const revokeButton = Array.from(
      document.body.querySelectorAll("button"),
    ).find((b) => b.textContent === i18n.global.t("browse.share.revoke"));
    expect(revokeButton).toBeDefined();
    await revokeButton?.click();
    await flushPromises();

    expect(document.body.textContent).toContain("revoke failed");
  });

  it("surfaces a public URL revoke error inline", async () => {
    const confirm = useConfirmStore();
    vi.spyOn(confirm, "open").mockResolvedValue(true);
    vi.mocked(sharesApi.listShareUrls).mockResolvedValue([createToken("st1")]);
    vi.mocked(sharesApi.deleteShareUrl).mockRejectedValue(
      new Error("revoke failed"),
    );

    setAuthenticated("user-1");
    wrapper = mountOpen({ ownerId: "user-1" });
    await flushPromises();

    const urlsTab = Array.from(document.body.querySelectorAll("button")).find(
      (b) => b.textContent === i18n.global.t("browse.share.shareUrls"),
    );
    await urlsTab?.click();
    await flushPromises();

    const revokeButton = Array.from(
      document.body.querySelectorAll("button"),
    ).find((b) => b.textContent === i18n.global.t("browse.share.revoke"));
    expect(revokeButton).toBeDefined();
    await revokeButton?.click();
    await flushPromises();

    expect(document.body.textContent).toContain("revoke failed");
  });

  it("hides creation controls from non-owners", async () => {
    setAuthenticated("user-1");
    wrapper = mountOpen({ ownerId: "user-2" });
    await flushPromises();

    const createGrant = Array.from(
      document.body.querySelectorAll("button"),
    ).find((b) => b.textContent === i18n.global.t("browse.share.createGrant"));
    expect(createGrant).toBeUndefined();
  });

  it("surfaces a grant load error", async () => {
    vi.mocked(sharesApi.listShareGrants).mockRejectedValue(
      new Error("forbidden"),
    );

    setAuthenticated("user-1");
    wrapper = mountOpen({ ownerId: "user-1" });
    await flushPromises();

    expect(document.body.textContent).toContain("forbidden");
  });

  it("surfaces a URL creation error", async () => {
    vi.mocked(sharesApi.createShareUrl).mockRejectedValue(
      new Error("create failed"),
    );

    setAuthenticated("user-1");
    wrapper = mountOpen({ ownerId: "user-1" });
    await flushPromises();

    const urlsTab = Array.from(document.body.querySelectorAll("button")).find(
      (b) => b.textContent === i18n.global.t("browse.share.shareUrls"),
    );
    await urlsTab?.click();
    await flushPromises();

    const createButton = Array.from(
      document.body.querySelectorAll("button"),
    ).find(
      (b) => b.textContent === i18n.global.t("browse.share.createShareUrl"),
    );
    await createButton?.click();
    await flushPromises();

    expect(document.body.textContent).toContain("create failed");
  });

  it("shows a public URL tab for public items viewed by non-owners", async () => {
    const writeText = vi.fn();
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText },
      configurable: true,
    });

    setAuthenticated("user-1");
    wrapper = mountOpen({
      ownerId: "user-2",
      visibility: "public",
    });
    await flushPromises();

    const publicTab = Array.from(document.body.querySelectorAll("button")).find(
      (b) => b.textContent === i18n.global.t("browse.share.publicUrl"),
    );
    expect(publicTab).toBeDefined();
    await publicTab?.click();
    await flushPromises();

    const urlInput = document.body.querySelector(
      'input[type="text"]',
    ) as HTMLInputElement;
    expect(urlInput?.value).toBe("http://localhost:3000/albums/album-1");

    const copyButton = Array.from(
      document.body.querySelectorAll("button"),
    ).find((b) => b.textContent === i18n.global.t("common.copy"));
    await copyButton?.click();
    await flushPromises();

    expect(writeText).toHaveBeenCalledWith(
      "http://localhost:3000/albums/album-1",
    );
  });

  it("shows all three tabs for the owner of a public item", async () => {
    setAuthenticated("user-1");
    wrapper = mountOpen({
      ownerId: "user-1",
      visibility: "public",
    });
    await flushPromises();

    const tabLabels = ["shareGrants", "shareUrls", "publicUrl"].map((key) =>
      i18n.global.t(`browse.share.${key}`),
    );
    for (const label of tabLabels) {
      expect(
        Array.from(document.body.querySelectorAll("button")).some(
          (b) => b.textContent === label,
        ),
      ).toBe(true);
    }
  });

  it("renders the confirm dialog above the share modal when revoking", async () => {
    vi.mocked(sharesApi.listShareGrants).mockResolvedValue([
      createGrant("sg1", "user-2"),
    ]);
    setAuthenticated("user-1");

    // ConfirmDialog is mounted in App.vue and exists before any modal.
    const confirmWrapper = mount(ConfirmDialog, { attachTo: document.body });

    wrapper = mountOpen({ ownerId: "user-1" });
    await flushPromises();

    const revokeButton = Array.from(
      document.body.querySelectorAll("button"),
    ).find((b) => b.textContent === i18n.global.t("browse.share.revoke"));
    expect(revokeButton).toBeDefined();
    revokeButton?.click();
    await flushPromises();
    await nextTick();

    const overlays = document.body.querySelectorAll(".app-modal__overlay");
    expect(overlays.length).toBe(2);

    const shareOverlay = Array.from(overlays).find((o) =>
      (o as HTMLElement)
        .getAttribute("style")
        ?.includes("--app-modal-depth: 0"),
    );
    const confirmOverlay = Array.from(overlays).find((o) =>
      (o as HTMLElement)
        .getAttribute("style")
        ?.includes("--app-modal-depth: 1"),
    );

    expect(shareOverlay).toBeDefined();
    expect(confirmOverlay).toBeDefined();

    expect((shareOverlay as HTMLElement).textContent).toContain("Meadowland");
    expect((confirmOverlay as HTMLElement).textContent).toContain(
      i18n.global.t("common.cancel"),
    );

    confirmWrapper.unmount();
  });
});
