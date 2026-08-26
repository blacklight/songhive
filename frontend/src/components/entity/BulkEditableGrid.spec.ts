import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { createRouter, createMemoryHistory } from "vue-router";
import { setActivePinia, createPinia } from "pinia";
import { i18n } from "@/i18n";
import { useAuthStore } from "@/stores/auth";
import type { UserResponse } from "@/api/users";
import BulkEditableGrid from "./BulkEditableGrid.vue";

interface TestItem {
  id: string;
  name: string;
  owner_id: string | null;
}

function createItem(
  id: string,
  name: string,
  ownerId: string | null = "user-1",
): TestItem {
  return { id, name, owner_id: ownerId };
}

function setAuthenticated(userId = "user-1", isAdmin = false) {
  const authStore = useAuthStore();
  authStore.$patch({
    accessToken: "token",
    refreshToken: "refresh",
    expiresAt: Date.now() + 60000,
    user: { id: userId, username: "test" } as UserResponse,
    role: isAdmin ? "admin" : "user",
    status: "authenticated",
  });
}

function createTestRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [{ path: "/", component: { template: "<div/>" } }],
  });
}

function mountGrid(props: Record<string, unknown> = {}) {
  const deleteOne = vi.fn().mockResolvedValue(undefined);
  const refresh = vi.fn().mockResolvedValue(undefined);
  const search = vi.fn();
  const loadMore = vi.fn();
  const retry = vi.fn();

  const wrapper = mount(BulkEditableGrid, {
    attachTo: document.body,
    global: { plugins: [createTestRouter()] },
    props: {
      title: "Items",
      icon: "list",
      items: [] as TestItem[],
      loading: false,
      error: null,
      hasMore: false,
      query: "",
      entitySingular: i18n.global.t("browse.entities.item"),
      entityPlural: i18n.global.t("browse.entities.item"),
      deleteOne,
      refresh,
      getName: ((item: TestItem) => item.name) as (item: unknown) => string,
      search,
      loadMore,
      retry,
      ...props,
    },
    slots: {
      card: `<template #default="{ item, bulkMode }">
        <span class="test-card" :class="{ 'test-card--bulk': bulkMode }">{{ item.name }}</span>
      </template>`,
    },
  });

  return { wrapper, deleteOne, refresh, search, loadMore, retry };
}

