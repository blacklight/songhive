<script setup lang="ts">
import { computed, ref } from "vue";
import { useI18n } from "vue-i18n";
import { RouterLink, RouterView, useRoute, useRouter } from "vue-router";
import { useAuthStore } from "@/stores/auth";
import { useInstanceStore } from "@/stores/instance";
import AppButton from "@/components/ui/AppButton.vue";
import AppToast from "@/components/feedback/AppToast.vue";
import AppAvatar from "@/components/ui/AppAvatar.vue";
import AppIcon from "@/components/ui/AppIcon.vue";
import PlayerBarSlot from "@/components/player/PlayerBarSlot.vue";

const { t } = useI18n();
const authStore = useAuthStore();
const instanceStore = useInstanceStore();
const router = useRouter();
const route = useRoute();
const isMobileMenuOpen = ref(false);

const displayName = computed(() => {
  if (!authStore.user) return "";
  return authStore.user.display_name || authStore.user.username;
});

async function logout() {
  await authStore.logout();
  await router.push("/login");
}

function toggleMenu() {
  isMobileMenuOpen.value = !isMobileMenuOpen.value;
}

type NavItem = {
  name: string;
  to: string;
  requiresAuth: boolean;
  icon: string;
};

function isNavItemActive(item: NavItem): boolean {
  if (item.to === "/") {
    return route.path === "/";
  }
  return route.path === item.to || route.path.startsWith(`${item.to}/`);
}

const navItems = computed<NavItem[]>(() => [
  { name: t("nav.home"), to: "/", requiresAuth: false, icon: "house" },
  {
    name: t("nav.library"),
    to: "/libraries",
    requiresAuth: false,
    icon: "folder-open",
  },
  {
    name: t("nav.artists"),
    to: "/artists",
    requiresAuth: false,
    icon: "users",
  },
  {
    name: t("nav.albums"),
    to: "/albums",
    requiresAuth: false,
    icon: "compact-disc",
  },
  { name: t("nav.tracks"), to: "/tracks", requiresAuth: false, icon: "music" },
  {
    name: t("nav.playlists"),
    to: "/playlists",
    requiresAuth: false,
    icon: "list",
  },
  {
    name: t("nav.hashtags"),
    to: "/hashtags",
    requiresAuth: false,
    icon: "hashtag",
  },
  {
    name: t("nav.genres"),
    to: "/genres",
    requiresAuth: false,
    icon: "tags",
  },
  {
    name: t("nav.history"),
    to: "/history",
    requiresAuth: true,
    icon: "clock-rotate-left",
  },
  {
    name: t("nav.favorites"),
    to: "/favorites",
    requiresAuth: true,
    icon: "heart",
  },
  { name: t("nav.files"), to: "/files", requiresAuth: true, icon: "file" },
  {
    name: t("nav.radio"),
    to: "/radio",
    requiresAuth: true,
    icon: "tower-broadcast",
  },
  {
    name: t("nav.about"),
    to: "/about",
    requiresAuth: false,
    icon: "circle-info",
  },
]);

const visibleNavItems = computed(() =>
  navItems.value.filter(
    (item) => !item.requiresAuth || authStore.isAuthenticated,
  ),
);

const adminItem = { name: t("nav.admin"), to: "/admin", icon: "shield-halved" };
const loginItem = {
  name: t("nav.login"),
  to: "/login",
  icon: "right-to-bracket",
};
</script>

<template>
  <div class="app-layout">
    <a href="#main" class="skip-link">Skip to main content</a>
    <AppButton
      variant="ghost"
      size="sm"
      class="app-layout__menu-toggle"
      icon="bars"
      :title="isMobileMenuOpen ? t('common.closeMenu') : t('common.openMenu')"
      :aria-label="
        isMobileMenuOpen ? t('common.closeMenu') : t('common.openMenu')
      "
      :aria-expanded="isMobileMenuOpen"
      @click="toggleMenu"
    />
    <aside
      class="app-layout__sidebar"
      :class="{ 'app-layout__sidebar--open': isMobileMenuOpen }"
    >
      <header class="app-layout__brand">
        <RouterLink
          to="/"
          class="app-layout__brand-link"
          :aria-label="t('pages.goHome')"
          @click="isMobileMenuOpen = false"
        >
          <img src="/logo.png" alt="" class="app-layout__logo" />
          <span class="app-layout__brand-name">{{ instanceStore.name }}</span>
        </RouterLink>
      </header>
      <nav class="app-layout__nav" role="navigation" aria-label="Main">
        <ul>
          <li v-for="item in visibleNavItems" :key="item.to">
            <RouterLink
              :to="item.to"
              class="app-layout__nav-link"
              :class="{ 'router-link-active': isNavItemActive(item) }"
              :active-class="item.to === '/' ? '' : undefined"
              :exact-active-class="
                item.to === '/' ? 'router-link-active' : undefined
              "
              @click="isMobileMenuOpen = false"
            >
              <AppIcon :name="item.icon" />
              {{ item.name }}
            </RouterLink>
          </li>
          <li v-if="authStore.isAdmin" class="app-layout__admin">
            <RouterLink
              :to="adminItem.to"
              class="app-layout__nav-link"
              @click="isMobileMenuOpen = false"
            >
              <AppIcon :name="adminItem.icon" />
              {{ adminItem.name }}
            </RouterLink>
          </li>
        </ul>
      </nav>
      <footer v-if="!authStore.isAuthenticated" class="app-layout__menu-footer">
        <RouterLink
          :to="loginItem.to"
          class="app-layout__login"
          @click="isMobileMenuOpen = false"
        >
          <AppIcon :name="loginItem.icon" spacing="right" />
          {{ loginItem.name }}
        </RouterLink>
      </footer>

      <footer v-else class="app-layout__menu-footer">
        <RouterLink
          :to="{ name: 'profile' }"
          class="app-layout__user"
          @click="isMobileMenuOpen = false"
        >
          <AppAvatar
            :src="authStore.user?.avatar_url || ''"
            :name="displayName"
            size="sm"
          />
          <span class="app-layout__user-name">{{ displayName }}</span>
        </RouterLink>
        <AppButton
          variant="ghost"
          size="sm"
          class="app-layout__logout"
          icon="right-from-bracket"
          :title="t('auth.logout')"
          @click="logout"
        />
      </footer>
    </aside>
    <main id="main" class="app-layout__main" role="main">
      <RouterView />
    </main>
    <footer class="app-layout__player">
      <PlayerBarSlot />
    </footer>
    <AppToast />
  </div>
