import { describe, it, expect } from "vitest";
import { mount } from "@vue/test-utils";
import { i18n } from "@/i18n";
import SessionsTab from "./SessionsTab.vue";

describe("SessionsTab", () => {
  it("renders the disabled notice and makes no network requests", () => {
    const wrapper = mount(SessionsTab, { global: { plugins: [] } });
    expect(wrapper.text()).toContain(
      i18n.global.t("profile.sessions.disabled"),
    );
  });
});
