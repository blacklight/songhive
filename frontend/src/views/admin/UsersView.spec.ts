import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { setActivePinia, createPinia } from "pinia";
import { i18n } from "@/i18n";
import { useToastStore } from "@/stores/toast";
import * as adminApi from "@/api/admin";
import type { AdminUserResponse } from "@/api/admin";
import UsersView from "./UsersView.vue";

vi.mock("@/api/admin", () => ({
  listUsers: vi.fn(),
  promoteUser: vi.fn(),
  demoteUser: vi.fn(),
  activateUser: vi.fn(),
  deactivateUser: vi.fn(),
  deleteUser: vi.fn(),
  setUserQuota: vi.fn(),
  bulkUserAction: vi.fn(),
}));

vi.mock("@/composables/useConfirm", () => ({
  useConfirm: vi.fn(),
}));

import { useConfirm } from "@/composables/useConfirm";

function createUser(
  id: string,
  username: string,
  role: AdminUserResponse["role"] = "user",
  isActive = true,
): AdminUserResponse {
  return {
    id,
    username,
    email: `${username}@example.com`,
    role,
    is_active: isActive,
  };
}

function findButtonByText(wrapper: ReturnType<typeof mount>, text: string) {
  return wrapper.findAll("button").find((b) => b.text().trim() === text);
}

