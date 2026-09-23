import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import type { VueWrapper } from "@vue/test-utils";
import { setActivePinia, createPinia } from "pinia";
import { nextTick } from "vue";
import OutputSelector from "./OutputSelector.vue";
import * as outputsApi from "@/api/outputs";
import * as playbackApi from "@/api/playback";
import { usePlaybackStore } from "@/stores/playback";
import { eventBus } from "@/api/ws";
import { i18n } from "@/i18n";

vi.mock("@/api/outputs", () => ({
  listOutputProviders: vi.fn(),
  listOutputs: vi.fn(),
}));

vi.mock("@/api/playback", () => ({
  getPlaybackSession: vi.fn(),
  setSessionOutputs: vi.fn(),
  sendPlaybackCommand: vi.fn(),
}));

const provider = {
  provider_type: "icecast",
  user_configurable: true,
  can_create: true,
  fields: [],
};

const output = {
  id: "o1",
  user_id: "u1",
  provider_type: "icecast",
  name: "Studio",
  config: {},
  capabilities: null,
  enabled: true,
  last_error: null,
  created_at: "",
  updated_at: "",
};

const sessionState = {
  session_id: "s1",
  user_id: "u1",
  state: "idle" as const,
  current_index: 0,
  position_seconds: 0,
  position_anchor_at: null,
  live_position_seconds: 0,
  repeat: "off" as const,
  shuffle: false,
  controller_connection_id: null,
  queue: [],
  outputs: [],
  last_active_at: null,
};

function createWrapper() {
  return mount(OutputSelector, {
    global: {
      plugins: [i18n],
    },
  });
}

function menuItems(): HTMLElement[] {
  return Array.from(document.body.querySelectorAll('[role="menuitem"]'));
}

describe("OutputSelector", () => {
  let wrapper: VueWrapper | null = null;

  beforeEach(() => {
    setActivePinia(createPinia());
    vi.clearAllMocks();
    vi.mocked(outputsApi.listOutputProviders).mockResolvedValue([provider]);
    vi.mocked(outputsApi.listOutputs).mockResolvedValue([output]);
    vi.mocked(playbackApi.sendPlaybackCommand).mockResolvedValue(sessionState);
    vi.mocked(playbackApi.setSessionOutputs).mockResolvedValue(sessionState);
    vi.spyOn(eventBus, "connect").mockImplementation(() => {});
    vi.spyOn(eventBus, "playbackControl").mockImplementation(() => {});
    vi.spyOn(eventBus, "on").mockImplementation(() => {});
    vi.spyOn(eventBus, "off").mockImplementation(() => {});
  });

  afterEach(() => {
    wrapper?.unmount();
    wrapper = null;
    document.body.innerHTML = "";
  });

  it("renders a single icon button for the active output", async () => {
    wrapper = createWrapper();
    await flushPromises();
    await nextTick();

    const buttons = wrapper.findAll("button");
    expect(buttons.length).toBe(1);
    expect(buttons[0]?.find("i").classes()).toContain("fa-display");
  });

  it("opens a menu of icon+label output items", async () => {
    wrapper = createWrapper();
    await flushPromises();

    await wrapper.find("button").trigger("click");
    await flushPromises();

    const items = menuItems();
    expect(items.length).toBe(2);
    expect(items[0]?.textContent).toContain("This device");
    expect(items[0]?.querySelector("i")?.className).toContain("fa-display");
    expect(items[1]?.textContent).toContain("icecast: Studio");
    expect(items[1]?.querySelector("i")?.className).toContain(
      "fa-tower-broadcast",
    );
    expect(items[0]?.className).toContain("context-menu__item--active");
    expect(document.body.querySelector(".context-menu__header")).toBeNull();
  });

  it("closes the menu when the button is clicked again", async () => {
    wrapper = createWrapper();
    await flushPromises();

    const button = wrapper.find("button");
    await button.trigger("click");
    await flushPromises();
    expect(menuItems().length).toBe(2);

    await button.trigger("click");
    await flushPromises();
    expect(menuItems().length).toBe(0);
  });

  it("selects an output from the menu", async () => {
    wrapper = createWrapper();
    await flushPromises();

    await wrapper.find("button").trigger("click");
    await flushPromises();
    menuItems()[1]?.click();
    await flushPromises();

    expect(playbackApi.setSessionOutputs).toHaveBeenCalledWith(
      expect.objectContaining({ output_ids: ["o1"] }),
    );
    expect(menuItems().length).toBe(0);
  });

  it("shows errors in the menu instead of cramped inline text", async () => {
    wrapper = createWrapper();
    await flushPromises();

    const playbackStore = usePlaybackStore();
    playbackStore.error = "Another connection is controlling this session";
    await nextTick();

    const button = wrapper.find("button");
    expect(button.classes()).toContain("output-selector__button--error");

    await button.trigger("click");
    await flushPromises();

    const error = document.body.querySelector(".output-selector__error");
    expect(error?.textContent).toContain(
      "Another connection is controlling this session",
    );
  });
});
