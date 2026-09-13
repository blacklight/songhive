import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import * as activitiesApi from "@/api/activities";
import type { ActivityResponse } from "@/api/activities";
import { useAuthStore } from "@/stores/auth";
import ActivityEditModal from "./ActivityEditModal.vue";

vi.mock("@/api/activities", () => ({
  listEntityActivities: vi.fn(),
  likeActivity: vi.fn(),
  updateActivity: vi.fn(),
  deleteActivity: vi.fn(),
}));

vi.mock("@/api/search", () => ({
  searchPreview: vi.fn().mockResolvedValue({ query: "", sections: [] }),
}));

vi.mock("@/api/files", () => ({
  uploadFile: vi.fn(),
}));

const updateActivity = vi.mocked(activitiesApi.updateActivity);

function createActivity(
  overrides: Partial<ActivityResponse> = {},
): ActivityResponse {
  return {
    id: "a1",
    entity_type: "user",
    entity_id: "user-1",
    activity_type: "create",
    source_type: "local",
    source_actor: "urn:songhive:user:alice",
    source_id: "https://example.com/users/alice/objects/o1",
    owner_user_id: "user-1",
    visibility: "public",
    content: "<p>Hello</p>",
    content_source: "Hello",
    content_type: "text/markdown",
    language: "en",
    attachments: [],
    published_at: "2026-01-01T00:00:00Z",
    mentions: [],
    ...overrides,
  };
}

function mountModal(overrides: Partial<ActivityResponse> = {}) {
  return mount(ActivityEditModal, {
    attachTo: document.body,
    props: { open: true, activity: createActivity(overrides) },
  });
}

// AppModal teleports to <body> — query the document rather than the
// wrapper, mirroring the ShareDialog specs.
function bodyButtons(): HTMLButtonElement[] {
  return Array.from(document.body.querySelectorAll("button"));
}

async function submitForm() {
  const save = bodyButtons().find((b) => b.type === "submit");
  save?.closest("form")?.dispatchEvent(new Event("submit"));
  await flushPromises();
}

describe("ActivityEditModal", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    const authStore = useAuthStore();
    authStore.status = "authenticated";
    authStore.user = { id: "user-1", username: "alice" } as never;
    updateActivity.mockResolvedValue(createActivity());
  });

  afterEach(() => {
    document.body.innerHTML = "";
  });

  it("prefills the composer from the activity", () => {
    mountModal({
      content_source: "Hello **there**",
      visibility: "followers",
      language: "fr",
    });

    expect(
      (document.body.querySelector("textarea") as HTMLTextAreaElement).value,
    ).toBe("Hello **there**");
    const selects = Array.from(
      document.body.querySelectorAll("select"),
    ) as HTMLSelectElement[];
    expect(selects[0].value).toBe("text/markdown");
    expect(selects[1].value).toBe("followers");
    expect(
      (document.body.querySelector("#status-language") as HTMLInputElement)
        .value,
    ).toBe("fr");
  });

  it("maps marked attachments to chips and submits their ids", async () => {
    const wrapper = mountModal({
      attachments: [
        {
          type: "Document",
          mediaType: "image/png",
          url: "/api/v1/files/f1/download",
          name: "cover.png",
          "songhive:fileId": "f1",
        },
        {
          type: "Audio",
          mediaType: "audio/mpeg",
          url: "/api/v1/files/fa/download",
          name: "Artist - Song",
          "songhive:trackId": "t1",
        },
        // Entity-owned doc (e.g. a shared track) — no marker, not editable.
        { type: "Audio", url: "https://x/stream", name: "Owned" },
      ],
    });

    const chips = document.body.querySelectorAll(
      ".status-composer__attachment",
    );
    const chipText = Array.from(chips)
      .map((c) => c.textContent)
      .join(" ");
    expect(chipText).toContain("cover.png");
    expect(chipText).toContain("Artist - Song");
    expect(chipText).not.toContain("Owned");

    const textarea = document.body.querySelector(
      "textarea",
    ) as HTMLTextAreaElement;
    textarea.value = "edited";
    textarea.dispatchEvent(new Event("input"));
    await submitForm();

    expect(updateActivity).toHaveBeenCalledWith(
      "a1",
      expect.objectContaining({
        content: "edited",
        content_type: "text/markdown",
        language: "en",
        media_ids: ["f1"],
        track_ids: ["t1"],
      }),
    );
    expect(wrapper.emitted("close")).toHaveLength(1);
  });

  it("sends empty id lists when all attachments are removed", async () => {
    mountModal({
      attachments: [
        {
          type: "Document",
          url: "/api/v1/files/f1/download",
          name: "cover.png",
          "songhive:fileId": "f1",
        },
      ],
    });

    const remove = document.body.querySelector(
      ".status-composer__attachment button",
    ) as HTMLButtonElement;
    remove.click();
    await flushPromises();

    const textarea = document.body.querySelector(
      "textarea",
    ) as HTMLTextAreaElement;
    textarea.value = "no attachments left";
    textarea.dispatchEvent(new Event("input"));
    await submitForm();

    expect(updateActivity).toHaveBeenCalledWith(
      "a1",
      expect.objectContaining({ media_ids: [], track_ids: [] }),
    );
  });

  it("requires content or attachments on user statuses", () => {
    mountModal({ content_source: "" });
    const submit = bodyButtons().find((b) => b.type === "submit");
    expect(submit?.disabled).toBe(true);
  });
});
