import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import * as statusesApi from "@/api/statuses";
import * as searchApi from "@/api/search";
import * as filesApi from "@/api/files";
import { useAuthStore } from "@/stores/auth";
import StatusComposer from "./StatusComposer.vue";

vi.mock("@/api/statuses", async (importOriginal) => {
  const actual = await importOriginal<typeof statusesApi>();
  return { ...actual, createStatus: vi.fn() };
});

vi.mock("@/api/search", () => ({
  searchPreview: vi.fn(),
}));

vi.mock("@/api/files", () => ({
  uploadFile: vi.fn(),
}));

const createStatus = vi.mocked(statusesApi.createStatus);
const searchPreview = vi.mocked(searchApi.searchPreview);
const uploadFile = vi.mocked(filesApi.uploadFile);

function createActivity() {
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
    published_at: "2026-01-01T00:00:00Z",
    mentions: [],
  } as never;
}

function mountComposer(props: Record<string, unknown> = {}) {
  return mount(StatusComposer, { props });
}

async function typeText(
  wrapper: ReturnType<typeof mountComposer>,
  value: string,
) {
  const textarea = wrapper.find("textarea");
  await textarea.setValue(value);
  const el = textarea.element as HTMLTextAreaElement;
  el.setSelectionRange(value.length, value.length);
  await textarea.trigger("input");
}

