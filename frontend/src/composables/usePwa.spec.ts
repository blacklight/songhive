import { describe, it, expect, beforeEach } from "vitest";
import { mount } from "@vue/test-utils";
import { defineComponent, nextTick } from "vue";
import { type InstanceInfo } from "@/api/instance";
import { useInstanceStore } from "@/stores/instance";
import { useThemeStore } from "@/stores/theme";
import { usePwa } from "./usePwa";

const TestComponent = defineComponent({
  setup() {
    usePwa();
    return {};
  },
  template: "<div />",
});

function setupHead() {
  document.head.innerHTML = `
    <meta name="theme-color" content="#f9f8f7" id="pwa-theme-color" />
    <meta name="apple-mobile-web-app-status-bar-style" content="default" id="pwa-status-bar" />
    <meta name="apple-mobile-web-app-title" content="Songhive" id="pwa-apple-title" />
    <link rel="manifest" href="/manifest.webmanifest" id="pwa-manifest-link" />
  `;
}

describe("usePwa", () => {
  beforeEach(() => {
    setupHead();
  });

  it("sets the manifest and theme-color meta tags on mount", async () => {
    mount(TestComponent);
    await nextTick();

    const manifestLink = document.getElementById(
      "pwa-manifest-link",
    ) as HTMLLinkElement;
    const themeMeta = document.getElementById(
      "pwa-theme-color",
    ) as HTMLMetaElement;

    expect(manifestLink.href).toContain("/manifest.webmanifest");
    expect(manifestLink.href).toContain("theme=light");
    expect(themeMeta.content).toBe("#f9f8f7");
  });

  it("reacts to theme changes", async () => {
    mount(TestComponent);
    await nextTick();

    const themeStore = useThemeStore();
    const manifestLink = document.getElementById(
      "pwa-manifest-link",
    ) as HTMLLinkElement;
    const themeMeta = document.getElementById(
      "pwa-theme-color",
    ) as HTMLMetaElement;

    themeStore.setMode("dark");
    await nextTick();

    expect(manifestLink.href).toContain("theme=dark");
    expect(themeMeta.content).toBe("#1f2927");
  });

  it("updates the apple app title when the instance name changes", async () => {
    mount(TestComponent);
    await nextTick();

    const instanceStore = useInstanceStore();
    const appleTitle = document.getElementById(
      "pwa-apple-title",
    ) as HTMLMetaElement;

    instanceStore.$patch({
      instance: { title: "My Hive" } as unknown as InstanceInfo,
    });
    await nextTick();

    expect(appleTitle.content).toBe("My Hive");
  });
});
