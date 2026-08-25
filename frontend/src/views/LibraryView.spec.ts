import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { createRouter, createMemoryHistory } from "vue-router";
import { setActivePinia, createPinia } from "pinia";
import { i18n } from "@/i18n";
import { useAuthStore } from "@/stores/auth";
import { useToastStore } from "@/stores/toast";
import * as librariesApi from "@/api/libraries";
import type {
  LibraryResponse,
  LibraryCreate,
  Visibility,
} from "@/api/libraries";
import LibraryView from "./LibraryView.vue";

vi.mock("@/api/libraries", () => ({
  listLibraries: vi.fn(),
  createLibrary: vi.fn(),
  deleteLibrary: vi.fn(),
}));

function createTestRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: "/", component: { template: "<div/>" } },
      { path: "/libraries/:id", component: { template: "<div/>" } },
    ],
  });
}

function createLibrary(id: string, name: string): LibraryResponse {
  return {
    id,
    name,
    owner_id: "user-1",
    description: null,
    visibility: "public",
    can_write: true,
  };
}

function setAuthenticated() {
  const authStore = useAuthStore();
  authStore.accessToken = "token";
  authStore.refreshToken = "refresh";
  authStore.expiresAt = Date.now() + 10000;
  authStore.status = "authenticated";
}

describe("LibraryView", () => {
  let wrapper: ReturnType<typeof mount>;

  beforeEach(() => {
    setActivePinia(createPinia());
    vi.useFakeTimers();
    vi.clearAllMocks();
    vi.mocked(librariesApi.listLibraries).mockResolvedValue([]);
    vi.mocked(librariesApi.createLibrary).mockResolvedValue(
      createLibrary("library-1", "Main Library"),
    );
  });

  afterEach(() => {
    vi.useRealTimers();
    wrapper?.unmount();
    document.body.innerHTML = "";
  });

  it("fetches libraries on mount", async () => {
    vi.mocked(librariesApi.listLibraries).mockResolvedValue([
      createLibrary("library-1", "Main Library"),
    ]);

    wrapper = mount(LibraryView, {
      attachTo: document.body,
      global: { plugins: [createTestRouter()] },
    });
    await flushPromises();

    expect(librariesApi.listLibraries).toHaveBeenCalledWith({
      q: "",
      limit: 20,
      offset: 0,
    });
    expect(wrapper.text()).toContain("Main Library");
  });

  it("shows the empty state", async () => {
    wrapper = mount(LibraryView, {
      attachTo: document.body,
      global: { plugins: [createTestRouter()] },
    });
    await flushPromises();

    expect(wrapper.text()).toContain(
      i18n.global.t("browse.list.empty", {
        entity: i18n.global.t("browse.entities.libraries"),
      }),
    );
  });

  it("debounces search and resets the list", async () => {
    const fetcher = vi.mocked(librariesApi.listLibraries);
    fetcher
      .mockResolvedValueOnce([createLibrary("library-1", "First Library")])
      .mockResolvedValueOnce([createLibrary("library-2", "Searched Library")]);

    wrapper = mount(LibraryView, {
      attachTo: document.body,
      global: { plugins: [createTestRouter()] },
    });
    await flushPromises();

    const input = wrapper.find('input[type="search"]');
    await input.setValue("query");

    vi.advanceTimersByTime(0);
    vi.advanceTimersByTime(300);
    await flushPromises();

    expect(fetcher).toHaveBeenLastCalledWith({
      q: "query",
      limit: 20,
      offset: 0,
    });
    expect(wrapper.text()).toContain("Searched Library");
    expect(wrapper.text()).not.toContain("First Library");
  });

  it("loads the next page", async () => {
    const fetcher = vi.mocked(librariesApi.listLibraries);
    fetcher
      .mockResolvedValueOnce(
        Array.from({ length: 20 }, (_, i) =>
          createLibrary(`library-${i}`, `Library ${i}`),
        ),
      )
      .mockResolvedValueOnce([createLibrary("library-20", "Library 20")]);

    wrapper = mount(LibraryView, {
      attachTo: document.body,
      global: { plugins: [createTestRouter()] },
    });
    await flushPromises();

    const loadMore = wrapper
      .findAll("button")
      .find((b) => b.text() === i18n.global.t("browse.list.loadMore"));
    expect(loadMore).toBeDefined();

    await loadMore?.trigger("click");
    await flushPromises();

    expect(fetcher).toHaveBeenLastCalledWith({
      q: "",
      limit: 20,
      offset: 20,
    });
    expect(wrapper.text()).toContain("Library 19");
    expect(wrapper.text()).toContain("Library 20");
  });

  it("creates a library and refreshes the list", async () => {
    setAuthenticated();
    const fetcher = vi.mocked(librariesApi.listLibraries);
    fetcher
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce([createLibrary("library-1", "Main Library")]);

    wrapper = mount(LibraryView, {
      attachTo: document.body,
      global: { plugins: [createTestRouter()] },
    });
    await flushPromises();

    const createButton = wrapper
      .findAll("button")
      .find((b) => b.text() === i18n.global.t("browse.list.createLibrary"));
    expect(createButton).toBeDefined();
    await createButton?.trigger("click");
    await flushPromises();

    const nameInput = document.body.querySelector(
      '#create-library-form input[type="text"]',
    ) as HTMLInputElement;
    const descriptionInput = document.body.querySelector(
      "#create-library-form textarea",
    ) as HTMLTextAreaElement;
    const visibilityInput = document.body.querySelector(
      "#create-library-form select",
    ) as HTMLSelectElement;

    nameInput.value = "Main Library";
    nameInput.dispatchEvent(new Event("input"));
    descriptionInput.value = "My personal collection.";
    descriptionInput.dispatchEvent(new Event("input"));
    visibilityInput.value = "private";
    visibilityInput.dispatchEvent(new Event("change"));
    await flushPromises();

    const saveButton = Array.from(
      document.body.querySelectorAll("button"),
    ).find((b) => b.textContent === i18n.global.t("common.save"));
    expect(saveButton).toBeDefined();
    await saveButton?.click();
    await flushPromises();

    const expectedBody: LibraryCreate = {
      name: "Main Library",
      description: "My personal collection.",
    };
    expect(librariesApi.createLibrary).toHaveBeenCalledWith(expectedBody, {
      visibility: "private" as Visibility,
    });
    expect(fetcher).toHaveBeenCalledTimes(2);

    const toastStore = useToastStore();
    expect(toastStore.toasts[0].message).toBe(
      i18n.global.t("browse.createLibrary"),
    );

    expect(document.body.querySelector("#create-library-form")).toBeNull();
  });

  it("surfaces creation errors in the modal", async () => {
    setAuthenticated();
    vi.mocked(librariesApi.createLibrary).mockRejectedValue(
      new Error("create failed"),
    );

    wrapper = mount(LibraryView, {
      attachTo: document.body,
      global: { plugins: [createTestRouter()] },
    });
    await flushPromises();

    const createButton = wrapper
      .findAll("button")
      .find((b) => b.text() === i18n.global.t("browse.list.createLibrary"));
    await createButton?.trigger("click");
    await flushPromises();

    const nameInput = document.body.querySelector(
      '#create-library-form input[type="text"]',
    ) as HTMLInputElement;
    nameInput.value = "Main Library";
    nameInput.dispatchEvent(new Event("input"));
    await flushPromises();

    const saveButton = Array.from(
      document.body.querySelectorAll("button"),
    ).find((b) => b.textContent === i18n.global.t("common.save"));
    await saveButton?.click();
    await flushPromises();

    expect(document.body.textContent).toContain("create failed");
  });
});
