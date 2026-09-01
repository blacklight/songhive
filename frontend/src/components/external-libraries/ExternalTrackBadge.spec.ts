import { describe, it, expect } from "vitest";
import { mount } from "@vue/test-utils";
import { i18n } from "@/i18n";
import ExternalTrackBadge from "./ExternalTrackBadge.vue";

function mountBadge(props: {
  isExternal?: boolean;
  provider?: string | null;
  state?: string | null;
}) {
  return mount(ExternalTrackBadge, {
    props,
    global: { plugins: [i18n] },
  });
}

describe("ExternalTrackBadge", () => {
  it("renders nothing for non-external tracks", () => {
    const wrapper = mountBadge({ isExternal: false });
    expect(wrapper.find("*").exists()).toBe(false);
  });

  it("renders the provider and state for external tracks", () => {
    const wrapper = mountBadge({
      isExternal: true,
      provider: "s3",
      state: "active",
    });
    expect(wrapper.text()).toContain("s3");
    expect(wrapper.text()).toContain("active");
  });

  it("renders a generic external label when provider is missing", () => {
    const wrapper = mountBadge({ isExternal: true });
    expect(wrapper.text()).toContain(
      i18n.global.t("pages.externalLibraries.external"),
    );
  });
});
