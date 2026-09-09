import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { setActivePinia, createPinia } from "pinia";
import { i18n } from "@/i18n";
import { useToastStore } from "@/stores/toast";
import * as tracksApi from "@/api/tracks";
import * as tagsApi from "@/api/tags";
import type { TrackResponse } from "@/api/tracks";
import TagInput from "@/components/tags/TagInput.vue";
import BulkTrackEditModal from "./BulkTrackEditModal.vue";

vi.mock("@/api/tracks", () => ({
  getTrack: vi.fn(),
  updateTrack: vi.fn(),
}));

vi.mock("@/api/tags", () => ({
  addTags: vi.fn().mockResolvedValue(undefined),
  removeTag: vi.fn().mockResolvedValue(undefined),
}));

function makeTrack(overrides: Partial<TrackResponse> = {}): TrackResponse {
  return {
    id: "track-1",
    title: "Song One",
    artist_id: "artist-1",
    album_id: "album-1",
    track_number: 1,
    disc_number: null,
    release_year: 2001,
    genre: "rock",
    visibility: "public",
    filename: "one.mp3",
    is_external: false,
    can_rename_source: true,
    tags: ["live"],
    genres: ["rock"],
    artist: { id: "artist-1", name: "Shared Artist" },
    album: {
      id: "album-1",
      title: "Shared Album",
      artist_id: "artist-1",
      visibility: "public",
    },
    ...overrides,
  };
}

function mountModal(
  trackIds: string[],
  open = false,
): ReturnType<typeof mount> {
  return mount(BulkTrackEditModal, {
    props: { open, trackIds },
    attachTo: document.body,
  });
}

async function openModal(wrapper: ReturnType<typeof mount>) {
  await wrapper.setProps({ open: true });
  await flushPromises();
}

function formInputs(): NodeListOf<HTMLInputElement> {
  return document.body.querySelectorAll(
    'input[type="text"], input[type="number"]',
  );
}

function setInput(input: HTMLInputElement, value: string) {
  input.value = value;
  input.dispatchEvent(new Event("input"));
}

async function clickSave() {
  const saveButton = Array.from(document.body.querySelectorAll("button")).find(
    (b) => b.textContent === i18n.global.t("common.save"),
  );
  expect(saveButton).toBeDefined();
  saveButton?.click();
  await flushPromises();
}

