import { createApp } from "vue";
import { createPinia } from "pinia";
import App from "./App.vue";
import router from "./router";
import { i18n, initializeI18n } from "./i18n";
import { useThemeStore } from "./stores/theme";
import { useAuthStore } from "./stores/auth";
import "./styles/tokens.css";

const app = createApp(App);

app.use(createPinia());

const themeStore = useThemeStore();
themeStore.apply();

const authStore = useAuthStore();
authStore.registerClientProviders();

(async () => {
  await initializeI18n();
  app.use(i18n);
  app.use(router);
  app.mount("#app");
})();
