import type { RemoteObject } from "@/api/remote";

/**
 * A browse-grid entry: either a local entity or a cached remote
 * (federated) object rendered in the same grid. Remote entries prefix
 * their id so keys never collide with local entity ids, and are flagged
 * ``remote`` so card slots and bulk-edit gating can branch on it.
 */
export type EntityListItem<T> =
  | {
      remote: false;
      id: string;
      owner_id?: string | null;
      entity: T;
    }
  | {
      remote: true;
      id: string;
      owner_id?: string | null;
      entity: RemoteObject;
    };

/** Display name for a remote object — falls back to its canonical URL. */
export function remoteEntityName(obj: RemoteObject): string {
  return obj.name || obj.canonical_url;
}

/**
 * Sort key for a remote object under a browse-list sort field. Remote
 * rows carry no creation timestamp — ``fetched_at`` (when the instance
 * cached them) is the closest analogue for the ``*_at`` fields.
 */
export function remoteEntitySortKey(
  obj: RemoteObject,
  sortBy: string,
): string | number | null {
  switch (sortBy) {
    case "name":
    case "title":
      return remoteEntityName(obj);
    case "artist_name":
      return obj.artist_name ?? null;
    case "album_title":
      return obj.album_name ?? null;
    case "created_at":
    case "updated_at":
      return obj.fetched_at ?? null;
    default:
      return null;
  }
}

export interface MergeEntityItemsOptions<T> {
  sortBy: string;
  sortDir: "asc" | "desc";
  /** Display name — drives the case-insensitive sort tiebreak. */
  nameOf: (item: EntityListItem<T>) => string;
  /** Active sort key for an entry; ``null`` sinks to the end. */
  keyOf: (
    item: EntityListItem<T>,
    sortBy: string,
  ) => string | number | null | undefined;
  /**
   * Identity shared by a local entity and its remote twins (e.g. a
   * normalized artist name). Entries whose key groups local and remote
   * members are clustered together, anchored at the first local member so
   * remote twins sit directly under their local match. Entries with no
   * key are never clustered.
   */
  groupKeyOf?: (item: EntityListItem<T>) => string | null | undefined;
}

function compareEntries<T>(
  a: EntityListItem<T>,
  b: EntityListItem<T>,
  options: MergeEntityItemsOptions<T>,
): number {
  const ka = options.keyOf(a, options.sortBy);
  const kb = options.keyOf(b, options.sortBy);
  if (ka == null && kb == null) {
    // fall through to the tiebreak
  } else if (ka == null) {
    return 1;
  } else if (kb == null) {
    return -1;
  } else {
    const cmp =
      typeof ka === "number" && typeof kb === "number"
        ? ka - kb
        : String(ka).localeCompare(String(kb), undefined, {
            sensitivity: "base",
            numeric: true,
          });
    if (cmp !== 0) return options.sortDir === "desc" ? -cmp : cmp;
  }
  const nameCmp = options
    .nameOf(a)
    .localeCompare(options.nameOf(b), undefined, {
      sensitivity: "base",
      numeric: true,
    });
  if (nameCmp !== 0) return nameCmp;
  if (a.remote !== b.remote) return a.remote ? 1 : -1;
  return a.id < b.id ? -1 : a.id > b.id ? 1 : 0;
}

function normalizeGroupKey(key: string | null | undefined): string | null {
  const normalized = key?.normalize("NFC").trim().replace(/\s+/g, " ");
  return normalized ? normalized.toLowerCase() : null;
}

/**
 * Cluster same-key entries so remote twins sit next to their local match.
 * Each group is emitted at the position of its first local member (or its
 * first remote member when no local twin exists); locals come first
 * inside the group.
 */
function clusterGroups<T>(
  sorted: EntityListItem<T>[],
  options: MergeEntityItemsOptions<T>,
): EntityListItem<T>[] {
  if (!options.groupKeyOf) return sorted;
  const groupKeyOf = options.groupKeyOf;
  const keys = new Map<EntityListItem<T>, string | null>();
  const groups = new Map<string, EntityListItem<T>[]>();
  for (const item of sorted) {
    const key = normalizeGroupKey(groupKeyOf(item));
    keys.set(item, key);
    if (key == null) continue;
    const members = groups.get(key);
    if (members) members.push(item);
    else groups.set(key, [item]);
  }
  const emitted = new Set<EntityListItem<T>>();
  const result: EntityListItem<T>[] = [];
  for (const item of sorted) {
    if (emitted.has(item)) continue;
    const members = groups.get(keys.get(item) ?? "");
    if (!members || members.length === 1) {
      emitted.add(item);
      result.push(item);
      continue;
    }
    const anchor = members.find((m) => !m.remote) ?? members[0];
    if (item !== anchor) continue;
    for (const member of [
      ...members.filter((m) => !m.remote),
      ...members.filter((m) => m.remote),
    ]) {
      emitted.add(member);
      result.push(member);
    }
  }
  return result;
}

/**
 * Merge local page items with cached remote objects into one sorted list
 * for the browse grids. Remote entries sort by their equivalent key and
 * cluster next to same-named local entities.
 */
export function mergeEntityItems<
  T extends { id: string; owner_id?: string | null },
>(
  localItems: T[],
  remoteItems: RemoteObject[],
  options: MergeEntityItemsOptions<T>,
): EntityListItem<T>[] {
  const items: EntityListItem<T>[] = [
    ...localItems.map((entity) => ({
      remote: false as const,
      id: entity.id,
      owner_id: entity.owner_id ?? null,
      entity,
    })),
    ...remoteItems.map((entity) => ({
      remote: true as const,
      id: `remote:${entity.id}`,
      owner_id: null,
      entity,
    })),
  ];
  const sorted = [...items].sort((a, b) => compareEntries(a, b, options));
  return clusterGroups(sorted, options);
}
