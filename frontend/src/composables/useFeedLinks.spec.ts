import { describe, it, expect, beforeEach } from "vitest";
import { defineComponent, ref } from "vue";
import { mount } from "@vue/test-utils";
import {
  syncAlternateLinks,
  syncFeedLinks,
  useAlternateLinks,
  useFeedLinks,
} from "./useFeedLinks";

const FEED_SELECTOR =
  'link[rel="alternate"][type="application/rss+xml"], link[rel="alternate"][type="application/atom+xml"]';

const feedHrefs = () =>
  Array.from(document.head.querySelectorAll(FEED_SELECTOR)).map((el) =>
    el.getAttribute("href"),
  );

describe("syncFeedLinks", () => {
  beforeEach(() => {
    document.head.querySelectorAll(FEED_SELECTOR).forEach((el) => el.remove());
  });

  it("injects absolute RSS and Atom links", () => {
    syncFeedLinks({
      rss: "/feeds/artists/abc.rss",
      atom: "/feeds/artists/abc.atom",
    });
    const links = document.head.querySelectorAll(FEED_SELECTOR);
    expect(links).toHaveLength(2);
    expect(links[0].getAttribute("type")).toBe("application/rss+xml");
    expect(links[0].getAttribute("href")).toBe(
      "http://localhost:3000/feeds/artists/abc.rss",
    );
    expect(links[0].getAttribute("title")).toBe("RSS feed");
    expect(links[1].getAttribute("type")).toBe("application/atom+xml");
    expect(links[1].getAttribute("href")).toBe(
      "http://localhost:3000/feeds/artists/abc.atom",
    );
    expect(links[1].getAttribute("title")).toBe("Atom feed");
  });

  it("replaces previously injected links instead of duplicating them", () => {
    syncFeedLinks({ rss: "/feeds/a.rss", atom: "/feeds/a.atom" });
    syncFeedLinks({ rss: "/feeds/b.rss", atom: "/feeds/b.atom" });
    expect(feedHrefs()).toEqual([
      "http://localhost:3000/feeds/b.rss",
      "http://localhost:3000/feeds/b.atom",
    ]);
  });

  it("removes server-injected feed links but keeps other alternates", () => {
    const serverLink = document.createElement("link");
    serverLink.rel = "alternate";
    serverLink.type = "application/rss+xml";
    serverLink.href = "http://localhost:3000/feeds/old.rss";
    document.head.appendChild(serverLink);
    const apLink = document.createElement("link");
    apLink.rel = "alternate";
    apLink.type = "application/activity+json";
    apLink.href = "http://localhost:3000/ap/objects/1";
    document.head.appendChild(apLink);

    syncFeedLinks(undefined);

    expect(feedHrefs()).toEqual([]);
    expect(
      document.head.querySelector('link[type="application/activity+json"]'),
    ).not.toBeNull();
  });
});

describe("syncAlternateLinks", () => {
  beforeEach(() => {
    document.head.querySelectorAll(FEED_SELECTOR).forEach((el) => el.remove());
  });

  it("injects arbitrary alternate links, e.g. an external podcast feed", () => {
    syncAlternateLinks([
      {
        href: "https://podcast.example.com/feed.xml",
        type: "application/rss+xml",
        title: "My Podcast",
      },
    ]);
    const links = document.head.querySelectorAll(FEED_SELECTOR);
    expect(links).toHaveLength(1);
    expect(links[0].getAttribute("href")).toBe(
      "https://podcast.example.com/feed.xml",
    );
    expect(links[0].getAttribute("title")).toBe("My Podcast");
  });

  it("replaces feed links and clears them on undefined", () => {
    syncFeedLinks({ rss: "/feeds/a.rss", atom: "/feeds/a.atom" });
    syncAlternateLinks([
      {
        href: "https://podcast.example.com/feed.xml",
        type: "application/rss+xml",
        title: "My Podcast",
      },
    ]);
    expect(feedHrefs()).toEqual(["https://podcast.example.com/feed.xml"]);
    syncAlternateLinks(undefined);
    expect(feedHrefs()).toEqual([]);
  });
});

describe("useAlternateLinks", () => {
  beforeEach(() => {
    document.head.querySelectorAll(FEED_SELECTOR).forEach((el) => el.remove());
  });

  it("tracks a reactive link list and cleans up on unmount", async () => {
    const links = ref([
      {
        href: "https://podcast.example.com/feed.xml",
        type: "application/rss+xml",
        title: "My Podcast",
      },
    ]);
    const wrapper = mount(
      defineComponent({
        setup() {
          useAlternateLinks(links);
          return () => null;
        },
      }),
    );
    expect(feedHrefs()).toEqual(["https://podcast.example.com/feed.xml"]);

    links.value = [
      {
        href: "https://other.example.com/rss",
        type: "application/rss+xml",
        title: "Other",
      },
    ];
    await Promise.resolve();
    expect(feedHrefs()).toEqual(["https://other.example.com/rss"]);

    wrapper.unmount();
    expect(feedHrefs()).toEqual([]);
  });
});

describe("useFeedLinks", () => {
  beforeEach(() => {
    document.head.querySelectorAll(FEED_SELECTOR).forEach((el) => el.remove());
  });

  const mountView = () => {
    const urls = ref({ rss: "/feeds/a.rss", atom: "/feeds/a.atom" });
    const wrapper = mount(
      defineComponent({
        setup() {
          useFeedLinks(urls);
          return () => null;
        },
      }),
    );
    return { wrapper, urls };
  };

  it("injects links on mount and removes them on unmount", () => {
    const { wrapper } = mountView();
    expect(feedHrefs()).toEqual([
      "http://localhost:3000/feeds/a.rss",
      "http://localhost:3000/feeds/a.atom",
    ]);
    wrapper.unmount();
    expect(feedHrefs()).toEqual([]);
  });

  it("updates links when the feed urls change", async () => {
    const { urls } = mountView();
    urls.value = { rss: "/feeds/b.rss", atom: "/feeds/b.atom" };
    await Promise.resolve();
    expect(feedHrefs()).toEqual([
      "http://localhost:3000/feeds/b.rss",
      "http://localhost:3000/feeds/b.atom",
    ]);
  });
});
