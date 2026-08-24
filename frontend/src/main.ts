import { createApp } from "vue";
import { createPinia } from "pinia";
import App from "./App.vue";
import router from "./router";
import { i18n, initializeI18n } from "./i18n";
import { useThemeStore } from "./stores/theme";
import { useAuthStore } from "./stores/auth";
import { usePlayerStore } from "./stores/player";
import { playerEngine } from "./player/engine";
import "./styles/tokens.css";

const app = createApp(App);
const pinia = createPinia();

app.use(pinia);

const themeStore = useThemeStore();
themeStore.apply();

const authStore = useAuthStore();
authStore.registerClientProviders();

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

(async () => {
  await initializeI18n();
  app.use(i18n);
  app.use(router);
  app.mount("#app");
})();
