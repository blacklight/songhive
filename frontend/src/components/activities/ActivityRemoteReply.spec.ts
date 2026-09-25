import { describe, it, expect, beforeEach } from "vitest";
import { mount } from "@vue/test-utils";
import { setActivePinia, createPinia } from "pinia";
import type { RemoteReply } from "@/api/activities";
import ActivityRemoteReply from "./ActivityRemoteReply.vue";

function createReply(overrides: Partial<RemoteReply> = {}): RemoteReply {
  return {
    object_id: "https://remote.example/notes/1",
    source_actor: "https://remote.example/users/bob",
    content: "<p>hi</p>",
    ...overrides,
  };
}

function mountReply(reply: RemoteReply) {
  return mount(ActivityRemoteReply, {
    props: { reply },
    global: {
      stubs: { RouterLink: true },
      renderStubDefaultSlot: true,
    },
  });
}

describe("ActivityRemoteReply", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
  });

  it("renders the URL-derived handle for plain remote actors", () => {
    const wrapper = mountReply(createReply());
    expect(wrapper.find(".remote-reply__handle").text()).toBe(
      "@bob@remote.example",
    );
  });

  it("prefers the resolved handle for opaque actor ids", () => {
    const wrapper = mountReply(
      createReply({
        source_actor: "https://remote.example/ap/users/117220292797596489",
        source_actor_handle: "amber@remote.example",
      }),
    );
    expect(wrapper.find(".remote-reply__handle").text()).toBe(
      "@amber@remote.example",
    );
    expect(wrapper.text()).not.toContain("117220292797596489");
    const link = wrapper.findComponent({ name: "RouterLink" });
    expect(link.props("to")).toEqual({
      name: "userProfile",
      params: { username: "amber@remote.example" },
    });
  });
});
