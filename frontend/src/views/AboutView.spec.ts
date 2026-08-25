import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { i18n } from "@/i18n";
import AboutView from "./AboutView.vue";
import * as instanceApi from "@/api/instance";

describe("AboutView", () => {
  let wrapper: ReturnType<typeof mount>;

  const originalEnv = { ...import.meta.env };

  beforeEach(() => {
    vi.stubEnv("VITE_DOCS_URL", "");
    vi.stubEnv("VITE_SUPPORT_URL", "");
  });

  afterEach(() => {
    wrapper?.unmount();
    Object.assign(import.meta.env, originalEnv);
    vi.restoreAllMocks();
  });

  it("renders instance data from the public endpoint", async () => {
    vi.spyOn(instanceApi, "getInstance").mockResolvedValue({
      title: "Test Hive",
      songhive_version: "1.2.3",
      description: "A test instance.",
      short_description: "A test instance.",
      uri: "test.example.com",
      email: "",
      version: "Songhive 1.2.3 (Mastodon-compatible)",
      urls: { streaming_api: "" },
      stats: { user_count: 0, status_count: 0, domain_count: 0 },
      thumbnail: null,
      languages: ["en"],
      registrations: true,
      approval_required: false,
      invites_enabled: false,
      configuration: {},
      contact_account: null,
      rules: [],
    } as instanceApi.InstanceInfo);

    wrapper = mount(AboutView);
    await flushPromises();

    expect(wrapper.text()).toContain("Test Hive");
    expect(wrapper.text()).toContain("1.2.3");
    expect(wrapper.text()).toContain("A test instance.");
  });

  it("renders fallback values when the endpoint fails", async () => {
    vi.spyOn(instanceApi, "getInstance").mockRejectedValue(
      new Error("Network error"),
    );

    wrapper = mount(AboutView);
    await flushPromises();

    expect(wrapper.text()).toContain(i18n.global.t("pages.about.defaultName"));
    expect(wrapper.text()).toContain("0.0.1");
    expect(wrapper.text()).toContain(
      i18n.global.t("pages.about.defaultDescription"),
    );
  });

  it("renders documentation and support links", async () => {
    vi.stubEnv("VITE_DOCS_URL", "https://docs.example.com");
    vi.stubEnv("VITE_SUPPORT_URL", "https://support.example.com");

    vi.spyOn(instanceApi, "getInstance").mockResolvedValue({
      title: "Test Hive",
      songhive_version: "1.2.3",
      description: "A test instance.",
      short_description: "A test instance.",
      uri: "test.example.com",
      email: "",
      version: "Songhive 1.2.3 (Mastodon-compatible)",
      urls: { streaming_api: "" },
      stats: { user_count: 0, status_count: 0, domain_count: 0 },
      thumbnail: null,
      languages: ["en"],
      registrations: true,
      approval_required: false,
      invites_enabled: false,
      configuration: {},
      contact_account: null,
      rules: [],
    } as instanceApi.InstanceInfo);

    wrapper = mount(AboutView);
    await flushPromises();

    expect(wrapper.text()).toContain(
      i18n.global.t("pages.about.documentation"),
    );
    expect(wrapper.text()).toContain(i18n.global.t("pages.about.support"));

    const links = wrapper.findAll("a");
    expect(
      links.some((a) => a.attributes("href") === "https://docs.example.com"),
    ).toBe(true);
    expect(
      links.some((a) => a.attributes("href") === "https://support.example.com"),
    ).toBe(true);
  });
});
