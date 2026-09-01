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
  listUserExternalTracks: vi.fn(),
  adminListExternalTracks: vi.fn(),
  listUserSyncRuns: vi.fn(),
  adminListExternalSyncRuns: vi.fn(),
}));

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
});