</template>

<style scoped>
.skip-link {
  position: absolute;
  top: -100%;
  left: var(--space-4);
  z-index: 100;
  padding: var(--space-2) var(--space-3);
  background: var(--color-accent);
  color: var(--color-accent-contrast);
  border-radius: var(--radius-md);
  text-decoration: none;
}

.skip-link:focus {
  top: var(--space-2);
}

.app-layout {
  display: flex;
  min-height: 100vh;
  background-color: var(--color-bg);
  color: var(--color-text);

  --logout-btn-width: 2.5rem;
}

.app-layout__menu-toggle {
  display: none;
  position: fixed;
  top: var(--space-3);
  left: var(--space-3);
  z-index: 30;
}

.app-layout__sidebar {
  width: var(--sidebar-width);
  flex-shrink: 0;
  background-color: var(--color-bg-menu);
  color: var(--color-text-menu);
  border-right: 1px solid var(--color-border);
  padding: var(--space-4);
  position: sticky;
  top: 0;
  height: calc(100vh - 2 * var(--space-4));
  display: flex;
  flex-direction: column;
  overflow-y: auto;
}

.app-layout__nav {
  flex: 1;
  min-height: 0;
}

.app-layout__nav ul {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.app-layout__nav a {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-2) var(--space-3);
  border-radius: var(--radius-md);
  color: var(--color-text-menu);
  text-decoration: none;
  transition: background-color var(--transition-fast);
}

.app-layout__nav a:hover {
  background-color: var(--color-surface-hover);
  color: var(--color-text-hover);
}

.app-layout__nav a.router-link-active {
  background-color: var(--color-surface-raised);
  color: var(--color-accent-contrast);
}

.app-layout__brand {
  margin-bottom: var(--space-4);
}

.app-layout__brand-link {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-2) var(--space-3);
  border-radius: var(--radius-md);
  color: var(--color-text-menu);
  text-decoration: none;
  font-size: 1.25rem;
  font-weight: 600;
  transition: background-color var(--transition-fast);
}

.app-layout__brand-link:hover {
  background-color: var(--color-surface-hover);
  color: var(--color-accent-contrast);
}

.app-layout__logo {
  width: 4rem;
  height: 4rem;
}

.app-layout__nav-link .fa-solid {
  margin-right: var(--space-2);
}

.app-layout__admin a {
  color: var(--color-accent);
}

.app-layout__menu-footer {
  margin-top: var(--space-4);
  display: flex;
  flex-direction: row;
  flex-shrink: 0;
}

.app-layout__login {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: var(--space-2);
  width: calc(100% - 2 * var(--space-3));
  padding: var(--space-2) var(--space-3);
  border-radius: var(--radius-md);
  background-color: var(--color-accent);
  color: var(--color-accent-contrast);
  text-decoration: none;
  font-weight: 500;
  transition: filter var(--transition-fast);
}

.app-layout__login:hover {
  filter: brightness(0.95);
}

.app-layout__user {
  width: calc(100% - var(--space-3) - var(--logout-btn-width));
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-2);
  border-radius: var(--radius-md);
  color: var(--color-text-menu);
  text-decoration: none;
  font-weight: 500;
  transition: background-color var(--transition-fast);
}

.app-layout__user:hover,
.app-layout__user.router-link-active {
  color: var(--color-accent-contrast);
}

.app-layout__user.router-link-active {
  background-color: var(--color-surface-raised);
}

.app-layout__user:hover {
  background-color: var(--color-surface-hover);
}

.app-layout__user-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.app-layout__logout {
  width: var(--logout-btn-width);
  margin-top: 0;
  color: var(--color-text-menu);
}

.app-layout__main {
  flex: 1;
  padding: var(--space-4);
  min-width: 0;
  padding-bottom: calc(var(--space-4) + 5rem);
}

.app-layout__player {
  position: fixed;
  bottom: 0;
  left: calc(var(--sidebar-width) + 2.05rem);
  right: 0;
  height: 5rem;
  background-color: var(--color-surface);
  border-top: 1px solid var(--color-border);
  z-index: var(--z-player);
}

@media (max-width: 767px) {
  .app-layout__menu-toggle {
    display: inline-flex;
  }

  .app-layout__brand {
    margin-top: 2.75rem;
  }

  .app-layout__sidebar {
    position: fixed;
    top: 0;
    left: 0;
    height: 100%;
    transform: translateX(-100%);
    transition: transform var(--transition-base);
    z-index: 20;
  }

  .app-layout__sidebar--open {
    transform: translateX(0);
  }

  .app-layout__main {
    left: 0;
  }

  .app-layout__player {
    left: 0;
  }
}

@media (prefers-reduced-motion: reduce) {
  .app-layout__sidebar {
    transition: none;
  }
}
</style>
