import {
  createRouter,
  createWebHistory,
  type RouteRecordRaw,
} from "vue-router";
import { useAuthStore } from "@/stores/auth";
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

const isRegistrationOpen = import.meta.env.VITE_REGISTRATION_OPEN !== "false";

const authChildren: RouteRecordRaw[] = [
  placeholder("login", "login", 2),
  placeholder("password-reset", "passwordReset", 2),
  placeholder("password-reset/confirm", "passwordResetConfirm", 2),
  placeholder("verify-email", "verifyEmail", 2),
];

// Interim: registration route is gated by a build-time env var until the
// backend exposes a public instance-info endpoint.
if (isRegistrationOpen) {
  authChildren.push(placeholder("register", "register", 2));
}

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
      placeholder("artists", "artists", 4),
      placeholder("artists/:id", "artist", 4),
      placeholder("albums", "albums", 4),
      placeholder("albums/:id", "album", 4),
      placeholder("albums/:id/edit", "albumEdit", 5, { requiresAuth: true }),
      placeholder("tracks", "tracks", 4),
      placeholder("tracks/:id", "track", 4),
      placeholder("tracks/:id/edit", "trackEdit", 5, { requiresAuth: true }),
      placeholder("playlists", "playlists", 4),
      placeholder("playlists/:id", "playlist", 4),
      placeholder("playlists/:id/edit", "playlistEdit", 5, {
        requiresAuth: true,
      }),
      placeholder("libraries", "libraries", 4),
      placeholder("libraries/:id", "library", 4),
      placeholder("libraries/:id/edit", "libraryEdit", 5, {
        requiresAuth: true,
      }),
      placeholder("history", "history", 4, { requiresAuth: true }),
      placeholder("favorites", "favorites", 4, { requiresAuth: true }),
      placeholder("files", "files", 5, { requiresAuth: true }),
      placeholder("files/:id", "file", 5, { requiresAuth: true }),
      placeholder("files/:id/edit", "fileEdit", 5, { requiresAuth: true }),
      placeholder("radio", "radio", 5),
      placeholder("about", "about", 6),
      placeholder("share/:token", "share", 6),
      placeholder("profile", "profile", 2, { requiresAuth: true }),
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

// Fail closed: no user can reach /admin/* until the backend exposes role on
// UserResponse. This is intentional and must be documented.
router.beforeEach(async (to) => {
  const authStore = useAuthStore();
  await authStore.bootstrap();

  if (to.meta.requiresAuth && !authStore.isAuthenticated) {
    return { path: "/login", query: { redirect: to.fullPath } };
  }

  if (to.meta.requiresAdmin && !authStore.isAdmin) {
    return { path: "/403" };
  }

  return true;
});

export default router;
