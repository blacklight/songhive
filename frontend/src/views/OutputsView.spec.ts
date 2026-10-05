import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { mount, flushPromises, type DOMWrapper } from "@vue/test-utils";
import { setActivePinia, createPinia } from "pinia";
import * as outputsApi from "@/api/outputs";
import type { OutputResponse, ProviderResponse } from "@/api/outputs";
import OutputsView from "./OutputsView.vue";

vi.mock("@/api/outputs", () => ({
  listOutputProviders: vi.fn(),
  listOutputs: vi.fn(),
  createOutput: vi.fn(),
  updateOutput: vi.fn(),
  deleteOutput: vi.fn(),
  validateOutput: vi.fn(),
}));

const httpProvider: ProviderResponse = {
  provider_type: "http",
  label: "HTTP stream (built-in)",
  user_configurable: true,
  can_create: true,
  fields: [
    { name: "mount", type: "text", required: true, label: "Mount point" },
    {
      name: "record_listens",
      type: "boolean",
      required: false,
      label: "Scrobbling & stats",
      default: true,
    },
  ],
};

const fakeProvider: ProviderResponse = {
  provider_type: "fake",
  label: "Fake",
  user_configurable: true,
  can_create: true,
  fields: [{ name: "mount", type: "text", required: true, label: "Mount" }],
};

function createOutput(overrides: Partial<OutputResponse> = {}): OutputResponse {
  return {
    id: "out-1",
    user_id: "user-1",
    provider_type: "http",
    name: "My mount",
    config: { mount: "radio" },
    capabilities: null,
    enabled: true,
    last_error: null,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

describe("OutputsView", () => {
  let wrapper: ReturnType<typeof mount>;

  beforeEach(() => {
    setActivePinia(createPinia());
    vi.resetAllMocks();
    vi.mocked(outputsApi.listOutputs).mockResolvedValue([]);
    vi.mocked(outputsApi.listOutputProviders).mockResolvedValue([
      httpProvider,
      fakeProvider,
    ]);
  });

  afterEach(() => {
    wrapper?.unmount();
    document.body.innerHTML = "";
  });

  async function mountView() {
    wrapper = mount(OutputsView, { attachTo: document.body });
    await flushPromises();
  }

  async function openNewForm() {
    const button = wrapper
      .findAll("button")
      .find((b) => b.text().includes("New output"));
    await button!.trigger("click");
    await flushPromises();
  }

  function recordListensBox(): DOMWrapper<Element> | undefined {
    return wrapper
      .findAll(".app-checkbox")
      .find((c) => c.text().includes("Scrobbling & stats"));
  }

  async function fillInput(labelText: string, value: string) {
    const label = wrapper
      .findAll("label")
      .find((l) => l.text().includes(labelText));
    expect(label).toBeDefined();
    await wrapper.find(`#${label!.attributes("for")}`).setValue(value);
  }

  it("shows the scrobbling toggle for providers advertising record_listens", async () => {
    await mountView();
    await openNewForm();

    const box = recordListensBox();
    expect(box).toBeDefined();
    const input = box!.find("input").element as HTMLInputElement;
    expect(input.checked).toBe(true);
  });

  it("hides the toggle for providers without record_listens", async () => {
    vi.mocked(outputsApi.listOutputProviders).mockResolvedValue([fakeProvider]);
    await mountView();
    await openNewForm();

    expect(recordListensBox()).toBeUndefined();
  });

  it("sends record_listens in the saved config", async () => {
    vi.mocked(outputsApi.createOutput).mockResolvedValue(createOutput());
    await mountView();
    await openNewForm();

    await fillInput("Name", "Radio");
    await fillInput("Mount point", "radio");

    const box = recordListensBox()!;
    await box.find("input").setValue(false);

    await wrapper.find("form").trigger("submit");
    await flushPromises();

    expect(outputsApi.createOutput).toHaveBeenCalledWith(
      expect.objectContaining({
        provider_type: "http",
        config: expect.objectContaining({ record_listens: false }),
      }),
    );
  });

  it("reflects a stored record_listens=false when editing", async () => {
    vi.mocked(outputsApi.listOutputs).mockResolvedValue([
      createOutput({ config: { mount: "radio", record_listens: false } }),
    ]);
    await mountView();

    const editButton = wrapper
      .findAll("button")
      .find((b) => b.attributes("aria-label") === "Edit");
    await editButton!.trigger("click");
    await flushPromises();

    const box = recordListensBox();
    expect(box).toBeDefined();
    const input = box!.find("input").element as HTMLInputElement;
    expect(input.checked).toBe(false);
  });
});
