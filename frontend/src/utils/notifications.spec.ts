import { describe, it, expect } from "vitest";
import type { NotificationResponse } from "@/api/notifications";
import {
  notificationActionText,
  notificationObjectKind,
} from "./notifications";

function notification(
  type: NotificationResponse["type"],
  payload: Record<string, unknown> = {},
): Pick<NotificationResponse, "type" | "payload"> {
  return { type, payload };
}

describe("notificationObjectKind", () => {
  it("names the track for likes on Audio objects", () => {
    expect(
      notificationObjectKind(
        notification("like", {
          object_type: "Audio",
          object_activity_id: "act-1",
          item_type: "track",
        }),
      ),
    ).toBe("track");
  });

  it("names the resolved item for likes without an activity object", () => {
    expect(
      notificationObjectKind(
        notification("like", { item_type: "album", item_id: "a-1" }),
      ),
    ).toBe("album");
  });

  it("names the post for likes on Note objects", () => {
    expect(
      notificationObjectKind(
        notification("like", {
          object_type: "Note",
          object_activity_id: "act-1",
          item_type: "track",
        }),
      ),
    ).toBe("post");
  });

  it("names the post for likes on activities without an object type", () => {
    expect(
      notificationObjectKind(
        notification("boost", { object_activity_id: "act-1" }),
      ),
    ).toBe("post");
  });

  it("names the post for user items and unresolved objects", () => {
    expect(
      notificationObjectKind(notification("like", { item_type: "user" })),
    ).toBe("post");
    expect(notificationObjectKind(notification("like"))).toBe("post");
  });

  it("names the reply target, not the reply note", () => {
    expect(
      notificationObjectKind(
        notification("reply", {
          object_content: "<p>nice!</p>",
          target_item_type: "track",
        }),
      ),
    ).toBe("track");
    expect(
      notificationObjectKind(
        notification("reply", {
          target_object_type: "Note",
          target_object_activity_id: "act-1",
          target_item_type: "track",
        }),
      ),
    ).toBe("post");
  });

  it("names the track for quotes of Audio objects", () => {
    expect(
      notificationObjectKind(
        notification("quote", {
          target_object_type: "Audio",
          target_object_activity_id: "act-1",
        }),
      ),
    ).toBe("track");
  });
});

describe("notificationActionText", () => {
  it("renders the object kind in the action text", () => {
    expect(
      notificationActionText(
        notification("like", { item_type: "track", item_id: "t-1" }),
      ),
    ).toBe("liked your track");
    expect(
      notificationActionText(notification("boost", { item_type: "playlist" })),
    ).toBe("boosted your playlist");
    expect(
      notificationActionText(
        notification("reply", { target_object_type: "Note" }),
      ),
    ).toBe("replied to your post");
  });

  it("renders the shared item with its indefinite article", () => {
    expect(
      notificationActionText(
        notification("share", { item_type: "album", item_id: "a-1" }),
      ),
    ).toBe("shared an album with you");
    expect(
      notificationActionText(
        notification("share", { item_type: "track", item_id: "t-1" }),
      ),
    ).toBe("shared a track with you");
  });

  it("falls back for unknown types", () => {
    expect(
      notificationActionText({
        type: "bogus" as NotificationResponse["type"],
        payload: {},
      }),
    ).toBe("sent you a notification");
  });
});
