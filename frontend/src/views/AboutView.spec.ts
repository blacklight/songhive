import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { setActivePinia, createPinia } from "pinia";
import { i18n } from "@/i18n";
import AboutView from "./AboutView.vue";
import * as instanceApi from "@/api/instance";

describe("AboutView", () => {
  let wrapper: ReturnType<typeof mount>;

  const originalEnv = { ...import.meta.env };

  beforeEach(() => {
    setActivePinia(createPinia());
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

  it("renders admin staff accounts with handles and profile links", async () => {
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
      staff_accounts: [
        {
          username: "admin",
          display_name: "Site Admin",
          avatar_url: "https://cdn.example.com/avatar.png",
          acct: "admin@test.example.com",
          url: "https://test.example.com/@admin",
          actor_url: "https://test.example.com/users/admin",
        },
      ],
      rules: [],
    } as instanceApi.InstanceInfo);

    wrapper = mount(AboutView);
    await flushPromises();

    expect(wrapper.text()).toContain("Site Admin");
    expect(wrapper.text()).toContain("@admin@test.example.com");

    const link = wrapper.find('a[href="https://test.example.com/@admin"]');
    expect(link.exists()).toBe(true);
    const avatar = link.find("img");
    expect(avatar.attributes("src")).toBe("https://cdn.example.com/avatar.png");
  });

  it("renders the configured contact person with a masked email", async () => {
    vi.spyOn(instanceApi, "getInstance").mockResolvedValue({
      title: "Test Hive",
      songhive_version: "1.2.3",
      description: "A test instance.",
      short_description: "A test instance.",
      uri: "test.example.com",
      email: "admin@example.com",
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
      contact: {
        name: "Jane Admin",
        email: "admin@example.com",
        url: "https://example.com/jane",
      },
      rules: [],
    } as instanceApi.InstanceInfo);

    wrapper = mount(AboutView);
    await flushPromises();

    expect(wrapper.text()).toContain("Jane Admin");
    expect(wrapper.text()).toContain("admin [at] example [dot] com");
    expect(wrapper.find('a[href^="mailto:"]').exists()).toBe(false);

    await wrapper.find(".about-view__masked-email").trigger("click");

    const mailto = wrapper.find('a[href="mailto:admin@example.com"]');
    expect(mailto.exists()).toBe(true);
    expect(mailto.text()).toBe("admin@example.com");

    const url = wrapper.find('a[href="https://example.com/jane"]');
    expect(url.exists()).toBe(true);
  });

  it("hides the contact section when no staff or contact exists", async () => {
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
      contact: null,
      staff_accounts: [],
      rules: [],
    } as instanceApi.InstanceInfo);

    wrapper = mount(AboutView);
    await flushPromises();

    expect(wrapper.text()).not.toContain(
      i18n.global.t("pages.about.administrators"),
    );
    expect(wrapper.find(".about-view__staff-list").exists()).toBe(false);
  });
});
