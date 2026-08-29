import {
  createRouter,
  createWebHistory,
  type RouteRecordRaw,
} from "vue-router";
import { useAuthStore } from "@/stores/auth";
import { useInstanceStore } from "@/stores/instance";
import AppLayout from "@/layouts/AppLayout.vue";
import AuthLayout from "@/layouts/AuthLayout.vue";
import AdminLayout from "@/layouts/AdminLayout.vue";

function placeholder(
  path: string,
  name: string,
  phase: number,
  meta?: Record<string, unknown>,
): RouteRecordRaw {
  return {
    path,
    component: () => import("@/views/PlaceholderView.vue"),
    props: { name, phase },
    meta,
  } as RouteRecordRaw;
}

const authChildren: RouteRecordRaw[] = [
  {
    path: "login",
    name: "login",
    component: () => import("@/views/LoginView.vue"),
  },
  {
    path: "register",
    name: "register",
    component: () => import("@/views/RegisterView.vue"),
  },
  {
    path: "password-reset",
    name: "passwordReset",
    component: () => import("@/views/PasswordResetView.vue"),
  },
  {
    path: "password-reset/confirm",
    name: "passwordResetConfirm",
    component: () => import("@/views/PasswordResetConfirmView.vue"),
  },
  {
    path: "verify-email",
    name: "verifyEmail",
    component: () => import("@/views/VerifyEmailView.vue"),
  },
];

const routes: RouteRecordRaw[] = [
  {
    path: "/",
    component: AppLayout,
    children: [
      {
        path: "",
        name: "home",
        component: () => import("@/views/HomeView.vue"),
      },
      {
        path: "artists",
        name: "artists",
        component: () => import("@/views/ArtistsView.vue"),
      },
      {
        path: "artists/:id",
        name: "artist",
        component: () => import("@/views/ArtistView.vue"),
      },
      {
        path: "artists/:id/edit",
        name: "artistEdit",
        component: () => import("@/views/ArtistEditView.vue"),
        meta: { requiresAuth: true },
      },
      {
        path: "albums",
        name: "albums",
        component: () => import("@/views/AlbumsView.vue"),
      },
      {
        path: "albums/:id",
        name: "album",
        component: () => import("@/views/AlbumView.vue"),
      },
      {
        path: "albums/:id/edit",
        name: "albumEdit",
        component: () => import("@/views/AlbumEditView.vue"),
        meta: { requiresAuth: true },
      },
      {
        path: "tracks",
        name: "tracks",
        component: () => import("@/views/TracksView.vue"),
      },
      {
        path: "tracks/:id",
        name: "track",
        component: () => import("@/views/TrackView.vue"),
      },
      {
        path: "tracks/:id/edit",
        name: "trackEdit",
        component: () => import("@/views/TrackEditView.vue"),
        meta: { requiresAuth: true },
      },
      {
        path: "playlists",
        name: "playlists",
        component: () => import("@/views/PlaylistsView.vue"),
      },
      {
        path: "playlists/:id",
        name: "playlist",
        component: () => import("@/views/PlaylistView.vue"),
      },
      {
        path: "playlists/:id/edit",
        name: "playlistEdit",
        component: () => import("@/views/PlaylistEditView.vue"),
        meta: { requiresAuth: true },
      },
      {
        path: "libraries",
        name: "libraries",
        component: () => import("@/views/LibraryView.vue"),
      },
      {
        path: "libraries/:id",
        name: "library",
        component: () => import("@/views/LibraryDetailView.vue"),
      },
      {
        path: "libraries/:id/edit",
        name: "libraryEdit",
        component: () => import("@/views/LibraryEditView.vue"),
        meta: { requiresAuth: true },
      },
      // History and favorites are Phase 5 views that require authentication.
      {
        path: "history",
        name: "history",
        component: () => import("@/views/HistoryView.vue"),
        meta: { requiresAuth: true },
      },
      {
        path: "favorites",
        name: "favorites",
        component: () => import("@/views/FavoritesView.vue"),
        meta: { requiresAuth: true },
      },
      {
        path: "files",
        name: "files",
        component: () => import("@/views/FilesView.vue"),
        meta: { requiresAuth: true },
      },
      {
        path: "files/:id",
        name: "file",
        component: () => import("@/views/FileDetailView.vue"),
        meta: { requiresAuth: true },
      },
      {
        path: "hashtags",
        name: "hashtags",
        component: () => import("@/views/HashtagsView.vue"),
      },
      {
        path: "hashtags/:name",
        name: "hashtag",
        component: () => import("@/views/HashtagView.vue"),
      },
      {
        path: "radio",
        name: "radio",
        component: () => import("@/views/RadioView.vue"),
      },
      {
        path: "about",
        name: "about",
        component: () => import("@/views/AboutView.vue"),
      },
      {
        path: "share/:token",
        name: "share",
        component: () => import("@/views/ShareView.vue"),
      },
      {
        path: "profile",
        name: "profile",
        component: () => import("@/views/ProfileView.vue"),
        meta: { requiresAuth: true },
      },
    ],
  },
  {
    path: "/",
    component: AuthLayout,
    children: authChildren,
  },
  {
    path: "/admin",
    component: AdminLayout,
    meta: { requiresAuth: true, requiresAdmin: true },
    children: [
      placeholder("", "adminDashboard", 6),
      placeholder("users", "adminUsers", 6),
      placeholder("users/:id", "adminUser", 6),
      placeholder("users/invite", "adminUserInvite", 6),
      placeholder("settings", "adminSettings", 6),
      placeholder("reports", "adminReports", 6),
      placeholder("invites", "adminInvites", 6),
      placeholder("audit", "adminAudit", 6),
      placeholder("storage", "adminStorage", 6),
    ],
  },
  { path: "/403", component: () => import("@/views/ForbiddenView.vue") },
  { path: "/404", component: () => import("@/views/NotFoundView.vue") },
  {
    path: "/:pathMatch(.*)*",
    component: () => import("@/views/NotFoundView.vue"),
  },
];

const router = createRouter({
  history: createWebHistory(),
  routes,
});

function getQueryCode(raw: unknown): string {
  if (Array.isArray(raw)) return raw[0] ?? "";
  return typeof raw === "string" ? raw : "";
}

// Fail closed: no user can reach /admin/* until the backend exposes role on
// UserResponse. This is intentional and must be documented.
router.beforeEach(async (to) => {
  const authStore = useAuthStore();
  await authStore.bootstrap();

  if (to.name === "register") {
    const instanceStore = useInstanceStore();
    await instanceStore.load();
    if (!instanceStore.registrations) {
      const code = getQueryCode(to.query.invite_code ?? to.query.code);
      if (!code || !instanceStore.invitesEnabled) {
        return authStore.isAuthenticated ? { path: "/" } : { path: "/login" };
      }
    }
  }

  if (to.meta.requiresAuth && !authStore.isAuthenticated) {
    return { path: "/login", query: { redirect: to.fullPath } };
  }

  if (to.meta.requiresAdmin && !authStore.isAdmin) {
    return { path: "/403" };
  }

  return true;
});

export default router;
