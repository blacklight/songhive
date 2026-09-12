import { describe, it, expect, vi, beforeEach } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { createRouter, createMemoryHistory } from "vue-router";
import NotificationItemCard from "./NotificationItemCard.vue";
import { getItemSummary } from "@/composables/useItemSummary";

vi.mock("@/composables/useItemSummary", () => ({
  getItemSummary: vi.fn().mockResolvedValue(null),
}));

const getItemSummaryMock = vi.mocked(getItemSummary);

function createTestRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: "/", component: { template: "<div/>" } },
      { path: "/:pathMatch(.*)*", component: { template: "<div/>" } },
    ],
  });
}

function mountCard(props: {
  itemType: string;
  itemId: string;
  title?: string | null;
  to: string;
}) {
  return mount(NotificationItemCard, {
    props,
    global: { plugins: [createTestRouter()] },
  });
}

describe("NotificationItemCard", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getItemSummaryMock.mockResolvedValue(null);
  });

  it("renders the payload title and links to the item page", async () => {
    const wrapper = mountCard({
      itemType: "album",
      itemId: "alb-1",
      title: "Cool Album",
      to: "/albums/alb-1",
    });
    await flushPromises();

    const card = wrapper.find("a.item-card");
    expect(card.attributes("href")).toBe("/albums/alb-1");
    expect(card.text()).toContain("Cool Album");
    expect(getItemSummaryMock).toHaveBeenCalledWith("album", "alb-1");
  });

  it("renders the fetched cover image when available", async () => {
    getItemSummaryMock.mockResolvedValue({
      title: "Fetched Title",
      imageUrl: "/api/v1/files/f-1/download",
    });
    const wrapper = mountCard({
      itemType: "track",
      itemId: "t-1",
      title: null,
      to: "/tracks/t-1",
    });
    await flushPromises();

    const img = wrapper.find(".item-card__art img");
    expect(img.exists()).toBe(true);
    expect(img.attributes("src")).toBe("/api/v1/files/f-1/download");
    expect(wrapper.text()).toContain("Fetched Title");
  });

  it("falls back to the item id and a type icon", async () => {
    const wrapper = mountCard({
      itemType: "track",
      itemId: "t-9",
      title: null,
      to: "/tracks/t-9",
    });
    await flushPromises();

    expect(wrapper.find(".item-card__art img").exists()).toBe(false);
    expect(wrapper.text()).toContain("t-9");
  });
});