describe("BulkTrackEditModal", () => {
  let wrapper: ReturnType<typeof mount> | undefined;

  beforeEach(() => {
    setActivePinia(createPinia());
    vi.clearAllMocks();
  });

  afterEach(() => {
    wrapper?.unmount();
    document.body.innerHTML = "";
  });

  it("fetches every selected track and pre-fills only shared values", async () => {
    const tracks = [
      makeTrack({ id: "track-1", title: "Song One" }),
      makeTrack({
        id: "track-2",
        title: "Song Two",
        track_number: 2,
        filename: "two.mp3",
      }),
    ];
    for (const track of tracks) {
      vi.mocked(tracksApi.getTrack).mockResolvedValueOnce(track);
    }

    wrapper = mountModal(["track-1", "track-2"]);
    await openModal(wrapper);

    expect(tracksApi.getTrack).toHaveBeenCalledWith("track-1", {
      include: "artist,album,tags,genres",
    });
    expect(tracksApi.getTrack).toHaveBeenCalledWith("track-2", {
      include: "artist,album,tags,genres",
    });

    const inputs = formInputs();
    // Order: title, artist, album, genre input, track number, disc number,
    // release year, filename, tag input.
    expect(inputs[0]!.value).toBe(""); // titles differ
    expect(inputs[1]!.value).toBe("Shared Artist");
    expect(inputs[2]!.value).toBe("Shared Album");
    expect(inputs[4]!.value).toBe(""); // track numbers differ
    expect(inputs[6]!.value).toBe("2001"); // shared release year
    expect(inputs[7]!.value).toBe(""); // filenames differ (one.mp3 vs other)

    const select = document.body.querySelector("select") as HTMLSelectElement;
    expect(select.value).toBe("public");
  });

  it("applies only the fields that were set to every selected track", async () => {
    const tracks = [
      makeTrack({ id: "track-1", title: "Song One" }),
      makeTrack({ id: "track-2", title: "Song Two" }),
    ];
    for (const track of tracks) {
      vi.mocked(tracksApi.getTrack).mockResolvedValueOnce(track);
    }
    vi.mocked(tracksApi.updateTrack).mockResolvedValue(tracks[0]);

    wrapper = mountModal(["track-1", "track-2"]);
    await openModal(wrapper);

    const inputs = formInputs();
    setInput(inputs[1]!, "New Artist");
    await flushPromises();

    await clickSave();

    expect(tracksApi.updateTrack).toHaveBeenCalledTimes(2);
    expect(tracksApi.updateTrack).toHaveBeenCalledWith("track-1", {
      artist_name: "New Artist",
    });
    expect(tracksApi.updateTrack).toHaveBeenCalledWith("track-2", {
      artist_name: "New Artist",
    });
    expect(tagsApi.addTags).not.toHaveBeenCalled();
    expect(tagsApi.removeTag).not.toHaveBeenCalled();

    expect(wrapper.emitted("saved")).toBeTruthy();
    expect(wrapper.emitted("close")).toBeTruthy();

    const toast = useToastStore();
    expect(toast.toasts.at(-1)?.type).toBe("success");
  });

  it("clears a shared album when the field is emptied", async () => {
    const tracks = [makeTrack({ id: "track-1" }), makeTrack({ id: "track-2" })];
    for (const track of tracks) {
      vi.mocked(tracksApi.getTrack).mockResolvedValueOnce(track);
    }
    vi.mocked(tracksApi.updateTrack).mockResolvedValue(tracks[0]);

    wrapper = mountModal(["track-1", "track-2"]);
    await openModal(wrapper);

    const inputs = formInputs();
    expect(inputs[2]!.value).toBe("Shared Album");
    setInput(inputs[2]!, "");
    await flushPromises();

    await clickSave();

    expect(tracksApi.updateTrack).toHaveBeenCalledTimes(2);
    expect(tracksApi.updateTrack).toHaveBeenCalledWith("track-1", {
      album_title: "",
    });
  });

  it("does not call the API when nothing changed", async () => {
    vi.mocked(tracksApi.getTrack).mockResolvedValue(makeTrack());

    wrapper = mountModal(["track-1"]);
    await openModal(wrapper);

    await clickSave();

    expect(tracksApi.updateTrack).not.toHaveBeenCalled();
    expect(document.body.textContent).toContain(
      i18n.global.t("browse.bulkEdit.nothingToUpdate"),
    );
    expect(wrapper.emitted("saved")).toBeFalsy();
    expect(wrapper.emitted("close")).toBeFalsy();
  });

  it("syncs tags per track when the field is modified", async () => {
    const tracks = [
      makeTrack({ id: "track-1", tags: ["a", "b"] }),
      makeTrack({ id: "track-2", tags: ["b", "c"] }),
    ];
    for (const track of tracks) {
      vi.mocked(tracksApi.getTrack).mockResolvedValueOnce(track);
    }

    wrapper = mountModal(["track-1", "track-2"]);
    await openModal(wrapper);

    const tagInput = wrapper.findComponent(TagInput);
    expect(tagInput.exists()).toBe(true);
    tagInput.vm.$emit("update:modelValue", ["x"]);
    await flushPromises();

    await clickSave();

    expect(tracksApi.updateTrack).not.toHaveBeenCalled();
    expect(tagsApi.addTags).toHaveBeenCalledWith("tracks", "track-1", {
      tags: ["x"],
    });
    expect(tagsApi.addTags).toHaveBeenCalledWith("tracks", "track-2", {
      tags: ["x"],
    });
    expect(tagsApi.removeTag).toHaveBeenCalledWith("tracks", "track-1", "a");
    expect(tagsApi.removeTag).toHaveBeenCalledWith("tracks", "track-1", "b");
    expect(tagsApi.removeTag).toHaveBeenCalledWith("tracks", "track-2", "b");
    expect(tagsApi.removeTag).toHaveBeenCalledWith("tracks", "track-2", "c");
    expect(wrapper.emitted("saved")).toBeTruthy();
  });

  it("reports partial failures and keeps the modal open", async () => {
    const tracks = [makeTrack({ id: "track-1" }), makeTrack({ id: "track-2" })];
    for (const track of tracks) {
      vi.mocked(tracksApi.getTrack).mockResolvedValueOnce(track);
    }
    vi.mocked(tracksApi.updateTrack)
      .mockResolvedValueOnce(tracks[0])
      .mockRejectedValueOnce(new Error("forbidden"));

    wrapper = mountModal(["track-1", "track-2"]);
    await openModal(wrapper);

    const inputs = formInputs();
    setInput(inputs[6]!, "1999"); // change shared release year
    await flushPromises();

    await clickSave();

    expect(tracksApi.updateTrack).toHaveBeenCalledTimes(2);
    expect(wrapper.emitted("saved")).toBeTruthy();
    expect(wrapper.emitted("close")).toBeFalsy();
    expect(document.body.textContent).toContain(
      i18n.global.t("browse.bulkEdit.saveError", {
        count: 1,
        message: i18n.global.t("errors.unknown"),
      }),
    );

    const toast = useToastStore();
    expect(toast.toasts.at(-1)?.type).toBe("warning");
  });

  it("disables the filename input when an external track cannot be renamed", async () => {
    const tracks = [
      makeTrack({ id: "track-1" }),
      makeTrack({
        id: "track-2",
        is_external: true,
        can_rename_source: false,
      }),
    ];
    for (const track of tracks) {
      vi.mocked(tracksApi.getTrack).mockResolvedValueOnce(track);
    }

    wrapper = mountModal(["track-1", "track-2"]);
    await openModal(wrapper);

    const filenameInput = formInputs()[7]!;
    expect(filenameInput.disabled).toBe(true);
  });
});
