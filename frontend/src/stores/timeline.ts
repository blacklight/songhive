import { ref, type Ref } from "vue";
import { defineStore } from "pinia";
import {
  listTimeline,
  type TimelineMode,
  type TimelineScope,
} from "@/api/timeline";
import type { ActivityResponse } from "@/api/activities";
import { getApiErrorMessage } from "@/api/client";

// The user's last explicit scope choice. The initial default ("mine" when
// the user has any visible own activity, otherwise "instance") is
// recomputed on each visit and only becomes sticky once the user picks a
// scope themselves.
const STORAGE_KEY = "songhive.home.timelineScope";

export function readStoredScope(): TimelineScope | null {
  try {
    const value = localStorage.getItem(STORAGE_KEY);
    return value === "mine" || value === "instance" || value === "federated"
      ? value
      : null;
  } catch {
    return null;
  }
}

export const useTimelineStore = defineStore("timeline", () => {
  const items: Ref<ActivityResponse[]> = ref([]);
  const scope: Ref<TimelineScope> = ref("instance");
  const mode: Ref<TimelineMode> = ref("posts");
  const nextCursor: Ref<string | null> = ref(null);
  const hasMore = ref(false);
  const loading = ref(false);
  const loadingMore = ref(false);
  const error: Ref<string | null> = ref(null);
  const initialized = ref(false);

  function errorMessage(err: unknown): string {
    return (
      getApiErrorMessage(err) ||
      (err instanceof Error ? err.message : "Unknown error")
    );
  }

  async function fetchPage(
    cursor: string | null,
    append: boolean,
  ): Promise<void> {
    const page = await listTimeline({
      scope: scope.value,
      mode: mode.value,
      cursor: cursor ?? undefined,
    });
    items.value = append
      ? [...items.value, ...page.activities]
      : page.activities;
    nextCursor.value = page.next_cursor ?? null;
    hasMore.value = !!page.next_cursor;
  }

  async function load(
    nextScope?: TimelineScope,
    nextMode?: TimelineMode,
  ): Promise<void> {
    if (loading.value) return;
    scope.value = nextScope ?? scope.value;
    mode.value = nextMode ?? mode.value;
    loading.value = true;
    error.value = null;
    try {
      await fetchPage(null, false);
    } catch (err) {
      error.value = errorMessage(err);
      items.value = [];
      hasMore.value = false;
      nextCursor.value = null;
    } finally {
      loading.value = false;
    }
  }

  /**
   * First load of the home feed. A stored explicit choice always wins
   * (``mine`` only when authenticated); otherwise authenticated users
   * start on ``mine`` and fall back to the instance feed when they have
   * no visible activity of their own yet, and anonymous visitors start on
   * the public ``instance`` scope.
   */
  async function init(authenticated: boolean): Promise<void> {
    if (initialized.value) return;
    initialized.value = true;
    const stored = readStoredScope();
    // "mine" is authenticated-only — a stale choice must not 401 the feed
    // after logout.
    const initial: TimelineScope =
      stored && (authenticated || stored !== "mine")
        ? stored
        : authenticated
          ? "mine"
          : "instance";
    await load(initial);
    if (
      !stored &&
      authenticated &&
      scope.value === "mine" &&
      items.value.length === 0 &&
      !error.value
    ) {
      await load("instance");
    }
  }

  async function setScope(next: TimelineScope): Promise<void> {
    if (next === scope.value) return;
    try {
      localStorage.setItem(STORAGE_KEY, next);
    } catch {
      // Persistence is best-effort; private-mode failures are fine.
    }
    await load(next);
  }

  async function setMode(next: TimelineMode): Promise<void> {
    if (next === mode.value) return;
    await load(scope.value, next);
  }

  async function loadMore(): Promise<void> {
    if (loadingMore.value || !hasMore.value || !nextCursor.value) return;
    loadingMore.value = true;
    error.value = null;
    try {
      await fetchPage(nextCursor.value, true);
    } catch (err) {
      error.value = errorMessage(err);
      hasMore.value = false;
    } finally {
      loadingMore.value = false;
    }
  }

  async function refresh(): Promise<void> {
    await load(scope.value);
  }

  function $reset(): void {
    items.value = [];
    scope.value = "instance";
    mode.value = "posts";
    nextCursor.value = null;
    hasMore.value = false;
    error.value = null;
    initialized.value = false;
  }

  return {
    items,
    scope,
    mode,
    nextCursor,
    hasMore,
    loading,
    loadingMore,
    error,
    initialized,
    init,
    load,
    setScope,
    setMode,
    loadMore,
    refresh,
    $reset,
  };
});
