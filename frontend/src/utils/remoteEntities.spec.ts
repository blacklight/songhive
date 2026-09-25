import { describe, it, expect } from "vitest";
import {
  mergeEntityItems,
  remoteEntityName,
  remoteEntitySortKey,
  type EntityListItem,
} from "./remoteEntities";
import type { RemoteObject } from "@/api/remote";

interface LocalArtist {
  id: string;
  name: string;
  created_at?: string | null;
}

function local(id: string, name: string, createdAt?: string): LocalArtist {
  return { id, name, created_at: createdAt ?? null };
}

function remote(
  id: string,
  name: string | null,
  fetchedAt?: string,
): RemoteObject {
  return {
    id,
    canonical_url: `https://remote.example/o/${id}`,
    object_type: "Person",
    resource_type: "artist",
    domain: "remote.example",
    actor_url: "https://remote.example/users/alice",
    name,
    visibility: "public",
    unavailable: false,
    fetched_at: fetchedAt ?? null,
    url: `/remote/artist/${id}`,
  };
}

const nameMerge = (
  locals: LocalArtist[],
  remotes: RemoteObject[],
  sortBy = "name",
  sortDir: "asc" | "desc" = "asc",
) =>
  mergeEntityItems(locals, remotes, {
    sortBy,
    sortDir,
    nameOf: (item) =>
      item.remote ? remoteEntityName(item.entity) : item.entity.name,
    keyOf: (item, field) =>
      item.remote
        ? remoteEntitySortKey(item.entity, field)
        : field === "created_at"
          ? (item.entity.created_at ?? null)
          : item.entity.name,
    groupKeyOf: (item) =>
      item.remote ? remoteEntityName(item.entity) : item.entity.name,
  });

function labels(items: EntityListItem<LocalArtist>[]): string[] {
  return items.map(
    (item) =>
      `${item.remote ? "remote" : "local"}:${item.remote ? remoteEntityName(item.entity) : item.entity.name}`,
  );
}

describe("remoteEntitySortKey", () => {
  it("maps name fields to the object name and dates to fetched_at", () => {
    const obj = remote("1", "Alpha", "2024-01-01T00:00:00Z");
    expect(remoteEntitySortKey(obj, "name")).toBe("Alpha");
    expect(remoteEntitySortKey(obj, "title")).toBe("Alpha");
    expect(remoteEntitySortKey(obj, "created_at")).toBe("2024-01-01T00:00:00Z");
    expect(remoteEntitySortKey(obj, "release_year")).toBeNull();
  });

  it("maps album_title to the remote album name", () => {
    const obj = remote("1", "Alpha");
    expect(remoteEntitySortKey(obj, "album_title")).toBeNull();
    obj.album_name = "The Album";
    expect(remoteEntitySortKey(obj, "album_title")).toBe("The Album");
  });
});

describe("mergeEntityItems", () => {
  it("interleaves remote entries into a name-sorted local list", () => {
    const merged = nameMerge(
      [local("a", "Alpha"), local("c", "Charlie")],
      [remote("r1", "Beta")],
    );
    expect(labels(merged)).toEqual([
      "local:Alpha",
      "remote:Beta",
      "local:Charlie",
    ]);
  });

  it("wraps remote entries with a flagged id prefix", () => {
    const merged = nameMerge([], [remote("r1", "Beta")]);
    expect(merged[0].remote).toBe(true);
    expect(merged[0].id).toBe("remote:r1");
  });

  it("keeps local twins ahead of same-named remote entries", () => {
    const merged = nameMerge(
      [local("a", "Twin")],
      [remote("r1", "Twin"), remote("r2", "twin")],
    );
    expect(labels(merged)).toEqual([
      "local:Twin",
      "remote:Twin",
      "remote:twin",
    ]);
  });

  it("clusters remote twins under their local match under other sorts", () => {
    // created_at desc would scatter the remote twin to the end without
    // clustering — the group key pins it under the local "Beta".
    const merged = nameMerge(
      [
        local("a", "Beta", "2024-02-01T00:00:00Z"),
        local("b", "Alpha", "2024-01-01T00:00:00Z"),
      ],
      [remote("r1", "beta", "2020-01-01T00:00:00Z")],
      "created_at",
      "desc",
    );
    expect(labels(merged)).toEqual([
      "local:Beta",
      "remote:beta",
      "local:Alpha",
    ]);
  });

  it("honours descending direction", () => {
    const merged = nameMerge(
      [local("a", "Alpha"), local("c", "Charlie")],
      [remote("r1", "Beta")],
      "name",
      "desc",
    );
    expect(labels(merged)).toEqual([
      "local:Charlie",
      "remote:Beta",
      "local:Alpha",
    ]);
  });

  it("sinks entries without a sort key to the end", () => {
    // Remote entries carry no release_year — "Alpha" must not jump ahead
    // of the keyed local "Zebra" on a year sort.
    const merged = mergeEntityItems(
      [local("a", "Zebra")],
      [remote("r1", "Alpha")],
      {
        sortBy: "release_year",
        sortDir: "asc",
        nameOf: (item) =>
          item.remote ? remoteEntityName(item.entity) : item.entity.name,
        keyOf: (item) => (item.remote ? null : 1999),
      },
    );
    expect(labels(merged)).toEqual(["local:Zebra", "remote:Alpha"]);
  });
});
