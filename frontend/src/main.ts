import { createApp, watch } from "vue";
import { createPinia } from "pinia";
import App from "./App.vue";
import router from "./router";
import { i18n, initializeI18n } from "./i18n";
import { useThemeStore } from "./stores/theme";
import { useAuthStore } from "./stores/auth";
import { useInstanceStore } from "./stores/instance";
import { usePlayerStore } from "./stores/player";
import { playerEngine } from "./player/engine";
import "./styles/tokens.css";
import "@fortawesome/fontawesome-free/css/all.min.css";

const app = createApp(App);
const pinia = createPinia();

app.use(pinia);

const themeStore = useThemeStore();
themeStore.apply();

const authStore = useAuthStore();
authStore.registerClientProviders();

const instanceStore = useInstanceStore();
void instanceStore.load();

const playerStore = usePlayerStore();
playerEngine.init({
  onTimeUpdate: (t) => playerStore.updateTime(t),
  onDuration: (d) => playerStore.updateDuration(d),
  onEnded: () => playerStore.next(),
  onStateChange: (s) => playerStore.setPlaybackState(s),
  onError: (err) => {
    playerStore.setPlaybackState("error");
    console.error("Playback error", err);
  },
});
playerStore.registerEngine(playerEngine);

// The engine samples scrobble status at boot — before the router guard
// restores the session and before any mid-session login — so now-playing
// reports would stay off for the whole session if that first fetch fails.
// Re-evaluate whenever the auth state settles.
watch(
  () => authStore.status,
  (status) => {
    if (status === "authenticated") {
      void playerEngine.refreshScrobbleStatus();
    } else if (status === "unauthenticated") {
      playerEngine.resetScrobbleStatus();
    }
  },
);

(async () => {
  await initializeI18n();
  app.use(i18n);
  app.use(router);
  app.mount("#app");
})();
