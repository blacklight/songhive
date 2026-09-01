import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { createRouter, createMemoryHistory } from "vue-router";
import { setActivePinia, createPinia } from "pinia";
import { i18n } from "@/i18n";
import * as externalLibrariesApi from "@/api/externalLibraries";
import ExternalLibrariesView from "./ExternalLibrariesView.vue";

vi.mock("@/api/externalLibraries", () => ({
  listUserProviders: vi.fn(),
  listUserExternalLibraries: vi.fn(),
  adminListExternalLibraries: vi.fn(),
  listAdminProviders: vi.fn(),
}));

function createTestRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: "/", component: { template: "<div/>" } },
      {
        path: "/profile/external-libraries",
        component: { template: "<div/>" },
      },
      {
        path: "/profile/external-libraries/:id",
        component: { template: "<div/>" },
      },
      { path: "/admin/external-libraries", component: { template: "<div/>" } },
    ],
  });
}

describe("ExternalLibrariesView", () => {
  let wrapper: ReturnType<typeof mount>;

  beforeEach(() => {
    setActivePinia(createPinia());
    vi.clearAllMocks();
    vi.mocked(externalLibrariesApi.listUserProviders).mockResolvedValue([
      {
        provider_type: "s3",
        user_configurable: true,
        capabilities_summary: {},
      },
    ]);
    vi.mocked(externalLibrariesApi.listUserExternalLibraries).mockResolvedValue(
      {
        libraries: [],
        total: 0,
      },
    );
  });

  afterEach(() => {
    wrapper?.unmount();
    document.body.innerHTML = "";
  });

  it("loads providers and libraries on mount", async () => {
    wrapper = mount(ExternalLibrariesView, {
      attachTo: document.body,
      global: { plugins: [createTestRouter()] },
    });
    await flushPromises();

    expect(externalLibrariesApi.listUserProviders).toHaveBeenCalled();
    expect(externalLibrariesApi.listUserExternalLibraries).toHaveBeenCalledWith(
      {
        limit: 20,
        offset: 0,
      },
    );
  });

  it("renders an external library", async () => {
    vi.mocked(externalLibrariesApi.listUserExternalLibraries).mockResolvedValue(
      {
        libraries: [
          {
            id: "el1",
            library_id: "lib1",
            provider_type: "s3",
            scope: "user",
            name: "My Bucket",
            config: {},
            enabled: true,
            include_in_library_index: false,
            sync_enabled: true,
            can_manage: true,
            can_sync: true,
            created_at: "2024-01-01T00:00:00Z",
            updated_at: "2024-01-01T00:00:00Z",
          },
        ],
        total: 1,
      },
    );

    wrapper = mount(ExternalLibrariesView, {
      attachTo: document.body,
      global: { plugins: [createTestRouter()] },
    });
    await flushPromises();

    expect(wrapper.text()).toContain("My Bucket");
    expect(wrapper.text()).toContain("s3");
  });

  it("shows the no libraries message when empty", async () => {
    wrapper = mount(ExternalLibrariesView, {
      attachTo: document.body,
      global: { plugins: [createTestRouter()] },
    });
    await flushPromises();

    expect(wrapper.text()).toContain(
      i18n.global.t("pages.externalLibraries.noLibraries"),
    );
  });

  it("switches to admin endpoints on an admin path", async () => {
    const router = createTestRouter();
    await router.push("/admin/external-libraries");

    wrapper = mount(ExternalLibrariesView, {
      attachTo: document.body,
      global: { plugins: [router] },
    });
    await flushPromises();

    expect(externalLibrariesApi.listAdminProviders).toHaveBeenCalled();
    expect(
      externalLibrariesApi.adminListExternalLibraries,
    ).toHaveBeenCalledWith({
      limit: 20,
      offset: 0,
    });
  });
});