describe("BulkEditableGrid", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
  });

  afterEach(() => {
    document.body.innerHTML = "";
  });

  it("renders the card slot for each item", () => {
    setAuthenticated();
    const { wrapper } = mountGrid({
      items: [createItem("1", "A"), createItem("2", "B")],
    });

    const cards = wrapper.findAll(".test-card");
    expect(cards).toHaveLength(2);
    expect(cards[0].text()).toBe("A");
    expect(cards[1].text()).toBe("B");
  });

  it("enters and exits bulk mode", async () => {
    setAuthenticated();
    const { wrapper } = mountGrid({
      items: [createItem("1", "A")],
    });

    const startButton = wrapper
      .findAll("button")
      .find((b) => b.text() === i18n.global.t("browse.bulkEdit.start"));
    expect(startButton).toBeDefined();

    await startButton?.trigger("click");
    await flushPromises();

    expect(wrapper.findAll(".test-card--bulk")).toHaveLength(1);
    expect(
      wrapper
        .findAll("button")
        .some((b) => b.text() === i18n.global.t("browse.bulkEdit.done")),
    ).toBe(true);

    const doneButton = wrapper
      .findAll("button")
      .find((b) => b.text() === i18n.global.t("browse.bulkEdit.done"));
    await doneButton?.trigger("click");
    await flushPromises();

    expect(wrapper.findAll(".test-card--bulk")).toHaveLength(0);
  });

  it("selects and deselects all manageable items", async () => {
    setAuthenticated();
    const { wrapper } = mountGrid({
      items: [
        createItem("1", "A", "user-1"),
        createItem("2", "B", "user-1"),
        createItem("3", "C", "user-2"),
      ],
    });

    const startButton = wrapper
      .findAll("button")
      .find((b) => b.text() === i18n.global.t("browse.bulkEdit.start"));
    await startButton?.trigger("click");
    await flushPromises();

    const checkboxes = wrapper.findAll('input[type="checkbox"]');
    expect(checkboxes).toHaveLength(4);

    const selectAllLabel = i18n.global.t("browse.bulkEdit.selectAll");
    const selectAll = wrapper
      .findAll(".app-checkbox")
      .find((c) => c.text() === selectAllLabel)
      ?.find('input[type="checkbox"]');
    expect(selectAll).toBeDefined();

    await selectAll?.setValue(true);
    await flushPromises();

    expect((checkboxes[0].element as HTMLInputElement).checked).toBe(true);
    expect((checkboxes[1].element as HTMLInputElement).checked).toBe(true);
    expect((checkboxes[2].element as HTMLInputElement).checked).toBe(true);
    expect((checkboxes[3].element as HTMLInputElement).checked).toBe(false);

    await selectAll?.setValue(false);
    await flushPromises();

    expect((checkboxes[0].element as HTMLInputElement).checked).toBe(false);
    expect((checkboxes[1].element as HTMLInputElement).checked).toBe(false);
    expect((checkboxes[2].element as HTMLInputElement).checked).toBe(false);
  });

  it("deletes a single item with the right id and recursive flag", async () => {
    setAuthenticated();
    const { wrapper, deleteOne } = mountGrid({
      items: [createItem("1", "A")],
      recursive: true,
    });

    const deleteButton = wrapper.find(".bulk-editable-grid__item-delete");
    expect(deleteButton.exists()).toBe(true);

    await deleteButton.trigger("click");
    await flushPromises();

    const confirmButton = Array.from(
      document.body.querySelectorAll("button"),
    ).find((b) => b.textContent === i18n.global.t("common.delete"));
    expect(confirmButton).toBeDefined();

    await confirmButton?.click();
    await flushPromises();

    expect(deleteOne).toHaveBeenCalledWith("1", false);
  });

  it("bulk deletes selected items with the right ids and recursive flag", async () => {
    setAuthenticated();
    const { wrapper, deleteOne } = mountGrid({
      items: [createItem("1", "A"), createItem("2", "B")],
      recursive: true,
    });

    const startButton = wrapper
      .findAll("button")
      .find((b) => b.text() === i18n.global.t("browse.bulkEdit.start"));
    await startButton?.trigger("click");
    await flushPromises();

    const checkboxes = wrapper.findAll('input[type="checkbox"]');
    await checkboxes[0].setValue(true);
    await checkboxes[1].setValue(true);
    await flushPromises();

    const deleteSelected = wrapper
      .findAll("button")
      .find(
        (b) => b.text() === i18n.global.t("browse.bulkEdit.deleteSelected"),
      );
    await deleteSelected?.trigger("click");
    await flushPromises();

    const confirmButton = Array.from(
      document.body.querySelectorAll("button"),
    ).find((b) => b.textContent === i18n.global.t("common.delete"));
    expect(confirmButton).toBeDefined();

    await confirmButton?.click();
    await flushPromises();

    expect(deleteOne).toHaveBeenCalledWith("1", false);
    expect(deleteOne).toHaveBeenCalledWith("2", false);
  });

  it("shows the empty state", () => {
    setAuthenticated();
    const { wrapper } = mountGrid();

    expect(wrapper.text()).toContain(
      i18n.global.t("browse.list.empty", {
        entity: i18n.global.t("browse.entities.item"),
      }),
    );
  });

  it("shows the loading state", () => {
    setAuthenticated();
    const { wrapper } = mountGrid({ loading: true });

    expect(wrapper.findAll(".skeleton").length).toBeGreaterThan(0);
  });

  it("shows the error state with a retry button", async () => {
    setAuthenticated();
    const retry = vi.fn();
    const { wrapper } = mountGrid({ error: "network failure", retry });

    expect(wrapper.text()).toContain("network failure");

    const retryButton = wrapper
      .findAll("button")
      .find((b) => b.text() === i18n.global.t("common.retry"));
    await retryButton?.trigger("click");

    expect(retry).toHaveBeenCalled();
  });

  it("hides the bulk edit start button when no manageable items", () => {
    setAuthenticated("user-1");
    const { wrapper } = mountGrid({
      items: [createItem("1", "A", "user-2")],
    });

    const startButton = wrapper
      .findAll("button")
      .find((b) => b.text() === i18n.global.t("browse.bulkEdit.start"));
    expect(startButton).toBeUndefined();
  });

  it("hides the bulk edit start button while loading", () => {
    setAuthenticated();
    const { wrapper } = mountGrid({
      items: [createItem("1", "A")],
      loading: true,
    });

    const startButton = wrapper
      .findAll("button")
      .find((b) => b.text() === i18n.global.t("browse.bulkEdit.start"));
    expect(startButton).toBeUndefined();
  });
});
