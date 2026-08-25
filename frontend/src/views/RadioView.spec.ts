import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { createRouter, createMemoryHistory } from "vue-router";
import { setActivePinia, createPinia } from "pinia";
import { i18n } from "@/i18n";
import * as radiosApi from "@/api/radios";
import type { TrackResponse } from "@/api/tracks";
import { usePlayerStore } from "@/stores/player";
import { useToastStore } from "@/stores/toast";
import { useAuthStore } from "@/stores/auth";
import RadioView from "./RadioView.vue";

vi.mock("@/api/radios", () => ({
  listRadios: vi.fn(),
  createRadio: vi.fn(),
  getRadioTracks: vi.fn(),
}));

function createTestRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: "/", component: { template: "<div/>" } },
      { name: "login", path: "/login", component: { template: "<div/>" } },
    ],
  });
}

function createRadio(
  id: string,
  name: string,
  visibility = "public",
  description: string | null = null,
): radiosApi.RadioResponse {
  return { id, name, description, owner_id: "user-1", visibility };
}

function createTrack(id: string, title: string): TrackResponse {
  return {
    id,
    title,
    artist_id: "artist-1",
    album_id: null,
    track_number: null,
    disc_number: null,
    duration: 185,
    genre: null,
    audio_url: "https://example.com/audio.mp3",
    visibility: "public",
    owner_id: "user-1",
  };
}

function setAuthenticated(auth: ReturnType<typeof useAuthStore>) {
  auth.accessToken = "token";
  auth.refreshToken = "refresh";
  auth.expiresAt = Date.now() + 10000;
}

