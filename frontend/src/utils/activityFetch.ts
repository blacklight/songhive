import { getActivity, type ActivityResponse } from "@/api/activities";

// Cards embedding the same activity (notification rows, reaction cards)
// share one fetch; results are cached for the lifetime of the page so
// re-renders and paginated rows are free. ``null`` entries cache failures
// too — a deleted or forbidden activity won't become fetchable later.
const cache = new Map<string, Promise<ActivityResponse | null>>();

export function fetchActivityCached(
  activityId: string,
): Promise<ActivityResponse | null> {
  let pending = cache.get(activityId);
  if (!pending) {
    pending = getActivity(activityId).catch(() => null);
    cache.set(activityId, pending);
  }
  return pending;
}

export function clearActivityCache(): void {
  cache.clear();
}
