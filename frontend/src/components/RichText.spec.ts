import { describe, it, expect } from "vitest";
import { mount } from "@vue/test-utils";
import { createRouter, createMemoryHistory } from "vue-router";
import RichText from "./RichText.vue";
import { parseRichText } from "@/utils/richText";

function createTestRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: "/", component: { template: "<div/>" } },
      {
        path: "/@:username",
        name: "userProfile",
        component: { template: "<div/>" },
      },
      {
        path: "/tags/:name",
        name: "tag",
        component: { template: "<div/>" },
      },
    ],
  });
}

describe("parseRichText", () => {
  it("returns text for inputs without links", () => {
    const tokens = parseRichText("just plain text");
    expect(tokens).toEqual([{ type: "text", value: "just plain text" }]);
  });

  it("linkifies http(s) URLs and strips trailing punctuation", () => {
    const tokens = parseRichText("See https://example.com.");
    expect(tokens).toEqual([
      { type: "text", value: "See " },
      { type: "url", url: "https://example.com", label: "example.com" },
      { type: "text", value: "." },
    ]);
  });

  it("keeps wrapping brackets outside the URL anchor", () => {
    const tokens = parseRichText("(see https://example.com/path)");
    expect(tokens).toEqual([
      { type: "text", value: "(see " },
      {
        type: "url",
        url: "https://example.com/path",
        label: "example.com/path",
      },
      { type: "text", value: ")" },
    ]);
  });

  it("linkifies local mentions", () => {
    const tokens = parseRichText("Hi @Alice!");
    expect(tokens).toEqual([
      { type: "text", value: "Hi " },
      { type: "mention", handle: "@alice", username: "alice", remote: false },
      { type: "text", value: "!" },
    ]);
  });

  it("does not treat email addresses or URLs as mentions", () => {
    const tokens = parseRichText("mail me at alice@example.com");
    expect(tokens).toEqual([
      { type: "text", value: "mail me at alice@example.com" },
    ]);
  });

  it("linkifies remote mentions", () => {
    const tokens = parseRichText("Hi @Bob@Remote.example", "local.example");
    expect(tokens).toEqual([
      { type: "text", value: "Hi " },
      {
        type: "mention",
        handle: "@bob@remote.example",
        username: "bob",
        domain: "remote.example",
        remote: true,
        url: "https://remote.example/@bob",
      },
    ]);
  });

  it("treats same-domain mentions as local", () => {
    const tokens = parseRichText("Hi @Alice@local.example", "local.example");
    expect(tokens).toEqual([
      { type: "text", value: "Hi " },
      { type: "mention", handle: "@alice", username: "alice", remote: false },
    ]);
  });

  it("linkifies valid hashtags and leaves invalid ones as text", () => {
    const tokens = parseRichText("#Music #123");
    expect(tokens).toEqual([
      { type: "tag", name: "music", display: "#Music" },
      { type: "text", value: " " },
      { type: "text", value: "#123" },
    ]);
  });

  it("does not link hashtags inside words or HTML entities", () => {
    const tokens = parseRichText("word#tag and &#123;");
    expect(tokens).toEqual([{ type: "text", value: "word#tag and &#123;" }]);
  });
});

describe("RichText", () => {
  it("renders text, mentions, tags and URLs", async () => {
    const router = createTestRouter();
    await router.push("/");
    await router.isReady();

    const wrapper = mount(RichText, {
      props: {
        text: "Hi @alice, check #music at https://example.com.",
        instanceDomain: "local.example",
      },
      global: { plugins: [router] },
    });

    const links = wrapper.findAll("a");
    expect(links.length).toBe(3);

    const html = wrapper.html();
    expect(html).toContain("Hi ");
    expect(html).toContain("@alice");
    expect(html).toContain("#music");
    expect(html).toContain("https://example.com");
  });
});
