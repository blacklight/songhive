import { describe, it, expect } from "vitest";
import { mount } from "@vue/test-utils";
import { i18n } from "@/i18n";
import type { SearchResultSection } from "@/api/search";
import SearchSuggestions from "./SearchSuggestions.vue";

const sections: SearchResultSection[] = [
  {
    entity: "tracks",
    total: 1,
    items: [
      {
        type: "track",
        id: "track-1",
        title: "Waiting Room",
        url: "/tracks/track-1",
      },
    ],
  },
];

function mountSuggestions(props: {
  sections?: SearchResultSection[];
  loading?: boolean;
  error?: string | null;
  query?: string;
  remote?: boolean;
}) {
  return mount(SearchSuggestions, {
    props: {
      sections: [],
      loading: false,
      error: null,
      ...props,
    },
    global: { plugins: [i18n] },
  });
}

function keydown(key: string): KeyboardEvent {
  return new KeyboardEvent("keydown", { key });
}

describe("SearchSuggestions remote entry", () => {
  it("offers a fediverse lookup for @user@domain queries", () => {
    const wrapper = mountSuggestions({
      sections,
      query: "@alice@remote.example",
      remote: true,
    });

    const entry = wrapper.find(".search-suggestions__section--remote");
    expect(entry.exists()).toBe(true);
    expect(entry.text()).toContain("@alice@remote.example");
  });

  it("offers a fediverse lookup for https:// URL queries", () => {
    const wrapper = mountSuggestions({
      sections,
      query: "https://remote.example/objects/1",
      remote: true,
    });

    const entry = wrapper.find(".search-suggestions__section--remote");
    expect(entry.exists()).toBe(true);
    expect(entry.text()).toContain("https://remote.example/objects/1");
  });

  it("shows the remote entry even when there are no local results", () => {
    const wrapper = mountSuggestions({
      sections: [],
      query: "@alice@remote.example",
      remote: true,
    });

    expect(wrapper.find(".search-suggestions__empty").exists()).toBe(false);
    expect(wrapper.find(".search-suggestions__section--remote").exists()).toBe(
      true,
    );
  });

  it.each([
    ["plain text", "waiting room"],
    ["non-https URL", "http://remote.example/x"],
    ["handle without leading @", "alice@remote.example"],
    ["bare mention", "@alice"],
  ])("does not offer the entry for %s", (_label, query) => {
    const wrapper = mountSuggestions({ sections, query, remote: true });
    expect(wrapper.find(".search-suggestions__section--remote").exists()).toBe(
      false,
    );
  });

  it("does not offer the entry when remote lookup is unavailable", () => {
    const wrapper = mountSuggestions({
      sections,
      query: "@alice@remote.example",
      remote: false,
    });
    expect(wrapper.find(".search-suggestions__section--remote").exists()).toBe(
      false,
    );
  });

  it("emits remote-lookup with the query on click", async () => {
    const wrapper = mountSuggestions({
      sections,
      query: "@alice@remote.example",
      remote: true,
    });

    await wrapper
      .find(".search-suggestions__section--remote button")
      .trigger("click");

    expect(wrapper.emitted("remote-lookup")?.[0]).toEqual([
      "@alice@remote.example",
    ]);
    expect(wrapper.emitted("select")).toBeFalsy();
  });

  it("reaches the remote entry with arrow keys and selects it with Enter", async () => {
    const wrapper = mountSuggestions({
      sections,
      query: "@alice@remote.example",
      remote: true,
    });

    const vm = wrapper.vm as unknown as {
      handleKeydown(event: KeyboardEvent): boolean;
      activeDescendantId(): string | undefined;
    };

    expect(vm.handleKeydown(keydown("ArrowDown"))).toBe(true);
    expect(vm.handleKeydown(keydown("ArrowDown"))).toBe(true);
    await wrapper.vm.$nextTick();

    const entry = wrapper.find(".search-suggestions__section--remote button");
    expect(entry.classes()).toContain("search-suggestions__item--active");
    expect(vm.activeDescendantId()).toBe(entry.attributes("id"));

    expect(vm.handleKeydown(keydown("Enter"))).toBe(true);
    expect(wrapper.emitted("remote-lookup")?.[0]).toEqual([
      "@alice@remote.example",
    ]);
  });

  it("wraps the highlight from the remote entry back to the first item", () => {
    const wrapper = mountSuggestions({
      sections,
      query: "@alice@remote.example",
      remote: true,
    });

    const vm = wrapper.vm as unknown as {
      handleKeydown(event: KeyboardEvent): boolean;
    };

    vm.handleKeydown(keydown("ArrowDown"));
    vm.handleKeydown(keydown("ArrowDown"));
    expect(vm.handleKeydown(keydown("ArrowDown"))).toBe(true);
    expect(vm.handleKeydown(keydown("Enter"))).toBe(true);

    expect(wrapper.emitted("remote-lookup")).toBeFalsy();
    expect(wrapper.emitted("select")?.[0]).toEqual([sections[0].items[0]]);
  });
});