describe("StatusComposer", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.useFakeTimers();
    createStatus.mockResolvedValue(createActivity());
    searchPreview.mockResolvedValue({ query: "", sections: [] });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("keeps the submit button disabled while the composer is empty", () => {
    const wrapper = mountComposer();
    const submit = wrapper.find('button[type="submit"]');
    expect(submit.attributes("disabled")).toBeDefined();
  });

  it("posts a status with markdown and the browser locale by default", async () => {
    const wrapper = mountComposer();
    await typeText(wrapper, "Hello **world**");
    await wrapper.find("form").trigger("submit");
    await flushPromises();

    expect(createStatus).toHaveBeenCalledWith({
      status: "Hello **world**",
      content_type: "text/markdown",
      visibility: "public",
      language: navigator.language,
      media_ids: [],
      track_ids: [],
    });
    expect(wrapper.emitted("submitted")).toHaveLength(1);
    expect(wrapper.find("textarea").element).toHaveProperty("value", "");
  });

  it("defaults the format to the user's stored preference", () => {
    const authStore = useAuthStore();
    authStore.user = {
      id: "user-1",
      username: "alice",
      status_content_type: "text/plain",
    } as never;
    const wrapper = mountComposer();
    const selects = wrapper.findAll("select");
    expect((selects[0].element as HTMLSelectElement).value).toBe("text/plain");
  });

  it("autocompletes a local user mention after typing @", async () => {
    searchPreview.mockResolvedValue({
      query: "al",
      sections: [
        {
          entity: "users",
          total: 1,
          items: [
            {
              type: "user",
              id: "alice",
              name: "alice",
              title: "Alice",
              subtitle: "alice",
              url: "/@alice",
            },
          ],
        },
      ],
    });
    const wrapper = mountComposer();
    await typeText(wrapper, "hi @al");
    vi.advanceTimersByTime(400);
    await flushPromises();

    expect(searchPreview).toHaveBeenCalledWith("al", ["users"], 5);
    const item = wrapper.find(".search-suggestions__item");
    expect(item.exists()).toBe(true);
    await item.trigger("click");
    await flushPromises();

    const textarea = wrapper.find("textarea").element as HTMLTextAreaElement;
    expect(textarea.value).toBe("hi @alice ");
  });

  it("attaches a track picked from the track search", async () => {
    searchPreview.mockResolvedValue({
      query: "song",
      sections: [
        {
          entity: "tracks",
          total: 1,
          items: [
            {
              type: "track",
              id: "track-1",
              title: "Song",
              subtitle: "Band",
              url: "/tracks/track-1",
            },
          ],
        },
      ],
    });
    const wrapper = mountComposer();
    await typeText(wrapper, "listen to this");

    const trackButton = wrapper.find('button[aria-label="Attach a track"]');
    await trackButton.trigger("click");
    const searchInput = wrapper.find('input[type="search"]');
    await searchInput.setValue("song");
    vi.advanceTimersByTime(400);
    await flushPromises();

    const item = wrapper.find(".search-suggestions__item");
    expect(item.exists()).toBe(true);
    await item.trigger("click");
    await flushPromises();

    expect(wrapper.text()).toContain("Song");
    await wrapper.find("form").trigger("submit");
    await flushPromises();

    expect(createStatus).toHaveBeenCalledWith(
      expect.objectContaining({ track_ids: ["track-1"] }),
    );
  });

  it("uploads a file attachment as private and sends its id", async () => {
    // Real timers so the mocked upload's promise chain settles with
    // flushPromises; no debounced fetch is involved in this test.
    vi.useRealTimers();
    uploadFile.mockResolvedValue({ id: "file-1" } as never);
    const wrapper = mountComposer();
    await typeText(wrapper, "with a file");

    const file = new File(["data"], "pic.png", { type: "image/png" });
    const input = wrapper.find('input[type="file"]');
    Object.defineProperty(input.element, "files", { value: [file] });
    await input.trigger("change");
    await flushPromises();
    await flushPromises();

    expect(uploadFile).toHaveBeenCalledWith(
      file,
      "private",
      expect.any(Function),
      undefined,
      undefined,
      undefined,
      false,
    );
    expect(wrapper.text()).toContain("pic.png");

    await wrapper.find("form").trigger("submit");
    await flushPromises();
    expect(createStatus).toHaveBeenCalledWith(
      expect.objectContaining({ media_ids: ["file-1"] }),
    );
  });

  it("uses the custom submit handler when provided", async () => {
    const submit = vi.fn().mockResolvedValue({ track_id: "t1" });
    const wrapper = mountComposer({ submit, allowEmpty: true });
    await wrapper.find("form").trigger("submit");
    await flushPromises();
    expect(submit).toHaveBeenCalled();
    expect(createStatus).not.toHaveBeenCalled();
    expect(wrapper.emitted("submitted")).toHaveLength(1);
  });

  it("surfaces an API error when submission fails", async () => {
    createStatus.mockRejectedValue(new Error("boom"));
    const wrapper = mountComposer();
    await typeText(wrapper, "hello");
    await wrapper.find("form").trigger("submit");
    await flushPromises();
    expect(wrapper.find('[role="alert"]').exists()).toBe(true);
    expect(wrapper.emitted("submitted")).toBeUndefined();
  });

  it("seeds text, format, visibility, and language from initial props", async () => {
    const submit = vi.fn().mockResolvedValue({});
    const wrapper = mountComposer({
      submit,
      initialStatus: "existing **text**",
      initialContentType: "text/plain",
      initialVisibility: "followers",
      initialLanguage: "fr",
    });

    expect(wrapper.find("textarea").element).toHaveProperty(
      "value",
      "existing **text**",
    );
    const selects = wrapper.findAll("select");
    expect((selects[0].element as HTMLSelectElement).value).toBe("text/plain");
    expect((selects[1].element as HTMLSelectElement).value).toBe("followers");
    expect(
      (wrapper.find("#status-language").element as HTMLInputElement).value,
    ).toBe("fr");

    await wrapper.find("form").trigger("submit");
    await flushPromises();
    expect(submit).toHaveBeenCalledWith(
      expect.objectContaining({
        status: "existing **text**",
        content_type: "text/plain",
        visibility: "followers",
        language: "fr",
      }),
    );
  });

  it("seeds attachments and submits the remaining ids after removal", async () => {
    const submit = vi.fn().mockResolvedValue({});
    const wrapper = mountComposer({
      submit,
      initialStatus: "keep",
      initialMedia: [{ id: "file-1", name: "cover.png" }],
      initialTracks: [{ id: "track-1", title: "Song", subtitle: "Band" }],
    });

    expect(wrapper.text()).toContain("cover.png");
    expect(wrapper.text()).toContain("Song");

    // Remove the file, keep the track.
    const chips = wrapper.findAll(".status-composer__attachment");
    await chips[0].find("button").trigger("click");
    expect(wrapper.text()).not.toContain("cover.png");

    await wrapper.find("form").trigger("submit");
    await flushPromises();
    expect(submit).toHaveBeenCalledWith(
      expect.objectContaining({
        media_ids: [],
        track_ids: ["track-1"],
      }),
    );
  });

  it("treats an explicit null initial language as empty", () => {
    const wrapper = mountComposer({ initialLanguage: null });
    expect(
      (wrapper.find("#status-language").element as HTMLInputElement).value,
    ).toBe("");
  });
});
