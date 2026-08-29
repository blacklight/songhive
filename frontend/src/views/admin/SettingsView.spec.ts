import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { setActivePinia, createPinia } from "pinia";
import { i18n } from "@/i18n";
import * as adminApi from "@/api/admin";
import type { SettingResponse } from "@/api/admin";
import SettingsView from "./SettingsView.vue";

vi.mock("@/api/admin", () => ({
  listSettings: vi.fn(),
  updateSetting: vi.fn(),
}));

function createSetting(
  key: string,
  value: unknown,
  type: string,
): SettingResponse {
  return { key, value, type, updated_at: null };
}

describe("SettingsView", () => {
  let wrapper: ReturnType<typeof mount>;

  beforeEach(() => {
    setActivePinia(createPinia());
    vi.clearAllMocks();
    vi.mocked(adminApi.listSettings).mockResolvedValue([]);
  });

  afterEach(() => {
    wrapper?.unmount();
  });

  it("loads and renders settings", async () => {
    vi.mocked(adminApi.listSettings).mockResolvedValue([
      createSetting("instance_name", "Songhive", "string"),
      createSetting("federation_enabled", false, "boolean"),
    ]);

    wrapper = mount(SettingsView, { global: { plugins: [i18n] } });
    await flushPromises();

    expect(adminApi.listSettings).toHaveBeenCalled();
    const input = wrapper.find('input[type="text"]');
    expect((input.element as HTMLInputElement).value).toBe("Songhive");
  });

  it("only PUTs changed settings", async () => {
    vi.mocked(adminApi.listSettings).mockResolvedValue([
      createSetting("instance_name", "Songhive", "string"),
      createSetting("federation_enabled", false, "boolean"),
    ]);
    vi.mocked(adminApi.updateSetting).mockResolvedValue(
      createSetting("instance_name", "New Name", "string"),
    );

    wrapper = mount(SettingsView, { global: { plugins: [i18n] } });
    await flushPromises();

    const input = wrapper.find('input[type="text"]');
    await input.setValue("New Name");
    await flushPromises();

    const saveButton = wrapper
      .findAll("button")
      .find((b) => b.text() === i18n.global.t("pages.admin.settings.save"));
    await saveButton?.trigger("click");
    await flushPromises();

    expect(adminApi.updateSetting).toHaveBeenCalledWith(
      "instance_name",
      "New Name",
    );
    expect(adminApi.updateSetting).toHaveBeenCalledTimes(1);
  });

  it("coerces a boolean setting on save", async () => {
    vi.mocked(adminApi.listSettings).mockResolvedValue([
      createSetting("federation_enabled", false, "boolean"),
    ]);
    vi.mocked(adminApi.updateSetting).mockResolvedValue(
      createSetting("federation_enabled", true, "boolean"),
    );

    wrapper = mount(SettingsView, { global: { plugins: [i18n] } });
    await flushPromises();

    const checkbox = wrapper.find('input[type="checkbox"]');
    await checkbox.setValue(true);
    await flushPromises();

    const saveButton = wrapper
      .findAll("button")
      .find((b) => b.text() === i18n.global.t("pages.admin.settings.save"));
    await saveButton?.trigger("click");
    await flushPromises();

    expect(adminApi.updateSetting).toHaveBeenCalledWith(
      "federation_enabled",
      true,
    );
  });
});
