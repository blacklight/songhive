<script setup lang="ts">
import { ref } from "vue";
import { useI18n } from "vue-i18n";
import { RouterLink, RouterView } from "vue-router";
import AppButton from "@/components/ui/AppButton.vue";
import AppIcon from "@/components/ui/AppIcon.vue";
import AppToast from "@/components/feedback/AppToast.vue";

const { t } = useI18n();
const isMobileMenuOpen = ref(false);

function toggleMenu() {
  isMobileMenuOpen.value = !isMobileMenuOpen.value;
}

const adminNav = [
  { name: "Dashboard", to: "/admin", icon: "gauge" },
  { name: "Users", to: "/admin/users", icon: "users" },
  { name: "Settings", to: "/admin/settings", icon: "gear" },
  { name: "Reports", to: "/admin/reports", icon: "flag" },
  { name: "Invites", to: "/admin/invites", icon: "user-plus" },
  { name: "Audit", to: "/admin/audit", icon: "clipboard-list" },
  { name: "Storage", to: "/admin/storage", icon: "database" },
];
</script>

<template>
  <div class="admin-layout">
    <AppButton
      variant="ghost"
      size="sm"
      class="admin-layout__menu-toggle"
      icon="bars"
      :title="isMobileMenuOpen ? t('common.closeMenu') : t('common.openMenu')"
      :aria-label="
        isMobileMenuOpen ? t('common.closeMenu') : t('common.openMenu')
      "
      :aria-expanded="isMobileMenuOpen"
      @click="toggleMenu"
    />
    <aside
      class="admin-layout__sidebar"
      :class="{ 'admin-layout__sidebar--open': isMobileMenuOpen }"
    >
      <nav class="admin-layout__nav" role="navigation" aria-label="Admin">
        <ul>
          <li v-for="item in adminNav" :key="item.to">
            <RouterLink
              :to="item.to"
              class="admin-layout__nav-link"
              :active-class="item.to === '/admin' ? '' : undefined"
              :exact-active-class="
                item.to === '/admin' ? 'router-link-active' : undefined
              "
              @click="isMobileMenuOpen = false"
            >
              <AppIcon :name="item.icon" />
              {{ item.name }}
            </RouterLink>
          </li>
        </ul>
        <RouterLink to="/" class="admin-layout__back">
          <AppIcon name="arrow-left" spacing="right" />
          {{ t("common.cancel") }}
        </RouterLink>
      </nav>
    </aside>
    <main class="admin-layout__main" role="main">
      <RouterView />
    </main>
    <AppToast />
  </div>
</template>

<style scoped>
.admin-layout {
  display: flex;
  min-height: 100vh;
  background-color: var(--color-bg);
  color: var(--color-text);
}

.admin-layout__menu-toggle {
  display: none;
  position: fixed;
  top: var(--space-3);
  left: var(--space-3);
  z-index: 30;
}

.admin-layout__sidebar {
  width: var(--sidebar-width);
  flex-shrink: 0;
  background-color: var(--color-surface);
  border-right: 1px solid var(--color-border);
  padding: var(--space-4);
  position: sticky;
  top: 0;
  height: 100vh;
  overflow-y: auto;
}

.admin-layout__nav ul {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.admin-layout__nav a {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-2) var(--space-3);
  border-radius: var(--radius-md);
  color: var(--color-text);
  text-decoration: none;
}

.admin-layout__nav a.router-link-active {
  background-color: var(--color-surface-raised);
}

.admin-layout__nav a:hover {
  background-color: var(--color-bg-hover);
}

.admin-layout__back {
  display: inline-flex;
  align-items: center;
  margin-top: var(--space-4);
  padding: var(--space-2) var(--space-3);
  color: var(--color-text-muted);
  text-decoration: none;
}

.admin-layout__main {
  flex: 1;
  padding: var(--space-4);
  min-width: 0;
}

@media (max-width: 767px) {
  .admin-layout__menu-toggle {
    display: inline-flex;
  }

  .admin-layout__sidebar {
    position: fixed;
    top: 0;
    left: 0;
    height: 100%;
    transform: translateX(-100%);
    transition: transform var(--transition-base);
    z-index: 20;
  }

  .admin-layout__sidebar--open {
    transform: translateX(0);
  }
}

@media (prefers-reduced-motion: reduce) {
  .admin-layout__sidebar {
    transition: none;
  }
}
</style>
