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
        path: "search",
        name: "search",
        component: () => import("@/views/SearchView.vue"),
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
      // Activity feeds are public reads: the endpoint serves `public`
      // activities to anonymous users, matching the entity detail routes.
      {
        path: "tracks/:id/activities",
        name: "trackActivities",
        component: () => import("@/views/EntityActivitiesView.vue"),
        props: (route) => ({
          entityType: "track",
          entityId: String(route.params.id),
        }),
      },
      {
        path: "albums/:id/activities",
        name: "albumActivities",
        component: () => import("@/views/EntityActivitiesView.vue"),
        props: (route) => ({
          entityType: "album",
          entityId: String(route.params.id),
        }),
      },
      {
        path: "artists/:id/activities",
        name: "artistActivities",
        component: () => import("@/views/EntityActivitiesView.vue"),
        props: (route) => ({
          entityType: "artist",
          entityId: String(route.params.id),
        }),
      },
      {
        path: "playlists/:id/activities",
        name: "playlistActivities",
        component: () => import("@/views/EntityActivitiesView.vue"),
        props: (route) => ({
          entityType: "playlist",
          entityId: String(route.params.id),
        }),
      },
      {
        path: "libraries/:id/activities",
        name: "libraryActivities",
        component: () => import("@/views/EntityActivitiesView.vue"),
        props: (route) => ({
          entityType: "library",
          entityId: String(route.params.id),
        }),
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
        path: "notifications",
        name: "notifications",
        component: () => import("@/views/NotificationsView.vue"),
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
        path: "tags",
        name: "tags",
        component: () => import("@/views/TagsView.vue"),
      },
      {
        path: "tags/:name",
        name: "tag",
        component: () => import("@/views/TagView.vue"),
      },
      {
        path: "genres",
        name: "genres",
        component: () => import("@/views/GenresView.vue"),
      },
      {
        path: "genres/:name",
        name: "genre",
        component: () => import("@/views/GenreView.vue"),
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
        path: "settings",
        name: "settings",
        component: () => import("@/views/ProfileView.vue"),
        meta: { requiresAuth: true },
      },
      {
        path: "settings/external-libraries",
        name: "settingsExternalLibraries",
        component: () => import("@/views/ExternalLibrariesView.vue"),
        meta: { requiresAuth: true },
      },
      {
        path: "settings/external-libraries/new",
        name: "settingsExternalLibraryCreate",
        component: () => import("@/views/ExternalLibraryEditView.vue"),
        meta: { requiresAuth: true },
      },
      {
        path: "settings/external-libraries/:id",
        name: "settingsExternalLibraryEdit",
        component: () => import("@/views/ExternalLibraryEditView.vue"),
        meta: { requiresAuth: true },
      },
      {
        path: "users",
        name: "usersDirectory",
        component: () => import("@/views/UsersDirectoryView.vue"),
      },
      {
        path: "users/:username",
        name: "userRedirect",
        redirect: (to) => ({ path: `/@${to.params.username}` }),
      },
      {
        path: "@:username",
        component: () => import("@/views/UserProfileView.vue"),
        children: [
          {
            path: "",
            name: "userProfile",
            redirect: { name: "userProfilePosts" },
          },
          {
            path: "posts",
            name: "userProfilePosts",
            component: () => import("@/views/UserProfileTabView.vue"),
            props: { tab: "posts" },
          },
          {
            path: "activity",
            name: "userProfileActivity",
            component: () => import("@/views/UserProfileTabView.vue"),
            props: { tab: "activity" },
          },
          {
            path: "tracks",
            name: "userProfileTracks",
            component: () => import("@/views/UserProfileTabView.vue"),
            props: { tab: "tracks" },
          },
          {
            path: "albums",
            name: "userProfileAlbums",
            component: () => import("@/views/UserProfileTabView.vue"),
            props: { tab: "albums" },
          },
          {
            path: "libraries",
            name: "userProfileLibraries",
            component: () => import("@/views/UserProfileTabView.vue"),
            props: { tab: "libraries" },
          },
          {
            path: "playlists",
            name: "userProfilePlaylists",
            component: () => import("@/views/UserProfileTabView.vue"),
            props: { tab: "playlists" },
          },
        ],
      },
      {
        path: "@:username/followers",
        name: "userFollowers",
        component: () => import("@/views/UserFollowersView.vue"),
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
      {
        path: "",
        name: "adminDashboard",
        component: () => import("@/views/admin/DashboardView.vue"),
      },
      {
        path: "users",
        name: "adminUsers",
        component: () => import("@/views/admin/UsersView.vue"),
      },
      {
        path: "settings",
        name: "adminSettings",
        component: () => import("@/views/admin/SettingsView.vue"),
      },
      {
        path: "reports",
        name: "adminReports",
        component: () => import("@/views/admin/ReportsView.vue"),
      },
      {
        path: "invites",
        name: "adminInvites",
        component: () => import("@/views/admin/InvitesView.vue"),
      },
      {
        path: "audit",
        name: "adminAudit",
        component: () => import("@/views/admin/AuditView.vue"),
      },
      {
        path: "tasks",
        name: "adminTasks",
        component: () => import("@/views/admin/TasksView.vue"),
      },
      {
        path: "celery",
        name: "adminCelery",
        component: () => import("@/views/admin/CeleryView.vue"),
      },
      {
        path: "external-libraries",
        name: "adminExternalLibraries",
        component: () => import("@/views/ExternalLibrariesView.vue"),
        meta: { requiresAuth: true, requiresAdmin: true },
      },
      {
        path: "external-libraries/new",
        name: "adminExternalLibraryCreate",
        component: () => import("@/views/ExternalLibraryEditView.vue"),
        meta: { requiresAuth: true, requiresAdmin: true },
      },
      {
        path: "external-libraries/:id",
        name: "adminExternalLibraryEdit",
        component: () => import("@/views/ExternalLibraryEditView.vue"),
        meta: { requiresAuth: true, requiresAdmin: true },
      },
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