describe("UsersView", () => {
  let wrapper: ReturnType<typeof mount>;
  const confirm = vi.fn();

  beforeEach(() => {
    setActivePinia(createPinia());
    vi.useFakeTimers();
    vi.clearAllMocks();
    confirm.mockResolvedValue(true);
    vi.mocked(useConfirm).mockReturnValue({ confirm, store: {} as never });
    vi.mocked(adminApi.listUsers).mockResolvedValue([]);
  });

  afterEach(() => {
    vi.useRealTimers();
    wrapper?.unmount();
    document.body.innerHTML = "";
  });

  it("lists users on mount", async () => {
    vi.mocked(adminApi.listUsers).mockResolvedValue([
      createUser("u1", "alice"),
    ]);

    wrapper = mount(UsersView, { global: { plugins: [i18n] } });
    await flushPromises();

    expect(adminApi.listUsers).toHaveBeenCalledWith({
      q: "",
      limit: 25,
      offset: 0,
    });
    expect(wrapper.text()).toContain("alice");
  });

  it("searches users with a debounced query", async () => {
    vi.mocked(adminApi.listUsers)
      .mockResolvedValueOnce([createUser("u1", "alice")])
      .mockResolvedValueOnce([createUser("u2", "bob")]);

    wrapper = mount(UsersView, { global: { plugins: [i18n] } });
    await flushPromises();

    const input = wrapper.find('input[type="search"]');
    await input.setValue("bob");
    vi.advanceTimersByTime(300);
    await flushPromises();

    expect(adminApi.listUsers).toHaveBeenLastCalledWith({
      q: "bob",
      limit: 25,
      offset: 0,
    });
    expect(wrapper.text()).toContain("bob");
  });

  it("promotes a user without confirmation", async () => {
    vi.mocked(adminApi.listUsers).mockResolvedValue([
      createUser("u1", "alice"),
    ]);
    vi.mocked(adminApi.promoteUser).mockResolvedValue(
      createUser("u1", "alice", "admin"),
    );

    wrapper = mount(UsersView, { global: { plugins: [i18n] } });
    await flushPromises();

    const promoteButton = findButtonByText(
      wrapper,
      i18n.global.t("pages.admin.users.promote"),
    );
    expect(promoteButton).toBeDefined();
    await promoteButton?.trigger("click");
    await flushPromises();

    expect(confirm).not.toHaveBeenCalled();
    expect(adminApi.promoteUser).toHaveBeenCalledWith("u1");
  });

  it("demotes a user after confirmation", async () => {
    vi.mocked(adminApi.listUsers).mockResolvedValue([
      createUser("u1", "alice", "admin"),
    ]);
    vi.mocked(adminApi.demoteUser).mockResolvedValue(
      createUser("u1", "alice", "user"),
    );

    wrapper = mount(UsersView, { global: { plugins: [i18n] } });
    await flushPromises();

    const demoteButton = findButtonByText(
      wrapper,
      i18n.global.t("pages.admin.users.demote"),
    );
    expect(demoteButton).toBeDefined();
    await demoteButton?.trigger("click");
    await flushPromises();

    expect(confirm).toHaveBeenCalled();
    expect(adminApi.demoteUser).toHaveBeenCalledWith("u1");
  });

  it("deletes a user after confirmation", async () => {
    vi.mocked(adminApi.listUsers).mockResolvedValue([
      createUser("u1", "alice"),
    ]);
    vi.mocked(adminApi.deleteUser).mockResolvedValue(undefined);

    wrapper = mount(UsersView, { global: { plugins: [i18n] } });
    await flushPromises();

    const deleteButton = wrapper
      .findAll("button")
      .find(
        (b) =>
          b.attributes("aria-label") ===
          i18n.global.t("pages.admin.users.delete"),
      );
    expect(deleteButton).toBeDefined();
    await deleteButton?.trigger("click");
    await flushPromises();

    expect(confirm).toHaveBeenCalled();
    expect(adminApi.deleteUser).toHaveBeenCalledWith("u1");
  });

  it("runs a bulk delete after confirmation with recursive true", async () => {
    vi.mocked(adminApi.listUsers).mockResolvedValue([
      createUser("u1", "alice"),
      createUser("u2", "bob"),
    ]);
    vi.mocked(adminApi.bulkUserAction).mockResolvedValue({
      action: "delete",
      processed: 2,
      failed: [],
    });

    wrapper = mount(UsersView, { global: { plugins: [i18n] } });
    await flushPromises();

    const checkboxes = wrapper.findAll('input[type="checkbox"]');
    const selectAll = checkboxes[0];
    await selectAll.setValue(true);
    await flushPromises();

    const bulkDelete = findButtonByText(
      wrapper,
      i18n.global.t("pages.admin.users.bulkDelete"),
    );
    expect(bulkDelete).toBeDefined();
    await bulkDelete?.trigger("click");
    await flushPromises();

    expect(confirm).toHaveBeenCalled();
    expect(adminApi.bulkUserAction).toHaveBeenCalledWith({
      action: "delete",
      user_ids: expect.arrayContaining(["u1", "u2"]),
      recursive: true,
    });
    const toastStore = useToastStore();
    expect(toastStore.toasts).toHaveLength(1);
    expect(toastStore.toasts[0].type).toBe("success");
  });

  async function openQuotaModal(user: AdminUserResponse) {
    wrapper?.unmount();
    document.body.innerHTML = "";
    vi.mocked(adminApi.listUsers).mockResolvedValue([user]);
    wrapper = mount(UsersView, {
      attachTo: document.body,
      global: { plugins: [i18n] },
    });
    await flushPromises();

    const quotaButton = Array.from(
      document.body.querySelectorAll("button"),
    ).find(
      (b) =>
        b.getAttribute("aria-label") ===
        i18n.global.t("pages.admin.users.quotaTitle", {
          username: user.username,
        }),
    );
    expect(quotaButton).toBeDefined();
    quotaButton?.click();
    await flushPromises();

    const modal = document.body.querySelector(".app-modal");
    expect(modal).not.toBeNull();
    return modal as HTMLElement;
  }

  function modalSelect(modal: HTMLElement): HTMLSelectElement {
    return modal.querySelector("select") as HTMLSelectElement;
  }

  function modalSaveButton(): HTMLButtonElement {
    const button = Array.from(document.body.querySelectorAll("button")).find(
      (b) => b.textContent === i18n.global.t("common.save"),
    );
    expect(button).toBeDefined();
    return button as HTMLButtonElement;
  }

  it("saves a custom quota converted from MiB to bytes", async () => {
    const user = createUser("u1", "alice");
    const modal = await openQuotaModal(user);
    vi.mocked(adminApi.setUserQuota).mockResolvedValue({
      ...user,
      upload_quota: 10 * 1024 * 1024,
    });

    modalSelect(modal).value = "custom";
    modalSelect(modal).dispatchEvent(new Event("change"));
    await flushPromises();

    const mbInput = modal.querySelector(
      'input[type="number"]',
    ) as HTMLInputElement;
    mbInput.value = "10";
    mbInput.dispatchEvent(new Event("input"));
    await flushPromises();

    modalSaveButton().click();
    await flushPromises();

    expect(adminApi.setUserQuota).toHaveBeenCalledWith("u1", 10 * 1024 * 1024);
    expect(document.body.querySelector(".app-modal")).toBeNull();
  });

  it("saves the unlimited mode as -1 and the default mode as null", async () => {
    const user = createUser("u1", "alice");
    user.upload_quota = 512;
    vi.mocked(adminApi.setUserQuota).mockResolvedValue(user);

    let modal = await openQuotaModal(user);
    modalSelect(modal).value = "unlimited";
    modalSelect(modal).dispatchEvent(new Event("change"));
    await flushPromises();
    modalSaveButton().click();
    await flushPromises();
    expect(adminApi.setUserQuota).toHaveBeenLastCalledWith("u1", -1);

    modal = await openQuotaModal(user);
    modalSelect(modal).value = "default";
    modalSelect(modal).dispatchEvent(new Event("change"));
    await flushPromises();
    modalSaveButton().click();
    await flushPromises();
    expect(adminApi.setUserQuota).toHaveBeenLastCalledWith("u1", null);
  });

  it("rejects a blank custom quota without calling the API", async () => {
    const user = createUser("u1", "alice");
    const modal = await openQuotaModal(user);

    modalSelect(modal).value = "custom";
    modalSelect(modal).dispatchEvent(new Event("change"));
    await flushPromises();

    modalSaveButton().click();
    await flushPromises();

    expect(adminApi.setUserQuota).not.toHaveBeenCalled();
    expect(modal.textContent).toContain(
      i18n.global.t("pages.admin.users.quotaInvalid"),
    );
  });
});
