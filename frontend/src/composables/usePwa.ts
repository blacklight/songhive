import { watch } from "vue";
import { useInstanceStore } from "@/stores/instance";
import { useThemeStore } from "@/stores/theme";

const MANIFEST_PATH = "/manifest.webmanifest";

const THEME_COLORS: Record<
  "light" | "dark",
  { meta: string; appleStatusBar: string }
> = {
  light: { meta: "#f9f8f7", appleStatusBar: "default" },
  dark: { meta: "#1f2927", appleStatusBar: "black" },
};

function setMetaContent(name: string, content: string): void {
  const element = document.querySelector(
    `meta[name="${name}"]`,
  ) as HTMLMetaElement | null;
  if (element) {
    element.content = content;
  }
}

function setAppleTitle(title: string): void {
  const element = document.getElementById(
    "pwa-apple-title",
  ) as HTMLMetaElement | null;
  if (element) {
    element.content = title;
  }
}

function updateManifest(theme: "light" | "dark"): void {
  const link = document.getElementById(
    "pwa-manifest-link",
  ) as HTMLLinkElement | null;
  if (!link) {
    return;
  }

  const url = new URL(MANIFEST_PATH, window.location.origin);
  url.searchParams.set("theme", theme);
  link.href = url.toString();
}

function updateThemeMeta(theme: "light" | "dark"): void {
  const colors = THEME_COLORS[theme];
  setMetaContent("theme-color", colors.meta);
  setMetaContent(
    "apple-mobile-web-app-status-bar-style",
    colors.appleStatusBar,
  );
}

function registerServiceWorker(): void {
  if (import.meta.env.DEV) {
    return;
  }

  if (!("serviceWorker" in navigator)) {
    return;
  }

  navigator.serviceWorker
    .register("/sw.js", { scope: "/" })
    .catch((error: unknown) => {
      console.warn("Service worker registration failed:", error);
    });
}

export function usePwa() {
  const themeStore = useThemeStore();
  const instanceStore = useInstanceStore();

  watch(
    () => themeStore.resolvedMode,
    (theme) => {
      updateManifest(theme);
      updateThemeMeta(theme);
    },
    { immediate: true },
  );

  watch(
    () => instanceStore.name,
    (name) => {
      setAppleTitle(name);
    },
    { immediate: true },
  );

  return { registerServiceWorker };
}
