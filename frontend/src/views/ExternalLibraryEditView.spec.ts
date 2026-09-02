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
}));

function getInputByLabel(label: string): HTMLInputElement | null {
  const labels = Array.from(document.body.querySelectorAll("label"));
  const found = labels.find((l) => (l.textContent ?? "").trim() === label);
  if (!found) return null;
  const forId = found.getAttribute("for");
  if (!forId) return null;
  return document.body.querySelector(`#${forId}`) as HTMLInputElement | null;
}

function createTestRouter(path = "/profile/external-libraries/new") {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: "/", component: { template: "<div/>" } },
      {
        path: "/profile/external-libraries/new",
        component: { template: "<div/>" },
      },
      {
        path: "/profile/external-libraries/:id",
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

    const router = createTestRouter("/profile/external-libraries/el1");
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

  it("falls back to raw JSON config when the provider has no template", async () => {
    vi.mocked(externalLibrariesApi.listAdminProviders).mockResolvedValue([
      {
        provider_type: "local",
        user_configurable: false,
        capabilities_summary: {},
      },
      {
        provider_type: "s3",
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
    providerSelect.value = "s3";
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
});