describe("RadioView", () => {
  let wrapper: ReturnType<typeof mount>;
  let player: ReturnType<typeof usePlayerStore>;
  let auth: ReturnType<typeof useAuthStore>;

  beforeEach(() => {
    setActivePinia(createPinia());
    vi.resetAllMocks();
    vi.mocked(radiosApi.listRadios).mockResolvedValue([]);
    vi.mocked(radiosApi.createRadio).mockResolvedValue(
      createRadio("r1", "New Station"),
    );
    vi.mocked(radiosApi.getRadioTracks).mockResolvedValue([]);
    player = usePlayerStore();
    auth = useAuthStore();
    vi.spyOn(player, "playAll");
  });

  afterEach(() => {
    wrapper?.unmount();
    document.body.innerHTML = "";
  });

  async function mountView() {
    const router = createTestRouter();
    await router.push("/");
    await router.isReady();
    wrapper = mount(RadioView, {
      attachTo: document.body,
      global: { plugins: [router] },
    });
    await flushPromises();
  }

  it("renders radio stations on mount", async () => {
    vi.mocked(radiosApi.listRadios).mockResolvedValue([
      createRadio("r1", "Test Radio", "public", "A description"),
    ]);
    setAuthenticated(auth);

    await mountView();

    expect(radiosApi.listRadios).toHaveBeenCalledWith({ limit: 20, offset: 0 });
    expect(wrapper.text()).toContain("Test Radio");
    expect(wrapper.text()).toContain("A description");
    expect(wrapper.text()).toContain(i18n.global.t("browse.visibility.public"));
  });

  it("hides the create form when unauthenticated", async () => {
    await mountView();

    expect(wrapper.text()).not.toContain(
      i18n.global.t("pages.radio.createTitle"),
    );
    expect(wrapper.text()).toContain(i18n.global.t("pages.radio.loginHint"));
  });

  it("shows the create form when authenticated", async () => {
    setAuthenticated(auth);

    await mountView();

    expect(wrapper.text()).toContain(i18n.global.t("pages.radio.createTitle"));
    expect(wrapper.text()).not.toContain(
      i18n.global.t("pages.radio.loginHint"),
    );
  });

  it("plays a station", async () => {
    setAuthenticated(auth);
    const track = createTrack("t1", "Song One");
    vi.mocked(radiosApi.listRadios).mockResolvedValue([
      createRadio("r1", "Station One"),
    ]);
    vi.mocked(radiosApi.getRadioTracks).mockResolvedValue([track]);

    await mountView();

    const play = wrapper
      .findAll("button")
      .find((b) => b.text() === i18n.global.t("common.play"));
    expect(play).toBeDefined();
    await play?.trigger("click");
    await flushPromises();

    expect(radiosApi.getRadioTracks).toHaveBeenCalledWith("r1", {
      count: 20,
    });
    expect(player.playAll).toHaveBeenCalledWith(
      expect.arrayContaining([
        expect.objectContaining({
          id: "t1",
          title: "Song One",
          artist_name: "",
        }),
      ]),
    );
  });

  it("creates a station", async () => {
    setAuthenticated(auth);
    const listRadios = vi.mocked(radiosApi.listRadios);
    listRadios
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce([createRadio("r1", "New Station")]);

    await mountView();

    const nameInput = wrapper.find('input[type="text"]');
    await nameInput.setValue("New Station");
    await flushPromises();

    const textareas = wrapper.findAll("textarea");
    await textareas[0].setValue("A station");
    await textareas[1].setValue("config");
    await flushPromises();

    const select = wrapper.find("select").element as HTMLSelectElement;
    select.value = "public";
    select.dispatchEvent(new Event("change"));
    await flushPromises();

    const createButton = wrapper
      .findAll("button")
      .find((b) => b.text() === i18n.global.t("pages.radio.create"));
    expect(createButton).toBeDefined();
    (createButton?.element as HTMLButtonElement)?.click();
    await flushPromises();

    expect(radiosApi.createRadio).toHaveBeenCalledWith(
      expect.objectContaining({
        name: "New Station",
        description: "A station",
        config: "config",
      }),
      "public",
    );
    expect(listRadios).toHaveBeenCalledTimes(2);
    expect(wrapper.text()).toContain("New Station");
  });

  it("shows an error banner with a retry button", async () => {
    vi.mocked(radiosApi.listRadios).mockRejectedValue(
      new Error("network failure"),
    );

    await mountView();

    expect(wrapper.text()).toContain("network failure");

    vi.mocked(radiosApi.listRadios).mockResolvedValue([
      createRadio("r1", "Station One"),
    ]);

    const retry = wrapper
      .findAll("button")
      .find((b) => b.text() === i18n.global.t("common.retry"));
    expect(retry).toBeDefined();
    await retry?.trigger("click");
    await flushPromises();

    expect(wrapper.text()).toContain("Station One");
    expect(wrapper.text()).not.toContain("network failure");
  });

  it("loads more stations", async () => {
    setAuthenticated(auth);
    const listRadios = vi.mocked(radiosApi.listRadios);
    listRadios
      .mockResolvedValueOnce(
        Array.from({ length: 20 }, (_, i) =>
          createRadio(`r${i}`, `Station ${i}`),
        ),
      )
      .mockResolvedValueOnce([createRadio("r20", "Station 20")]);

    await mountView();

    const loadMore = wrapper
      .findAll("button")
      .find((b) => b.text() === i18n.global.t("browse.list.loadMore"));
    expect(loadMore).toBeDefined();
    await loadMore?.trigger("click");
    await flushPromises();

    expect(listRadios).toHaveBeenLastCalledWith({ limit: 20, offset: 20 });
    expect(wrapper.text()).toContain("Station 20");
  });

  it("shows a toast when play fails", async () => {
    setAuthenticated(auth);
    const toast = useToastStore();
    vi.mocked(radiosApi.listRadios).mockResolvedValue([
      createRadio("r1", "Station One"),
    ]);
    vi.mocked(radiosApi.getRadioTracks).mockRejectedValue(
      new Error("no tracks"),
    );

    await mountView();

    const play = wrapper
      .findAll("button")
      .find((b) => b.text() === i18n.global.t("common.play"));
    await play?.trigger("click");
    await flushPromises();

    expect(player.playAll).not.toHaveBeenCalled();
    expect(toast.toasts).toHaveLength(1);
    expect(toast.toasts[0].type).toBe("error");
    expect(toast.toasts[0].message).toContain("no tracks");
  });

  it("shows the empty state", async () => {
    setAuthenticated(auth);
    vi.mocked(radiosApi.listRadios).mockResolvedValue([]);

    await mountView();

    expect(wrapper.text()).toContain(i18n.global.t("pages.radio.empty"));
  });
});
