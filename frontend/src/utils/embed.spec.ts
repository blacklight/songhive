import { describe, it, expect } from "vitest";
import {
  audioEmbed,
  embedIframeHeight,
  getEmbedPageUrl,
  getEmbedScriptUrl,
  iframeEmbed,
  markdownEmbed,
  scriptEmbed,
  trackLinkText,
  trackListEmbed,
  EMBED_LIST_MAX_TRACKS,
} from "./embed";

describe("embed utils", () => {
  it("builds the embed page URL for an entity", () => {
    expect(getEmbedPageUrl("track", "abc-123")).toBe(
      "http://localhost:3000/embed/track/abc-123",
    );
    expect(getEmbedPageUrl("playlist", "pl-1")).toBe(
      "http://localhost:3000/embed/playlist/pl-1",
    );
  });

  it("builds the embed script URL", () => {
    expect(getEmbedScriptUrl()).toBe("http://localhost:3000/embed.js");
  });

  it("uses a compact iframe height for tracks and a taller one for collections", () => {
    expect(embedIframeHeight("track")).toBeLessThan(embedIframeHeight("album"));
    expect(embedIframeHeight("album")).toBe(embedIframeHeight("playlist"));
    expect(embedIframeHeight("playlist")).toBe(embedIframeHeight("library"));
    expect(embedIframeHeight("library")).toBe(embedIframeHeight("artist"));
  });

  describe("trackLinkText", () => {
    it("joins artist and title when both are available", () => {
      expect(
        trackLinkText({ artistName: "The Larks", title: "Song One" }),
      ).toBe("The Larks - Song One");
    });

    it("falls back to the title alone", () => {
      expect(trackLinkText({ title: "Song One" })).toBe("Song One");
      expect(trackLinkText({ artistName: "", title: "Song One" })).toBe(
        "Song One",
      );
    });

    it("falls back to the filename", () => {
      expect(trackLinkText({ filename: "song-one.mp3" })).toBe("song-one.mp3");
      expect(trackLinkText({ title: "  ", filename: "song-one.mp3" })).toBe(
        "song-one.mp3",
      );
    });
  });

  describe("markdownEmbed", () => {
    it("wraps the label and url", () => {
      expect(markdownEmbed("The Larks - Song One", "https://x/t/1")).toBe(
        "[The Larks - Song One](https://x/t/1)",
      );
    });

    it("escapes square brackets in the label", () => {
      expect(markdownEmbed("A [B] C", "https://x/t/1")).toBe(
        "[A \\[B\\] C](https://x/t/1)",
      );
    });
  });

  describe("audioEmbed", () => {
    it("renders an audio tag inside a paragraph with a metadata link", () => {
      const html = audioEmbed({
        pageUrl: "https://music.example.com/tracks/t1",
        audioUrl: "/api/v1/files/f1/download",
        label: "The Larks - Song One",
      });
      expect(html).toContain(
        '<a href="https://music.example.com/tracks/t1">The Larks - Song One</a>',
      );
      expect(html).toContain(
        '<audio src="http://localhost:3000/api/v1/files/f1/download" controls',
      );
      expect(html.startsWith("<p>")).toBe(true);
      expect(html.endsWith("</p>")).toBe(true);
    });

    it("escapes HTML in metadata", () => {
      const html = audioEmbed({
        pageUrl: "https://x/t/1",
        audioUrl: "https://x/a.mp3",
        label: '<img onerror="x">',
      });
      expect(html).not.toContain("<img");
      expect(html).toContain("&lt;img");
    });
  });

  describe("trackListEmbed", () => {
    it("renders a div with one audio element per track", () => {
      const html = trackListEmbed({
        pageUrl: "https://x/albums/a1",
        title: "Meadowland",
        tracks: [
          { label: "A - One", audioUrl: "/a1.mp3" },
          { label: "A - Two", audioUrl: "/a2.mp3" },
        ],
      });
      expect(html).toContain('<div class="songhive-tracklist">');
      expect(html).toContain('<a href="https://x/albums/a1">Meadowland</a>');
      expect(html.match(/<audio /g)?.length).toBe(2);
      expect(html).toContain("http://localhost:3000/a1.mp3");
    });
  });

  describe("scriptEmbed", () => {
    it("emits a placeholder div and the embed script tag", () => {
      const html = scriptEmbed("track", "t-1");
      expect(html).toContain(
        '<div class="songhive-embed" data-type="track" data-id="t-1"></div>',
      );
      expect(html).toContain(
        '<script async src="http://localhost:3000/embed.js"',
      );
    });
  });

  describe("iframeEmbed", () => {
    it("emits an iframe pointing at the embed page", () => {
      const html = iframeEmbed("album", "a-1", "Meadowland");
      expect(html).toContain('src="http://localhost:3000/embed/album/a-1"');
      expect(html).toContain('title="Meadowland"');
      expect(html).toContain(`height="${embedIframeHeight("album")}"`);
    });
  });

  it("caps the inline track list embed size", () => {
    expect(EMBED_LIST_MAX_TRACKS).toBe(250);
  });
});
