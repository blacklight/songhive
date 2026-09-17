import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { createMemoryHistory, createRouter } from "vue-router";
import { i18n } from "@/i18n";
import { getRemoteObject, type RemoteObject } from "@/api/remote";
import type { ActivityResponse } from "@/api/activities";
import { ApiError } from "@/api/client";
import RemoteActivityView from "./RemoteActivityView.vue";

vi.mock("@/api/remote", () => ({
  getRemoteObject: vi.fn(),
}));

function createObject(overrides?: Partial<RemoteObject>): RemoteObject {
  return {
    id: "obj-1",
    canonical_url: "https://remote.example/users/alice/objects/1",
    object_type: "Note",
    resource_type: null,
    domain: "remote.example",
    actor_url: "https://remote.example/users/alice",
    name: null,
    content: "<p>hello fediverse</p>",
    visibility: "public",
    unavailable: false,
    url: "/activities/@alice@remote.example/obj-1",
    ...overrides,
  };
}

function createRouterFor() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      {
        path: "/activities/@:handle/:id",
        name: "remoteActivity",
        component: RemoteActivityView,
      },
      {
        path: "/remote/:kind/:id",
        name: "remoteResource",
        component: { template: "<div />" },
      },
      {
        path: "/@:username",
        name: "user",
        component: { template: "<div />" },
      },
    ],
  });
}

describe("RemoteActivityView", () => {
  let wrapper: ReturnType<typeof mount>;

  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    wrapper?.unmount();
    document.body.innerHTML = "";
  });

  async function mountView(id = "obj-1") {
    const router = createRouterFor();
    await router.push(`/activities/@alice@remote.example/${id}`);
    await router.isReady();
    wrapper = mount(RemoteActivityView, {
      global: {
        plugins: [router, i18n],
        stubs: { ActivityCard: true },
      },
    });
    await flushPromises();
    return router;
  }

  it("refreshes the canonical URL on load", async () => {
    vi.mocked(getRemoteObject).mockResolvedValue({
      object: createObject(),
      activity: { id: "act-1" } as ActivityResponse,
    });
    await mountView();
    expect(getRemoteObject).toHaveBeenCalledWith("obj-1", { refresh: true });
  });

  it("renders the materialized activity card", async () => {
    vi.mocked(getRemoteObject).mockResolvedValue({
      object: createObject(),
      activity: { id: "act-1" } as ActivityResponse,
    });
    await mountView();
    expect(wrapper.findComponent({ name: "ActivityCard" }).exists()).toBe(true);
  });

  it("shows the tombstone notice for unavailable objects", async () => {
    vi.mocked(getRemoteObject).mockResolvedValue({
      object: createObject({ unavailable: true }),
      activity: null,
    });
    await mountView();
    expect(wrapper.find(".remote-activity__tombstone").exists()).toBe(true);
  });

  it("renders remote HTML as safe segments when no activity exists", async () => {
    vi.mocked(getRemoteObject).mockResolvedValue({
      object: createObject({
        content:
          "<p>hello <strong>fediverse</strong> " +
          '<a href="https://remote.example/x">link</a></p>' +
          "<script>alert(1)</script>",
      }),
      activity: null,
    });
    await mountView();
    const card = wrapper.find(".remote-activity__object");
    expect(card.exists()).toBe(true);
    const content = card.find(".remote-activity__content");
    // Remote HTML is sanitized into segments — links and formatting render,
    // script contents never surface.
    expect(content.text()).toContain("hello fediverse");
    expect(content.find("a").attributes("href")).toBe(
      "https://remote.example/x",
    );
    expect(content.text()).not.toContain("alert");
  });

  it("redirects resource objects to the remote resource route", async () => {
    vi.mocked(getRemoteObject).mockResolvedValue({
      object: createObject({ resource_type: "track" }),
      activity: null,
    });
    const router = await mountView();
    await flushPromises();
    expect(router.currentRoute.value.name).toBe("remoteResource");
    expect(router.currentRoute.value.params).toMatchObject({
      kind: "track",
      id: "obj-1",
    });
  });

  it("renders an error state when the object cannot be loaded", async () => {
    vi.mocked(getRemoteObject).mockRejectedValue(
      new ApiError("Remote object not found", 404),
    );
    await mountView();
    expect(wrapper.find(".remote-activity__error").exists()).toBe(true);
  });

  it("links back to the remote actor profile", async () => {
    vi.mocked(getRemoteObject).mockResolvedValue({
      object: createObject(),
      activity: { id: "act-1" } as ActivityResponse,
    });
    await mountView();
    const back = wrapper.find(".remote-activity__back");
    expect(back.attributes("href")).toBe("/@alice@remote.example");
  });
});
