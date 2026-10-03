import { describe, it, expect, vi, beforeEach } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { createRouter, createMemoryHistory } from "vue-router";
import { i18n } from "@/i18n";
import * as remoteApi from "@/api/remote";
import * as activitiesApi from "@/api/activities";
import ObjectPermalinkView from "./ObjectPermalinkView.vue";

vi.mock("@/api/remote", () => ({
  remoteLookup: vi.fn(),
}));

vi.mock("@/api/activities", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/activities")>()),
  lookupActivity: vi.fn(),
}));

function createTestRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      {
        path: "/users/:username/objects/:objectId",
        component: ObjectPermalinkView,
      },
      { path: "/activities/:id", component: { template: "<div/>" } },
      { path: "/tracks/:id", component: { template: "<div/>" } },
    ],
  });
}

async function mountPermalink(path = "/users/alice/objects/obj-1") {
  const router = createTestRouter();
  await router.push(path);
  await router.isReady();
  const wrapper = mount(ObjectPermalinkView, {
    global: { plugins: [router] },
  });
  await flushPromises();
  return { router, wrapper };
}

describe("ObjectPermalinkView", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("redirects to the SPA route returned by the local-target lookup", async () => {
    vi.mocked(remoteApi.remoteLookup).mockResolvedValue({
      kind: "local",
      url: "/tracks/track-9",
    });

    const { router } = await mountPermalink();

    expect(remoteApi.remoteLookup).toHaveBeenCalledWith(
      `${window.location.origin}/users/alice/objects/obj-1`,
    );
    expect(router.currentRoute.value.path).toBe("/tracks/track-9");
  });

  it("falls back to the activity lookup when the remote lookup is denied", async () => {
    vi.mocked(remoteApi.remoteLookup).mockRejectedValue(new Error("denied"));
    vi.mocked(activitiesApi.lookupActivity).mockResolvedValue({
      id: "act-7",
    } as activitiesApi.ActivityResponse);

    const { router } = await mountPermalink();

    expect(activitiesApi.lookupActivity).toHaveBeenCalledWith(
      `${window.location.origin}/users/alice/objects/obj-1`,
    );
    expect(router.currentRoute.value.path).toBe("/activities/act-7");
  });

  it("does not follow an unresolved permalink echoed back by the lookup", async () => {
    // ``resolve_local_target`` returns the input path unchanged on a miss —
    // following it would re-enter the permalink route forever.
    vi.mocked(remoteApi.remoteLookup).mockResolvedValue({
      kind: "local",
      url: "/users/alice/objects/obj-1",
    });
    vi.mocked(activitiesApi.lookupActivity).mockRejectedValue(
      new Error("missing"),
    );

    const { router, wrapper } = await mountPermalink();

    expect(router.currentRoute.value.path).toBe("/users/alice/objects/obj-1");
    expect(wrapper.text()).toContain(
      i18n.global.t("activities.objectUnavailable"),
    );
  });

  it("shows the unavailable state when nothing resolves the object", async () => {
    vi.mocked(remoteApi.remoteLookup).mockRejectedValue(new Error("denied"));
    vi.mocked(activitiesApi.lookupActivity).mockRejectedValue(
      new Error("missing"),
    );

    const { wrapper } = await mountPermalink();

    expect(wrapper.text()).toContain(
      i18n.global.t("activities.objectUnavailable"),
    );
  });
});
