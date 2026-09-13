import { describe, it, expect } from "vitest";
import { parseActivityContent } from "./activityContent";
import { parseActorRef } from "./actorRef";

const DOMAIN = "songhive.example";

describe("parseActorRef", () => {
  it("parses the urn fallback", () => {
    expect(parseActorRef("urn:songhive:user:alice", DOMAIN)).toEqual({
      username: "alice",
      remoteUrl: null,
      handle: "@alice",
    });
  });

  it("parses relative and same-host actor URLs as local", () => {
    for (const ref of [
      "/users/alice",
      "/@alice",
      `https://${DOMAIN}/users/alice`,
      `https://${DOMAIN}/@alice`,
    ]) {
      expect(parseActorRef(ref, DOMAIN).username).toBe("alice");
    }
  });

  it("parses other-host URLs as remote", () => {
    const ref = parseActorRef("https://remote.example/users/bob", DOMAIN);
    expect(ref.username).toBeNull();
    expect(ref.remoteUrl).toBe("https://remote.example/users/bob");
    expect(ref.handle).toBe("@bob@remote.example");
  });
});

describe("parseActivityContent", () => {
  it("keeps mention anchors as remote links with their real href", () => {
    const segments = parseActivityContent(
      '<p>hi <a href="https://remote.example/@bob" class="u-url mention">@bob</a></p>',
      { instanceDomain: DOMAIN },
    );
    expect(segments).toEqual([
      { type: "text", value: "hi " },
      {
        type: "mention",
        handle: "@bob",
        url: "https://remote.example/@bob",
      },
    ]);
  });

  it("routes same-host actor anchors to the local profile", () => {
    const segments = parseActivityContent(
      `<a href="https://${DOMAIN}/users/alice">@alice</a> hi`,
      { instanceDomain: DOMAIN },
    );
    expect(segments).toEqual([
      { type: "mention", handle: "@alice", username: "alice" },
      { type: "text", value: " hi" },
    ]);
  });

  it("routes relative /users/ anchors to the local profile", () => {
    const segments = parseActivityContent('<a href="/users/alice">@alice</a>');
    expect(segments).toEqual([
      { type: "mention", handle: "@alice", username: "alice" },
    ]);
  });

  it("linkifies bare local and remote handles in text", () => {
    const segments = parseActivityContent("hi @alice and @bob@remote.example", {
      instanceDomain: DOMAIN,
    });
    expect(segments).toEqual([
      { type: "text", value: "hi " },
      { type: "mention", handle: "@alice", username: "alice", url: undefined },
      { type: "text", value: " and " },
      {
        type: "mention",
        handle: "@bob@remote.example",
        username: undefined,
        url: "https://remote.example/@bob",
      },
    ]);
  });

  it("treats same-domain handles as local", () => {
    const segments = parseActivityContent(`hi @alice@${DOMAIN}`, {
      instanceDomain: DOMAIN,
    });
    expect(segments[1]).toEqual({
      type: "mention",
      handle: "@alice",
      username: "alice",
      url: undefined,
    });
  });

  it("prefers the mentions list actor URL over the guessed one", () => {
    const segments = parseActivityContent("hi @bob@remote.example", {
      instanceDomain: DOMAIN,
      mentions: [
        {
          handle: "@bob@remote.example",
          actor_url: "https://remote.example/users/bob",
        },
      ],
    });
    expect(segments[1]).toEqual({
      type: "mention",
      handle: "@bob@remote.example",
      url: "https://remote.example/users/bob",
    });
  });

  it("resolves mention entries pointing at local actors", () => {
    const segments = parseActivityContent("hi @alice", {
      instanceDomain: DOMAIN,
      mentions: [
        {
          handle: "@alice",
          actor_url: `https://${DOMAIN}/users/alice`,
          user_id: "u1",
        },
      ],
    });
    expect(segments[1]).toEqual({
      type: "mention",
      handle: "@alice",
      username: "alice",
    });
  });

  it("renders tag anchors as tag segments", () => {
    const segments = parseActivityContent(
      '<a href="https://remote.example/tags/music" rel="tag">#music</a>',
      { instanceDomain: DOMAIN },
    );
    expect(segments).toEqual([
      { type: "tag", name: "music", display: "#music" },
    ]);
  });

  it("renders same-host links as local routes and others as external", () => {
    const segments = parseActivityContent(
      `<a href="https://${DOMAIN}/tracks/t1">a track</a> and <a href="https://remote.example/page">a page</a>`,
      { instanceDomain: DOMAIN },
    );
    expect(segments).toEqual([
      { type: "link", label: "a track", to: "/tracks/t1" },
      { type: "text", value: " and " },
      { type: "link", label: "a page", url: "https://remote.example/page" },
    ]);
  });

  it("linkifies bare URLs in text", () => {
    const segments = parseActivityContent("see https://remote.example/x now", {
      instanceDomain: DOMAIN,
    });
    expect(segments).toEqual([
      { type: "text", value: "see " },
      {
        type: "link",
        label: "remote.example/x",
        url: "https://remote.example/x",
      },
      { type: "text", value: " now" },
    ]);
  });

  it("drops unsafe hrefs but keeps the label text", () => {
    const segments = parseActivityContent(
      '<a href="javascript:alert(1)">click</a> <a href="javascript:alert(2)"></a>',
    );
    expect(segments).toEqual([{ type: "text", value: "click" }]);
  });

  it("skips script/style content entirely", () => {
    const segments = parseActivityContent(
      "<p>hi<script>alert(1)</script><style>x</style></p>",
    );
    expect(segments).toEqual([{ type: "text", value: "hi" }]);
  });

  it("converts block elements and <br> to newlines", () => {
    const segments = parseActivityContent("<p>one</p><p>two<br>three</p>", {
      instanceDomain: DOMAIN,
    });
    expect(segments).toEqual([{ type: "text", value: "one\ntwo\nthree" }]);
  });

  it("breaks the line before a block element following inline content", () => {
    // ``normalize_post_content`` appends the track-page link as its own
    // ``<p>`` after the post body; it must not render glued to the text.
    const segments = parseActivityContent(
      'a great track<p><a href="/tracks/t1">Artist - Title</a></p>',
      { instanceDomain: DOMAIN },
    );
    expect(segments).toEqual([
      { type: "text", value: "a great track\n" },
      { type: "link", label: "Artist - Title", to: "/tracks/t1" },
    ]);
  });

  it("carries inline font formatting as marks on text segments", () => {
    const segments = parseActivityContent(
      "<p>plain <strong>bold</strong> and <em>italic</em> " +
        "with <code>code</code> and <del>struck</del> and <u>under</u></p>",
    );
    expect(segments).toEqual([
      { type: "text", value: "plain " },
      { type: "text", value: "bold", marks: ["bold"] },
      { type: "text", value: " and " },
      { type: "text", value: "italic", marks: ["italic"] },
      { type: "text", value: " with " },
      { type: "text", value: "code", marks: ["code"] },
      { type: "text", value: " and " },
      { type: "text", value: "struck", marks: ["strikethrough"] },
      { type: "text", value: " and " },
      { type: "text", value: "under", marks: ["underline"] },
    ]);
  });

  it("stacks nested marks and applies them to links and mentions", () => {
    const segments = parseActivityContent(
      '<p><strong><em>both</em> <a href="https://x.example/p">link</a>' +
        ' <a href="/users/alice">@alice</a></strong></p>',
      { instanceDomain: DOMAIN },
    );
    expect(segments).toEqual([
      { type: "text", value: "both", marks: ["bold", "italic"] },
      { type: "text", value: " ", marks: ["bold"] },
      {
        type: "link",
        label: "link",
        url: "https://x.example/p",
        marks: ["bold"],
      },
      { type: "text", value: " ", marks: ["bold"] },
      {
        type: "mention",
        handle: "@alice",
        username: "alice",
        marks: ["bold"],
      },
    ]);
  });

  it("merges adjacent text with the same marks across nesting order", () => {
    const segments = parseActivityContent(
      "<p><strong>a<em>b</em></strong><em><strong>c</strong></em></p>",
    );
    // ``a`` is bold, ``b`` is bold+italic, ``c`` is bold+italic too — the
    // different nesting order must not split ``b`` and ``c``.
    expect(segments).toEqual([
      { type: "text", value: "a", marks: ["bold"] },
      { type: "text", value: "bc", marks: ["bold", "italic"] },
    ]);
  });

  it("keeps block newlines but marks heading text as bold", () => {
    const segments = parseActivityContent("<h2>Title</h2><p>body</p>");
    expect(segments).toEqual([
      { type: "text", value: "Title", marks: ["bold"] },
      { type: "text", value: "\nbody" },
    ]);
  });

  it("renders unordered list items as bullet-prefixed lines", () => {
    const segments = parseActivityContent(
      "<p>intro</p><ul><li>one</li><li>two</li></ul><p>outro</p>",
    );
    expect(segments).toEqual([
      { type: "text", value: "intro\n• one\n• two\noutro" },
    ]);
  });

  it("renders ordered list items as numbered lines, honoring start", () => {
    const segments = parseActivityContent(
      '<ol><li>first</li><li>second</li></ol><ol start="4"><li>fourth</li></ol>',
    );
    expect(segments).toEqual([
      { type: "text", value: "1. first\n2. second\n4. fourth" },
    ]);
  });

  it("indents nested lists two spaces per level", () => {
    const segments = parseActivityContent(
      "<ul><li>a<ul><li>nested</li></ul></li><li>b</li></ul>",
    );
    expect(segments).toEqual([{ type: "text", value: "• a\n  • nested\n• b" }]);
  });

  it("keeps the marker on the same line as block children", () => {
    const segments = parseActivityContent(
      "<ul><li><p>wrapped</p></li><li>plain</li></ul>",
    );
    expect(segments).toEqual([{ type: "text", value: "• wrapped\n• plain" }]);
  });

  it("preserves marks inside list items", () => {
    const segments = parseActivityContent(
      "<ul><li><strong>bold</strong> item</li></ul>",
    );
    expect(segments).toEqual([
      { type: "text", value: "• " },
      { type: "text", value: "bold", marks: ["bold"] },
      { type: "text", value: " item" },
    ]);
  });

  it("omits Mastodon invisible link chrome and marks ellipsis", () => {
    const segments = parseActivityContent(
      '<a href="https://remote.example/very/long/url">' +
        '<span class="invisible">https://</span>' +
        '<span class="ellipsis">remote.example/very</span>' +
        '<span class="invisible">/long/url</span></a>',
      { instanceDomain: DOMAIN },
    );
    expect(segments).toEqual([
      {
        type: "link",
        label: "remote.example/very…",
        url: "https://remote.example/very/long/url",
      },
    ]);
  });
});
