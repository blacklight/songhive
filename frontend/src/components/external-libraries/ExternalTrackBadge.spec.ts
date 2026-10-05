import { describe, it, expect } from "vitest";
import { mount } from "@vue/test-utils";
import ExternalTrackBadge from "./ExternalTrackBadge.vue";

function mountBadge(props: { isExternal?: boolean; provider?: string | null }) {
  return mount(ExternalTrackBadge, {
    props,
  });
}

describe("ExternalTrackBadge", () => {
  it("renders nothing for non-external tracks", () => {
    const wrapper = mountBadge({ isExternal: false });
    expect(wrapper.find("*").exists()).toBe(false);
  });

  it("renders a provider-specific icon with an accessible label", () => {
    const wrapper = mountBadge({ isExternal: true, provider: "local" });
    const badge = wrapper.find("[role='img']");
    expect(badge.exists()).toBe(true);
    expect(badge.attributes("aria-label")).toBe("Local storage");
    expect(badge.attributes("title")).toBe("Local storage");
    expect(wrapper.find("i").classes()).toContain("fa-hard-drive");
  });

  it("uses the AWS brand icon for S3 tracks", () => {
    const wrapper = mountBadge({ isExternal: true, provider: "s3" });
    const badge = wrapper.find("[role='img']");
    expect(badge.attributes("aria-label")).toBe("Amazon S3");
    const icon = wrapper.find("i");
    expect(icon.classes()).toContain("fa-brands");
    expect(icon.classes()).toContain("fa-aws");
  });

  it("uses the TIDAL brand icon for TIDAL tracks", () => {
    const wrapper = mountBadge({ isExternal: true, provider: "tidal" });
    const badge = wrapper.find("[role='img']");
    expect(badge.attributes("aria-label")).toBe("TIDAL");
    const icon = wrapper.find("i");
    expect(icon.classes()).toContain("fa-brands");
    expect(icon.classes()).toContain("fa-tidal");
  });

  it("uses the YouTube brand icon for YouTube tracks", () => {
    const wrapper = mountBadge({ isExternal: true, provider: "youtube" });
    const badge = wrapper.find("[role='img']");
    expect(badge.attributes("aria-label")).toBe("YouTube");
    const icon = wrapper.find("i");
    expect(icon.classes()).toContain("fa-brands");
    expect(icon.classes()).toContain("fa-youtube");
  });

  it("renders an inline SVG for Jellyfin tracks", () => {
    const wrapper = mountBadge({ isExternal: true, provider: "jellyfin" });
    const badge = wrapper.find("[role='img']");
    expect(badge.attributes("aria-label")).toBe("Jellyfin");
    expect(badge.find("svg.provider-icon").exists()).toBe(true);
    expect(badge.find("i").exists()).toBe(false);
  });

  it("falls back to a cloud icon with the provider as label", () => {
    const wrapper = mountBadge({ isExternal: true, provider: "unknown" });
    const badge = wrapper.find("[role='img']");
    expect(badge.attributes("aria-label")).toBe("Unknown");
    expect(wrapper.find("i").classes()).toContain("fa-cloud");
  });

  it("renders a generic cloud icon when the provider is missing", () => {
    const wrapper = mountBadge({ isExternal: true });
    const badge = wrapper.find("[role='img']");
    expect(badge.attributes("aria-label")).toBe("External");
    expect(wrapper.find("i").classes()).toContain("fa-cloud");
  });
});
