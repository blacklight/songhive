import { onUnmounted, toValue, watch, type MaybeRefOrGetter } from "vue";
import type { FeedUrls } from "@/utils/feeds";

const FEED_LINK_SELECTOR =
  'link[rel="alternate"][type="application/rss+xml"], link[rel="alternate"][type="application/atom+xml"]';

const FEED_LINK_DEFS: ReadonlyArray<{
  key: keyof FeedUrls;
  type: string;
  title: string;
}> = [
  { key: "rss", type: "application/rss+xml", title: "RSS feed" },
  { key: "atom", type: "application/atom+xml", title: "Atom feed" },
];

/**
 * Replace every RSS/Atom ``rel="alternate"`` link in ``document.head`` with
 * links pointing at ``urls`` (or remove them all when ``urls`` is undefined).
 *
 * The backend injects the same tags into the SPA shell for the initially
 * loaded page; this keeps them accurate after client-side navigation so feed
 * readers that evaluate the DOM (and browser feed-discovery extensions) see
 * the feed of the page currently displayed.
 */
export function syncFeedLinks(urls: FeedUrls | undefined) {
  document.head
    .querySelectorAll(FEED_LINK_SELECTOR)
    .forEach((el) => el.remove());
  if (!urls) {
    return;
  }
  for (const def of FEED_LINK_DEFS) {
    const link = document.createElement("link");
    link.rel = "alternate";
    link.type = def.type;
    link.href = new URL(urls[def.key], document.baseURI).href;
    link.title = def.title;
    document.head.appendChild(link);
  }
}

/**
 * Keep ``document.head``'s RSS/Atom ``rel="alternate"`` links in sync with
 * the feed URLs of the page currently rendered. Re-runs when ``urls``
 * changes (e.g. same-view navigation to another entity) and removes the
 * links when the component unmounts.
 */
export function useFeedLinks(urls: MaybeRefOrGetter<FeedUrls | undefined>) {
  watch(() => toValue(urls), syncFeedLinks, { immediate: true });
  onUnmounted(() => syncFeedLinks(undefined));
}
