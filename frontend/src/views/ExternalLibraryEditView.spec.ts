import {
  describe,
  it,
  expect,
  beforeEach,
  afterEach,
  vi,
  type Mock,
} from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { createRouter, createMemoryHistory } from "vue-router";
import { setActivePinia, createPinia } from "pinia";
import { i18n } from "@/i18n";
import * as externalLibrariesApi from "@/api/externalLibraries";
import { redirectToOAuthProvider } from "@/utils/externalOAuth";
import ExternalLibraryEditView from "./ExternalLibraryEditView.vue";

vi.mock("@/api/externalLibraries", () => ({
  listUserProviders: vi.fn(),
  listAdminProviders: vi.fn(),
  getUserExternalLibrary: vi.fn(),
  adminGetExternalLibrary: vi.fn(),
  createUserExternalLibrary: vi.fn(),
  adminCreateExternalLibrary: vi.fn(),
  updateUserExternalLibrary: vi.fn(),
  adminUpdateExternalLibrary: vi.fn(),
  deleteUserExternalLibrary: vi.fn(),
  adminDeleteExternalLibrary: vi.fn(),
  syncUserExternalLibrary: vi.fn(),
  adminSyncExternalLibrary: vi.fn(),
  listUserExternalTracks: vi.fn(),
  adminListExternalTracks: vi.fn(),
  restoreUserExternalTrack: vi.fn(),
  adminRestoreExternalTrack: vi.fn(),
  deleteUserExternalTrack: vi.fn(),
  adminDeleteExternalTrack: vi.fn(),
  listUserSyncRuns: vi.fn(),
  adminListExternalSyncRuns: vi.fn(),
  beginExternalOAuth: vi.fn(),
  claimExternalOAuth: vi.fn(),
}));

vi.mock("@/utils/externalOAuth", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/utils/externalOAuth")>();
  return {
    ...actual,
    redirectToOAuthProvider: vi.fn(),
  };
});

function getInputByLabel(label: string): HTMLInputElement | null {
  const labels = Array.from(document.body.querySelectorAll("label"));
  const found = labels.find((l) => (l.textContent ?? "").trim() === label);
  if (!found) return null;
  const forId = found.getAttribute("for");
  if (!forId) return null;
  return document.body.querySelector(`#${forId}`) as HTMLInputElement | null;
}

function createTestRouter(path = "/settings/external-libraries/new") {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: "/", component: { template: "<div/>" } },
      {
        path: "/settings/external-libraries/new",
        component: { template: "<div/>" },
      },
      {
        path: "/settings/external-libraries/:id",
        component: { template: "<div/>" },
      },
      {
        path: "/admin/external-libraries/new",
        component: { template: "<div/>" },
      },
      {
        path: "/admin/external-libraries/:id",
        component: { template: "<div/>" },
      },
    ],
  });
  void router.push(path);
  return router;
}

const sampleProvider = {
  provider_type: "s3",
  user_configurable: true,
  capabilities_summary: {},
};

describe("ExternalLibraryEditView", () => {
  let wrapper: ReturnType<typeof mount>;

  beforeEach(() => {
    setActivePinia(createPinia());
    vi.clearAllMocks();
    vi.mocked(externalLibrariesApi.listUserProviders).mockResolvedValue([
      sampleProvider,
    ]);
    vi.mocked(externalLibrariesApi.createUserExternalLibrary).mockResolvedValue(
      {
        id: "el1",
        library_id: "lib1",
        provider_type: "s3",
        scope: "user",
        name: "New Library",
        config: { bucket: "music" },
        enabled: true,
        include_in_library_index: false,
        sync_enabled: true,
        sync_interval_seconds: null,
        can_manage: true,
        can_sync: true,
        created_at: "2024-01-01T00:00:00Z",
        updated_at: "2024-01-01T00:00:00Z",
      },
    );
  });

  afterEach(() => {
    wrapper?.unmount();
    document.body.innerHTML = "";
  });

  it("loads providers and renders the create form", async () => {
    const router = createTestRouter();
    wrapper = mount(ExternalLibraryEditView, {
      attachTo: document.body,
      global: { plugins: [router] },
    });
    await flushPromises();

    expect(externalLibrariesApi.listUserProviders).toHaveBeenCalled();
    expect(wrapper.text()).toContain(
      i18n.global.t("pages.externalLibraries.newTitle"),
    );
  });

  it("creates an external library on submit", async () => {
    const router = createTestRouter();
    wrapper = mount(ExternalLibraryEditView, {
      attachTo: document.body,
      global: { plugins: [router] },
    });
    await flushPromises();

    const nameInput = document.body.querySelector(
      "input[type=text]",
    ) as HTMLInputElement;
    nameInput.value = "New Library";
    nameInput.dispatchEvent(new Event("input"));

    const bucketInput = getInputByLabel(
      i18n.global.t("pages.externalLibraries.providers.s3.fields.bucket.label"),
    );
    expect(bucketInput).not.toBeNull();
    bucketInput!.value = "music";
    bucketInput!.dispatchEvent(new Event("input"));
    await flushPromises();

    const saveButton = Array.from(
      document.body.querySelectorAll("button"),
    ).find(
      (b) => b.textContent === i18n.global.t("pages.externalLibraries.create"),
    );
    expect(saveButton).toBeDefined();
    await saveButton?.click();
    await flushPromises();

    expect(externalLibrariesApi.createUserExternalLibrary).toHaveBeenCalledWith(
      expect.objectContaining({
        provider_type: "s3",
        name: "New Library",
        include_in_library_index: false,
        config: expect.objectContaining({ bucket: "music" }),
      }),
    );
  });

  it("submits user updates without include_in_library_index", async () => {
    vi.mocked(externalLibrariesApi.getUserExternalLibrary).mockResolvedValue({
      id: "el1",
      library_id: "lib1",
      provider_type: "s3",
      scope: "user",
      name: "Existing Library",
      config: { bucket: "music" },
      enabled: true,
      include_in_library_index: false,
      sync_enabled: true,
      sync_interval_seconds: 3600,
      can_manage: true,
      can_sync: true,
      created_at: "2024-01-01T00:00:00Z",
      updated_at: "2024-01-01T00:00:00Z",
    });
    vi.mocked(externalLibrariesApi.updateUserExternalLibrary).mockResolvedValue(
      {
        id: "el1",
        library_id: "lib1",
        provider_type: "s3",
        scope: "user",
        name: "Updated Library",
        config: { bucket: "music" },
        enabled: true,
        include_in_library_index: false,
        sync_enabled: true,
        sync_interval_seconds: null,
        can_manage: true,
        can_sync: true,
        created_at: "2024-01-01T00:00:00Z",
        updated_at: "2024-01-01T00:00:00Z",
      },
    );

    const router = createTestRouter("/settings/external-libraries/el1");
    await router.isReady();
    wrapper = mount(ExternalLibraryEditView, {
      attachTo: document.body,
      global: { plugins: [router] },
    });
    await flushPromises();
    await flushPromises();

    const nameInput = document.body.querySelector(
      "input[type=text]",
    ) as HTMLInputElement;
    nameInput.value = "Updated Library";
    nameInput.dispatchEvent(new Event("input"));
    await flushPromises();

    const saveButton = Array.from(
      document.body.querySelectorAll("button"),
    ).find((b) =>
      (b.textContent ?? "").includes(
        i18n.global.t("pages.externalLibraries.save"),
      ),
    );
    expect(saveButton).toBeDefined();
    await saveButton?.click();
    await flushPromises();

    expect(externalLibrariesApi.updateUserExternalLibrary).toHaveBeenCalledWith(
      "el1",
      expect.objectContaining({
        name: "Updated Library",
      }),
    );
    const body = (externalLibrariesApi.updateUserExternalLibrary as Mock).mock
      .calls[0][1];
    expect(body).not.toHaveProperty("include_in_library_index");
  });

  it("renders the local provider form and submits a structured config", async () => {
    vi.mocked(externalLibrariesApi.listAdminProviders).mockResolvedValue([
      {
        provider_type: "local",
        user_configurable: false,
        capabilities_summary: {},
      },
    ]);
    vi.mocked(
      externalLibrariesApi.adminCreateExternalLibrary,
    ).mockResolvedValue({
      id: "el1",
      library_id: "lib1",
      provider_type: "local",
      scope: "admin",
      name: "Local Library",
      config: { root: "/music" },
      enabled: true,
      include_in_library_index: false,
      sync_enabled: true,
      sync_interval_seconds: null,
      can_manage: true,
      can_sync: true,
      created_at: "2024-01-01T00:00:00Z",
      updated_at: "2024-01-01T00:00:00Z",
    });

    const router = createTestRouter("/admin/external-libraries/new");
    await router.isReady();
    wrapper = mount(ExternalLibraryEditView, {
      attachTo: document.body,
      global: { plugins: [router] },
    });
    await flushPromises();

    expect(wrapper.text()).toContain(
      i18n.global.t(
        "pages.externalLibraries.providers.local.fields.root.label",
      ),
    );

    const nameInput = document.body.querySelector(
      "input[type=text]",
    ) as HTMLInputElement;
    nameInput.value = "Local Library";
    nameInput.dispatchEvent(new Event("input"));
    await flushPromises();

    const rootInput = getInputByLabel(
      i18n.global.t(
        "pages.externalLibraries.providers.local.fields.root.label",
      ),
    );
    expect(rootInput).not.toBeNull();
    rootInput!.value = "/music";
    rootInput!.dispatchEvent(new Event("input"));
    await flushPromises();

    const followCheckbox = getInputByLabel(
      i18n.global.t(
        "pages.externalLibraries.providers.local.fields.follow_symlinks.label",
      ),
    );
    expect(followCheckbox).not.toBeNull();
    followCheckbox!.checked = true;
    followCheckbox!.dispatchEvent(new Event("change"));
    await flushPromises();

    const saveButton = Array.from(
      document.body.querySelectorAll("button"),
    ).find(
      (b) => b.textContent === i18n.global.t("pages.externalLibraries.create"),
    );
    expect(saveButton).toBeDefined();
    await saveButton?.click();
    await flushPromises();

    expect(
      externalLibrariesApi.adminCreateExternalLibrary,
    ).toHaveBeenCalledWith(
      expect.objectContaining({
        provider_type: "local",
        name: "Local Library",
        config: expect.objectContaining({
          root: "/music",
          follow_symlinks: true,
        }),
      }),
    );
  });

  it("renders the webdav provider form and submits a structured config", async () => {
    vi.mocked(externalLibrariesApi.listUserProviders).mockResolvedValue([
      {
        provider_type: "webdav",
        user_configurable: true,
        capabilities_summary: {},
      },
    ]);
    vi.mocked(externalLibrariesApi.createUserExternalLibrary).mockResolvedValue(
      {
        id: "el1",
        library_id: "lib1",
        provider_type: "webdav",
        scope: "user",
        name: "DAV Library",
        config: { url: "https://dav.example.com/files/alice" },
        enabled: true,
        include_in_library_index: false,
        sync_enabled: true,
        sync_interval_seconds: null,
        can_manage: true,
        can_sync: true,
        created_at: "2024-01-01T00:00:00Z",
        updated_at: "2024-01-01T00:00:00Z",
      },
    );

    const router = createTestRouter("/settings/external-libraries/new");
    await router.isReady();
    wrapper = mount(ExternalLibraryEditView, {
      attachTo: document.body,
      global: { plugins: [router] },
    });
    await flushPromises();

    expect(wrapper.text()).toContain(
      i18n.global.t(
        "pages.externalLibraries.providers.webdav.fields.url.label",
      ),
    );
    expect(wrapper.text()).not.toContain(
      i18n.global.t("pages.externalLibraries.configHint"),
    );

    const urlInput = getInputByLabel(
      i18n.global.t(
        "pages.externalLibraries.providers.webdav.fields.url.label",
      ),
    );
    expect(urlInput).not.toBeNull();
    urlInput!.value = "https://dav.example.com/files/alice";
    urlInput!.dispatchEvent(new Event("input"));

    const saveButton = Array.from(
      document.body.querySelectorAll("button"),
    ).find(
      (b) => b.textContent === i18n.global.t("pages.externalLibraries.create"),
    );
    expect(saveButton).toBeDefined();
    await saveButton?.click();
    await flushPromises();

    expect(externalLibrariesApi.createUserExternalLibrary).toHaveBeenCalledWith(
      expect.objectContaining({
        provider_type: "webdav",
        config: expect.objectContaining({
          url: "https://dav.example.com/files/alice",
          verify_ssl: true,
          timeout: 30,
        }),
      }),
    );
    const body = (externalLibrariesApi.createUserExternalLibrary as Mock).mock
      .calls[0][0];
    expect(body.config).not.toHaveProperty("ca_bundle");
  });

  it("renders the dropbox provider form and submits a structured config", async () => {
    vi.mocked(externalLibrariesApi.listUserProviders).mockResolvedValue([
      {
        provider_type: "dropbox",
        user_configurable: true,
        capabilities_summary: {},
      },
    ]);
    vi.mocked(externalLibrariesApi.createUserExternalLibrary).mockResolvedValue(
      {
        id: "el1",
        library_id: "lib1",
        provider_type: "dropbox",
        scope: "user",
        name: "Dropbox Library",
        config: { access_token: "token" },
        enabled: true,
        include_in_library_index: false,
        sync_enabled: true,
        sync_interval_seconds: null,
        can_manage: true,
        can_sync: true,
        created_at: "2024-01-01T00:00:00Z",
        updated_at: "2024-01-01T00:00:00Z",
      },
    );

    const router = createTestRouter("/settings/external-libraries/new");
    await router.isReady();
    wrapper = mount(ExternalLibraryEditView, {
      attachTo: document.body,
      global: { plugins: [router] },
    });
    await flushPromises();

    expect(wrapper.text()).toContain(
      i18n.global.t(
        "pages.externalLibraries.providers.dropbox.fields.access_token.label",
      ),
    );
    expect(wrapper.text()).not.toContain(
      i18n.global.t("pages.externalLibraries.configHint"),
    );

    const tokenInput = getInputByLabel(
      i18n.global.t(
        "pages.externalLibraries.providers.dropbox.fields.access_token.label",
      ),
    );
    expect(tokenInput).not.toBeNull();
    tokenInput!.value = "test-token";
    tokenInput!.dispatchEvent(new Event("input"));

    const appKeyInput = getInputByLabel(
      i18n.global.t(
        "pages.externalLibraries.providers.dropbox.fields.app_key.label",
      ),
    );
    expect(appKeyInput).not.toBeNull();
    appKeyInput!.value = "test-app-key";
    appKeyInput!.dispatchEvent(new Event("input"));

    const rootInput = getInputByLabel(
      i18n.global.t(
        "pages.externalLibraries.providers.dropbox.fields.root.label",
      ),
    );
    expect(rootInput).not.toBeNull();
    rootInput!.value = "/Music";
    rootInput!.dispatchEvent(new Event("input"));

    const saveButton = Array.from(
      document.body.querySelectorAll("button"),
    ).find(
      (b) => b.textContent === i18n.global.t("pages.externalLibraries.create"),
    );
    expect(saveButton).toBeDefined();
    await saveButton?.click();
    await flushPromises();

    expect(externalLibrariesApi.createUserExternalLibrary).toHaveBeenCalledWith(
      expect.objectContaining({
        provider_type: "dropbox",
        config: expect.objectContaining({
          access_token: "test-token",
          app_key: "test-app-key",
          root: "/Music",
          timeout: 30,
          temporary_links: true,
        }),
      }),
    );
    const body = (externalLibrariesApi.createUserExternalLibrary as Mock).mock
      .calls[0][0];
    expect(body.config).not.toHaveProperty("refresh_token");
    expect(body.config).not.toHaveProperty("app_secret");
  });

  it("falls back to raw JSON config when the provider has no template", async () => {
    vi.mocked(externalLibrariesApi.listAdminProviders).mockResolvedValue([
      {
        provider_type: "local",
        user_configurable: false,
        capabilities_summary: {},
      },
      {
        provider_type: "custom",
        user_configurable: true,
        capabilities_summary: {},
      },
    ]);

    const router = createTestRouter("/admin/external-libraries/new");
    await router.isReady();
    wrapper = mount(ExternalLibraryEditView, {
      attachTo: document.body,
      global: { plugins: [router] },
    });
    await flushPromises();

    expect(wrapper.text()).toContain(
      i18n.global.t(
        "pages.externalLibraries.providers.local.fields.root.label",
      ),
    );

    const providerSelect = document.body.querySelector(
      "select",
    ) as HTMLSelectElement;
    providerSelect.value = "custom";
    providerSelect.dispatchEvent(new Event("change"));
    await flushPromises();

    expect(wrapper.text()).toContain(
      i18n.global.t("pages.externalLibraries.config"),
    );
  });

  it("renders sync runs as cards on narrow viewports", async () => {
    const run: externalLibrariesApi.ExternalSyncRunResponse = {
      id: "sr1",
      external_library_id: "el1",
      triggered_by: "manual",
      status: "success",
      started_at: "2024-01-01T00:00:00Z",
      completed_at: "2024-01-01T00:00:05Z",
      items_seen: 10,
      tracks_created: 5,
      tracks_updated: 2,
      tracks_tombstoned: 0,
      tracks_shadowed: 0,
    };

    vi.mocked(externalLibrariesApi.adminGetExternalLibrary).mockResolvedValue({
      id: "el1",
      library_id: "lib1",
      provider_type: "s3",
      scope: "admin",
      name: "Existing Library",
      config: { bucket: "music" },
      enabled: true,
      include_in_library_index: false,
      sync_enabled: true,
      sync_interval_seconds: null,
      can_manage: true,
      can_sync: true,
      created_at: "2024-01-01T00:00:00Z",
      updated_at: "2024-01-01T00:00:00Z",
    });
    vi.mocked(externalLibrariesApi.adminListExternalSyncRuns).mockResolvedValue(
      {
        syncRuns: [run],
        total: 1,
      },
    );

    const router = createTestRouter("/admin/external-libraries/el1");
    await router.isReady();
    wrapper = mount(ExternalLibraryEditView, {
      attachTo: document.body,
      global: { plugins: [router] },
    });
    await flushPromises();

    const syncRunsTab = Array.from(
      document.body.querySelectorAll("button"),
    ).find(
      (b) =>
        b.textContent === i18n.global.t("pages.externalLibraries.syncRuns"),
    );
    expect(syncRunsTab).toBeDefined();
    await syncRunsTab?.click();
    await flushPromises();

    const card = document.body.querySelector(
      ".external-library-edit-view__sync-run-card",
    );
    expect(card).not.toBeNull();
    expect(card?.textContent).toContain("success");
    expect(card?.textContent).toContain("10");
  });

  it("polls sync runs silently without re-enabling the loading state", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });

    const queuedRun: externalLibrariesApi.ExternalSyncRunResponse = {
      id: "sr1",
      external_library_id: "el1",
      triggered_by: "manual",
      status: "queued",
      started_at: "2024-01-01T00:00:00Z",
      completed_at: null,
      items_seen: 0,
      tracks_created: 0,
      tracks_updated: 0,
      tracks_tombstoned: 0,
      tracks_shadowed: 0,
    };

    vi.mocked(externalLibrariesApi.adminGetExternalLibrary).mockResolvedValue({
      id: "el1",
      library_id: "lib1",
      provider_type: "s3",
      scope: "admin",
      name: "Existing Library",
      config: { bucket: "music" },
      enabled: true,
      include_in_library_index: false,
      sync_enabled: true,
      sync_interval_seconds: null,
      can_manage: true,
      can_sync: true,
      created_at: "2024-01-01T00:00:00Z",
      updated_at: "2024-01-01T00:00:00Z",
    });
    vi.mocked(externalLibrariesApi.adminSyncExternalLibrary).mockResolvedValue({
      sync_run_id: "sr1",
    });
    vi.mocked(externalLibrariesApi.adminListExternalSyncRuns).mockResolvedValue(
      {
        syncRuns: [queuedRun],
        total: 1,
      },
    );

    const router = createTestRouter("/admin/external-libraries/el1");
    await router.isReady();
    wrapper = mount(ExternalLibraryEditView, {
      attachTo: document.body,
      global: { plugins: [router] },
    });
    await flushPromises();

    const syncRunsTab = Array.from(
      document.body.querySelectorAll("button"),
    ).find(
      (b) =>
        b.textContent === i18n.global.t("pages.externalLibraries.syncRuns"),
    );
    expect(syncRunsTab).toBeDefined();
    await syncRunsTab?.click();
    await flushPromises();

    const syncButton = Array.from(
      document.body.querySelectorAll("button"),
    ).find(
      (b) => b.textContent === i18n.global.t("pages.externalLibraries.sync"),
    );
    expect(syncButton).toBeDefined();
    await syncButton?.click();
    await flushPromises();

    expect(wrapper.vm.syncRunsLoading).toBe(false);
    const callsBefore = vi.mocked(
      externalLibrariesApi.adminListExternalSyncRuns,
    ).mock.calls.length;

    await vi.advanceTimersByTimeAsync(2000);
    await flushPromises();

    expect(
      externalLibrariesApi.adminListExternalSyncRuns,
    ).toHaveBeenCalledTimes(callsBefore + 1);
    expect(wrapper.vm.syncRunsLoading).toBe(false);

    vi.useRealTimers();
  });

  it("renders tracks as cards", async () => {
    const track: externalLibrariesApi.ExternalTrackResponse = {
      id: "et1",
      external_library_id: "el1",
      track_id: null,
      provider_key: "song.mp3",
      state: "active",
      sha256: null,
      last_seen_at: "2024-01-01T00:00:00Z",
      last_synced_at: null,
      write_back_pending: false,
      write_back_error: null,
      sync_error: null,
      display_path: "music/song.mp3",
    };

    vi.mocked(externalLibrariesApi.listAdminProviders).mockResolvedValue([
      sampleProvider,
    ]);
    vi.mocked(externalLibrariesApi.adminGetExternalLibrary).mockResolvedValue({
      id: "el1",
      library_id: "lib1",
      provider_type: "s3",
      scope: "admin",
      name: "Existing Library",
      config: { bucket: "music" },
      enabled: true,
      include_in_library_index: false,
      sync_enabled: true,
      sync_interval_seconds: null,
      can_manage: true,
      can_sync: true,
      created_at: "2024-01-01T00:00:00Z",
      updated_at: "2024-01-01T00:00:00Z",
    });
    vi.mocked(externalLibrariesApi.adminListExternalTracks).mockResolvedValue({
      tracks: [track],
      total: 1,
    });

    const router = createTestRouter("/admin/external-libraries/el1");
    await router.isReady();
    wrapper = mount(ExternalLibraryEditView, {
      attachTo: document.body,
      global: { plugins: [router] },
    });
    await flushPromises();

    const tracksTab = Array.from(document.body.querySelectorAll("button")).find(
      (b) => b.textContent === i18n.global.t("pages.externalLibraries.tracks"),
    );
    expect(tracksTab).toBeDefined();
    await tracksTab?.click();
    await flushPromises();

    const cards = document.body.querySelectorAll(
      ".external-library-edit-view__track-card",
    );
    expect(cards.length).toBe(1);
    expect(cards[0].textContent).toContain("song.mp3");
    expect(cards[0].textContent).toContain("active");
    expect(cards[0].textContent).toContain("music/song.mp3");

    expect(document.body.querySelector("table")).toBeNull();
  });

  it("shows the connect button for OAuth providers and starts the flow", async () => {
    vi.mocked(externalLibrariesApi.listUserProviders).mockResolvedValue([
      {
        provider_type: "dropbox",
        user_configurable: true,
        capabilities_summary: {},
        oauth_supported: true,
        oauth_callback_url:
          "https://songhive.example/api/v1/external-libraries/oauth/callback",
      },
    ]);
    vi.mocked(externalLibrariesApi.beginExternalOAuth).mockResolvedValue({
      authorize_url: "https://www.dropbox.com/oauth2/authorize?state=state-1",
      state: "state-1",
    });

    const router = createTestRouter("/settings/external-libraries/new");
    await router.isReady();
    wrapper = mount(ExternalLibraryEditView, {
      attachTo: document.body,
      global: { plugins: [router] },
    });
    await flushPromises();

    const connectButton = Array.from(
      document.body.querySelectorAll("button"),
    ).find((b) =>
      (b.textContent ?? "").includes(
        i18n.global.t("pages.externalLibraries.oauthConnect", {
          provider: "dropbox",
        }),
      ),
    );
    expect(connectButton).toBeDefined();

    // The provider help explains the Dropbox app setup and shows the
    // OAuth callback URL to register in the App Console.
    const help = document.body.querySelector(
      ".external-library-edit-view__provider-help",
    );
    expect(help?.textContent).toContain("dropbox.com/developers/apps");
    expect(help?.textContent).toContain(
      "https://songhive.example/api/v1/external-libraries/oauth/callback",
    );

    const consoleLink = help?.querySelector("a");
    expect(consoleLink?.getAttribute("href")).toBe(
      "https://www.dropbox.com/developers/apps",
    );

    const codeTexts = Array.from(help?.querySelectorAll("code") ?? []).map(
      (el) => el.textContent ?? "",
    );
    expect(codeTexts).toContain(
      "account_info.read, files.metadata.read, files.content.read",
    );
    expect(codeTexts).toContain("files.metadata.write, files.content.write");
    expect(codeTexts).toContain(
      "https://songhive.example/api/v1/external-libraries/oauth/callback",
    );

    // The app key is marked as required; the token fields are not.
    const requiredBadges = Array.from(
      document.body.querySelectorAll(".external-library-edit-view__required"),
    );
    expect(requiredBadges.length).toBe(1);
    expect(requiredBadges[0].textContent).toContain(
      i18n.global.t("common.required"),
    );

    const appKeyInput = getInputByLabel(
      i18n.global.t(
        "pages.externalLibraries.providers.dropbox.fields.app_key.label",
      ),
    );
    expect(appKeyInput).not.toBeNull();
    appKeyInput!.value = "dbx-key";
    appKeyInput!.dispatchEvent(new Event("input"));
    await flushPromises();

    await connectButton?.click();
    await flushPromises();

    expect(externalLibrariesApi.beginExternalOAuth).toHaveBeenCalledWith(
      expect.objectContaining({
        provider_type: "dropbox",
        return_to: "/settings/external-libraries/new",
        config: expect.objectContaining({ app_key: "dbx-key" }),
      }),
    );
    expect(redirectToOAuthProvider).toHaveBeenCalledWith(
      "https://www.dropbox.com/oauth2/authorize?state=state-1",
    );
    const stash = sessionStorage.getItem("songhive:external-oauth:state-1");
    expect(stash).toContain('"providerType":"dropbox"');
    // Secret-bearing fields are never persisted to browser storage; the
    // backend carries them back inside the claimed config fragment.
    expect(stash).not.toContain("dbx-key");
    expect(stash).not.toContain("app_key");
  });

  it("does not show the connect button for providers without OAuth", async () => {
    const router = createTestRouter("/settings/external-libraries/new");
    await router.isReady();
    wrapper = mount(ExternalLibraryEditView, {
      attachTo: document.body,
      global: { plugins: [router] },
    });
    await flushPromises();

    const connectButton = Array.from(
      document.body.querySelectorAll("button"),
    ).find((b) =>
      (b.textContent ?? "").includes(
        i18n.global.t("pages.externalLibraries.oauthConnect", {
          provider: "s3",
        }),
      ),
    );
    expect(connectButton).toBeUndefined();
  });

  it("claims granted tokens on return and submits them with the config", async () => {
    vi.mocked(externalLibrariesApi.listUserProviders).mockResolvedValue([
      {
        provider_type: "dropbox",
        user_configurable: true,
        capabilities_summary: {},
        oauth_supported: true,
      },
    ]);
    vi.mocked(externalLibrariesApi.claimExternalOAuth).mockResolvedValue({
      provider_type: "dropbox",
      config: {
        access_token: "at-1",
        refresh_token: "rt-1",
        account_id: "dbid:42",
        app_key: "dbx-key",
        app_secret: "dbx-secret",
      },
    });

    const router = createTestRouter(
      "/settings/external-libraries/new?oauth_state=state-1",
    );
    await router.isReady();
    wrapper = mount(ExternalLibraryEditView, {
      attachTo: document.body,
      global: { plugins: [router] },
    });
    await flushPromises();

    expect(externalLibrariesApi.claimExternalOAuth).toHaveBeenCalledWith(
      "state-1",
    );

    const tokenInput = getInputByLabel(
      i18n.global.t(
        "pages.externalLibraries.providers.dropbox.fields.access_token.label",
      ),
    ) as HTMLInputElement;
    expect(tokenInput?.value).toBe("at-1");

    const saveButton = Array.from(
      document.body.querySelectorAll("button"),
    ).find(
      (b) => b.textContent === i18n.global.t("pages.externalLibraries.create"),
    );
    await saveButton?.click();
    await flushPromises();

    expect(externalLibrariesApi.createUserExternalLibrary).toHaveBeenCalledWith(
      expect.objectContaining({
        provider_type: "dropbox",
        config: expect.objectContaining({
          access_token: "at-1",
          refresh_token: "rt-1",
          account_id: "dbid:42",
        }),
      }),
    );
  });

  it("shows an error when the provider returns an OAuth error", async () => {
    const router = createTestRouter(
      "/settings/external-libraries/new?oauth_error=access_denied",
    );
    await router.isReady();
    wrapper = mount(ExternalLibraryEditView, {
      attachTo: document.body,
      global: { plugins: [router] },
    });
    await flushPromises();

    expect(externalLibrariesApi.claimExternalOAuth).not.toHaveBeenCalled();
    expect(wrapper.text()).toContain("access_denied");
  });
});
